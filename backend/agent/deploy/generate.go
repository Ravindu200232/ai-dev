package deploy

import (
	"encoding/json"
	"path/filepath"
	"strconv"
	"strings"
)

// Generation turns the plan into the files that carry it out: the workflows
// GitHub runs, the CloudFormation the account is built from, the release
// script the instance executes, and the report the customer reads before any
// of it happens.
//
// Everything it writes goes into the staging copy and is recorded. Nothing is
// invented here — every value comes from the spec intake read or the plan, and
// the files themselves live in assets/ as the files they are.

// ArtifactVersion is the shape of the manifest. A run made by an older agent
// is readable by its version number rather than by guessing.
const ArtifactVersion = 3

// Build placeholders. `next build` imports every module in the project, so it
// needs a database to connect to and an auth secret to start with. These are
// visible on purpose: they are worthless, and a real value here would be a
// real value in a public build log.
const (
	BuildMongoURI   = "mongodb://127.0.0.1:27017/deployment_agent_build"
	BuildAuthSecret = "build-only-placeholder-real-value-injected-at-runtime"
	BuildAuthURL    = "http://localhost:3000"
)

// Generator writes the deployment into the staging copy.
type Generator struct{ Emit Emit }

// workflowData is what the generated files are filled in from. It is one
// struct for every asset so a value cannot mean one thing in the CI workflow
// and another in the deploy workflow.
type workflowData struct {
	ProjectSlug     string
	Root            string
	Paths           string
	Branch          string
	TriggerBranches string
	PackageManager  string
	Lockfile        string
	Cache           bool
	Preinstall      string
	InstallCommand  string
	BuildCommand    string
	AuditCommand    string
	HealthPath      string
	Port            int
	HasBetterAuth   bool
	Standalone      bool
	InstanceType    string
	SecretNames     string
}

// Generate writes every artifact for one target and returns what it wrote.
func (g *Generator) Generate(spec *Spec, plan *Plan, staged, target string) ([]Artifact, Contract, error) {
	profile := ProfileFor(target)
	service := planned(spec.Services[0], plan)
	w := writer{source: spec.SourcePath, staged: staged}

	serviceRoot := staged
	if service.Root != "" {
		serviceRoot = filepath.Join(staged, filepath.FromSlash(service.Root))
	}
	contract := DiscoverContract(service, serviceRoot)

	plan.Target = profile.Target
	plan.RuntimeStrategy = profile.RuntimeStrategy
	plan.Environment = asMap(contract)

	prefix := ""
	if service.Root != "" {
		prefix = service.Root + "/"
	}
	data := g.data(spec, plan, service, contract, profile)
	records := []Artifact{}

	write := func(relative, content, kind string) {
		record, err := w.write(relative, content, kind)
		if err == nil {
			records = append(records, record)
		}
	}
	render := func(relative, name, kind string) {
		body, err := asset(name, data)
		if err == nil {
			write(relative, body, kind)
		}
	}

	g.Emit.step("runtime", StatusRunning, 32, "Generating runtime assets", nil)
	write(prefix+".env.example", envExample(contract), "environment")
	g.Emit.step("runtime", StatusComplete, 44, "Runtime assets generated", nil)

	patched, changes, problems := applyPatches(w, service, profile.Target)
	records = append(records, patched...)
	plan.SourcePatches = append(plan.SourcePatches, changes...)
	plan.Risks = append(plan.Risks, problems...)

	g.Emit.step("cicd", StatusRunning, 48, "Generating GitHub Actions workflows", nil)
	render(".github/workflows/ci.yml", "ci.yml", "cicd")
	switch profile.Target {
	case TargetEC2:
		render(".github/workflows/deploy.yml", "deploy-ec2.yml", "cicd")
	case TargetECS:
		render(".github/workflows/deploy.yml", "deploy-ecs.yml", "cicd")
	default:
		render(".github/workflows/deploy.yml", "deploy-vercel.yml", "cicd")
	}
	g.Emit.step("cicd", StatusComplete, 57, "CI/CD workflows generated", nil)

	switch profile.Target {
	case TargetEC2:
		g.Emit.step("aws", StatusRunning, 60, "Generating AWS EC2 artifacts", nil)
		render("infra/bootstrap.yml", "bootstrap-ec2.yml", "aws")
		write("infra/parameters.example.json", indented(parametersExample(spec, plan, service)), "aws")
		render("deploy/release.sh", "release.sh", "aws")
		g.Emit.step("aws", StatusComplete, 70, "AWS EC2 artifacts generated", nil)

	case TargetECS:
		g.Emit.step("aws", StatusRunning, 60, "Generating AWS ECS artifacts", nil)
		render("infra/bootstrap.yml", "bootstrap-ecs.yml", "aws")
		write("infra/parameters.example.json", indented(parametersExample(spec, plan, service)), "aws")
		render(prefix+"Dockerfile", "Dockerfile", "aws")
		write(prefix+".dockerignore", assetText("dockerignore"), "aws")
		write("deploy/task-definition.json", indented(taskDefinition(service, plan, contract)), "aws")
		g.Emit.step("aws", StatusComplete, 70, "AWS ECS artifacts generated", nil)

	default:
		g.Emit.step("provider", StatusRunning, 60, "Generating Vercel artifacts", nil)
		write("deploy/vercel-environment.json", indented(vercelEnvironment(contract)), "vercel")
		config := prefix + "vercel.json"
		if w.has(config) {
			// A vercel.json the customer wrote is a decision they made.
			plan.Recommendations = append(plan.Recommendations,
				"Kept your existing "+config+"; the agent did not overwrite it.")
		} else {
			write(config, indented(vercelConfig(service)), "vercel")
		}
		g.Emit.step("provider", StatusComplete, 70, "Vercel artifacts generated", nil)
	}

	readiness := InitialReadiness(records, profile)
	report, err := reportMarkdown(spec, plan, service, readiness, profile)
	if err != nil {
		return nil, contract, err
	}
	write("deployment-report.md", report, "report")
	write("readiness-score.json", indented(readiness), "report")
	write("deployment-manifest.json", indented(manifest(spec, plan, service, contract, profile, records)), "manifest")

	g.Emit.step("generator", StatusComplete, 76,
		"Generated "+strconv.Itoa(len(records))+" reviewed artifacts", nil)
	return records, contract, nil
}

