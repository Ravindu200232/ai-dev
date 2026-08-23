package deploy

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// Nothing gets deployed that has not been checked here first.
//
// The agent generated these files, so most of what this looks for is a bug in
// the agent rather than a problem with the customer's project — which is the
// point: a generated deployment that quietly lost its OIDC restriction, or
// carried a secret into a repository, is exactly the failure nobody would
// notice until it mattered.
//
// Errors stop the run. Warnings are shown on the review screen and do not.

// Validation is what the checks found.
type Validation struct {
	Passed   bool     `json:"passed"`
	Errors   []string `json:"errors"`
	Warnings []string `json:"warnings"`
	Checks   []Check  `json:"checks"`
}

// Check is one thing that was looked at, named the way the review screen lists
// it.
type Check struct {
	Name   string `json:"name"`
	Status string `json:"status"`
}

// Validator checks the generated artifacts before anything is deployed.
type Validator struct{ Emit Emit }

// buildOnlyValues are the placeholder values the generated files are allowed
// to contain: they are in the files on purpose and are worthless.
var buildOnlyValues = []string{"AWS_SECRETS_MANAGER_ARN", BuildMongoURI}

// Validate checks everything the run generated.
func (v *Validator) Validate(staged string, records []Artifact, target string) Validation {
	profile := ProfileFor(target)
	v.Emit.step("security", StatusRunning, 78,
		"Validating generated artifacts and secret boundaries", nil)

	result := Validation{Errors: []string{}, Warnings: []string{}, Checks: []Check{}}
	present := map[string]bool{}
	bodies := map[string]string{}

	for _, record := range records {
		present[record.Path] = true
		body, err := os.ReadFile(filepath.Join(staged, filepath.FromSlash(record.Path)))
		if err != nil {
			result.fail("Missing generated artifact: " + record.Path)
			continue
		}
		bodies[record.Path] = string(body)
	}

	// Nothing generated may carry a secret value. .env.example is the one file
	// whose whole purpose is to name them, and it never holds one.
	for _, record := range records {
		if record.Path == ".env.example" {
			continue
		}
		if leaked := looksLikeSecret(bodies[record.Path]); leaked {
			result.fail("Potential secret value detected in " + record.Path)
		}
	}

	// Every JSON file the agent wrote has to parse, or the thing that reads it
	// fails somewhere far away from here.
	for _, record := range records {
		if !strings.HasSuffix(record.Path, ".json") {
			continue
		}
		var any any
		if err := json.Unmarshal([]byte(bodies[record.Path]), &any); err != nil {
			result.fail("Invalid JSON in " + record.Path + ": " + err.Error())
			continue
		}
		result.pass("JSON: " + record.Path)
	}

	for _, required := range profile.RequiredArtifacts {
		if hasArtifact(present, required) {
			result.pass(required)
			continue
		}
		result.check(required, GateFailed)
		result.fail("Required artifact not generated: " + required)
	}

	v.workflows(&result, bodies, profile)
	v.infrastructure(&result, staged, bodies, profile)
	v.manifest(&result, bodies, present, profile)

	result.Passed = len(result.Errors) == 0
	status, message := StatusComplete, "Security validation passed"
	if !result.Passed {
		status = StatusFailed
		message = "Security validation failed with " + strconv.Itoa(len(result.Errors)) + " issue(s)"
	}
	v.Emit.step("security", status, 84, message, asMap(result))
	return result
}

// workflows checks that what GitHub will run is what was meant: the right
// triggers, the right job, and no more permission than the job needs.
func (v *Validator) workflows(result *Validation, bodies map[string]string, profile Profile) {
	for _, want := range []struct{ file, job string }{
		{"ci.yml", "validate"},
		{"deploy.yml", "deploy"},
	} {
		path := ".github/workflows/" + want.file
		body, ok := bodies[path]
		if !ok {
			result.fail("Invalid GitHub workflow " + want.file + ": it was not generated")
			continue
		}
		workflow := readYAML(body)

		if !workflow.hasSection("on") {
			result.fail("Invalid GitHub workflow " + want.file + ": workflow triggers are missing")
			continue
		}
		if !workflow.hasKey("jobs", want.job) {
			result.fail("Invalid GitHub workflow " + want.file + ": required job " + want.job + " is missing")
			continue
		}
		if want.file == "deploy.yml" {
			if problem := deployPermissions(workflow, profile); problem != "" {
				result.fail("Invalid GitHub workflow " + want.file + ": " + problem)
				continue
			}
		}
		result.pass("Workflow YAML: " + want.file)
	}
}

