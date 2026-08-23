package deploy

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"agentforge/agent/core"
)

// Planner decides how the project gets deployed.
//
// Almost nothing here is the model's to decide. The commands, the port, the
// variables and the service root are facts intake read off disk, and they are
// written into the plan before the model is asked anything — so a model that
// is slow, absent, or wrong cannot change what gets run. What the model adds
// is judgement: the risks worth showing the customer, the recommendations, the
// deployment-compatibility patches, the CI jobs and the instance size. Each of
// those is filtered against a list of what is allowed before it is kept.
//
// A model that fails is not an error. The plan it would have improved is
// already complete, and the run says so on the review screen.

// Where a deployment goes, and what it is made of. These are one topology, not
// a menu: everything downstream — the templates, the workflows, the monitor —
// is written for this shape.
const (
	DefaultRegion  = "ap-south-1"
	Infrastructure = "cloudformation"
	Topology       = "ec2-single-instance"
	Database       = "mongodb-atlas"

	RuntimeStrategy = "nextjs-standalone"
	FreeTierARM     = "t4g.micro"
	FreeTierX86     = "t3.micro"
)

// jobs are the CI stages a deployment may ask for, in the order they run.
var jobs = []string{"validate", "build", "push", "deploy", "stabilize", "smoke-test"}

// jobsRequired are the ones a deployment is not a deployment without. A model
// that leaves any of them out gets the whole list instead of its own.
var jobsRequired = []string{"build", "push", "deploy", "smoke-test"}

// instances are the sizes a single-instance deployment may use. Nothing larger
// is offered: this is one Next.js process, and a bigger machine would cost the
// customer money for capacity the topology cannot use.
var instances = []string{"t3.micro", "t3.small", "t3.medium", "t4g.micro", "t4g.small"}

// patches are the changes a Next.js project may need to be deployable at all.
// They are recorded in the plan and the manifest; nothing else may be patched.
var patches = []string{"standalone-output", "health-route", "same-origin-auth", "defer-build-database"}

// repairs are what may be done to a build that failed. The list is closed on
// purpose: a model diagnosing a build must choose from things whose effect is
// already known, never write a change of its own.
var repairs = []string{
	"ensure-standalone-output",
	"ensure-type-safe-health-route",
	"same-origin-auth",
	"normalize-alert-variant",
}

const planSystem = `You are a conservative DevOps deployment planner. You are given a redacted
Next.js project specification that has already been read off disk.

Plan for one Next.js process managed by systemd on a single EC2 instance behind nginx, released
from S3 through SSM Run Command, with MongoDB Atlas via AWS Secrets Manager, GitHub OIDC,
CloudWatch and CloudFormation. Do not split a monolith into microservices. Source patches must be
limited to deployment compatibility — never features, never refactoring. Never request, repeat,
infer or output a credential value.

Reply with one JSON object and nothing else:

{
  "risks": ["short sentences — what could go wrong with this deployment"],
  "recommendations": ["short sentences — what the customer should do about it"],
  "source_patches": [{"path": "…", "reason": "…", "change": "…"}],
  "generation": {
    "github_jobs": ["validate", "build", "push", "deploy", "stabilize", "smoke-test"],
    "aws_sizing": {"instance_type": "t3.micro"},
    "required_patches": ["standalone-output", "health-route"]
  }
}

github_jobs must include build, push, deploy and smoke-test. instance_type must be one of
t3.micro, t3.small, t3.medium, t4g.micro, t4g.small — choose the smallest that will do.
required_patches may only contain standalone-output, health-route, same-origin-auth or
defer-build-database. Commands, ports and environment values are already decided; do not
restate them and do not invent new ones.`

const repairSystem = `You are diagnosing a failed Next.js production build for a deployment that has
already been reviewed. Choose only from the predefined compatibility actions below. A TypeScript
error saying an Alert variant such as ` + "`info`" + ` is not assignable to the project's Alert variant union
may use normalize-alert-variant; that action normalizes only that exact unsupported variant to the
component's default variant, in the isolated staging copy. Never output source code, commands,
credentials, environment values, or a file change of your own. Use no actions at all when nothing
listed applies.

Reply with one JSON object and nothing else:

{"summary": "one sentence", "actions": ["ensure-standalone-output"]}

actions may only contain ensure-standalone-output, ensure-type-safe-health-route, same-origin-auth
or normalize-alert-variant, at most four, with no repeats.`

