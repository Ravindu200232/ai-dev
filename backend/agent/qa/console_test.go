package qa

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The spec is generated, so nothing else would notice if a change to the
// template stopped it parsing. Playwright would simply report a broken suite.
func TestConsoleSpecIsValidJavaScript(t *testing.T) {
	if _, err := exec.LookPath("node"); err != nil {
		t.Skip("node is not installed")
	}
	routes, err := json.Marshal([]string{"/", "/login", "/admin/rooms"})
	if err != nil {
		t.Fatal(err)
	}

	// .mjs so node parses the import statements as the module they are.
	path := filepath.Join(t.TempDir(), "console.mjs")
	if err := os.WriteFile(path, []byte(fmt.Sprintf(consoleSpec, routes)), 0o644); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command("node", "--check", path).CombinedOutput(); err != nil {
		t.Fatalf("the generated spec does not parse: %v\n%s", err, out)
	}
}

func TestConsoleSpecCoversEveryRoute(t *testing.T) {
	routes, err := json.Marshal([]string{"/", "/admin/bookings"})
	if err != nil {
		t.Fatal(err)
	}
	spec := fmt.Sprintf(consoleSpec, routes)

	for _, want := range []string{`"/"`, `"/admin/bookings"`} {
		if !strings.Contains(spec, want) {
			t.Errorf("the spec does not visit %s", want)
		}
	}
	// An uncaught exception never reaches the console listener, so both hooks
	// have to be attached or a thrown error passes for a clean page.
	for _, hook := range []string{"'console'", "'pageerror'"} {
		if !strings.Contains(spec, hook) {
			t.Errorf("the spec does not listen for %s", hook)
		}
	}
	// A 500 that renders no console output would otherwise pass.
	if !strings.Contains(spec, "status() >= 500") {
		t.Error("the spec does not notice a server error")
	}
}

// Warnings and info are not failures; only errors are.
func TestConsoleSpecOnlyFailsOnErrors(t *testing.T) {
	spec := fmt.Sprintf(consoleSpec, "[]")
	if !strings.Contains(spec, "msg.type() !== 'error'") {
		t.Error("the spec does not filter down to errors")
	}
	if !strings.Contains(spec, "IGNORE") {
		t.Error("the spec keeps no allowance for noise the app cannot fix")
	}
}

// The log used to say errors were found and never what they were, which left
// nothing to act on when the repair could not fix them either.
func TestConsoleProblemsPullsOutWhatTheBrowserSaid(t *testing.T) {
	report := strings.Join([]string{
		"Running 8 tests using 4 workers",
		"  1) [chromium] > zz-console.spec.js:14:3 > no console errors on /admin/rooms",
		"    Error: the browser logged errors on /admin/rooms",
		"    Expected: []",
		"    Received: [",
		`      "TypeError: rooms.map is not a function",`,
		`      "Failed to load resource: the server responded with a status of 500"`,
		"    ]",
		"  8 passed",
	}, "\n")

	got := consoleProblems(report)
	joined := strings.Join(got, "\n")

	if !strings.Contains(joined, "rooms.map is not a function") {
		t.Errorf("the actual fault was dropped:\n%s", joined)
	}
	if !strings.Contains(joined, "/admin/rooms") {
		t.Errorf("the route was dropped, so there is nothing to look at:\n%s", joined)
	}
	// The runner's own framing is noise.
	if strings.Contains(joined, "Running 8 tests") {
		t.Errorf("the runner's banner is not a browser error:\n%s", joined)
	}
	// A long line must not push everything else out of the log.
	for _, line := range consoleProblems(strings.Repeat("Error: x", 400)) {
		if len(line) > 220 {
			t.Errorf("a line was left %d characters long", len(line))
		}
	}
}
