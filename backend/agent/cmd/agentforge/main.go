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
	srv.Agent = builder.NewAgent()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// The database has to be settled before the sidecars are told where it is.
	mongo.Ensure(ctx)
	sidecars.Start(ctx, mongo.URI())

	errs := make(chan error, 2)
	go func() {
		errs <- srv.ServeHTTP(ctx, addr(core.UIPort), nil)
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
