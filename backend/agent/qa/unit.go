package qa

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"agentforge/agent/core"
)

// Unit testing runs in rounds, and the rounds are strictly sequential: a round
// repairs what the previous round's failures proved, so overlapping them would
// have round N judging code round N-1 had already replaced.
//
// Four rounds is the budget for repairing, not a number of rounds to run: a
// suite that passes first time is done, because running a green suite again
// proves nothing. If the fourth round is still closing failures the budget
// extends, one round at a time, up to the ceiling — and it stops early the
// moment a round closes nothing, because a loop that is not converging will not
// converge by going round again.

const (
	baseRounds    = 4
	roundCeiling  = 10
	maxNoProgress = 2
	vitestTimeout = 8 * time.Minute
	maxTargets    = 14
)

// Unit authors the suite, then runs and repairs it.
func (s *Suite) Unit(ctx context.Context, run *core.Run) error {
	run.TestStart()
	core.Refresh(run)

	if err := s.ensureHarness(run); err != nil {
		return err
	}
	qualifying := unitTargets(run)
	if len(qualifying) == 0 {
		run.Warn("there is no application code to unit test")
		return nil
	}

	// A test costs a model call, so only so many are written in one build.
	// Which ones is decided by unitRank, and pages come last — so on a large
	// app the cap falls on the pages. Saying so is the point: a count with
	// nothing to compare it against reads like full coverage.
	targets, beyondCap := qualifying, []string(nil)
	if len(targets) > maxTargets {
		targets, beyondCap = targets[:maxTargets], targets[maxTargets:]
		run.Warn(fmt.Sprintf("🧪 Writing unit tests for %d of %d file(s) — %d will have none",
			len(targets), len(qualifying), len(beyondCap)))
	} else {
		run.Info(fmt.Sprintf("🧪 Writing unit tests for all %d file(s)", len(targets)))
	}
	skipped := map[string]bool{}
	for i, target := range targets {
		if err := run.Check(); err != nil {
			return err
		}
		run.Progress("unit", 58+6*float64(i)/float64(len(targets)))
		switch err := s.authorUnitTest(ctx, run, target); {
		case errors.Is(err, errTestSkipped):
			skipped[target] = true
		case err != nil:
			run.Warn("could not write a test for " + target + ": " + err.Error())
		}
	}

	// Asking for a test is not the same as getting one, and nothing later
	// notices the difference: vitest reports on the tests that exist, so a
	// file with none simply never appears in the count that follows.
	untested := s.writeMissingUnitTests(ctx, run, targets, skipped)

	if err := s.unitRounds(ctx, run); err != nil {
		return err
	}
	untested = append(untested, beyondCap...)
	if len(untested) > 0 {
		run.Warn(fmt.Sprintf("⚠ %d of %d file(s) have no unit test", len(untested), len(qualifying)))
		for _, target := range untested {
			run.Info("   • " + target)
			s.report.Suite.Unresolved = append(s.report.Suite.Unresolved, target+" has no unit test")
		}
	}
	return nil
}

// rounds decides how many times the loop goes round. It is separate from the
// loop so the auto-scaling rule can be reasoned about — and tested — without a
// model or a test runner in the way.
type rounds struct {
	budget   int
	round    int
	stalled  int
	previous int // failures the last round ended with, -1 before the first
	extended bool
}

func newRounds() *rounds {
	return &rounds{budget: baseRounds, previous: -1}
}

// verdict is what to do after a round.
type verdict int

const (
	repairAndContinue verdict = iota
	stopGreen
	stopStalled
	stopBudget
)

// observe records one round's failure count and says what happens next.
func (r *rounds) observe(failed int) verdict {
	r.round++
	if failed == 0 {
		return stopGreen
	}
	if r.previous >= 0 {
		if failed >= r.previous {
			r.stalled++
		} else {
			r.stalled = 0
			// Still closing failures as the floor runs out: buy another round.
			if r.round >= r.budget && r.budget < roundCeiling {
				r.budget++
				r.extended = true
			}
		}
	}
	r.previous = failed

	switch {
	case r.stalled >= maxNoProgress:
		return stopStalled
	case r.round >= r.budget || r.round >= roundCeiling:
		return stopBudget
	default:
		return repairAndContinue
	}
}

