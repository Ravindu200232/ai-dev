package deploy

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func contractOf(t *testing.T) Contract {
	t.Helper()
	source := project(t)
	staged := filepath.Join(t.TempDir(), "staged")
	spec, err := (&Intake{}).Read(t.Context(), source, staged)
	if err != nil {
		t.Fatal(err)
	}
	return DiscoverContract(spec.Services[0], staged)
}

func entry(contract Contract, name string) EnvEntry {
	for _, item := range contract.Entries {
		if item.Name == name {
			return item
		}
	}
	return EnvEntry{}
}

func TestContractDecidesWhoSuppliesWhat(t *testing.T) {
	contract := contractOf(t)

	if got := entry(contract, "MONGODB_URI"); got.Resolution != ResolveUser || !got.Secret || !got.Required {
		t.Errorf("MONGODB_URI = %+v", got)
	}
	// The project imports better-auth but never names the secret, so the
	// contract adds it and the agent generates the value.
	if got := entry(contract, "BETTER_AUTH_SECRET"); got.Resolution != ResolveGenerate || !got.ValuePresent {
		t.Errorf("BETTER_AUTH_SECRET = %+v", got)
	}
	if got := entry(contract, "NEXT_PUBLIC_SITE_NAME"); got.Secret || got.Scope != "both" || !got.Public {
		t.Errorf("a public variable is not a secret and is needed twice: %+v", got)
	}
	if got := entry(contract, "STRIPE_SECRET_KEY"); !got.Secret || got.Resolution != ResolveUser {
		t.Errorf("STRIPE_SECRET_KEY = %+v", got)
	}
	// Sorted, so the review screen and the .env.example agree on the order.
	names := []string{}
	for _, item := range contract.Entries {
		names = append(names, item.Name)
	}
	if strings.Join(names, ",") != "BETTER_AUTH_SECRET,MONGODB_URI,NEXT_PUBLIC_SITE_NAME,STRIPE_SECRET_KEY" {
		t.Errorf("entries = %v", names)
	}
}

func TestPlatformVariablesAreNotAskedFor(t *testing.T) {
	contract := DiscoverContract(Service{Environment: []EnvVar{
		{Name: "NODE_ENV", Required: true, Scope: "runtime"},
		{Name: "AWS_REGION", Required: true, Secret: true, Scope: "runtime"},
	}}, "")
	for _, name := range []string{"NODE_ENV", "AWS_REGION"} {
		got := entry(contract, name)
		if got.Resolution != ResolveProvider || got.Secret || !got.ValuePresent {
			t.Errorf("%s = %+v", name, got)
		}
	}
	if problems := CheckContract(contract); len(problems) != 0 {
		t.Errorf("problems = %v", problems)
	}
}

func TestDevelopmentOnlyVariablesAreNotRequired(t *testing.T) {
	root := t.TempDir()
	body := `if (process.env.NODE_ENV === 'development') {
  load(process.env.DEBUG_PANEL_URL)
}
const always = process.env.API_BASE
`
	if err := os.WriteFile(filepath.Join(root, "app.js"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	contract := DiscoverContract(Service{Environment: []EnvVar{
		{Name: "DEBUG_PANEL_URL", Required: true, Scope: "runtime", Sources: []string{"app.js"}},
		{Name: "API_BASE", Required: true, Scope: "runtime", Sources: []string{"app.js"}},
		{Name: "NEXT_PUBLIC_AGENTFORGE_DEVTOOLS_SRC", Required: true, Scope: "build"},
	}}, root)

	if got := entry(contract, "DEBUG_PANEL_URL"); !got.DevelopmentOnly || got.Required {
		t.Errorf("a variable only read in development is not production's problem: %+v", got)
	}
	if got := entry(contract, "API_BASE"); got.DevelopmentOnly || !got.Required {
		t.Errorf("an unguarded read is required: %+v", got)
	}
	if got := entry(contract, "NEXT_PUBLIC_AGENTFORGE_DEVTOOLS_SRC"); !got.DevelopmentOnly {
		t.Errorf("the Studio's own devtools hook is never production's: %+v", got)
	}
	for _, item := range ProductionEntries(contract) {
		if item.DevelopmentOnly {
			t.Errorf("%s reached the production list", item.Name)
		}
	}
}

func TestResolveKeepsValuesOutOfTheContract(t *testing.T) {
	contract := contractOf(t)
	resolved, values, err := ResolveContract(contract, map[string]string{
		"mongodb_uri":           "mongodb+srv://user:hunter2@cluster/db",
		"STRIPE_SECRET_KEY":     "sk_live_1234",
		"NEXT_PUBLIC_SITE_NAME": "Corner Shop",
	})
	if err != nil {
		t.Fatal(err)
	}
	if values["MONGODB_URI"] == "" {
		t.Error("a supplied name is matched whatever case it was typed in")
	}
	if values["BETTER_AUTH_SECRET"] == "" || len(values["BETTER_AUTH_SECRET"]) < 32 {
		t.Errorf("a generated secret is generated: %q", values["BETTER_AUTH_SECRET"])
	}
	if body := SafeJSON(resolved); strings.Contains(body, "hunter2") || strings.Contains(body, "sk_live") {
		t.Errorf("the contract carried a value: %s", body)
	}
	if problems := CheckContract(resolved); len(problems) != 0 {
		t.Errorf("everything was supplied, so nothing is unresolved: %v", problems)
	}
	if left := Unresolved(resolved); len(left) != 0 {
		t.Errorf("unresolved = %+v", left)
	}
}

func TestUnresolvedBlocksTheDeployment(t *testing.T) {
	contract := contractOf(t)
	resolved, _, err := ResolveContract(contract, nil)
	if err != nil {
		t.Fatal(err)
	}
	problems := CheckContract(resolved)
	if len(problems) == 0 {
		t.Fatal("a deployment with no database URI is not ready")
	}
	if !warned(problems, "MONGODB_URI") {
		t.Errorf("problems = %v", problems)
	}
	if names := Unresolved(resolved); len(names) != 3 {
		t.Errorf("unresolved = %+v", names)
	}
}

func TestCheckContractRejectsNonsense(t *testing.T) {
	problems := CheckContract(Contract{Entries: []EnvEntry{
		{Name: "A", Resolution: "made_up"},
		{Name: "B", Resolution: ResolveUser, Public: true, Secret: true, ValuePresent: true},
		{Name: "B", Resolution: ResolveUser, ValuePresent: true},
		{Name: "PORT", Resolution: ResolveUser, ValuePresent: true},
	}})
	for _, want := range []string{"no supported resolution", "cannot be classified as a secret",
		"Duplicate environment variable: B", "PORT must be provider managed"} {
		if !warned(problems, want) {
			t.Errorf("%q missing from %v", want, problems)
		}
	}
}

func TestRuntimeSecretEntriesIncludeWhatTheDeployerFillsIn(t *testing.T) {
	entries := RuntimeSecretEntries(contractOf(t))
	names := []string{}
	for _, item := range entries {
		names = append(names, item.Name)
	}
	for _, want := range []string{"MONGODB_URI", "BETTER_AUTH_SECRET", "BETTER_AUTH_URL", "BASE_URL"} {
		if !contains(names, want) {
			t.Errorf("%s missing from the runtime secret: %v", want, names)
		}
	}
	if contains(names, "NEXT_PUBLIC_SITE_NAME") {
		t.Error("a public value is not carried in the runtime secret")
	}
}