// planned is the service as the plan says it should be run. Discovery stays
// authoritative for anything the plan left empty, so a plan that lost a field
// cannot silently produce a deployment that runs the wrong command.
func planned(service Service, plan *Plan) Service {
	if plan.ServiceRoot != "" {
		service.Root = plan.ServiceRoot
	}
	for _, pair := range []struct {
		into *string
		from string
	}{
		{&service.PackageManager, plan.PackageManager},
		{&service.InstallCommand, plan.InstallCommand},
		{&service.BuildCommand, plan.BuildCommand},
		{&service.StartCommand, plan.StartCommand},
		{&service.HealthPath, plan.HealthPath},
	} {
		if pair.from != "" {
			*pair.into = pair.from
		}
	}
	if plan.Port != 0 {
		service.Port = plan.Port
	}
	if service.HealthPath == "" {
		service.HealthPath = DefaultHealthPath
	}
	return service
}

// data fills in everything the assets read.
func (g *Generator) data(spec *Spec, plan *Plan, service Service, contract Contract, profile Profile) workflowData {
	branch := spec.Repository.Branch
	if branch == "" {
		branch = "main"
	}
	paths := "**"
	root := "."
	if service.Root != "" {
		paths = service.Root + "/**"
		root = service.Root
	}

	lockfile, cache, preinstall, audit := toolchain(service)
	names := []string{}
	for _, entry := range RuntimeSecretEntries(contract) {
		names = append(names, entry.Name)
	}
	allowed, _ := json.Marshal(names)

	build := service.BuildCommand
	if build == "" {
		build = "npm run build"
	}

	return workflowData{
		ProjectSlug:     plan.ProjectSlug,
		Root:            root,
		Paths:           paths,
		Branch:          branch,
		TriggerBranches: triggerBranches(branch),
		PackageManager:  service.PackageManager,
		Lockfile:        lockfile,
		Cache:           cache,
		Preinstall:      preinstall,
		InstallCommand:  service.InstallCommand,
		BuildCommand:    build,
		AuditCommand:    audit,
		HealthPath:      service.HealthPath,
		Port:            service.Port,
		HasBetterAuth:   service.HasBetterAuth,
		Standalone:      profile.Target == TargetEC2,
		InstanceType:    text(plan.AWSSizing["instance_type"]),
		SecretNames:     string(allowed),
	}
}