// unitRounds is the sequential repair loop.
func (s *Suite) unitRounds(ctx context.Context, run *core.Run) error {
	budget := newRounds()
	var last vitestRun

	for {
		if err := run.Check(); err != nil {
			return err
		}
		run.Round = budget.round + 1
		run.TestRun(run.Round)
		run.Progress("unit", 64+6*float64(run.Round)/float64(roundCeiling))
		run.Info(fmt.Sprintf("🧪 Unit round %d of %d", run.Round, budget.budget))

		result, err := s.runVitest(ctx, run)
		if err != nil {
			return err
		}
		last = result
		s.recordUnit(run, result, run.Round)

		budget.extended = false
		switch budget.observe(result.Failed) {
		case stopGreen:
			run.Info(fmt.Sprintf("✅ %d unit test(s) pass", result.Passed))
			run.TestResult("pass", "Unit tests pass", fmt.Sprintf("%d case(s)", result.Passed))
			s.report.Suite = result.stageReport("unit")
			return nil

		case stopStalled:
			run.TestResult("fail", fmt.Sprintf("%d unit test(s) failing", result.Failed),
				firstLines(result.firstFailure(), 4))
			run.Warn(fmt.Sprintf("   %d round(s) closed nothing — stopping the unit loop", budget.stalled))

		case stopBudget:
			run.TestResult("fail", fmt.Sprintf("%d unit test(s) failing", result.Failed),
				firstLines(result.firstFailure(), 4))

		case repairAndContinue:
			run.TestResult("fail", fmt.Sprintf("%d unit test(s) failing", result.Failed),
				firstLines(result.firstFailure(), 4))
			if budget.extended {
				run.Info(fmt.Sprintf("   still closing failures — extending to %d rounds", budget.budget))
			}
			run.TestFixing(run.Round, result.failureMessages(3))
			if err := s.repairUnit(ctx, run, result); err != nil {
				run.Warn("the unit repair failed: " + err.Error())
			} else {
				continue
			}
		}
		break
	}

	s.report.Suite = last.stageReport("unit")
	if last.Failed > 0 {
		run.Warn(fmt.Sprintf("⚠ %d unit test(s) still failing", last.Failed))
	}
	return nil
}

// runVitest runs the suite once and reads the machine-readable report.
func (s *Suite) runVitest(ctx context.Context, run *core.Run) (vitestRun, error) {
	out := filepath.ToSlash(filepath.Join(".agentforge", "qa", "vitest.json"))
	res, err := run.Shell.Exec(ctx, vitestTimeout, core.NPX(), "vitest", "run",
		"--reporter=json", "--outputFile="+out)
	if err != nil && res.Stdout == "" {
		return vitestRun{}, fmt.Errorf("vitest could not run: %w", err)
	}

	var report vitestReport
	if err := core.ReadJSON(filepath.Join(run.Dir, ".agentforge", "qa", "vitest.json"), &report); err != nil {
		return vitestRun{}, fmt.Errorf("vitest wrote no report: %w", err)
	}
	if len(report.TestResults) == 0 && res.Code != 0 {
		// vitest never got as far as a test — that is a real failure worth showing.
		return vitestRun{}, fmt.Errorf("vitest failed before any test ran:\n%s", res.Tail(20))
	}
	return summarise(report), nil
}

// recordUnit appends one line to the QA history the Testing tab reads.
func (s *Suite) recordUnit(run *core.Run, result vitestRun, round int) {
	rate := 0
	if result.Total > 0 {
		rate = result.Passed * 100 / result.Total
	}
	appendHistory(run, map[string]any{
		"stage": "unit", "round": round, "passed": result.Passed,
		"failed": result.Failed, "total": result.Total, "rate": rate,
		"at": time.Now().UTC().Format(time.RFC3339),
	})
}

const unitAuthorSystem = `You write one Vitest test file for a Next.js App Router
application.

You are given the source file exactly as it is on disk. Test what it actually
does — the exported functions, the rendered output, the states a user can reach.
Never test something the file does not contain.

Environment: vitest with globals, jsdom, @testing-library/react. Import the file
under test through the '@/' alias. Anything reaching MongoDB must be mocked with
vi.mock('@/lib/db.js', ...) — the tests never open a database connection.

Output format — nothing else:

<<<FILE tests/unit/<name>.test.js
...the complete test file...
>>>END

If the file has nothing meaningfully testable, answer with exactly: SKIP`