// planReply is only the part of the model's answer that is kept. Everything
// else about a deployment is already known, so it is not asked for.
type planReply struct {
	Risks           []string            `json:"risks"`
	Recommendations []string            `json:"recommendations"`
	SourcePatches   []map[string]string `json:"source_patches"`
	Generation      struct {
		GitHubJobs []string `json:"github_jobs"`
		AWSSizing  struct {
			InstanceType string `json:"instance_type"`
		} `json:"aws_sizing"`
		RequiredPatches []string `json:"required_patches"`
	} `json:"generation"`
}

type repairReply struct {
	Summary string   `json:"summary"`
	Actions []string `json:"actions"`
}

// Planner turns what intake found into how it will be deployed.
type Planner struct {
	LLM  *core.LLM
	Emit Emit
}

// Plan is the deployment plan for a project. It always returns one: the model
// improves the plan, it does not produce it.
func (p *Planner) Plan(ctx context.Context, spec *Spec) *Plan {
	service := spec.Services[0]
	plan := &Plan{
		ProjectSlug:    stackSlug(spec.Name),
		PrimaryService: service.Name,
		Region:         DefaultRegion,
		Infrastructure: Infrastructure,
		Topology:       Topology,
		Database:       Database,
		Target:         TargetEC2,

		SourcePatches:   []map[string]string{},
		Risks:           []string{},
		Recommendations: []string{},
		RepairActions:   []string{},
		Environment:     map[string]any{},
	}
	plan.Model = p.model()
	applyDetected(plan, service)

	p.Emit.step("planner", StatusRunning, 18, "Planning with "+plan.Model, nil)
	if err := p.refine(ctx, spec, plan); err != nil {
		plan.Risks = append(plan.Risks,
			"Ollama planning was unavailable; deterministic safe defaults were used.")
		plan.Recommendations = append(plan.Recommendations,
			"Sign in to Ollama and re-run analysis to include AI recommendations.")
		p.Emit(Event{Type: EventLog, Stage: "planner", Status: StatusWarning, Percent: 27,
			Message: err.Error()})
	} else {
		plan.ModelUsed = true
		p.Emit(Event{Type: EventPrompt, Stage: "planner", Status: StatusComplete, Percent: 27,
			Message: "AI deployment plan validated"})
	}

	// These two follow from what the project imports, so they are said
	// whether or not a model was reachable.
	if service.HasMongoDB {
		plan.Recommendations = append(plan.Recommendations,
			"Store MONGODB_URI only in AWS Secrets Manager and inject it at runtime.")
	}
	if service.HasBetterAuth {
		plan.Recommendations = append(plan.Recommendations,
			"Use same-origin Better Auth client configuration behind the ALB.")
	}

	p.Emit.step("planner", StatusComplete, 29, "Deployment plan ready", nil)
	return plan
}

func (p *Planner) model() string {
	if p.LLM == nil {
		return ""
	}
	return p.LLM.ModelFor(core.RolePlanner)
}

// refine asks the model for the parts of the plan that are judgement, and
// keeps only what is allowed.
func (p *Planner) refine(ctx context.Context, spec *Spec, plan *Plan) error {
	if p.LLM == nil {
		return errors.New("no model is configured for deployment planning")
	}
	var reply planReply
	err := p.LLM.JSONValid(ctx, core.RolePlanner, planSystem,
		SafeJSON(map[string]any{"project": spec}), &reply,
		func() error { return checkPlanReply(&reply) })
	if err != nil {
		return err
	}

	plan.Risks = sentences(reply.Risks, 20)
	plan.Recommendations = sentences(reply.Recommendations, 20)
	plan.SourcePatches = sourcePatches(reply.SourcePatches)
	plan.GitHubJobs = chosenJobs(reply.Generation.GitHubJobs)
	plan.AWSSizing = map[string]any{"instance_type": instanceType(reply.Generation.AWSSizing.InstanceType)}
	plan.RequiredPatches = allowed(reply.Generation.RequiredPatches, patches, len(patches))
	return nil
}

