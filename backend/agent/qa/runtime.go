// Package qa runs a built application through the checks that decide whether it
// actually works: it boots, its units pass, its API answers, its journeys walk,
// and it is not obviously slow or insecure.
package qa

import (
	"context"
	"fmt"
	"net"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"agentforge/agent/core"
)

// The first question is always the same: does it run? Everything after this
// stage needs a live dev server, so this is the one stage whose failure stops
// the pipeline.

const (
	installTimeout = 12 * time.Minute
	readyTimeout   = 3 * time.Minute
	maxRuntimeFix  = 5
	settleAfterFix = 12 * time.Second
)

// Suite carries the state the QA stages share: the dev server they all talk to
// and the report they all contribute to.
type Suite struct {
	mu      sync.Mutex
	dev     *devServer
	report  Report
	summary *E2ESummary
}

func NewSuite() *Suite {
	return &Suite{report: Report{
		Suite:    StageReport{Name: "unit"},
		API:      StageReport{Name: "api"},
		E2E:      StageReport{Name: "e2e"},
		Runtime:  StageReport{Name: "runtime"},
		Security: SecurityReport{Findings: []Finding{}},
	}}
}

// Close stops anything the suite started.
func (s *Suite) Close() {
	s.mu.Lock()
	dev := s.dev
	s.dev = nil
	s.mu.Unlock()
	if dev != nil {
		dev.stop()
	}
}

// BaseURL is where the app under test is served.
func (s *Suite) BaseURL() string {
	return "http://127.0.0.1:" + strconv.Itoa(core.DevPort)
}

// Dev installs dependencies, starts `npm run dev`, and repairs the app until it
// boots without a runtime error.
func (s *Suite) Dev(ctx context.Context, run *core.Run) error {
	if err := s.install(ctx, run); err != nil {
		return err
	}

	dev := newDevServer(run)
	s.mu.Lock()
	s.dev = dev
	s.mu.Unlock()

	run.Info("▶️  npm run dev")
	if err := dev.start(ctx); err != nil {
		return fmt.Errorf("the dev server would not start: %w", err)
	}

	for attempt := 1; attempt <= maxRuntimeFix; attempt++ {
		if err := run.Check(); err != nil {
			return err
		}
		problem := dev.waitReady(ctx, readyTimeout)
		if problem == "" {
			run.Info("✅ the app boots clean")
			s.report.Runtime.Passed = 1
			return nil
		}
		run.Warn(fmt.Sprintf("🐞 runtime error (%d/%d)", attempt, maxRuntimeFix))
		run.TestResult("fail", "The app did not boot", firstLines(problem, 6))

		fixed, err := s.repairRuntime(ctx, run, problem)
		if err != nil {
			return fmt.Errorf("the runtime error could not be repaired: %w", err)
		}
		if !fixed {
			s.report.Runtime.Failed = 1
			s.report.Runtime.Unresolved = append(s.report.Runtime.Unresolved, firstLines(problem, 4))
			return fmt.Errorf("the app does not boot: %s", firstLines(problem, 3))
		}
		run.TestFixing(attempt, []string{firstLines(problem, 2)})
		dev.clear()
		// Next recompiles on write; give it time before judging it again.
		if !sleepCtx(ctx, settleAfterFix) {
			return run.Check()
		}
	}
	s.report.Runtime.Failed = 1
	return fmt.Errorf("the app still does not boot after %d repairs", maxRuntimeFix)
}

// install runs npm install when the tree has no dependencies yet.
func (s *Suite) install(ctx context.Context, run *core.Run) error {
	if run.Shell.Exists("node_modules/next/package.json") {
		return nil
	}
	run.Info("📦 npm install — this takes a few minutes the first time")
	res, err := run.Shell.Exec(ctx, installTimeout, core.NPM(), "install", "--no-audit", "--no-fund")
	if err != nil {
		return fmt.Errorf("npm install: %w", err)
	}
	if !res.OK() {
		return fmt.Errorf("npm install failed:\n%s", res.Tail(25))
	}
	run.Info("📦 dependencies installed")
	return nil
}