// authorUnitTest writes the test for one source file.
func (s *Suite) authorUnitTest(ctx context.Context, run *core.Run, target string) error {
	dest := unitTestPath(target)
	if run.Shell.Exists(dest) {
		return nil // an earlier round already wrote it
	}

	body, _, err := run.Shell.Read(target)
	if err != nil {
		return err
	}
	prompt := fmt.Sprintf("FILE UNDER TEST: %s\n\n%s\n\nWrite %s.\n\nPROJECT LAYOUT\n%s",
		target, body, dest, core.StructureBlock(run.Structure))

	writer := core.NewFileWriter(run)
	text, err := run.LLM.Stream(ctx, core.RoleQA, unitAuthorSystem, prompt, writer)
	if err != nil {
		return err
	}
	writer.Finish()
	if len(writer.Written()) > 0 {
		return nil
	}
	// Nothing was written. Saying so is the whole point: the caller only
	// reacts to an error, so returning nil here is how a file ends up with no
	// test at all while the log says one was written for it.
	if strings.Contains(strings.ToUpper(text), "SKIP") {
		return errTestSkipped
	}
	return errors.New("the author produced no test file")
}

const unitRepairSystem = `You repair failing Vitest tests for a Next.js
application.

For each failure decide which side is wrong:
- The test asserts something the component never promised → fix the test.
- The component genuinely misbehaves → fix the component.

Never delete a test, never mark one skipped, and never weaken an assertion just
to get green. Emit the complete file for everything you change.

Output format — nothing else:

<<<FILE path/to/file
...the complete file...
>>>END`

// repairUnit fixes one round's failures, one prompt for the whole round so the
// model can see failures that share a cause.
func (s *Suite) repairUnit(ctx context.Context, run *core.Run, result vitestRun) error {
	var b strings.Builder
	b.WriteString("FAILING TESTS\n")
	for i, f := range result.Failures {
		if i >= 8 {
			fmt.Fprintf(&b, "…and %d more\n", len(result.Failures)-8)
			break
		}
		fmt.Fprintf(&b, "\n%s › %s\n%s\n", f.File, f.Title, firstLines(f.Message, 12))
	}

	// Read the test files and the sources they exercise.
	var paths []string
	for _, f := range result.Failures {
		paths = append(paths, f.File)
		if src := sourceForTest(run, f.File); src != "" {
			paths = append(paths, src)
		}
	}
	b.WriteString("\nTHE FILES INVOLVED\n")
	b.WriteString(core.ReadFiles(run, dedupePaths(paths), 50000))

	writer := core.NewFileWriter(run)
	if _, err := run.LLM.Stream(ctx, core.RoleQA, unitRepairSystem, b.String(), writer); err != nil {
		return err
	}
	writer.Finish()
	if len(writer.Written()) == 0 {
		return fmt.Errorf("the repair produced no changes")
	}
	return nil
}

// --- targets and naming -------------------------------------------------------

// unitTargets picks what is worth unit testing: the app's own source, not the
// scaffold and not the tests.
func unitTargets(run *core.Run) []string {
	if run.Structure == nil {
		return nil
	}
	var out []string
	for _, f := range run.Structure.Files {
		if !strings.HasSuffix(f, ".js") && !strings.HasSuffix(f, ".jsx") {
			continue
		}
		if strings.HasPrefix(f, "tests/") || strings.Contains(f, ".test.") ||
			strings.Contains(f, ".config.") {
			continue
		}
		if !strings.HasPrefix(f, "app/") && !strings.HasPrefix(f, "lib/") &&
			!strings.HasPrefix(f, "components/") {
			continue
		}
		if f == "app/layout.jsx" {
			continue // a layout is covered by every page test
		}
		out = append(out, f)
	}
	sort.Slice(out, func(i, j int) bool { return unitRank(out[i]) < unitRank(out[j]) })
	return out
}

