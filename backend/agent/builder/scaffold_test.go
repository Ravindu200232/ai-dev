package builder

import (
	"encoding/json"
	"strings"
	"testing"

	"agentforge/agent/server"
)

// The lint setup is generated, so a mistake in it does not show up until a
// build runs eslint in a project nobody has looked at. Both halves matter: the
// config has to be the flat array eslint-config-next 16 exports, and the
// packages that provide it have to be installed.
func TestScaffoldShipsALintSetupThatRuns(t *testing.T) {
	run := newRun(t, nil)
	p := NewPipeline(server.Message{Type: "agent_build"}, nil)
	if err := p.scaffold(run); err != nil {
		t.Fatal(err)
	}

	cfg, _, err := run.Shell.Read("eslint.config.mjs")
	if err != nil {
		t.Fatalf("no eslint config was scaffolded: %v", err)
	}
	if !strings.Contains(cfg, "eslint-config-next/core-web-vitals") {
		t.Errorf("the config does not pull in the Next rules:\n%s", cfg)
	}
	// Going through FlatCompat is what an older project would do, and on
	// eslint 9 it dies with a circular structure before reading a file.
	if strings.Contains(cfg, "FlatCompat") {
		t.Errorf("FlatCompat does not work with eslint 9:\n%s", cfg)
	}
	// Linting the build output would report thousands of problems in code
	// nobody wrote.
	if !strings.Contains(cfg, ".next/**") {
		t.Error("the config does not ignore the build output")
	}

	body, _, err := run.Shell.Read("package.json")
	if err != nil {
		t.Fatal(err)
	}
	var pkg struct {
		Scripts         map[string]string `json:"scripts"`
		DevDependencies map[string]string `json:"devDependencies"`
	}
	if err := json.Unmarshal([]byte(body), &pkg); err != nil {
		t.Fatal(err)
	}
	if pkg.Scripts["lint"] == "" {
		t.Error("there is no lint script")
	}
	for _, need := range []string{"eslint", "eslint-config-next"} {
		if pkg.DevDependencies[need] == "" {
			t.Errorf("%s is not installed, so the config cannot load", need)
		}
	}
	// A dev script that does not pin the port strands the app somewhere the
	// harness never looks.
	if !strings.Contains(pkg.Scripts["dev"], "--port") {
		t.Errorf("the dev script does not pin a port: %q", pkg.Scripts["dev"])
	}
}
