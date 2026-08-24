package deploy

import (
	"context"
	"strings"
	"testing"
)

// noHome points the home directory somewhere empty, so a test reads no
// settings file and writes nothing into the real one. Windows needs
// USERPROFILE as well, since that is what os.UserHomeDir reads there.
func noHome(t *testing.T) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
}

// planner returns a planner with no model, which is the state a machine is in
// when Ollama is not running — and the state most of these tests want, since
// what matters is that the plan is right without one.
func planner() (*Planner, *[]Event) {
	seen := &[]Event{}
	return &Planner{Emit: func(event Event) { *seen = append(*seen, event) }}, seen
}

func TestPlanIsCompleteWithoutAModel(t *testing.T) {
	spec := read(t, project(t))
	p, seen := planner()
	plan := p.Plan(context.Background(), spec, TargetEC2)

	if plan.ProjectSlug != "corner-shop" || plan.PrimaryService != "corner-shop" {
		t.Errorf("slug = %q %q", plan.ProjectSlug, plan.PrimaryService)
	}
	if plan.Region != DefaultRegion || plan.Target != TargetEC2 || plan.Topology != Topology {
		t.Errorf("topology = %+v", plan)
	}
	if plan.InstallCommand != "npm ci" || plan.BuildCommand != "npm run build" ||
		plan.StartCommand != "npm run start" || plan.Port != 4010 {
		t.Errorf("the plan did not take what intake read: %+v", plan)
	}
	if plan.HealthPath != DefaultHealthPath || plan.RuntimeStrategy != RuntimeStrategy {
		t.Errorf("runtime = %q %q", plan.HealthPath, plan.RuntimeStrategy)
	}
	if len(plan.GitHubJobs) != len(jobs) {
		t.Errorf("jobs = %v", plan.GitHubJobs)
	}
	if plan.AWSSizing["instance_type"] != FreeTierX86 {
		t.Errorf("sizing = %v", plan.AWSSizing)
	}
	if plan.ModelUsed {
		t.Error("no model answered, so none was used")
	}
	if !warned(plan.Risks, "Ollama planning was unavailable") {
		t.Errorf("risks = %v", plan.Risks)
	}
	if !warned(plan.Recommendations, "MONGODB_URI") || !warned(plan.Recommendations, "Better Auth") {
		t.Errorf("what the project imports is said with or without a model: %v", plan.Recommendations)
	}

	first, last := (*seen)[0], (*seen)[len(*seen)-1]
	if first.Stage != "planner" || first.Status != StatusRunning {
		t.Errorf("first = %+v", first)
	}
	if last.Status != StatusComplete || last.Percent != 29 {
		t.Errorf("last = %+v", last)
	}
}

func TestPlanCarriesTheEnvironmentContractWithoutValues(t *testing.T) {
	spec := read(t, project(t))
	p, _ := planner()
	plan := p.Plan(context.Background(), spec, TargetEC2)

	if len(plan.EnvironmentContract) != 3 {
		t.Fatalf("contract = %+v", plan.EnvironmentContract)
	}
	for _, item := range plan.EnvironmentContract {
		if item.Name == "" || item.Scope == "" {
			t.Errorf("incomplete: %+v", item)
		}
	}
	// The plan says what is needed, never where it was found or what it is.
	if body := SafeJSON(plan.EnvironmentContract); strings.Contains(body, "sources") ||
		strings.Contains(body, "app/page.tsx") {
		t.Errorf("contract = %s", body)
	}
	if body := SafeJSON(plan); strings.Contains(body, "hunter2") {
		t.Error("a plan must never carry a value")
	}
}

func TestChosenJobs(t *testing.T) {
	cases := []struct {
		requested []string
		want      []string
	}{
		// A complete selection is kept, in pipeline order.
		{[]string{"deploy", "build", "smoke-test", "push"},
			[]string{"build", "push", "deploy", "smoke-test"}},
		// A selection missing something essential is not a selection.
		{[]string{"build", "deploy"}, jobs},
		{nil, jobs},
		// Anything invented is dropped before the check.
		{[]string{"build", "push", "deploy", "smoke-test", "rm -rf /"},
			[]string{"build", "push", "deploy", "smoke-test"}},
	}
	for _, c := range cases {
		got := chosenJobs(c.requested)
		if strings.Join(got, ",") != strings.Join(c.want, ",") {
			t.Errorf("chosenJobs(%v) = %v, want %v", c.requested, got, c.want)
		}
	}
}

