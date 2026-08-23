package qa

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"agentforge/agent/core"
)

// End-to-end testing happens in two halves, because asking a model to write a
// browser test straight from a file listing produces tests for an application
// it imagined. First it reads the app and writes down what the app actually
// does — the e2e summary. Then it writes journeys from that summary, and every
// journey is walked in a real browser through the Playwright CLI.

const (
	e2eTimeout    = 6 * time.Minute
	maxE2ERepairs = 3
	maxJourneys   = 8
)

const e2eSummarySystem = `You read a working web application and write down what
it does, so that browser tests can be written from fact rather than guesswork.

You are given the page and API sources exactly as they are on disk. Report only
what you can point at in the code.

Answer with JSON only:
{"app":"one sentence saying what this application is for",
 "entities":["the things it stores"],
 "routes":[{"route":"/orders","purpose":"what a person does here",
            "actions":["the operations available on this page"],
            "testids":["the data-testid values present"]}],
 "journeys":[{"id":"j1","title":"short name for the journey",
              "start":"/","steps":["what the person does, one action each"],
              "proves":"the visible change that shows it worked"}]}`

// E2EPlan reads the finished application and writes the e2e summary every
// journey is generated from.
func (s *Suite) E2EPlan(ctx context.Context, run *core.Run) error {
	core.Refresh(run)
	run.Info("🗺️  Reading the app to plan its journeys")

	sources := e2eSources(run)
	if len(sources) == 0 {
		return fmt.Errorf("there are no pages to walk")
	}

	var b strings.Builder
	b.WriteString("PROJECT LAYOUT\n")
	b.WriteString(core.StructureBlock(run.Structure))
	b.WriteString("\nPAGE AND API SOURCE\n")
	b.WriteString(core.ReadFiles(run, sources, 60000))
	if lines := run.Contract; lines != "" {
		b.WriteString("\nWHAT THE SPECIFICATION ASKED FOR\n")
		b.WriteString(firstLines(lines, 60))
	}

	var summary E2ESummary
	if err := run.LLM.JSON(ctx, core.RoleQA, e2eSummarySystem, b.String(), &summary); err != nil {
		return fmt.Errorf("the e2e summary could not be written: %w", err)
	}
	if len(summary.Journeys) > maxJourneys {
		summary.Journeys = summary.Journeys[:maxJourneys]
	}
	summary.WrittenAt = time.Now().UTC().Format(time.RFC3339)

	if err := core.WriteJSON(run.Paths.E2ESummaryFile(run.Project), summary); err != nil {
		run.Warn("could not save the e2e summary: " + err.Error())
	}
	s.summary = &summary
	run.Info(fmt.Sprintf("🗺️  %d journey(s) planned across %d route(s)",
		len(summary.Journeys), len(summary.Routes)))
	return nil
}

// E2ESummary is what the plan stage writes and the authoring stage reads.
type E2ESummary struct {
	App       string       `json:"app"`
	Entities  []string     `json:"entities"`
	Routes    []E2ERoute   `json:"routes"`
	Journeys  []E2EJourney `json:"journeys"`
	WrittenAt string       `json:"written_at"`
}

type E2ERoute struct {
	Route   string   `json:"route"`
	Purpose string   `json:"purpose"`
	Actions []string `json:"actions"`
	TestIDs []string `json:"testids"`
}

type E2EJourney struct {
	ID     string   `json:"id"`
	Title  string   `json:"title"`
	Start  string   `json:"start"`
	Steps  []string `json:"steps"`
	Proves string   `json:"proves"`
}

// E2E writes one spec per journey and walks each one in a real browser.
func (s *Suite) E2E(ctx context.Context, run *core.Run) error {
	summary := s.summary
	if summary == nil {
		var loaded E2ESummary
		if err := core.ReadJSON(run.Paths.E2ESummaryFile(run.Project), &loaded); err != nil || len(loaded.Journeys) == 0 {
			return fmt.Errorf("there is no e2e summary to build journeys from")
		}
		summary = &loaded
	}
	if len(summary.Journeys) == 0 {
		s.report.E2E.Note = "the app has no walkable journey"
		return nil
	}
	if err := s.ensureBrowser(ctx, run); err != nil {
		return err
	}

	run.Info(fmt.Sprintf("🎭 Walking %d journey(s) in a real browser", len(summary.Journeys)))

	// Sequential on purpose: each journey mutates the same database, so
	// overlapping them makes one journey's assertions depend on another's timing.
	for i, journey := range summary.Journeys {
		if err := run.Check(); err != nil {
			return err
		}
		run.Progress("e2e", 82+8*float64(i)/float64(len(summary.Journeys)))
		run.E2E(map[string]any{
			"state": "journey_start", "title": journey.Title,
			"index": 0, "total": len(journey.Steps),
		})

		passed := s.walk(ctx, run, *summary, journey)
		s.report.E2E.Total++
		if passed {
			s.report.E2E.Passed++
			run.E2E(map[string]any{
				"state": "journey_done", "title": journey.Title,
				"index": len(journey.Steps), "total": len(journey.Steps),
			})
			run.TestResult("pass", journey.Title, journey.Proves)
			continue
		}
		s.report.E2E.Failed++
		s.report.E2E.Unresolved = append(s.report.E2E.Unresolved, journey.Title)
	}

	if s.report.E2E.Failed == 0 {
		run.Info("✅ every journey walks")
	} else {
		run.Warn(fmt.Sprintf("⚠ %d journey(s) do not walk", s.report.E2E.Failed))
	}
	return nil
}