// deployPermissions is the least-privilege rule for the workflow that can
// reach the customer's cloud account.
func deployPermissions(workflow yamlDoc, profile Profile) string {
	if workflow.value("permissions", "contents") != "read" {
		return "least-privilege contents: read permission is missing"
	}
	token := workflow.value("permissions", "id-token")
	switch {
	case profile.NeedsOIDC && token != "write":
		return "least-privilege GitHub OIDC permissions are missing"
	case !profile.NeedsOIDC && token != "":
		return "this target must not request an OIDC id-token"
	}
	if !workflow.hasKey("on", "workflow_dispatch") {
		return "retry-safe workflow_dispatch trigger is missing"
	}
	return ""
}

// infrastructure checks the CloudFormation the account will be built from.
func (v *Validator) infrastructure(result *Validation, staged string,
	bodies map[string]string, profile Profile) {
	if profile.Target == TargetVercel {
		// Vercel has no infrastructure of ours; the environment mapping is
		// the artifact that has to be right instead.
		v.vercel(result, staged, bodies)
		return
	}

	bootstrap, ok := bodies["infra/bootstrap.yml"]
	if !ok {
		result.fail("Invalid CloudFormation template bootstrap.yml: it was not generated")
		return
	}
	if !readYAML(bootstrap).hasSection("Resources") {
		result.fail("Invalid CloudFormation template bootstrap.yml: CloudFormation Resources are missing")
		return
	}
	result.pass("CloudFormation YAML: bootstrap.yml")

	// Without the subject restriction, any repository on GitHub could assume
	// the deployment role.
	if !strings.Contains(bootstrap, "token.actions.githubusercontent.com:sub") {
		result.fail("GitHub OIDC subject restriction is missing")
	}

	if profile.Target == TargetEC2 {
		if strings.Contains(bootstrap, "CidrIp: 0.0.0.0/0") {
			result.warn("The instance security group allows HTTP from the internet; " +
				"the application itself binds to loopback behind nginx.")
		}
		return
	}

	if !strings.Contains(bootstrap, "SourceSecurityGroupId") {
		result.warn("The task security group does not restrict ingress to the load balancer; " +
			"the container may be reachable directly from the internet.")
	}
	if !strings.Contains(bootstrap, "iam:PassedToService") {
		result.fail("iam:PassRole in the deploy role is not conditioned on ecs-tasks.amazonaws.com")
	}

	dockerfile := ""
	for path, body := range bodies {
		if filepath.Base(path) == "Dockerfile" {
			dockerfile = body
			break
		}
	}
	if dockerfile == "" {
		result.fail("Dockerfile is missing")
		return
	}
	if !strings.Contains(dockerfile, "USER ") {
		result.warn("The container image runs as root; add a non-root USER.")
	}
	if !strings.Contains(dockerfile, ".next/static") {
		result.fail("The image does not copy .next/static into the standalone tree; " +
			"the deployed application would serve no CSS or images.")
	}
	result.pass("Container image")
}

// vercel checks that the environment mapping says exactly what the contract
// says, with no values in it.
func (v *Validator) vercel(result *Validation, staged string, bodies map[string]string) {
	status := GatePassed
	if problem := checkVercelEnvironment(bodies); problem != "" {
		status = GateFailed
		result.fail(problem)
	}
	result.check("Provider environment mapping", status)
	result.warn("A Vercel deploy token will be stored in this repository's GitHub Actions secrets; " +
		"anyone who can push a workflow there can use it.")
}

