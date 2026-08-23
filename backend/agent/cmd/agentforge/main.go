// Command agentforge is the AgentForge backend: the WebSocket the Studio
// streams a run over, the HTTP API it calls, and every agent behind them — the
// builder, QA, the SRS and the deployment agent. It is one process with no
// subprocess of its own but the database.
package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"agentforge/agent/builder"
	"agentforge/agent/core"
	"agentforge/agent/deploy"
	"agentforge/agent/qa"
	"agentforge/agent/server"
	"agentforge/agent/srs"
)

func main() {
	log.SetFlags(log.Ltime)

	paths := core.DiscoverPaths()
	hub := core.NewHub()
	llm := core.NewLLM()
	mongo := server.NewMongo(paths)

	srv := server.New(hub, paths, llm, mongo)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// The connection string is settled first so the SRS store knows where to
	// look, but fetching mongod can take minutes and must not hold up the
	// Studio: it finishes in the background and /mongo reports it.
	mongo.Resolve()

	// The SRS service is part of this binary. A machine with no database still
	// gets a working service, in memory, rather than a failure to start.
	srv.SRS = srs.NewService(srs.NewRepo(srs.Connect(ctx, mongo.URI(), srs.DatabaseName)), llm, paths)
	srv.Agent = builder.NewAgent(srv.Pictures, srv.SRS)

	// The deployment agent is part of this binary too. A machine with no AWS
	// or GitHub still gets a working service; it says so when asked to deploy.
	if agent, err := deploy.NewAgent(paths.Deploy, llm, hub); err != nil {
		log.Printf("the deployment agent could not start: %v", err)
	} else {
		srv.Deploy = agent
		go agent.Watch(ctx)
	}

	go mongo.Ensure(ctx)

	errs := make(chan error, 2)
	go func() {
		errs <- srv.ServeHTTP(ctx, addr(core.UIPort), func(project string) ([]byte, error) {
			return qa.PDF(paths, project)
		})
	}()
	go func() {
		errs <- srv.ServeWS(ctx, addr(core.WSPort))
	}()

	banner(paths, mongo)

	select {
	case <-ctx.Done():
		fmt.Println("\n🛑 Shutting down…")
	case err := <-errs:
		if err != nil {
			log.Printf("a listener stopped: %v", err)
		}
		stop()
	}

	srv.CancelActive()
	mongo.Stop()
	// Give the subprocesses a moment to notice the cancelled context.
	time.Sleep(500 * time.Millisecond)
	fmt.Println("   ✅ stopped")
}

func addr(port int) string { return "127.0.0.1:" + strconv.Itoa(port) }

func banner(paths core.Paths, mongo *server.Mongo) {
	line := "──────────────────────────────────────────────"
	fmt.Printf("\n%s\n", line)
	fmt.Printf("  ⚡ AgentForge backend\n")
	fmt.Printf("  🌐 API        →  http://127.0.0.1:%d%s\n", core.UIPort, core.APIPrefix)
	fmt.Printf("  🔌 WebSocket  →  ws://127.0.0.1:%d\n", core.WSPort)
	fmt.Printf("  📄 SRS        →  http://127.0.0.1:%d%s/srs\n", core.UIPort, core.APIPrefix)
	fmt.Printf("  🚀 Deploy     →  http://127.0.0.1:%d%s/deploy\n", core.UIPort, core.APIPrefix)
	fmt.Printf("  🍃 MongoDB    →  %s\n", mongo.URI())
	fmt.Printf("  📁 Projects   →  %s\n", paths.Projects)
	fmt.Printf("  🎛️  Studio     →  http://localhost:3000%s\n", core.APIPrefix[:len(core.APIPrefix)-4])
	fmt.Printf("%s\n\n", line)
}
