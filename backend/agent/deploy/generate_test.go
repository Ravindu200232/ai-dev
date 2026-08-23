package deploy

import (
	"context"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The generated files are pinned against what the Python deployment agent
// produced for the same project, byte for byte, because a customer's
// deployment is these files and nothing else. Two of them are deliberately
// not identical, and say so where they are checked.

// update rewrites the golden files from what the generator produces now. Run
// it — `go test ./deploy -run Golden -update` — after a deliberate change to a
// generated file, and read the diff before committing it.
var update = flag.Bool("update", false, "rewrite the golden files in testdata")

func generated(t *testing.T, target string) (*Spec, *Plan, []Artifact, string, Contract) {
	t.Helper()
	staged := filepath.Join(t.TempDir(), "staged")
	spec, err := (&Intake{}).Read(context.Background(), project(t), staged)
	if err != nil {
		t.Fatal(err)
	}
	plan := (&Planner{}).Plan(context.Background(), spec, target)
	// The model was not reachable, and its absence is a risk on every plan.
	// The golden was captured before that line existed, so the comparison is
	// of what generation does, not of what planning said.
	plan.Risks = nil
	plan.Recommendations = nil
	plan.Model = "gemma4:31b-cloud"

	records, contract, err := (&Generator{}).Generate(spec, plan, staged, target)
	if err != nil {
		t.Fatal(err)
	}
	return spec, plan, records, staged, contract
}

func TestGeneratedFilesMatchTheGolden(t *testing.T) {
	for _, target := range []string{TargetEC2, TargetECS, TargetVercel} {
		t.Run(target, func(t *testing.T) {
			_, _, records, staged, _ := generated(t, target)

			for _, record := range records {
				want, err := os.ReadFile(filepath.Join("testdata", target, record.Path))
				if os.IsNotExist(err) {
					// bootstrap.yml is the embedded template with one value
					// filled in; TestBootstrapTemplates checks that instead.
					continue
				}
				if err != nil {
					t.Fatal(err)
				}
				got, err := os.ReadFile(filepath.Join(staged, filepath.FromSlash(record.Path)))
				if err != nil {
					t.Fatal(err)
				}
				if *update {
					if err := os.WriteFile(filepath.Join("testdata", target, record.Path), got, 0o644); err != nil {
						t.Fatal(err)
					}
					continue
				}
				if string(got) != string(want) {
					wantLine, gotLine := firstDifference(string(want), string(got))
					t.Errorf("%s differs:\n  want %s\n   got %s", record.Path, wantLine, gotLine)
				}
			}
		})
	}
}

// firstDifference is the line the two texts stop agreeing on, with a little
// either side. A whole-file dump of a 100-line workflow tells nobody anything.
func firstDifference(want, got string) (string, string) {
	wantLines, gotLines := strings.Split(want, "\n"), strings.Split(got, "\n")
	for i := 0; i < len(wantLines) || i < len(gotLines); i++ {
		w, g := "", ""
		if i < len(wantLines) {
			w = wantLines[i]
		}
		if i < len(gotLines) {
			g = gotLines[i]
		}
		if w != g {
			return "line " + itoa(i+1) + ": " + w, "line " + itoa(i+1) + ": " + g
		}
	}
	return want, got
}

func TestEveryTargetGeneratesWhatItNeeds(t *testing.T) {
	for _, target := range []string{TargetEC2, TargetECS, TargetVercel} {
		t.Run(target, func(t *testing.T) {
			_, _, records, staged, _ := generated(t, target)
			written := map[string]bool{}
			for _, record := range records {
				written[record.Path] = true
				if record.SHA256 == "" || record.Size == 0 {
					t.Errorf("unrecorded: %+v", record)
				}
			}
			for _, required := range ProfileFor(target).RequiredArtifacts {
				if !written[required] {
					t.Errorf("%s is required for %s but was not generated", required, target)
				}
			}
			// Everything recorded is really there.
			for path := range written {
				if _, err := os.Stat(filepath.Join(staged, filepath.FromSlash(path))); err != nil {
					t.Errorf("%s was recorded but not written", path)
				}
			}
		})
	}
}

func TestBootstrapTemplates(t *testing.T) {
	_, plan, _, staged, _ := generated(t, TargetEC2)
	body, err := os.ReadFile(filepath.Join(staged, "infra", "bootstrap.yml"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), "Default: "+FreeTierX86) {
		t.Error("the EC2 template did not take the planned instance type")
	}
	if strings.Contains(string(body), "<%") {
		t.Error("an unrendered placeholder reached the template")
	}
	if plan.RuntimeStrategy != RuntimeStrategy {
		t.Errorf("runtime strategy = %q", plan.RuntimeStrategy)
	}

	_, _, _, ecsStaged, _ := generated(t, TargetECS)
	ecs, err := os.ReadFile(filepath.Join(ecsStaged, "infra", "bootstrap.yml"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(ecs), "Default: 4010") ||
		!strings.Contains(string(ecs), "HealthCheckPath: /api/health") {
		t.Error("the ECS template did not take the port and health path")
	}
}

func TestNothingGeneratedCarriesASecret(t *testing.T) {
	for _, target := range []string{TargetEC2, TargetECS, TargetVercel} {
		_, _, records, staged, _ := generated(t, target)
		for _, record := range records {
			body, err := os.ReadFile(filepath.Join(staged, filepath.FromSlash(record.Path)))
			if err != nil {
				continue
			}
			if strings.Contains(string(body), "hunter2") {
				t.Errorf("%s carries a value from the customer's .env", record.Path)
			}
		}
	}
}

func TestManifestRecordsWhatItOwns(t *testing.T) {
	_, plan, records, staged, _ := generated(t, TargetEC2)
	body, err := os.ReadFile(filepath.Join(staged, "deployment-manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var got Manifest
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	if got.Version != ArtifactVersion || got.Target != TargetEC2 {
		t.Errorf("manifest = %+v", got)
	}
	if len(got.AgentOwnedFiles) != len(records) {
		t.Errorf("owned = %d, generated = %d", len(got.AgentOwnedFiles), len(records))
	}
	if got.Generation.Port != plan.Port || got.Generation.InstallCommand != plan.InstallCommand {
		t.Errorf("the manifest does not restate the plan: %+v", got.Generation)
	}
	if got.ProjectSlug != "corner-shop" {
		t.Errorf("slug = %q", got.ProjectSlug)
	}
}

func TestReportSaysWhatTheTargetIs(t *testing.T) {
	for target, want := range map[string]string{
		TargetEC2:    "AWS EC2 (t3.micro) behind nginx",
		TargetECS:    "AWS ECS Fargate behind an application load balancer",
		TargetVercel: "Vercel production deployment",
	} {
		_, _, _, staged, _ := generated(t, target)
		body, err := os.ReadFile(filepath.Join(staged, "deployment-report.md"))
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(body), want) {
			t.Errorf("%s report does not say what it is:\n%s", target, body)
		}
		// The Vercel target's long-lived token is said plainly, every time.
		if target == TargetVercel && !strings.Contains(string(body), "long-lived credential") {
			t.Error("the Vercel report does not warn about the deploy token")
		}
	}
}

func TestToolchain(t *testing.T) {
	cases := []struct {
		manager, install string
		root             string
		lockfile         string
		cache            bool
		preinstall       string
		audit            string
	}{
		{"npm", "npm ci", "", "package-lock.json", true, "# package manager is ready",
			"npm audit --omit=dev --audit-level=high"},
		{"npm", "npm install", "", "package-lock.json", false, "# package manager is ready",
			"npm audit --omit=dev --audit-level=high"},
		{"pnpm", "pnpm install --frozen-lockfile", "apps/web", "apps/web/pnpm-lock.yaml", true,
			"corepack enable", "pnpm audit --prod --audit-level high"},
		{"yarn", "yarn install --frozen-lockfile", "", "yarn.lock", true,
			"corepack enable", "yarn audit --groups dependencies --level high"},
		{"bun", "bun install --frozen-lockfile", "", "bun.lock", false,
			"# package manager is ready", "bun audit"},
	}
	for _, c := range cases {
		lockfile, cache, preinstall, audit := toolchain(Service{
			PackageManager: c.manager, InstallCommand: c.install, Root: c.root,
		})
		if lockfile != c.lockfile || cache != c.cache || preinstall != c.preinstall || audit != c.audit {
			t.Errorf("toolchain(%s) = %q %v %q %q", c.manager, lockfile, cache, preinstall, audit)
		}
	}
}

func TestTriggerBranches(t *testing.T) {
	cases := map[string]string{
		"main":    "main, master",
		"master":  "master, main",
		"develop": "develop, main, master",
	}
	for branch, want := range cases {
		if got := triggerBranches(branch); got != want {
			t.Errorf("triggerBranches(%q) = %q, want %q", branch, got, want)
		}
	}
}
