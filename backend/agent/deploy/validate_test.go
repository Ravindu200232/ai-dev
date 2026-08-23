package deploy

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// validated generates a deployment and checks it, which is the order the run
// itself does it in.
func validated(t *testing.T, target string) (Validation, string, []Artifact) {
	t.Helper()
	_, plan, records, staged, _ := generated(t, target)
	// Generation was checked against the golden with no model; validation
	// requires one, so the manifest is rewritten as a run with a model would
	// have written it.
	plan.ModelUsed = true
	withModelUsed(t, staged, records)
	return (&Validator{}).Validate(staged, records, target), staged, records
}

// withModelUsed re-renders the manifest with model_used set, the way the run
// does when the planner reached a model.
func withModelUsed(t *testing.T, staged string, records []Artifact) {
	t.Helper()
	path := filepath.Join(staged, "deployment-manifest.json")
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	updated := strings.Replace(string(body), `"model_used": false`, `"model_used": true`, 1)
	if err := os.WriteFile(path, []byte(updated), 0o644); err != nil {
		t.Fatal(err)
	}
	for i, record := range records {
		if record.Path == "deployment-manifest.json" {
			records[i].SHA256 = SHA256([]byte(updated))
		}
	}
}

func TestGeneratedDeploymentsValidate(t *testing.T) {
	for _, target := range []string{TargetEC2, TargetECS, TargetVercel} {
		t.Run(target, func(t *testing.T) {
			result, _, _ := validated(t, target)
			if !result.Passed {
				t.Fatalf("errors = %v", result.Errors)
			}
			if len(result.Checks) == 0 {
				t.Error("a validation with no checks proves nothing")
			}
			for _, check := range result.Checks {
				if check.Status != GatePassed {
					t.Errorf("%s = %s", check.Name, check.Status)
				}
			}
			for _, required := range ProfileFor(target).RequiredArtifacts {
				if !named(result.Checks, required) {
					t.Errorf("%s was not checked for", required)
				}
			}
		})
	}
}

func TestVercelValidationSaysWhatItCosts(t *testing.T) {
	result, _, _ := validated(t, TargetVercel)
	if !warned(result.Warnings, "anyone who can push a workflow there can use it") {
		t.Errorf("warnings = %v", result.Warnings)
	}
	if !named(result.Checks, "Provider environment mapping") {
		t.Errorf("checks = %+v", result.Checks)
	}
}

func TestEC2ValidationWarnsAboutTheOpenPort(t *testing.T) {
	result, _, _ := validated(t, TargetEC2)
	if !warned(result.Warnings, "allows HTTP from the internet") {
		t.Errorf("warnings = %v", result.Warnings)
	}
	if !result.Passed {
		t.Errorf("a warning does not stop a deployment: %v", result.Errors)
	}
}

func TestAMissingArtifactStopsTheDeployment(t *testing.T) {
	result, staged, records := validated(t, TargetEC2)
	if !result.Passed {
		t.Fatal(result.Errors)
	}
	if err := os.Remove(filepath.Join(staged, "deploy", "release.sh")); err != nil {
		t.Fatal(err)
	}
	after := (&Validator{}).Validate(staged, records, TargetEC2)
	if after.Passed || !warned(after.Errors, "Missing generated artifact: deploy/release.sh") {
		t.Errorf("errors = %v", after.Errors)
	}
}

func TestASecretInAGeneratedFileStopsTheDeployment(t *testing.T) {
	_, staged, records := validated(t, TargetEC2)
	path := filepath.Join(staged, ".github", "workflows", "deploy.yml")
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	leaked := string(body) + "\n# MONGODB_URI=mongodb+srv://user:hunter2@cluster/db\n"
	if err := os.WriteFile(path, []byte(leaked), 0o644); err != nil {
		t.Fatal(err)
	}
	result := (&Validator{}).Validate(staged, records, TargetEC2)
	if result.Passed {
		t.Fatal("a connection string in a workflow is a leaked secret")
	}
	if !warned(result.Errors, "Potential secret value detected") {
		t.Errorf("errors = %v", result.Errors)
	}
	// The error itself must not repeat the secret.
	for _, message := range result.Errors {
		if strings.Contains(message, "hunter2") {
			t.Errorf("the error leaked it again: %q", message)
		}
	}
}

func TestBuildPlaceholdersAreNotSecrets(t *testing.T) {
	if looksLikeSecret("MONGODB_URI: " + BuildMongoURI) {
		t.Error("the build placeholder is in every generated workflow on purpose")
	}
	if !looksLikeSecret("mongodb+srv://user:hunter2@cluster/db") {
		t.Error("a real connection string is a secret")
	}
	if !looksLikeSecret("AKIAIOSFODNN7EXAMPLE") {
		t.Error("an access key is a secret whatever it is called")
	}
}

func TestAWorkflowThatAsksForTooMuchIsRejected(t *testing.T) {
	_, staged, records := validated(t, TargetVercel)
	path := filepath.Join(staged, ".github", "workflows", "deploy.yml")
	body, _ := os.ReadFile(path)
	// Vercel deploys with a token and must never be able to assume an AWS role.
	widened := strings.Replace(string(body), "permissions:\n  contents: read",
		"permissions:\n  contents: read\n  id-token: write", 1)
	if err := os.WriteFile(path, []byte(widened), 0o644); err != nil {
		t.Fatal(err)
	}
	result := (&Validator{}).Validate(staged, records, TargetVercel)
	if result.Passed || !warned(result.Errors, "must not request an OIDC id-token") {
		t.Errorf("errors = %v", result.Errors)
	}
}

func TestADeploymentWithoutAModelIsNotAllowed(t *testing.T) {
	_, _, records, staged, _ := generated(t, TargetEC2)
	result := (&Validator{}).Validate(staged, records, TargetEC2)
	if result.Passed || !warned(result.Errors, "a validated Ollama plan is required") {
		t.Errorf("errors = %v", result.Errors)
	}
}

func TestReadYAML(t *testing.T) {
	doc := readYAML(`name: CI

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "  not: a key"
`)
	if !doc.hasSection("on") || !doc.hasKey("on", "workflow_dispatch") {
		t.Errorf("sections = %+v", doc.sections)
	}
	if doc.value("permissions", "contents") != "read" ||
		doc.value("permissions", "id-token") != "write" {
		t.Errorf("permissions = %+v", doc.sections["permissions"])
	}
	if !doc.hasKey("jobs", "deploy") {
		t.Error("the job was not found")
	}
	if doc.hasSection("nothing") || doc.value("on", "nothing") != "" {
		t.Error("a section that is not there is not there")
	}
	if doc.value("jobs", "not") != "" {
		t.Error("a line inside a block scalar was read as a key")
	}
}

func named(checks []Check, name string) bool {
	for _, check := range checks {
		if check.Name == name {
			return true
		}
	}
	return false
}