// unitRank tests shared code before the pages that depend on it.
func unitRank(path string) int {
	switch {
	case strings.HasPrefix(path, "lib/"):
		return 0
	case strings.HasPrefix(path, "components/"):
		return 1
	case strings.HasPrefix(path, "app/api/"):
		return 2
	default:
		return 3
	}
}

// testNameFor turns app/orders/[id]/page.jsx into app-orders-id-page.
func testNameFor(target string) string {
	name := strings.TrimSuffix(strings.TrimSuffix(target, ".jsx"), ".js")
	replacer := strings.NewReplacer("/", "-", "[", "", "]", "", ".", "-")
	return strings.Trim(replacer.Replace(name), "-")
}

// sourceForTest maps a test file back to what it tests, so a repair can read both.
func sourceForTest(run *core.Run, testPath string) string {
	base := strings.TrimSuffix(filepath.Base(testPath), ".test.js")
	for _, target := range unitTargets(run) {
		if testNameFor(target) == base {
			return target
		}
	}
	return ""
}

func dedupePaths(paths []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, p := range paths {
		if p == "" || seen[p] {
			continue
		}
		seen[p] = true
		out = append(out, p)
	}
	return out
}

// ensureHarness writes the test setup file the vitest config expects.
func (s *Suite) ensureHarness(run *core.Run) error {
	if run.Shell.Exists("tests/setup.js") {
		return nil
	}
	return run.Shell.Write("tests/setup.js", `import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
`)
}

// --- vitest report ------------------------------------------------------------

type vitestReport struct {
	NumTotalTests  int `json:"numTotalTests"`
	NumPassedTests int `json:"numPassedTests"`
	NumFailedTests int `json:"numFailedTests"`
	TestResults    []struct {
		Name             string `json:"name"`
		Status           string `json:"status"`
		AssertionResults []struct {
			Title           string   `json:"title"`
			Status          string   `json:"status"`
			FailureMessages []string `json:"failureMessages"`
		} `json:"assertionResults"`
	} `json:"testResults"`
}

type failure struct {
	File    string
	Title   string
	Message string
}

// vitestRun is one round's outcome, counted from the assertions rather than
// trusted from the summary counters.
type vitestRun struct {
	Passed, Failed, Skipped, Total int
	Files                          int
	Failures                       []failure
}

func summarise(report vitestReport) vitestRun {
	out := vitestRun{Files: len(report.TestResults)}
	for _, suite := range report.TestResults {
		file := suite.Name
		for _, a := range suite.AssertionResults {
			out.Total++
			switch a.Status {
			case "passed":
				out.Passed++
			case "failed":
				out.Failed++
				out.Failures = append(out.Failures, failure{
					File:    relativeTestPath(file),
					Title:   a.Title,
					Message: strings.Join(a.FailureMessages, "\n"),
				})
			default:
				out.Skipped++
			}
		}
	}
	return out
}

// relativeTestPath turns vitest's absolute path back into a project path.
func relativeTestPath(path string) string {
	slashed := filepath.ToSlash(path)
	if i := strings.Index(slashed, "/tests/"); i >= 0 {
		return slashed[i+1:]
	}
	return slashed
}

func (v vitestRun) firstFailure() string {
	if len(v.Failures) == 0 {
		return ""
	}
	return v.Failures[0].File + " › " + v.Failures[0].Title + "\n" + v.Failures[0].Message
}

func (v vitestRun) failureMessages(limit int) []string {
	var out []string
	for i, f := range v.Failures {
		if i >= limit {
			break
		}
		out = append(out, f.File+" › "+f.Title)
	}
	return out
}

func (v vitestRun) stageReport(name string) StageReport {
	report := StageReport{Name: name, Passed: v.Passed, Failed: v.Failed, Total: v.Total}
	for _, f := range v.Failures {
		report.Unresolved = append(report.Unresolved, f.File+" › "+f.Title)
	}
	return report
}

// appendHistory adds one line to .agentforge/qa/history.jsonl.
func appendHistory(run *core.Run, row map[string]any) {
	data, err := json.Marshal(row)
	if err != nil {
		return
	}
	path := filepath.Join(run.Paths.Meta(run.Project), "qa", "history.jsonl")
	if err := appendLine(path, string(data)); err != nil {
		run.Warn("could not record QA history: " + err.Error())
	}
}