// toolchain is what each package manager needs from a workflow.
func toolchain(service Service) (lockfile string, cache bool, preinstall, audit string) {
	name := map[string]string{
		"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml",
		"yarn": "yarn.lock", "bun": "bun.lock",
	}[service.PackageManager]
	if name == "" {
		name = "package-lock.json"
	}
	lockfile = name
	if service.Root != "" {
		lockfile = service.Root + "/" + name
	}

	if service.PackageManager == "pnpm" || service.PackageManager == "yarn" {
		// Corepack is how a runner gets the package manager the project pins.
		preinstall = "corepack enable"
	} else {
		preinstall = "# package manager is ready"
	}

	switch service.PackageManager {
	case "bun":
		return lockfile, false, preinstall, "bun audit"
	case "pnpm":
		audit = "pnpm audit --prod --audit-level high"
	case "yarn":
		audit = "yarn audit --groups dependencies --level high"
	default:
		audit = "npm audit --omit=dev --audit-level=high"
	}
	// Caching needs a lockfile to key on, and `npm install` is the one install
	// that does not promise there is one.
	cache = !(service.PackageManager == "npm" && service.InstallCommand == "npm install")
	return lockfile, cache, preinstall, audit
}

// triggerBranches are the branches the deploy workflow fires on: the project's
// own, plus the two names a repository is likely to be renamed to.
func triggerBranches(branch string) string {
	out := []string{branch}
	for _, name := range []string{"main", "master"} {
		if !contains(out, name) {
			out = append(out, name)
		}
	}
	return strings.Join(out, ", ")
}

// --- the files that are data rather than templates -----------------------------------------

// envExample is what a developer copies to .env. It lists the names and never
// a value, and leaves out anything the platform supplies or only development
// reads.
func envExample(contract Contract) string {
	lines := []string{"# Copy to .env for local use. Never commit real values."}
	seen := map[string]bool{}
	for _, entry := range contract.Entries {
		if seen[entry.Name] || entry.Resolution == ResolveProvider || entry.DevelopmentOnly {
			continue
		}
		seen[entry.Name] = true
		suffix := ""
		if !entry.Required {
			suffix = " # optional"
		}
		lines = append(lines, entry.Name+"="+suffix)
	}
	return strings.Join(append(lines, ""), "\n")
}

// RuntimeSecretEntries is every key the runtime secret carries: the ones the
// contract declares, plus the ones the deployment can only fill in once it
// knows the address the app answers on.
func RuntimeSecretEntries(contract Contract) []EnvEntry {
	entries := []EnvEntry{}
	declared := map[string]bool{}
	for _, entry := range ProductionEntries(contract) {
		if entry.Secret && (entry.Scope == "runtime" || entry.Scope == "both") {
			entries = append(entries, entry)
			declared[entry.Name] = true
		}
	}
	for _, name := range DeployerInjected {
		if !declared[name] {
			entries = append(entries, EnvEntry{
				Name: name, Required: false, Secret: true, Scope: "runtime",
				Resolution: ResolveProvider, ValuePresent: true,
				Sources: []string{"deployer:stack-outputs"},
			})
		}
	}
	return entries
}

// Parameters are what the CloudFormation stack is created with. The example
// is written next to the template so a customer deploying by hand has the
// shape in front of them.
type Parameters struct {
	ProjectSlug             string   `json:"ProjectSlug"`
	GitHubSubjects          []string `json:"GitHubSubjects"`
	AppPort                 int      `json:"AppPort"`
	InstanceType            string   `json:"InstanceType"`
	ExistingOidcProviderArn string   `json:"ExistingOidcProviderArn"`
}

func parametersExample(spec *Spec, plan *Plan, service Service) Parameters {
	branch := spec.Repository.Branch
	if branch == "" {
		branch = "main"
	}
	instance := text(plan.AWSSizing["instance_type"])
	if instance == "" {
		instance = FreeTierX86
	}
	return Parameters{
		ProjectSlug: plan.ProjectSlug,
		GitHubSubjects: []string{
			"repo:owner/repository:ref:refs/heads/" + branch,
			"repo:owner@OWNER_ID/repository@REPOSITORY_ID:ref:refs/heads/" + branch,
		},
		AppPort:      service.Port,
		InstanceType: instance,
	}
}