// walk authors, runs and repairs one journey until it passes or the budget runs out.
func (s *Suite) walk(ctx context.Context, run *core.Run, summary E2ESummary, journey E2EJourney) bool {
	spec := "tests/e2e/" + core.SafeName(journey.ID) + ".spec.js"

	for attempt := 1; attempt <= maxE2ERepairs; attempt++ {
		if run.Check() != nil {
			return false
		}
		if attempt == 1 || !run.Shell.Exists(spec) {
			if err := s.authorJourney(ctx, run, summary, journey, spec); err != nil {
				run.Warn("could not write " + spec + ": " + err.Error())
				return false
			}
		}

		res, err := run.Shell.Exec(ctx, e2eTimeout, core.NPX(), "playwright", "test", spec, "--reporter=line")
		if err != nil && res.Stdout == "" && res.Stderr == "" {
			run.Warn("playwright could not run: " + err.Error())
			return false
		}
		if res.OK() {
			return true
		}

		failure := res.Tail(30)
		run.E2E(map[string]any{
			"state": "step_failed", "title": journey.Title,
			"message": firstLines(failure, 2),
			"index":   attempt, "total": len(journey.Steps),
		})
		run.TestResult("fail", journey.Title, firstLines(failure, 5))

		if attempt == maxE2ERepairs {
			return false
		}
		run.TestFixing(attempt, []string{journey.Title + ": " + firstLines(failure, 1)})
		if err := s.repairJourney(ctx, run, journey, spec, failure); err != nil {
			run.Warn("the journey repair failed: " + err.Error())
			return false
		}
		if !sleepCtx(ctx, settleAfterFix) {
			return false
		}
	}
	return false
}

const journeyAuthorSystem = `You write one Playwright spec that walks a real
user journey through a running Next.js application.

You are given a summary of what the app actually does, including the
data-testid values that exist. Use those. Never invent a selector.

Rules:
- Use @playwright/test. baseURL is already configured, so navigate with
  page.goto('/orders'), not a full URL.
- Prefer getByTestId, then getByRole with an accessible name. Use a CSS
  selector only when nothing else identifies the element.
- Assert the visible change that proves the step worked — new text on the page,
  a row that now exists, a field that cleared. A successful click is not proof.
- Wait with Playwright's own expect(...).toBeVisible() rather than a fixed sleep.
- One test() per journey. No test.skip, no test.fixme.

Output format — nothing else:

<<<FILE tests/e2e/<id>.spec.js
...the complete spec...
>>>END`

func (s *Suite) authorJourney(ctx context.Context, run *core.Run, summary E2ESummary,
	journey E2EJourney, spec string) error {

	var b strings.Builder
	fmt.Fprintf(&b, "APPLICATION: %s\n\n", summary.App)
	fmt.Fprintf(&b, "JOURNEY %s: %s\nStarts at: %s\nProves: %s\nSteps:\n",
		journey.ID, journey.Title, journey.Start, journey.Proves)
	for _, step := range journey.Steps {
		b.WriteString("  - " + step + "\n")
	}

	b.WriteString("\nWHAT EACH ROUTE OFFERS\n")
	for _, r := range summary.Routes {
		fmt.Fprintf(&b, "- %s — %s\n  actions: %s\n  testids: %s\n",
			r.Route, r.Purpose, strings.Join(r.Actions, ", "), strings.Join(r.TestIDs, ", "))
	}

	// The pages the journey touches, so the selectors come from real markup.
	b.WriteString("\nTHE PAGES THIS JOURNEY TOUCHES\n")
	b.WriteString(core.ReadFiles(run, pagesFor(run, journey, summary), 36000))
	fmt.Fprintf(&b, "\nWrite %s.\n", spec)

	writer := core.NewFileWriter(run)
	if _, err := run.LLM.Stream(ctx, core.RoleQA, journeyAuthorSystem, b.String(), writer.Feed); err != nil {
		return err
	}
	writer.Finish()
	if len(writer.Written()) == 0 {
		return fmt.Errorf("no spec was written")
	}
	return nil
}