const runtimeFixSystem = `You repair one runtime or compile error in a Next.js
App Router application.

You are given the error exactly as the dev server printed it, and the current
contents of the files it names. Fix the cause, not the symptom, and change as
little as possible. Do not delete a feature to make an error go away.

Output format — nothing else:

<<<FILE path/to/file.jsx
...the complete corrected file...
>>>END

If the error does not name a file you were given, or you cannot fix it from what
you can see, answer with exactly: CANNOT FIX`

// repairRuntime asks the model to fix what the dev server printed.
func (s *Suite) repairRuntime(ctx context.Context, run *core.Run, problem string) (bool, error) {
	suspects := filesIn(problem, run)
	if len(suspects) == 0 {
		suspects = defaultSuspects(run)
	}

	var b strings.Builder
	b.WriteString("THE DEV SERVER PRINTED\n")
	b.WriteString(firstLines(problem, 40))
	b.WriteString("\n\nTHE FILES IT NAMES\n")
	b.WriteString(core.ReadFiles(run, suspects, 40000))
	b.WriteString("\nPROJECT LAYOUT\n")
	b.WriteString(core.StructureBlock(run.Structure))

	writer := core.NewFileWriter(run)
	text, err := run.LLM.Stream(ctx, core.RoleBuilder, runtimeFixSystem, b.String(), writer.Feed)
	if err != nil {
		return false, err
	}
	writer.Finish()
	if strings.Contains(strings.ToUpper(text), "CANNOT FIX") && len(writer.Written()) == 0 {
		return false, nil
	}
	return len(writer.Written()) > 0, nil
}

// Which file has the bug is the whole of a repair. Without it the model is
// handed a generic set of files and asked to fix a fault in code it was never
// shown, so everything below is about reading that answer out of whatever a
// tool happened to print.

// pathToken matches anything in a message that looks like a path to a
// JavaScript or TypeScript file. It has to cope with all three shapes these
// tools print: `./app/page.jsx` from Next, an absolute path from a Node stack
// trace, and on Windows the same paths written with backslashes and a drive
// letter.
var pathToken = regexp.MustCompile(`(?:[A-Za-z]:)?[\w./\\\[\]-]+\.[jt]sx?`)

// projectRoots are the directories a project's own source lives in.
var projectRoots = map[string]bool{
	"app": true, "src": true, "lib": true, "components": true, "tests": true,
}