// taskDefinition is the Fargate task. The __PLACEHOLDER__ values are filled in
// by the deploy workflow at release time, when the roles and the image exist.
// TaskDefinition is the Fargate task. The __PLACEHOLDER__ values are filled in
// by the deploy workflow at release time, when the roles and the image exist.
type TaskDefinition struct {
	Family                  string      `json:"family"`
	NetworkMode             string      `json:"networkMode"`
	RequiresCompatibilities []string    `json:"requiresCompatibilities"`
	CPU                     string      `json:"cpu"`
	Memory                  string      `json:"memory"`
	ExecutionRoleArn        string      `json:"executionRoleArn"`
	TaskRoleArn             string      `json:"taskRoleArn"`
	ContainerDefinitions    []Container `json:"containerDefinitions"`
}

// Container is the one container the task runs.
type Container struct {
	Name             string            `json:"name"`
	Image            string            `json:"image"`
	Essential        bool              `json:"essential"`
	PortMappings     []PortMapping     `json:"portMappings"`
	Environment      []NameValue       `json:"environment"`
	Secrets          []SecretRef       `json:"secrets"`
	LogConfiguration *LogConfiguration `json:"logConfiguration"`
}

type PortMapping struct {
	ContainerPort int    `json:"containerPort"`
	Protocol      string `json:"protocol"`
}

type NameValue struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

type SecretRef struct {
	Name      string `json:"name"`
	ValueFrom string `json:"valueFrom"`
}

type LogConfiguration struct {
	LogDriver string            `json:"logDriver"`
	Options   map[string]string `json:"options"`
}

func taskDefinition(service Service, plan *Plan, contract Contract) TaskDefinition {
	port := service.Port
	if port == 0 {
		port = defaultPort
	}
	secrets := []SecretRef{}
	for _, entry := range RuntimeSecretEntries(contract) {
		secrets = append(secrets, SecretRef{
			Name: entry.Name, ValueFrom: "__RUNTIME_SECRET_ARN__:" + entry.Name + "::",
		})
	}
	return TaskDefinition{
		Family:                  plan.ProjectSlug + "-task",
		NetworkMode:             "awsvpc",
		RequiresCompatibilities: []string{"FARGATE"},
		CPU:                     "256",
		Memory:                  "512",
		ExecutionRoleArn:        "__EXECUTION_ROLE_ARN__",
		TaskRoleArn:             "__TASK_ROLE_ARN__",
		ContainerDefinitions: []Container{{
			Name:         plan.ProjectSlug + "-app",
			Image:        "__IMAGE__",
			Essential:    true,
			PortMappings: []PortMapping{{ContainerPort: port, Protocol: "tcp"}},
			Environment: []NameValue{
				{Name: "NODE_ENV", Value: "production"},
				{Name: "PORT", Value: strconv.Itoa(port)},
				{Name: "HOSTNAME", Value: "0.0.0.0"},
			},
			Secrets: secrets,
			LogConfiguration: &LogConfiguration{
				LogDriver: "awslogs",
				Options: map[string]string{
					"awslogs-group":         "/deployment-agent/" + plan.ProjectSlug,
					"awslogs-region":        "__AWS_REGION__",
					"awslogs-stream-prefix": "app",
				},
			},
		}},
	}
}

// VercelConfig is the project file Vercel reads when it builds.
type VercelConfig struct {
	Schema         string `json:"$schema"`
	Framework      string `json:"framework"`
	InstallCommand string `json:"installCommand"`
	BuildCommand   string `json:"buildCommand"`
}

func vercelConfig(service Service) VercelConfig {
	build := service.BuildCommand
	if build == "" {
		build = "npm run build"
	}
	return VercelConfig{
		Schema:         "https://openapi.vercel.sh/vercel.json",
		Framework:      "nextjs",
		InstallCommand: service.InstallCommand,
		BuildCommand:   build,
	}
}

// VercelEnvironment is what the customer has to set on the Vercel project. It
// is written out rather than set for them: this target has no OIDC, so the
// agent holds as little of their account as it can.
type VercelEnvironment struct {
	Variables []VercelVariable `json:"variables"`
}

type VercelVariable struct {
	Name       string   `json:"name"`
	Targets    []string `json:"targets"`
	Type       string   `json:"type"`
	Scope      string   `json:"scope"`
	Resolution string   `json:"resolution"`
}