// checkPlanReply is what the model has to get right before the answer is used.
// It is deliberately short: everything it does not cover is filtered rather
// than rejected, because a plan is worth having even when part of it was
// nonsense.
func checkPlanReply(reply *planReply) error {
	if len(reply.Risks) == 0 && len(reply.Recommendations) == 0 {
		return errors.New("say at least one risk or one recommendation")
	}
	if instanceType(reply.Generation.AWSSizing.InstanceType) == "" {
		return errors.New("aws_sizing.instance_type must be one of " + strings.Join(instances, ", "))
	}
	return nil
}

// RepairBuild asks what to do about a build that failed, and returns only the
// actions that are on the list. The plan records them so the review screen and
// the evidence bundle both show what was done and why.
func (p *Planner) RepairBuild(ctx context.Context, spec *Spec, plan *Plan, buildError string) ([]string, error) {
	if p.LLM == nil {
		return nil, errors.New("no model is configured for build repair")
	}
	p.Emit.step("repair", StatusRunning, 91, "Requesting a bounded Ollama compatibility repair", nil)

	payload := SafeJSON(map[string]any{
		"project":              spec,
		"generation":           plan,
		"redacted_build_error": tail(buildError, 8000),
	})
	var reply repairReply
	err := p.LLM.JSONValid(ctx, core.RolePlanner, repairSystem, payload, &reply, nil)
	if err != nil {
		p.Emit.step("repair", StatusFailed, 93, "Compatibility repair could not be planned: "+err.Error(), nil)
		return nil, err
	}

	actions := allowed(reply.Actions, repairs, 4)
	for _, action := range actions {
		if !contains(plan.RepairActions, action) {
			plan.RepairActions = append(plan.RepairActions, action)
		}
	}

	message := "No safe predefined compatibility repair applies"
	if len(actions) > 0 {
		message = "Compatibility repair selected: " + strings.Join(actions, ", ")
	}
	p.Emit.step("repair", StatusComplete, 93, message, map[string]any{
		"actions": actions,
		"summary": clip(reply.Summary, 500),
	})
	return actions, nil
}

// --- what the model is not asked ---------------------------------------------------------

// applyDetected writes everything intake already knows. It runs before the
// model is called, so the plan is complete and correct even if nothing else
// happens.
func applyDetected(plan *Plan, service Service) {
	plan.ServiceRoot = service.Root
	plan.PackageManager = service.PackageManager
	plan.InstallCommand = service.InstallCommand
	plan.BuildCommand = service.BuildCommand
	plan.StartCommand = service.StartCommand
	plan.Port = service.Port
	plan.HealthPath = service.HealthPath
	if plan.HealthPath == "" {
		plan.HealthPath = DefaultHealthPath
	}

	plan.EnvironmentContract = make([]EnvVar, 0, len(service.Environment))
	for _, item := range service.Environment {
		// The contract carries the name, whether it is a secret and when it is
		// needed — never a value, and never where it was found.
		plan.EnvironmentContract = append(plan.EnvironmentContract, EnvVar{
			Name: item.Name, Secret: item.Secret, Scope: item.Scope,
		})
	}
	plan.RuntimeStrategy = RuntimeStrategy
	plan.GitHubJobs = append([]string{}, jobs...)
	// The smallest x86 instance is the default whatever the settings say: a
	// plan nobody has looked at yet should not be the expensive one.
	plan.AWSSizing = map[string]any{"instance_type": FreeTierX86}
	plan.RequiredPatches = []string{"standalone-output", "health-route"}
}

// --- filtering what came back -------------------------------------------------------------