// relativeSources are the project-relative paths one token could be naming.
//
// An absolute path has to be cut down to the part this project owns, and where
// to cut is a guess: a project living under /home/me/lib/shop has a "lib" in
// its own address. So every plausible cut is offered and the caller keeps the
// one that is really on disk.
func relativeSources(token string) []string {
	slashed := strings.ReplaceAll(token, `\`, "/")
	// A fault raised inside a dependency or a build artefact is not a file this
	// agent may edit, and the project has files by the same names.
	if strings.Contains(slashed, "node_modules/") || strings.Contains(slashed, "/.next/") {
		return nil
	}
	parts := strings.Split(strings.TrimPrefix(slashed, "./"), "/")

	var out []string
	for i, part := range parts[:len(parts)-1] {
		if projectRoots[part] {
			out = append(out, strings.Join(parts[i:], "/"))
		}
	}
	return out
}

// filesIn pulls project-relative paths out of an error message.
func filesIn(text string, run *core.Run) []string {
	seen := map[string]bool{}
	var out []string
	for _, token := range pathToken.FindAllString(text, -1) {
		for _, rel := range relativeSources(token) {
			if seen[rel] || !run.Shell.Exists(rel) {
				continue
			}
			seen[rel] = true
			out = append(out, rel)
		}
	}
	return out
}

// defaultSuspects is what to read when the error names nothing useful.
func defaultSuspects(run *core.Run) []string {
	var out []string
	for _, rel := range []string{"app/layout.jsx", "app/page.jsx", "lib/db.js", "package.json"} {
		if run.Shell.Exists(rel) {
			out = append(out, rel)
		}
	}
	return out
}

// --- the dev server ----------------------------------------------------------

// devServer owns the `npm run dev` process and everything it printed.
type devServer struct {
	run    *core.Run
	cmd    *exec.Cmd
	cancel context.CancelFunc

	mu      sync.Mutex
	lines   []string
	problem string
	ready   bool
}

func newDevServer(run *core.Run) *devServer { return &devServer{run: run} }

func (d *devServer) start(parent context.Context) error {
	ctx, cancel := context.WithCancel(parent)
	d.cancel = cancel
	cmd, err := d.run.Shell.Start(ctx, d.onLine, core.NPM(), "run", "dev")
	if err != nil {
		cancel()
		return err
	}
	d.cmd = cmd
	return nil
}

// onLine records output and notices the shapes that mean "this is broken".
func (d *devServer) onLine(line string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.lines = append(d.lines, line)
	if len(d.lines) > 400 {
		d.lines = d.lines[len(d.lines)-400:]
	}
	if isReadyLine(line) {
		d.ready = true
	}
	if d.problem == "" && isErrorLine(line) {
		d.problem = strings.Join(tailFrom(d.lines, 12), "\n")
	}
}

func isReadyLine(line string) bool {
	l := strings.ToLower(line)
	return strings.Contains(l, "ready in") || strings.Contains(l, "- local:") ||
		strings.Contains(l, "ready on") || strings.Contains(l, "started server on")
}

// isErrorLine matches what next dev prints when the app cannot compile or a
// module is missing. Warnings are deliberately not in this list.
func isErrorLine(line string) bool {
	l := strings.ToLower(line)
	for _, needle := range []string{
		"failed to compile", "module not found", "syntaxerror", "typeerror:",
		"referenceerror", "cannot find module", "unhandled runtime error",
		"error: ", "build error",
	} {
		if strings.Contains(l, needle) {
			return true
		}
	}
	return false
}

// waitReady blocks until the app answers, and returns the error text if it
// never does.
func (d *devServer) waitReady(ctx context.Context, timeout time.Duration) string {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if ctx.Err() != nil {
			return ""
		}
		d.mu.Lock()
		problem := d.problem
		d.mu.Unlock()
		if problem != "" {
			return problem
		}
		if portOpen(core.DevPort) {
			// Listening is not the same as serving: ask for the page.
			if body, err := fetchText(ctx, "http://127.0.0.1:"+strconv.Itoa(core.DevPort)+"/", 20*time.Second); err == nil {
				if bad := errorInPage(body); bad != "" {
					return bad
				}
				return ""
			}
		}
		if !sleepCtx(ctx, time.Second) {
			return ""
		}
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.problem != "" {
		return d.problem
	}
	return "the dev server did not answer within " + timeout.String() + "\n" +
		strings.Join(tailFrom(d.lines, 20), "\n")
}

// errorInPage catches a Next error overlay served with a 200.
func errorInPage(body string) string {
	for _, needle := range []string{"Unhandled Runtime Error", "Failed to compile", "Build Error"} {
		if idx := strings.Index(body, needle); idx >= 0 {
			end := idx + 800
			if end > len(body) {
				end = len(body)
			}
			return stripTags(body[idx:end])
		}
	}
	return ""
}

var tagPattern = regexp.MustCompile(`<[^>]*>`)

func stripTags(s string) string {
	return strings.TrimSpace(tagPattern.ReplaceAllString(s, " "))
}

// clear forgets the last problem so the next wait judges the recompile.
func (d *devServer) clear() {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.problem = ""
	d.ready = false
}

// Output returns what the dev server has printed, for a later stage to read.
func (d *devServer) Output() string {
	d.mu.Lock()
	defer d.mu.Unlock()
	return strings.Join(tailFrom(d.lines, 120), "\n")
}

func (d *devServer) stop() {
	if d.cancel != nil {
		d.cancel()
	}
	if d.cmd != nil && d.cmd.Process != nil {
		_ = d.cmd.Process.Kill()
	}
}

func portOpen(port int) bool {
	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+strconv.Itoa(port), 400*time.Millisecond)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

func tailFrom(lines []string, n int) []string {
	if len(lines) <= n {
		return lines
	}
	return lines[len(lines)-n:]
}

func firstLines(text string, n int) string {
	lines := strings.Split(strings.TrimSpace(text), "\n")
	if len(lines) > n {
		lines = lines[:n]
	}
	return strings.Join(lines, "\n")
}

func sleepCtx(ctx context.Context, d time.Duration) bool {
	select {
	case <-ctx.Done():
		return false
	case <-time.After(d):
		return true
	}
}
