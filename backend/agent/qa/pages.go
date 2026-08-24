package qa

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"path"
	"strconv"
	"strings"
	"time"

	"agentforge/agent/core"
)

// maxPageFix is how many repair rounds one broken page is given.
const maxPageFix = 3

// RuntimeCheck opens every page the app serves and repairs the ones that fail.
//
// It runs before the unit suite because the unit suite cannot see this class of
// fault. Units mount components in isolation, so a mistake in the page around
// them — a helper the API routes await and the page does not, a value read
// straight off a promise — passes every unit test and still serves a 500. Left
// until the journeys walk, that one missing await arrives instead as three
// failed journeys and a screenshot, which is a long way round to a one word fix.
func (s *Suite) RuntimeCheck(ctx context.Context, run *core.Run) error {
	core.Refresh(run)
	pages, apis := walkableRoutes(run), apiRoutes(run)
	if len(pages) == 0 && len(apis) == 0 {
		run.Info("nothing to open yet")
		return nil
	}
	var broken []string

	if len(pages) > 0 {
		run.Info(fmt.Sprintf("🩺 Opening %d page(s) to see that they render", len(pages)))
		for _, route := range pages {
			if err := run.Check(); err != nil {
				return err
			}
			problem := s.checkAndRepair(ctx, run, route+" does not render",
				func() string { return s.pageProblem(ctx, route) })

			s.report.Runtime.Total++
			if problem == "" {
				s.report.Runtime.Passed++
				run.TestResult("pass", route+" renders", "")
				continue
			}
			s.report.Runtime.Failed++
			broken = append(broken, route+" — "+firstLines(problem, 2))
		}
	}

	// The pages are only half of what the app serves. An endpoint answering
	// 500 breaks every page that fetches it, and no page check sees that: the
	// page renders, then asks for its data and gets nothing.
	if len(apis) > 0 {
		run.Info(fmt.Sprintf("🔌 Calling %d API route(s)", len(apis)))
		for _, route := range apis {
			if err := run.Check(); err != nil {
				return err
			}
			problem := s.checkAndRepair(ctx, run, route+" is failing",
				func() string { return s.apiProblem(ctx, route) })

			s.report.Runtime.Total++
			if problem == "" {
				s.report.Runtime.Passed++
				run.TestResult("pass", route+" answers", "")
				continue
			}
			s.report.Runtime.Failed++
			broken = append(broken, route+" — "+firstLines(problem, 2))
		}
	}

	// Last, and only here, does a browser get involved. Everything above
	// reads a response; a component that throws as it hydrates leaves a
	// response that is already complete and already correct, so nothing
	// above can see it. Installing a browser costs minutes on a first build,
	// which is why it waits until the cheap checks have had their turn.
	if err := s.ensureBrowser(ctx, run); err != nil {
		run.Warn("the console cannot be read without a browser: " + err.Error())
	} else if err := s.ConsoleCheck(ctx, run); err != nil {
		return err
	}

	s.report.Runtime.Unresolved = append(s.report.Runtime.Unresolved, broken...)
	if len(broken) > 0 {
		// Everything after this reads these routes, so this is worth saying
		// plainly. It still does not stop the run: what the later stages find
		// on what does work is worth more than stopping here.
		run.Warn(fmt.Sprintf("🩺 %d of %d route(s) are still not working",
			len(broken), len(pages)+len(apis)))
		return nil
	}
	run.Info(fmt.Sprintf("✅ all %d page(s) and %d API route(s) answer", len(pages), len(apis)))
	return nil
}