func vercelEnvironment(contract Contract) VercelEnvironment {
	out := VercelEnvironment{Variables: []VercelVariable{}}
	for _, entry := range ProductionEntries(contract) {
		if entry.Resolution == ResolveProvider || entry.Resolution == ResolveOptional {
			continue
		}
		kind := "plain"
		if entry.Secret {
			kind = "sensitive"
		}
		out.Variables = append(out.Variables, VercelVariable{
			Name:       entry.Name,
			Targets:    []string{"production", "preview"},
			Type:       kind,
			Scope:      entry.Scope,
			Resolution: entry.Resolution,
		})
	}
	return out
}

// Readiness is how ready the deployment is, out of 100. The categories are
// what the score is made of, so a number the customer disagrees with can be
// argued with in terms of what is missing.
type Readiness struct {
	Score      int        `json:"score"`
	Categories Categories `json:"categories"`
	Phase      string     `json:"phase"`
}

// Categories are the six things a deployment is scored on.
type Categories struct {
	Build      int `json:"build"`
	CICD       int `json:"cicd"`
	Provider   int `json:"provider"`
	Security   int `json:"security"`
	Monitoring int `json:"monitoring"`
	API        int `json:"api"`
}

// Total is the score the categories add up to.
func (c Categories) Total() int {
	return c.Build + c.CICD + c.Provider + c.Security + c.Monitoring + c.API
}

// InitialReadiness is how ready the deployment is before anything has run.
// Generating the files earns the two categories they are evidence for; the
// rest is earned by a deployment that actually works.
func InitialReadiness(records []Artifact, profile Profile) Readiness {
	kinds := map[string]bool{}
	for _, record := range records {
		kinds[record.Kind] = true
	}
	categories := Categories{}
	if kinds["cicd"] {
		categories.CICD = 20
	}
	if kinds[profile.ArtifactKind] {
		categories.Provider = 25
	}
	return Readiness{Score: categories.Total(), Categories: categories, Phase: "review"}
}

// Manifest is the record of what the agent generated and what it decided. It
// ships with the deployment, so a person reading the repository later can see
// which files are the agent's and what they were made from.
type Manifest struct {
	Version             int        `json:"version"`
	Project             string     `json:"project"`
	ProjectSlug         string     `json:"project_slug"`
	PrimaryService      string     `json:"primary_service"`
	ServiceRoot         string     `json:"service_root"`
	Model               string     `json:"model"`
	ModelUsed           bool       `json:"model_used"`
	Target              string     `json:"target"`
	EnvironmentContract Contract   `json:"environment_contract"`
	Generation          Generation `json:"generation"`
	Artifacts           []string   `json:"artifacts"`
	AgentOwnedFiles     []string   `json:"agent_owned_files"`
}

// Generation is the plan, as the manifest states it.
type Generation struct {
	ServiceRoot         string    `json:"service_root"`
	PackageManager      string    `json:"package_manager"`
	InstallCommand      string    `json:"install_command"`
	BuildCommand        string    `json:"build_command"`
	StartCommand        string    `json:"start_command"`
	Port                int       `json:"port"`
	HealthPath          string    `json:"health_path"`
	EnvironmentContract []PlanEnv `json:"environment_contract"`
	RuntimeStrategy     string    `json:"runtime_strategy"`
	GitHubJobs          []string  `json:"github_jobs"`
	AWSSizing           Sizing    `json:"aws_sizing"`
	RequiredPatches     []string  `json:"required_patches"`
	RepairActions       []string  `json:"repair_actions,omitempty"`
}

// Sizing is the instance the deployment runs on.
type Sizing struct {
	InstanceType string `json:"instance_type"`
}