func TestInstanceType(t *testing.T) {
	noHome(t) // no settings file, so the free tier is on
	cases := map[string]string{
		"t3.micro":    "t3.micro",
		"t4g.micro":   "t4g.micro",
		"t3.medium":   "t3.micro",  // priced out of the free tier
		"t4g.small":   "t4g.micro", // the architecture is kept
		"m5.24xlarge": "",          // not offered at all
		"":            "",
	}
	for requested, want := range cases {
		if got := instanceType(requested); got != want {
			t.Errorf("instanceType(%q) = %q, want %q", requested, got, want)
		}
	}
}

func TestAllowedFilters(t *testing.T) {
	got := allowed([]string{"health-route", "rm-rf", "standalone-output", "health-route"},
		patches, len(patches))
	if strings.Join(got, ",") != "standalone-output,health-route" {
		t.Errorf("allowed = %v", got)
	}
	if got := allowed([]string{"same-origin-auth", "ensure-standalone-output"}, repairs, 1); len(got) != 1 {
		t.Errorf("the limit is a limit: %v", got)
	}
}

func TestSourcePatchesKeepTheirShape(t *testing.T) {
	got := sourcePatches([]map[string]string{
		{"path": "next.config.js", "reason": "standalone output", "change": "output: 'standalone'",
			"command": "rm -rf /"},
		{"path": "  ", "reason": "nothing"},
	})
	if len(got) != 1 || len(got[0]) != 3 || got[0]["path"] != "next.config.js" {
		t.Fatalf("patches = %+v", got)
	}
	if _, extra := got[0]["command"]; extra {
		t.Error("a field nobody asked for was kept")
	}
}

func TestClipDoesNotSplitACharacter(t *testing.T) {
	if got := clip("héllo", 3); got != "hé" {
		t.Errorf("clip = %q", got)
	}
	if got := clip("short", 40); got != "short" {
		t.Errorf("clip = %q", got)
	}
	if got := tail("abcdef", 3); got != "def" {
		t.Errorf("tail = %q", got)
	}
}

func TestCheckPlanReply(t *testing.T) {
	var reply planReply
	if checkPlanReply(&reply) == nil {
		t.Error("an empty answer is not a plan")
	}
	reply.Risks = []string{"the health route does not exist yet"}
	if err := checkPlanReply(&reply); err == nil || !strings.Contains(err.Error(), "instance_type") {
		t.Errorf("err = %v", err)
	}
	reply.Generation.AWSSizing.InstanceType = "t3.small"
	noHome(t)
	if checkPlanReply(&reply) != nil {
		t.Error("a size that is priced down is still a valid answer")
	}
}

func TestStackSlug(t *testing.T) {
	cases := map[string]string{
		"corner-shop":            "corner-shop",
		"Corner Shop":            "corner-shop",
		"":                       "nextjs-app",
		strings.Repeat("ab", 30): strings.Repeat("ab", 16),
	}
	for value, want := range cases {
		if got := stackSlug(value); got != want {
			t.Errorf("stackSlug(%q) = %q, want %q", value, got, want)
		}
	}
}

func TestRepairNeedsAModel(t *testing.T) {
	spec := read(t, project(t))
	p, _ := planner()
	plan := p.Plan(context.Background(), spec, TargetEC2)
	if _, err := p.RepairBuild(context.Background(), spec, plan, "boom"); err == nil {
		t.Error("a repair with nothing to ask is an error, not an empty list")
	}
}

func TestAdviceIsForTheTargetItIsGoing(t *testing.T) {
	spec := read(t, project(t))
	for target, want := range map[string]string{
		TargetEC2:    "AWS Secrets Manager",
		TargetECS:    "AWS Secrets Manager",
		TargetVercel: "Vercel production environment variable",
	} {
		p, _ := planner()
		plan := p.Plan(context.Background(), spec, target)
		if plan.Target != target {
			t.Errorf("target = %q", plan.Target)
		}
		if !warned(plan.Recommendations, want) {
			t.Errorf("%s advice = %v", target, plan.Recommendations)
		}
	}

	// And the thing in front of the app is named correctly for each.
	p, _ := planner()
	if plan := p.Plan(context.Background(), spec, TargetEC2); !warned(plan.Recommendations, "nginx") {
		t.Errorf("ec2 = %v", plan.Recommendations)
	}
	p, _ = planner()
	if plan := p.Plan(context.Background(), spec, TargetECS); !warned(plan.Recommendations, "load balancer") {
		t.Errorf("ecs = %v", plan.Recommendations)
	}
	p, _ = planner()
	if plan := p.Plan(context.Background(), spec, TargetVercel); warned(plan.Recommendations, "nginx") {
		t.Errorf("vercel was told about nginx: %v", plan.Recommendations)
	}
}
