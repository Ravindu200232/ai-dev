// Package app holds the two things that operate on a finished application: the
// summary that describes it, and the edit flow that changes it.
package app

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"agentforge/agent/core"
)

// Once a build finishes, the agent reads the whole application and writes down
// what it is. Every later edit starts from that note instead of re-reading the
// project from scratch, which is what makes the selection and pencil tools fast
// enough to feel direct.

const summarySystem = `You read a finished web application and write a reference
note about it, for an agent that will later be asked to change one small part.

You are given the real file list and the real source. Describe only what is
there. The note's job is to let a later reader answer "which files do I need to
open for this request?" without reading the whole project again.

Answer with JSON only:
{"app":"one or two sentences on what this application is for",
 "stack":"the framework and storage it uses",
 "entities":["the things it stores"],
 "routes":[{"route":"/orders","file":"app/orders/page.jsx",
            "purpose":"what a person does here",
            "renders":["the components and sections on this page"],
            "testids":["the data-testid values present"]}],
 "apis":[{"route":"/api/orders","file":"app/api/orders/route.js",
          "methods":["GET","POST"],"purpose":"what it does"}],
 "shared":[{"file":"lib/db.js","purpose":"what other files use it for"}]}`

// Summary is the note itself.
type Summary struct {
	App       string      `json:"app"`
	Stack     string      `json:"stack"`
	Entities  []string    `json:"entities"`
	Routes    []RouteNote `json:"routes"`
	APIs      []APINote   `json:"apis"`
	Shared    []FileNote  `json:"shared"`
	Files     []string    `json:"files"`
	WrittenAt string      `json:"written_at"`
}

type RouteNote struct {
	Route   string   `json:"route"`
	File    string   `json:"file"`
	Purpose string   `json:"purpose"`
	Renders []string `json:"renders"`
	TestIDs []string `json:"testids"`
}

type APINote struct {
	Route   string   `json:"route"`
	File    string   `json:"file"`
	Methods []string `json:"methods"`
	Purpose string   `json:"purpose"`
}

type FileNote struct {
	File    string `json:"file"`
	Purpose string `json:"purpose"`
}

// Summarize reads the application and saves the note.
func Summarize(ctx context.Context, run *core.Run) error {
	core.Refresh(run)
	if run.Structure == nil || len(run.Structure.Files) == 0 {
		return fmt.Errorf("there is nothing to summarise")
	}
	run.Info("📝 Reading the whole app to write its summary")

	var b strings.Builder
	b.WriteString("PROJECT LAYOUT\n")
	b.WriteString(core.StructureBlock(run.Structure))
	b.WriteString("\nSOURCE\n")
	b.WriteString(core.ReadFiles(run, summarySources(run), 90000))

	var summary Summary
	if err := run.LLM.JSON(ctx, core.RolePlanner, summarySystem, b.String(), &summary); err != nil {
		return err
	}
	summary.Files = run.Structure.Files
	summary.WrittenAt = time.Now().UTC().Format(time.RFC3339)

	if err := core.WriteJSON(run.Paths.SummaryFile(run.Project), summary); err != nil {
		return err
	}
	run.Summary = map[string]any{"app": summary.App, "routes": len(summary.Routes)}
	run.Info(fmt.Sprintf("📝 summary saved — %d route(s), %d API route(s)",
		len(summary.Routes), len(summary.APIs)))
	return nil
}

// Load reads the saved note. A build that never finished has none, and the edit
// flow falls back to a fresh survey in that case.
func Load(run *core.Run) *Summary {
	var summary Summary
	if err := core.ReadJSON(run.Paths.SummaryFile(run.Project), &summary); err != nil {
		return nil
	}
	if summary.App == "" && len(summary.Routes) == 0 {
		return nil
	}
	return &summary
}

// summarySources are the files worth reading to describe the app: its own code,
// not its tests or its scaffold.
func summarySources(run *core.Run) []string {
	var pages, shared []string
	for _, f := range run.Structure.Files {
		if !strings.HasSuffix(f, ".js") && !strings.HasSuffix(f, ".jsx") {
			continue
		}
		if strings.HasPrefix(f, "tests/") || strings.Contains(f, ".config.") ||
			strings.Contains(f, ".test.") || strings.Contains(f, ".spec.") {
			continue
		}
		switch {
		case strings.HasPrefix(f, "app/"):
			base := filepath.Base(f)
			if strings.HasPrefix(base, "page.") || strings.HasPrefix(base, "route.") ||
				strings.HasPrefix(base, "layout.") {
				pages = append(pages, f)
			}
		case strings.HasPrefix(f, "lib/"), strings.HasPrefix(f, "components/"):
			shared = append(shared, f)
		}
	}
	return append(shared, pages...)
}

// Block renders the note for a prompt. This is what the edit flow reads instead
// of the whole project.
func (s *Summary) Block() string {
	if s == nil {
		return ""
	}
	var b strings.Builder
	fmt.Fprintf(&b, "WHAT THIS APPLICATION IS\n%s\nStack: %s\n", s.App, s.Stack)
	if len(s.Entities) > 0 {
		fmt.Fprintf(&b, "Stores: %s\n", strings.Join(s.Entities, ", "))
	}
	if len(s.Routes) > 0 {
		b.WriteString("\nPAGES\n")
		for _, r := range s.Routes {
			fmt.Fprintf(&b, "- %s (%s) — %s\n", r.Route, r.File, r.Purpose)
			if len(r.Renders) > 0 {
				fmt.Fprintf(&b, "    renders: %s\n", strings.Join(r.Renders, ", "))
			}
			if len(r.TestIDs) > 0 {
				fmt.Fprintf(&b, "    testids: %s\n", strings.Join(r.TestIDs, ", "))
			}
		}
	}
	if len(s.APIs) > 0 {
		b.WriteString("\nAPI\n")
		for _, a := range s.APIs {
			fmt.Fprintf(&b, "- %s %s (%s) — %s\n",
				strings.Join(a.Methods, "/"), a.Route, a.File, a.Purpose)
		}
	}
	if len(s.Shared) > 0 {
		b.WriteString("\nSHARED CODE\n")
		for _, f := range s.Shared {
			fmt.Fprintf(&b, "- %s — %s\n", f.File, f.Purpose)
		}
	}
	return b.String()
}

// FileForRoute maps a route back to the page that serves it, using the note.
func (s *Summary) FileForRoute(route string) string {
	if s == nil {
		return ""
	}
	route = strings.TrimSuffix(strings.TrimSpace(route), "/")
	if route == "" {
		route = "/"
	}
	for _, r := range s.Routes {
		if strings.TrimSuffix(r.Route, "/") == route ||
			(route == "/" && r.Route == "/") {
			return r.File
		}
	}
	return ""
}
