// Command agentforge is the AgentForge backend: the WebSocket the Studio
// streams a run over, the HTTP API it calls, and the builder and QA agents that
// do the work. The SRS and deployment services stay in Python and run as
// supervised subprocesses.
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
	"agentforge/agent/qa"
	"agentforge/agent/server"
)

func main() {
	log.SetFlags(log.Ltime)

	paths := core.DiscoverPaths()
	hub := core.NewHub()
	llm := core.NewLLM()
	mongo := server.NewMongo(paths)
	sidecars := server.NewSidecars(paths)

	srv := server.New(hub, paths, llm, sidecars, mongo)
	srv.Agent = builder.NewAgent(srv.Pictures)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// The connection string is settled first so the sidecars know where to
	// look, but fetching mongod can take minutes and must not hold up the
	// Studio: it finishes in the background and /mongo reports the progress.
	mongo.Resolve()
	sidecars.Start(ctx, mongo.URI())
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
	fmt.Printf("  📄 SRS agent  →  http://127.0.0.1:%d\n", core.SRSPort)
	fmt.Printf("  🚀 Deploy     →  http://127.0.0.1:%d\n", core.DeployPort)
	fmt.Printf("  🍃 MongoDB    →  %s\n", mongo.URI())
	fmt.Printf("  📁 Projects   →  %s\n", paths.Projects)
	fmt.Printf("  🎛️  Studio     →  http://localhost:3000%s\n", core.APIPrefix[:len(core.APIPrefix)-4])
	fmt.Printf("%s\n\n", line)
}