// chosenJobs keeps the model's selection in pipeline order, and falls back to
// the whole pipeline if it left out a job the deployment cannot do without.
func chosenJobs(requested []string) []string {
	chosen := allowed(requested, jobs, len(jobs))
	for _, required := range jobsRequired {
		if !contains(chosen, required) {
			return append([]string{}, jobs...)
		}
	}
	return chosen
}

// instanceType is the size the model asked for, if it is one that is offered
// and the account is allowed to pay for it. It returns "" for anything else,
// which is what makes the check reject the answer.
func instanceType(requested string) string {
	if !contains(instances, requested) {
		return ""
	}
	if freeTierOnly() && requested != FreeTierX86 && requested != FreeTierARM {
		// A larger instance is a bill the customer did not agree to. The
		// architecture is kept — an ARM choice stays ARM.
		if strings.HasPrefix(requested, "t4g") {
			return FreeTierARM
		}
		return FreeTierX86
	}
	return requested
}

// freeTierOnly is the Studio's setting, and it defaults to on: a deployment
// costs the customer money, so the safe reading of a missing setting is the
// one that does not.
func freeTierOnly() bool {
	switch value := core.LoadSettings()["aws_free_tier"].(type) {
	case bool:
		return value
	case string:
		switch strings.ToLower(strings.TrimSpace(value)) {
		case "0", "false", "no", "off":
			return false
		}
		return true
	case nil:
		return true
	}
	return true
}

// sourcePatches keeps the shape the review screen reads, and nothing else the
// model may have added to each row.
func sourcePatches(rows []map[string]string) []map[string]string {
	out := []map[string]string{}
	for _, row := range rows {
		if len(out) >= 12 {
			break
		}
		path := clip(row["path"], 240)
		if strings.TrimSpace(path) == "" {
			continue
		}
		out = append(out, map[string]string{
			"path":   path,
			"reason": clip(row["reason"], 500),
			"change": clip(row["change"], 500),
		})
	}
	return out
}

// --- small shared helpers ------------------------------------------------------------------

// allowed keeps the items that are on the list, in the list's own order, with
// no repeats.
func allowed(requested, permitted []string, limit int) []string {
	want := map[string]bool{}
	for _, item := range requested {
		want[strings.TrimSpace(item)] = true
	}
	out := []string{}
	for _, item := range permitted {
		if want[item] && len(out) < limit {
			out = append(out, item)
		}
	}
	return out
}

func sentences(values []string, limit int) []string {
	out := []string{}
	for _, value := range values {
		if value = strings.TrimSpace(value); value == "" {
			continue
		}
		if len(out) >= limit {
			break
		}
		out = append(out, clip(value, 500))
	}
	return out
}

func contains(values []string, needle string) bool {
	for _, value := range values {
		if value == needle {
			return true
		}
	}
	return false
}

func clip(value string, limit int) string {
	value = strings.TrimSpace(value)
	if len(value) <= limit {
		return value
	}
	return strings.TrimSpace(string([]rune(value)[:runesFor(value, limit)]))
}

// runesFor is how many runes fit in limit bytes, so clipping a long answer
// cannot cut a character in half.
func runesFor(value string, limit int) int {
	count, size := 0, 0
	for _, r := range value {
		next := size + len(string(r))
		if next > limit {
			break
		}
		size = next
		count++
	}
	return count
}

func tail(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	return value[len(value)-limit:]
}

// stackSlug is the plan's own name for the project. It is stricter than a
// service name because it becomes a CloudFormation stack name.
func stackSlug(value string) string {
	out := strings.Trim(nonSlug.ReplaceAllString(strings.ToLower(value), "-"), "-")
	if len(out) > 32 {
		out = strings.Trim(out[:32], "-")
	}
	if out == "" {
		return "nextjs-app"
	}
	return out
}

// asMap is a plan or a spec as the store holds it: JSON, so one record shape
// serves the file, the API and the Studio without a second definition.
func asMap(value any) map[string]any {
	body, err := json.Marshal(value)
	if err != nil {
		return map[string]any{}
	}
	var out map[string]any
	if json.Unmarshal(body, &out) != nil {
		return map[string]any{}
	}
	return out
}