const journeyRepairSystem = `You repair one failing Playwright journey.

Decide which side is wrong:
- The spec looks for something the page never renders → fix the spec, using a
  selector the page source proves exists.
- The application genuinely does not do what the journey describes → fix the
  application.

Never weaken the assertion, skip the test, or replace a real check with a sleep.
Emit the complete file for everything you change.

Output format — nothing else:

<<<FILE path/to/file
...the complete file...
>>>END`

func (s *Suite) repairJourney(ctx context.Context, run *core.Run, journey E2EJourney,
	spec, failure string) error {

	paths := []string{spec}
	paths = append(paths, filesIn(failure, run)...)
	if page := pageFileFor(run, journey.Start); page != "" {
		paths = append(paths, page)
	}

	var b strings.Builder
	fmt.Fprintf(&b, "JOURNEY: %s\nProves: %s\n\n", journey.Title, journey.Proves)
	b.WriteString("PLAYWRIGHT REPORTED\n")
	b.WriteString(firstLines(failure, 30))
	b.WriteString("\n\nTHE FILES INVOLVED\n")
	b.WriteString(core.ReadFiles(run, dedupePaths(paths), 40000))

	writer := core.NewFileWriter(run)
	if _, err := run.LLM.Stream(ctx, core.RoleQA, journeyRepairSystem, b.String(), writer.Feed); err != nil {
		return err
	}
	writer.Finish()
	if len(writer.Written()) == 0 {
		return fmt.Errorf("the repair produced no changes")
	}
	return nil
}

// ensureBrowser makes sure the Playwright CLI has a browser to drive.
func (s *Suite) ensureBrowser(ctx context.Context, run *core.Run) error {
	res, err := run.Shell.Exec(ctx, 2*time.Minute, core.NPX(), "playwright", "--version")
	if err != nil || !res.OK() {
		return fmt.Errorf("the playwright CLI is not available: %s", res.Tail(5))
	}
	// Installing an existing browser is a fast no-op, so this is safe to repeat.
	run.Info("🎭 checking the browser is installed")
	install, err := run.Shell.Exec(ctx, 10*time.Minute, core.NPX(), "playwright", "install", "chromium")
	if err != nil {
		return fmt.Errorf("the browser could not be installed: %w", err)
	}
	if !install.OK() {
		run.Warn("playwright install reported a problem — trying the journeys anyway")
	}
	return nil
}

// e2eSources are the files worth reading to understand what the app does.
func e2eSources(run *core.Run) []string {
	if run.Structure == nil {
		return nil
	}
	var out []string
	for _, f := range run.Structure.Files {
		base := filepath.Base(f)
		if strings.HasPrefix(f, "app/") &&
			(strings.HasPrefix(base, "page.") || strings.HasPrefix(base, "route.")) {
			out = append(out, f)
		}
	}
	for _, f := range run.Structure.Files {
		if strings.HasPrefix(f, "components/") {
			out = append(out, f)
		}
	}
	return out
}

// pagesFor picks the page files a journey walks through.
func pagesFor(run *core.Run, journey E2EJourney, summary E2ESummary) []string {
	var out []string
	if page := pageFileFor(run, journey.Start); page != "" {
		out = append(out, page)
	}
	// Any route the summary knows about that the journey's steps mention.
	joined := strings.ToLower(strings.Join(journey.Steps, " ") + " " + journey.Title)
	for _, r := range summary.Routes {
		name := strings.Trim(strings.ToLower(r.Route), "/")
		if name != "" && strings.Contains(joined, name) {
			if page := pageFileFor(run, r.Route); page != "" {
				out = append(out, page)
			}
		}
	}
	if len(out) == 0 {
		out = e2eSources(run)
	}
	return dedupePaths(out)
}

// pageFileFor maps /orders back to app/orders/page.jsx.
func pageFileFor(run *core.Run, route string) string {
	route = strings.TrimSuffix(strings.TrimSpace(route), "/")
	for _, ext := range []string{".jsx", ".js"} {
		candidate := "app" + route + "/page" + ext
		if run.Shell.Exists(candidate) {
			return candidate
		}
	}
	return ""
}