func manifest(spec *Spec, plan *Plan, service Service, contract Contract,
	profile Profile, records []Artifact) Manifest {
	owned := make([]string, 0, len(records)+1)
	for _, record := range records {
		owned = append(owned, record.Path)
	}
	owned = append(owned, "deployment-manifest.json")

	return Manifest{
		Version:             ArtifactVersion,
		Project:             spec.Name,
		ProjectSlug:         plan.ProjectSlug,
		PrimaryService:      service.Name,
		ServiceRoot:         service.Root,
		Model:               plan.Model,
		ModelUsed:           plan.ModelUsed,
		Target:              profile.Target,
		EnvironmentContract: contract,
		Generation: Generation{
			ServiceRoot:         plan.ServiceRoot,
			PackageManager:      plan.PackageManager,
			InstallCommand:      plan.InstallCommand,
			BuildCommand:        plan.BuildCommand,
			StartCommand:        plan.StartCommand,
			Port:                plan.Port,
			HealthPath:          plan.HealthPath,
			EnvironmentContract: plan.EnvironmentContract,
			RuntimeStrategy:     plan.RuntimeStrategy,
			GitHubJobs:          plan.GitHubJobs,
			AWSSizing:           Sizing{InstanceType: text(plan.AWSSizing["instance_type"])},
			RequiredPatches:     plan.RequiredPatches,
			RepairActions:       plan.RepairActions,
		},
		Artifacts:       owned,
		AgentOwnedFiles: owned,
	}
}

// indented is how every generated JSON file is written: two spaces, and a
// trailing newline, so a diff of one is readable.
func indented(value any) string {
	body, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return "{}\n"
	}
	return string(body) + "\n"
}

// --- the report ----------------------------------------------------------------------------

// reportData is what the review report is written from.
type reportData struct {
	Project         string
	Service         string
	Root            string
	Version         string
	TargetSummary   string
	DatabaseNote    string
	Score           int
	Environment     string
	Risks           []string
	Recommendations []string
	SecurityNotes   []string
}

// awsSecurityNotes are true of both AWS targets: nothing long-lived is handed
// to GitHub, and the instance is not reachable except through nginx.
var awsSecurityNotes = []string{
	"No cloud access keys or provider tokens are written to GitHub; Actions authenticates through OIDC.",
	"Provider credentials are kept only in the in-memory credential vault.",
	"The instance security group exposes port 80 only; the Next.js process listens on loopback.",
}

// vercelSecurityNotes say the uncomfortable thing plainly. Vercel has no OIDC
// equivalent for deploying, so this target really does put a long-lived
// credential in the repository, and the customer has to know that.
var vercelSecurityNotes = []string{
	"The MongoDB URI is written to Vercel, not to GitHub.",
	"**A Vercel deploy token is stored in this repository's GitHub Actions secrets.** " +
		"Vercel has no OIDC equivalent for deploying, so unlike the AWS target this is a " +
		"long-lived credential: anyone who can push a workflow to this repository can use it. " +
		"Scope it to a team and rotate it if the repository changes hands.",
	"The token is passed to GitHub over stdin and is never written to disk by the agent.",
}

func reportMarkdown(spec *Spec, plan *Plan, service Service,
	readiness Readiness, profile Profile) (string, error) {
	names := []string{}
	for _, item := range spec.Services[0].Environment {
		names = append(names, item.Name)
	}
	environment := strings.Join(names, ", ")
	if environment == "" {
		environment = "None detected"
	}

	root := service.Root
	if root == "" {
		root = "."
	}

	data := reportData{
		Project:         spec.Name,
		Service:         spec.Services[0].Name,
		Root:            root,
		Version:         spec.Services[0].Version,
		Score:           readiness.Score,
		Environment:     environment,
		Risks:           plan.Risks,
		Recommendations: plan.Recommendations,
	}

	switch profile.Target {
	case TargetVercel:
		data.TargetSummary = "Vercel production deployment"
		data.DatabaseNote = "MongoDB Atlas URI set as a Vercel production environment variable"
		data.SecurityNotes = vercelSecurityNotes
	case TargetECS:
		data.TargetSummary = "AWS ECS Fargate behind an application load balancer"
		data.DatabaseNote = "MongoDB Atlas URI written directly to AWS Secrets Manager"
		data.SecurityNotes = awsSecurityNotes
	default:
		instance := text(plan.AWSSizing["instance_type"])
		if instance == "" {
			instance = FreeTierX86
		}
		data.TargetSummary = "AWS EC2 (" + instance + ") behind nginx, released from S3 via SSM"
		data.DatabaseNote = "MongoDB Atlas URI written directly to AWS Secrets Manager"
		data.SecurityNotes = awsSecurityNotes
	}

	if len(data.Risks) == 0 {
		data.Risks = []string{"No model risks reported."}
	}
	if len(data.Recommendations) == 0 {
		data.Recommendations = []string{"Use the generated review checks."}
	}
	return asset("report.md", data)
}
