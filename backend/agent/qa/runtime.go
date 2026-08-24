// Package qa runs a built application through the checks that decide whether it
// actually works: it boots, its units pass, its API answers, its journeys walk,
// and it is not obviously slow or insecure.
package qa

import (
	"context"
	"encoding/json"
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
	// portFreeWait is how many quarter seconds a reclaimed port is given to
	// come free; a killed process does not release it the instant it dies.
	portFreeWait = 40
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
	pinDevPort(run)

	// Compile everything before serving anything. The dev server only builds
	// a route when something asks for it, so without this a page that cannot
	// compile is found much later by whichever stage opens it first, and
	// reported as whatever the browser saw rather than as the compiler error.
	s.buildCheck(ctx, run)

	// Then read what compiled. A file the compiler accepts can still call a
	// hook conditionally or reference something that is not there on one
	// branch, and lint names the file and line for both.
	s.lintCheck(ctx, run)

	if portOpen(core.DevPort) {
		// Nothing this build started can be there yet, so the preview port is
		// held by something else, almost always the preview an earlier build
		// left running. Take it back: warning and carrying on only ends in a
		// dev server that cannot bind and a timeout that explains nothing.
		run.Warn(fmt.Sprintf("port %d is in use; stopping whatever holds it", core.DevPort))
		core.ReclaimPort(core.DevPort)
		for waited := 0; waited < portFreeWait && portOpen(core.DevPort); waited++ {
			if !sleepCtx(ctx, 250*time.Millisecond) {
				return run.Check()
			}
		}
		if portOpen(core.DevPort) {
			run.Warn(fmt.Sprintf("port %d is still held by something this build cannot stop", core.DevPort))
		}
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
			s.report.Runtime.Passed, s.report.Runtime.Total = 1, 1
			return nil
		}
		run.Warn(fmt.Sprintf("🐞 runtime error (%d/%d)", attempt, maxRuntimeFix))
		run.TestResult("fail", "The app did not boot", firstLines(problem, 6))

		fixed, err := s.repairRuntime(ctx, run, problem)
		if err != nil {
			return fmt.Errorf("the runtime error could not be repaired: %w", err)
		}
		if !fixed {
			s.report.Runtime.Failed, s.report.Runtime.Total = 1, 1
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
	s.report.Runtime.Failed, s.report.Runtime.Total = 1, 1
	return fmt.Errorf("the app still does not boot after %d repairs", maxRuntimeFix)
}

// pinDevPort makes sure the dev script still starts the app on the port this
// harness watches, and puts it back when it does not.
//
// The repair loop may rewrite package.json - it is one of the files handed over
// when a runtime error names none - and "address already in use" is exactly the
// kind of error it gets asked to fix. Dropping the port flag makes that error
// go away and takes the app with it: Next quietly picks another port, nothing
// answers on the one being watched, and the build ends in a timeout naming no
// file, which no later repair can act on. So the flag is checked every boot.
func pinDevPort(run *core.Run) {
	const rel = "package.json"
	// Read reports truncation in its second value, not absence; a missing file
	// comes back as an error.
	body, _, err := run.Shell.Read(rel)
	if err != nil {
		return
	}
	var pkg map[string]any
	if json.Unmarshal([]byte(body), &pkg) != nil {
		return
	}
	scripts, _ := pkg["scripts"].(map[string]any)
	if scripts == nil {
		return
	}
	want := fmt.Sprintf("next dev --port %d", core.DevPort)
	if current, _ := scripts["dev"].(string); current == want {
		return
	}
	scripts["dev"] = want
	out, err := json.MarshalIndent(pkg, "", "  ")
	if err != nil {
		return
	}
	if run.Shell.Write(rel, string(out)+"\n") == nil {
		run.Warn(fmt.Sprintf("the dev script had stopped pinning port %d; put it back", core.DevPort))
	}
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
	text, err := run.LLM.Stream(ctx, core.RoleBuilder, runtimeFixSystem, b.String(), writer)
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
		// Our own process saying it is up is what makes the listener below
		// ours. Without this, anything already on the port — a server this
		// build did not start — is accepted as the application under test.
		d.mu.Lock()
		ready := d.ready
		d.mu.Unlock()

		if ready && portOpen(core.DevPort) {
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
// serverRenderMarker is what Next writes into the streamed payload when a
// server component throws. It matters because the response is still a 200
// carrying what looks like a page: the status says nothing, the visible text
// says nothing, and this string is the only sign anything went wrong.
const serverRenderMarker = "Switched to client rendering because the server rendering errored"

func errorInPage(body string) string {
	// A compile failure replaces the page outright, and says so in words.
	for _, needle := range []string{"Unhandled Runtime Error", "Failed to compile", "Build Error"} {
		if idx := strings.Index(body, needle); idx >= 0 {
			end := idx + 800
			if end > len(body) {
				end = len(body)
			}
			return stripTags(body[idx:end])
		}
	}
	// A fault while rendering does not. Next serves the page, reports the
	// error inside the payload, and lets the client try again — so a check
	// that reads only the status code passes a page that never rendered.
	if idx := strings.Index(body, serverRenderMarker); idx >= 0 {
		if msg := unescapePayload(body[idx+len(serverRenderMarker):], 400); msg != "" {
			return "the server render threw: " + msg
		}
		return "the server render threw, and the page fell back to the client"
	}
	return ""
}

// unescapePayload makes the fragment Next streams readable. It arrives twice
// escaped — once for the JSON, once for the script tag holding it — so the
// message is unreadable, and unsearchable for a file name, until it is undone.
func unescapePayload(s string, limit int) string {
	if len(s) > limit {
		s = s[:limit]
	}
	s = strings.NewReplacer(
		"\\n", " ",
		"\\\"", "\"",
		"\\\\", "\\",
		"\\u0026", "&",
	).Replace(s)
	s = strings.TrimLeft(s, ":\" ")
	// The message ends where the next field of the payload begins.
	for _, stop := range []string{"\",\"", "\",", "\"}"} {
		if i := strings.Index(s, stop); i > 0 {
			s = s[:i]
		}
	}
	return strings.TrimSpace(s)
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

// stop ends the dev server and everything it started.
//
// `npm run dev` is a launcher: it spawns the real server and waits. Killing
// only the launcher used to leave that server running and holding the port, so
// the next build found something listening, took it for its own application,
// and tested the previous one — reporting green checks against code it had
// never touched.
func (d *devServer) stop() {
	if d.cancel != nil {
		d.cancel()
	}
	core.KillTree(d.cmd)
	d.cmd = nil
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
