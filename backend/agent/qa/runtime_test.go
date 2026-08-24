package qa

import (
	"encoding/json"
	"strconv"
	"strings"
	"testing"

	"agentforge/agent/core"
)

// The repair loop is handed package.json whenever a runtime error names no
// file, and "address already in use" is exactly such an error. Removing the
// port flag silences it and strands the app on a port nothing is watching, so
// the flag has to be restored rather than trusted.
func TestPinDevPortPutsTheFlagBack(t *testing.T) {
	run := runIn(t, map[string]string{
		"package.json": `{
  "name": "demo",
  "scripts": { "dev": "next dev", "build": "next build", "test": "vitest run" },
  "dependencies": { "next": "16.3.0" }
}`,
	})

	pinDevPort(run)

	body, _, err := run.Shell.Read("package.json")
	if err != nil {
		t.Fatalf("package.json unreadable: %v", err)
	}
	var pkg map[string]any
	if err := json.Unmarshal([]byte(body), &pkg); err != nil {
		t.Fatalf("pinDevPort left invalid JSON behind: %v", err)
	}
	scripts := pkg["scripts"].(map[string]any)

	if got := scripts["dev"].(string); !strings.Contains(got, "--port") {
		t.Errorf("the dev script still does not pin a port: %q", got)
	}
	// Restoring one script must not cost the rest of the file.
	if scripts["build"] != "next build" || scripts["test"] != "vitest run" {
		t.Errorf("the other scripts were disturbed: %v", scripts)
	}
	if pkg["name"] != "demo" {
		t.Errorf("the package name was lost: %v", pkg["name"])
	}
	if deps, _ := pkg["dependencies"].(map[string]any); deps["next"] != "16.3.0" {
		t.Errorf("the dependencies were lost: %v", pkg["dependencies"])
	}
}

func TestPinDevPortLeavesAGoodScriptAlone(t *testing.T) {
	run := runIn(t, map[string]string{
		"package.json": `{"scripts":{"dev":"next dev --port 5173"}}`,
	})
	before, _, _ := run.Shell.Read("package.json")
	pinDevPort(run)
	after, _, _ := run.Shell.Read("package.json")
	if before != after {
		t.Errorf("a correct file was rewritten:\n%s", after)
	}
}

// A project too early to have one must not bring the boot down.
func TestPinDevPortSurvivesNoPackageJSON(t *testing.T) {
	pinDevPort(runIn(t, map[string]string{"app/page.jsx": "x"}))
}

// And neither must one that is not JSON at all.
func TestPinDevPortSurvivesRubbish(t *testing.T) {
	run := runIn(t, map[string]string{"package.json": "this is not json"})
	pinDevPort(run)
	if body, _, _ := run.Shell.Read("package.json"); body != "this is not json" {
		t.Errorf("unreadable JSON should be left alone, got %q", body)
	}
}

func TestPinDevPortUsesTheWatchedPort(t *testing.T) {
	run := runIn(t, map[string]string{"package.json": `{"scripts":{"dev":"next dev"}}`})
	pinDevPort(run)
	body, _, _ := run.Shell.Read("package.json")
	want := "--port " + strconv.Itoa(core.DevPort)
	if !strings.Contains(body, want) {
		t.Errorf("the dev script must pin the port the harness watches (%s): %s", want, body)
	}
}