// checkAndRepair probes one route until it is clean or the budget runs out, and
// returns whatever is still wrong with it.
func (s *Suite) checkAndRepair(ctx context.Context, run *core.Run, what string, probe func() string) string {
	problem := probe()
	for attempt := 1; problem != "" && attempt <= maxPageFix; attempt++ {
		run.Warn(fmt.Sprintf("🐞 %s (%d/%d)", what, attempt, maxPageFix))
		run.TestResult("fail", what, firstLines(problem, 6))

		// A failure naming no file of ours is not one to hand to a repair: it
		// would be given a general set of files instead and asked to fix a
		// fault in code it was never shown.
		if named := filesIn(problem, run); len(named) == 0 {
			run.Warn("that failure names no file this build owns; leaving it")
			return problem
		}
		fixed, err := s.repairRuntime(ctx, run, problem)
		if err != nil {
			run.Warn("it could not be repaired: " + err.Error())
			return problem
		}
		if !fixed {
			run.Warn("the repair could not see what to change")
			return problem
		}
		run.TestFixing(attempt, []string{firstLines(problem, 2)})
		// Next recompiles on write; give it time before judging it again.
		if !sleepCtx(ctx, settleAfterFix) {
			return problem
		}
		problem = probe()
	}
	return problem
}

// pageProblem fetches one route and says what is wrong with it, or "" when the
// page is fine. A 5xx and an error overlay mean the same thing here.
func (s *Suite) pageProblem(ctx context.Context, route string) string {
	client := &http.Client{Timeout: 45 * time.Second}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.BaseURL()+route, nil)
	if err != nil {
		return ""
	}
	resp, err := client.Do(req)
	if err != nil {
		return route + " could not be opened: " + err.Error()
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 2<<20))

	verdict := pageVerdict(route, resp.StatusCode, string(body))
	if verdict == "" {
		return ""
	}

	var b strings.Builder
	b.WriteString(verdict)
	// Which file has the bug is the whole of a repair, and a status code names
	// none. The dev server's own output carries the stack that does.
	if printed := s.devOutput(); printed != "" {
		b.WriteString("\n\nTHE DEV SERVER'S RECENT OUTPUT\n")
		b.WriteString(printed)
	}
	return b.String()
}

// devOutput is what the dev server has printed, when one is running.
func (s *Suite) devOutput() string {
	s.mu.Lock()
	dev := s.dev
	s.mu.Unlock()
	if dev == nil {
		return ""
	}
	return dev.Output()
}

// pageVerdict judges one response: "" when the page is fine, otherwise what is
// wrong with it. It is kept apart from fetching so the rule can be tested
// without a server on the preview port.
func pageVerdict(route string, status int, body string) string {
	overlay := errorInPage(body)
	if overlay == "" && status < 500 {
		return ""
	}
	out := route + " answered " + strconv.Itoa(status)
	if overlay != "" {
		out += "\n" + overlay
	}
	return out
}

// apiRoutes are the endpoints the app serves that can be called without
// inventing an id. A route under a [param] segment is left alone: guessing an
// id gets a 404 that says nothing about whether the handler works.
func apiRoutes(run *core.Run) []string {
	if run.Structure == nil {
		return nil
	}
	var out []string
	for _, f := range run.Structure.Files {
		if !strings.HasPrefix(f, "app/api/") || strings.Contains(f, "[") {
			continue
		}
		base := path.Base(f)
		if base != "route.js" && base != "route.jsx" {
			continue
		}
		out = append(out, "/"+path.Dir(strings.TrimPrefix(f, "app/")))
	}
	return out
}

// apiProblem calls one endpoint and reports only what is unambiguously the
// app's fault. A 401 from a route behind auth, a 405 for a method it does not
// serve and a 404 for a row that is not there are all correct answers; a 5xx
// is the handler falling over, and it takes down every page that fetches it.
func (s *Suite) apiProblem(ctx context.Context, route string) string {
	client := &http.Client{Timeout: 45 * time.Second}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.BaseURL()+route, nil)
	if err != nil {
		return ""
	}
	resp, err := client.Do(req)
	if err != nil {
		return route + " could not be called: " + err.Error()
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))

	if resp.StatusCode < 500 {
		return ""
	}
	out := route + " answered " + strconv.Itoa(resp.StatusCode) + "\n" + strings.TrimSpace(string(body))
	if printed := s.devOutput(); printed != "" {
		out += "\n\nTHE DEV SERVER'S RECENT OUTPUT\n" + printed
	}
	return out
}