func checkVercelEnvironment(bodies map[string]string) string {
	body, ok := bodies["deploy/vercel-environment.json"]
	if !ok {
		return "Vercel environment mapping artifact is required"
	}
	var rendered struct {
		Variables []map[string]any `json:"variables"`
	}
	if err := json.Unmarshal([]byte(body), &rendered); err != nil {
		return "Vercel environment mapping is not readable: " + err.Error()
	}

	manifest, ok := bodies["deployment-manifest.json"]
	if !ok {
		return "Vercel environment mapping cannot be checked without the manifest"
	}
	var record struct {
		EnvironmentContract Contract `json:"environment_contract"`
	}
	if err := json.Unmarshal([]byte(manifest), &record); err != nil {
		return "Vercel environment mapping cannot be checked: " + err.Error()
	}

	seen := map[string]bool{}
	duplicates := []string{}
	names := map[string]map[string]any{}
	for _, item := range rendered.Variables {
		name := text(item["name"])
		if name == "" {
			continue
		}
		if seen[name] {
			duplicates = append(duplicates, name)
		}
		seen[name] = true
		names[name] = item
		if _, hasValue := item["value"]; hasValue {
			return "Vercel artifact must not contain a value for " + name
		}
	}
	if len(duplicates) > 0 {
		sort.Strings(duplicates)
		return "Duplicate environment mappings: " + strings.Join(duplicates, ", ")
	}

	expected := []string{}
	for _, entry := range ProductionEntries(record.EnvironmentContract) {
		if entry.Resolution == ResolveProvider || entry.Resolution == ResolveOptional {
			continue
		}
		expected = append(expected, entry.Name)
		item, mapped := names[entry.Name]
		if !mapped {
			continue
		}
		want := "plain"
		if entry.Secret {
			want = "sensitive"
		}
		if entry.Public && text(item["type"]) == "sensitive" {
			return "Public variable " + entry.Name + " cannot be stored as sensitive"
		}
		if text(item["type"]) != want {
			return "Vercel variable " + entry.Name + " has the wrong sensitivity"
		}
	}

	renderedNames := sortedKeys(names)
	sort.Strings(expected)
	if strings.Join(renderedNames, ",") != strings.Join(expected, ",") {
		return "Vercel production environment mapping mismatch; expected [" +
			strings.Join(expected, " ") + "], rendered [" + strings.Join(renderedNames, " ") + "]"
	}
	return ""
}

// manifest checks the record the deployment ships with: that it is this
// agent's, that it describes this target, and that it owns everything the run
// wrote — an artifact missing from it is an artifact nobody can put back.
func (v *Validator) manifest(result *Validation, bodies map[string]string,
	present map[string]bool, profile Profile) {
	body, ok := bodies["deployment-manifest.json"]
	if !ok {
		result.fail("Invalid deployment manifest: it was not generated")
		return
	}
	var record Manifest
	if err := json.Unmarshal([]byte(body), &record); err != nil {
		result.fail("Invalid deployment manifest: " + err.Error())
		return
	}
	if record.Version != ArtifactVersion {
		result.fail("Invalid deployment manifest: artifact schema version is stale")
		return
	}
	if !record.ModelUsed {
		// The plan the deployment is built from has to be one a model looked
		// at. Deterministic defaults are enough to show a customer what would
		// happen; they are not enough to do it to their account.
		result.fail("Invalid deployment manifest: a validated Ollama plan is required")
		return
	}
	if record.Generation.RuntimeStrategy != profile.RuntimeStrategy {
		result.fail("Invalid deployment manifest: validated " + profile.Label +
			" generation strategy is missing (expected " + profile.RuntimeStrategy + ")")
		return
	}
	owned := map[string]bool{}
	for _, path := range record.AgentOwnedFiles {
		owned[path] = true
	}
	for path := range present {
		if !owned[path] {
			result.fail("Invalid deployment manifest: agent-owned file manifest is incomplete")
			return
		}
	}
	result.pass("AI generation manifest")
}

// --- how a result is built -----------------------------------------------------------------

func (v *Validation) fail(message string) {
	v.Errors = append(v.Errors, RedactText(message))
}

func (v *Validation) warn(message string) {
	v.Warnings = append(v.Warnings, RedactText(message))
}

func (v *Validation) pass(name string) { v.check(name, GatePassed) }

func (v *Validation) check(name, status string) {
	v.Checks = append(v.Checks, Check{Name: name, Status: status})
}

// hasArtifact matches a required file at the repository root or under a
// service directory, since a monorepo puts the Dockerfile beside its service.
func hasArtifact(present map[string]bool, required string) bool {
	for path := range present {
		if path == required || strings.HasSuffix(path, "/"+required) {
			return true
		}
	}
	return false
}

// looksLikeSecret is the shape test from secret.go, minus the placeholders the
// generated files carry on purpose. Every match is checked, not just the
// first: a workflow contains the build placeholder by design, and stopping at
// it would let a real connection string in the same file through.
func looksLikeSecret(body string) bool {
	for _, pattern := range secretValues {
		for _, match := range pattern.FindAllString(body, -1) {
			if !buildPlaceholder(match) {
				return true
			}
		}
	}
	return false
}

func buildPlaceholder(match string) bool {
	for _, allowed := range buildOnlyValues {
		if strings.Contains(match, allowed) {
			return true
		}
	}
	return false
}
