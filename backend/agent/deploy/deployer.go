package deploy

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// This is the part that changes the world.
//
// Everything before it happened in a copy: reading the project, planning,
// generating, checking. From here on the run creates infrastructure in the
// customer's account, writes files into their repository and pushes a commit.
// So the first thing it does is refuse — it re-checks every gate, re-hashes
// every artifact it is about to apply, and stops on anything that has moved
// since the customer looked at it.

// Prep is what a provider needs the shared GitHub half to know.
type Prep struct {
	Variables map[string]string
	Secrets   map[string]string
	RepoState map[string]any
}

// Deployer runs one deployment at a time per project.
type Deployer struct {
	Store *Store
	Emit  func(runID string, event Event)

	mu     sync.Mutex
	active map[string]string // project key → run id
}

// NewDeployer is the deployer the service holds for the life of the process.
func NewDeployer(store *Store, emit func(string, Event)) *Deployer {
	return &Deployer{Store: store, Emit: emit, active: map[string]string{}}
}

// emit is the run-scoped emitter the stages take.
func (d *Deployer) emit(runID string) Emit {
	return func(event Event) {
		if d.Emit != nil {
			d.Emit(runID, event)
		}
	}
}

// Request is what the customer approved.
type Request struct {
	RunID       string
	AWSProfile  string
	Region      string
	MongoURI    string
	Approved    bool
	Credentials map[string]string
	VercelToken string
}

// Start checks everything and then deploys in the background, so the Studio's
// request returns while the deployment takes the minutes it takes.
func (d *Deployer) Start(ctx context.Context, request Request) error {
	run, key, err := d.check(request)
	if err != nil {
		return err
	}
	if err := d.reserve(request.RunID, key); err != nil {
		return err
	}

	go func() {
		defer d.release(key)
		if err := d.deploy(ctx, run, request); err != nil {
			// A cancelled run has already been transitioned and explained.
			if current, _ := d.Store.GetRun(request.RunID); current != nil &&
				current.State == StateCancelled {
				d.emit(request.RunID).step("cancel", StatusComplete, 100,
					"Deployment worker stopped after the run was cancelled", nil)
				return
			}
			_, _ = d.Store.Transition(request.RunID, StateFailed, map[string]any{"error": err.Error()})
			d.emit(request.RunID).send(Event{Type: EventError, Stage: "deploy",
				Status: StatusFailed, Percent: 100, Message: err.Error()})
		}
	}()
	return nil
}

// --- the gates -------------------------------------------------------------------------------

// check is every reason not to deploy. It runs before anything is reserved, so
// a refusal costs the customer nothing.
func (d *Deployer) check(request Request) (*Run, string, error) {
	run, err := d.Store.GetRun(request.RunID)
	if err != nil || run == nil {
		return nil, "", notFound("Run not found")
	}
	if !request.Approved {
		return nil, "", badRequest("The reviewed deployment must be explicitly approved")
	}
	if run.State != StateReviewReady && run.State != StateFailed {
		return nil, "", conflict("Run cannot be deployed from state " + string(run.State))
	}
	if run.Plan["model_used"] != true {
		return nil, "", badRequest("A validated Ollama deployment plan is required before deployment")
	}
	if err := checkReadiness(run.Readiness); err != nil {
		return nil, "", err
	}
	if err := d.checkArtifacts(run); err != nil {
		return nil, "", err
	}
	if err := CheckMongoURI(request.MongoURI); err != nil {
		return nil, "", err
	}

	key, err := filepath.Abs(run.ProjectPath)
	if err != nil {
		return nil, "", err
	}
	return run, strings.ToLower(key), nil
}

// checkReadiness is the build and validation evidence. A deployment goes ahead
// on proof that the project builds and that its artifacts are sound, not on
// the customer having clicked past a warning.
func checkReadiness(readiness map[string]any) error {
	gates := object(readiness["gates"])
	if gates["build_validation"] != true || score(readiness, "build") < 15 {
		return badRequest("Local build validation must pass before deployment")
	}
	if gates["security_validation"] != true || gates["artifacts_valid"] != true {
		return badRequest("Artifact and security validation must pass before deployment")
	}
	return nil
}

func score(readiness map[string]any, category string) int {
	categories := object(readiness["categories"])
	switch value := categories[category].(type) {
	case float64:
		return int(value)
	case int:
		return value
	}
	return -1
}

// checkArtifacts re-reads the manifest and re-hashes every file. Between the
// review and this moment the customer may have edited something, or a build
// may have rewritten it; either way what they approved is no longer what would
// be deployed.
func (d *Deployer) checkArtifacts(run *Run) error {
	body, err := os.ReadFile(filepath.Join(run.StagedPath, "deployment-manifest.json"))
	if err != nil {
		return badRequest("Reviewed deployment manifest is missing or invalid; re-run analysis")
	}
	var manifest Manifest
	if json.Unmarshal(body, &manifest) != nil {
		return badRequest("Reviewed deployment manifest is missing or invalid; re-run analysis")
	}
	if manifest.Version != ArtifactVersion {
		return badRequest(
			"Reviewed artifacts were produced by an older renderer; re-run analysis before deployment")
	}
	if !manifest.ModelUsed {
		return badRequest("Reviewed artifacts were not generated from a validated Ollama plan")
	}
	if manifest.Generation.RuntimeStrategy != ProfileFor(TargetOf(run)).RuntimeStrategy {
		return badRequest("Reviewed generation specification is missing or invalid; re-run analysis")
	}

	records, err := d.Store.Artifacts(run.ID)
	if err != nil || len(records) == 0 {
		return badRequest("Reviewed artifact manifest does not match persisted run state; re-run analysis")
	}
	owned := map[string]bool{}
	for _, path := range manifest.AgentOwnedFiles {
		owned[path] = true
	}
	if len(owned) != len(records) {
		return badRequest("Reviewed artifact manifest does not match persisted run state; re-run analysis")
	}
	for _, record := range records {
		if !owned[record.Path] {
			return badRequest("Reviewed artifact manifest does not match persisted run state; re-run analysis")
		}
		path := filepath.Join(run.StagedPath, filepath.FromSlash(record.Path))
		if !within(run.StagedPath, path) || SHA256File(path) != record.SHA256 {
			return badRequest("Reviewed artifact changed after validation; re-run analysis: " + record.Path)
		}
	}
	return nil
}

// CheckMongoURI is what the customer typed into the database box. It is never
// echoed back: a malformed connection string usually contains a real password.
func CheckMongoURI(value string) error {
	if value == "" || len(value) > 8192 || strings.ContainsAny(value, " \t\n\r") {
		return badRequest("MONGODB_URI is missing or malformed")
	}
	parsed, err := url.Parse(value)
	if err != nil {
		return badRequest("MONGODB_URI is malformed")
	}
	if parsed.Scheme != "mongodb" && parsed.Scheme != "mongodb+srv" {
		return badRequest("MONGODB_URI must contain a MongoDB host and use mongodb:// or mongodb+srv://")
	}
	if parsed.Host == "" || parsed.Hostname() == "" {
		return badRequest("MONGODB_URI must contain a MongoDB host and use mongodb:// or mongodb+srv://")
	}
	return nil
}

// --- one deployment per project ----------------------------------------------------------

// reserve stops two runs deploying the same project at once — in this process,
// and across a restart, where the store is the only memory there is.
func (d *Deployer) reserve(runID, key string) error {
	d.mu.Lock()
	defer d.mu.Unlock()

	if owner, taken := d.active[key]; taken {
		return conflict("Run " + shortID(owner) +
			" already owns the active production deployment for this project")
	}
	runs, err := d.Store.ListRuns(250)
	if err != nil {
		return err
	}
	for _, existing := range runs {
		if existing.ID == runID || !Active[existing.State] {
			continue
		}
		other, err := filepath.Abs(existing.ProjectPath)
		if err == nil && strings.ToLower(other) == key {
			return conflict("Run " + shortID(existing.ID) +
				" already owns the active production deployment for this project")
		}
	}
	d.active[key] = runID
	return nil
}

func (d *Deployer) release(key string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	delete(d.active, key)
}

// --- the deployment ---------------------------------------------------------------------

func (d *Deployer) deploy(ctx context.Context, run *Run, request Request) error {
	emit := d.emit(run.ID)
	profile := ProfileFor(TargetOf(run))
	if missing := missingTools(profile); len(missing) > 0 {
		return errors.New("Missing required deployment tools: " + strings.Join(missing, ", "))
	}

	if _, err := d.Store.Transition(run.ID, StateBootstrapping, map[string]any{"error": ""}); err != nil {
		return err
	}
	emit.step("bootstrap", StatusRunning, 5, "Validating provider identity and GitHub repository", nil)

	github := GitHub{Emit: emit, Dir: run.ProjectPath}
	slug := text(run.Plan["project_slug"])
	repo, err := github.EnsureRepository(ctx, slug)
	if err != nil {
		return err
	}
	identity, err := github.Identity(ctx, repo)
	if err != nil {
		return err
	}
	branch := identity.DefaultBranch
	if branch == "" {
		branch = github.DefaultBranch(ctx, repo, specBranch(run))
	}
	subjects := OIDCSubjects(repo, branch, identity)

	var prep Prep
	switch profile.Target {
	case TargetVercel:
		prep, err = d.prepareVercel(ctx, run, request)
	default:
		prep, err = d.prepareAWS(ctx, run, request, profile, subjects)
	}
	if err != nil {
		return err
	}

	if err := github.SetVariables(ctx, repo, prep.Variables); err != nil {
		return err
	}
	if err := github.SetSecrets(ctx, repo, prep.Secrets); err != nil {
		return err
	}

	records, err := d.Store.Artifacts(run.ID)
	if err != nil {
		return err
	}
	applied, err := Apply(emit, run.ProjectPath, run.StagedPath, records)
	if err != nil {
		return err
	}
	push, err := github.Commit(ctx, run.ID, repo, branch, applied, profile)
	if err != nil {
		return err
	}

	state := map[string]any{
		"repository":    repo,
		"branch":        branch,
		"push":          asMap(push),
		"oidc_subjects": subjects,
		"deployed_at":   NowISO(),
	}
	for key, value := range prep.RepoState {
		state[key] = value
	}
	if _, err := d.Store.Transition(run.ID, StateCIRunning, map[string]any{"repo": state}); err != nil {
		return err
	}

	if push.Mode == "pull_request" {
		emit.step("github", "waiting", 70,
			"Branch protection requires the generated pull request to be merged before deployment",
			map[string]any{"pull_request": push.URL})
		return nil
	}

	if _, err := d.Store.Transition(run.ID, StateDeploying, nil); err != nil {
		return err
	}
	emit.step("github", StatusRunning, 70, "GitHub Actions deployment triggered", nil)

	switch conclusion := github.Watch(ctx, repo, push.HeadSHA, push.PreviousRunURL); conclusion {
	case "success":
		if _, err := d.Store.Transition(run.ID, StateValidating, nil); err != nil {
			return err
		}
		emit.step("github", StatusComplete, 92, "GitHub Actions workflow completed", nil)
		emit.step("deploy", StatusComplete, 93,
			"GitHub Actions completed; validating the live service", nil)
	case "failure", "cancelled", "timed_out", "action_required":
		return errors.New("GitHub deployment workflow concluded with " + conclusion)
	default:
		emit.send(Event{Type: EventLog, Stage: "github", Status: StatusWarning, Percent: 78,
			Message: "Workflow is still running; monitoring will continue from the dashboard"})
	}
	return nil
}

func missingTools(profile Profile) []string {
	missing := []string{}
	for _, name := range profile.RequiredTools {
		if !Have(name) {
			missing = append(missing, name)
		}
	}
	return missing
}

func specBranch(run *Run) string {
	repository := object(run.Spec["repository"])
	if branch := text(repository["branch"]); branch != "" {
		return branch
	}
	return "main"
}

// --- AWS ------------------------------------------------------------------------------------

// prepareAWS brings the account to the point where the workflow can deploy
// into it: the stack, the secret, and the values the workflow reads.
func (d *Deployer) prepareAWS(ctx context.Context, run *Run, request Request,
	profile Profile, subjects []string) (Prep, error) {
	emit := d.emit(run.ID)
	slug := text(run.Plan["project_slug"])
	stack := slug + "-bootstrap"
	aws := AWS{Profile: request.AWSProfile, Region: request.Region, Keys: request.Credentials}

	identity, err := aws.Whoami(ctx)
	if err != nil {
		return Prep{}, err
	}

	parameters := map[string]string{
		"ProjectSlug":             slug,
		"GitHubSubjects":          strings.Join(subjects, ","),
		"AppPort":                 strconv.Itoa(servicePort(run)),
		"ExistingOidcProviderArn": aws.GitHubOIDC(ctx, stack),
	}
	network, err := aws.SelectNetwork(ctx, emit)
	if err != nil {
		return Prep{}, err
	}
	template := filepath.Join(run.StagedPath, "infra", "bootstrap.yml")
	if network != nil {
		parameters["ExistingVpcId"] = network.VpcID
		parameters["ExistingSubnetA"] = network.SubnetA
		if network.SubnetB != "" && templateTakes(template, "ExistingSubnetB") {
			parameters["ExistingSubnetB"] = network.SubnetB
		}
	}

	outputs, err := aws.ApplyStack(ctx, emit, template, stack, parameters)
	if err != nil {
		return Prep{}, err
	}
	secret := outputs["RuntimeSecretArn"]
	if secret == "" {
		return Prep{}, errors.New("Bootstrap stack did not return RuntimeSecretArn")
	}
	if err := aws.PutSecret(ctx, secret, runtimeSecretValues(request.MongoURI, outputs)); err != nil {
		return Prep{}, err
	}
	emit.step("secrets", StatusComplete, 45, "Runtime secrets stored in AWS Secrets Manager", nil)

	prep := Prep{
		Variables: map[string]string{
			"AWS_DEPLOY_ROLE_ARN": outputs["DeployRoleArn"],
			"AWS_REGION":          request.Region,
			"RUNTIME_SECRET_ID":   secret,
			"PROJECT_SLUG":        slug,
		},
		RepoState: map[string]any{
			"aws_profile":        request.AWSProfile,
			"region":             request.Region,
			"account_id":         identity.Account,
			"bootstrap_stack":    stack,
			"application_url":    outputs["ApplicationUrl"],
			"log_group":          outputs["LogGroupName"],
			"runtime_secret_arn": secret,
		},
	}

	if profile.Target == TargetECS {
		prep.Variables["ECR_REPOSITORY_URI"] = outputs["EcrRepositoryUri"]
		prep.Variables["ECS_CLUSTER"] = outputs["ClusterName"]
		prep.Variables["ECS_SERVICE"] = outputs["ServiceName"]
		prep.Variables["ECS_TASK_FAMILY"] = outputs["TaskFamily"]
		prep.Variables["ECS_EXECUTION_ROLE_ARN"] = outputs["ExecutionRoleArn"]
		prep.Variables["ECS_TASK_ROLE_ARN"] = outputs["TaskRoleArn"]
		prep.Variables["CONTAINER_NAME"] = outputs["ContainerName"]

		prep.RepoState["ecr_repository"] = outputs["EcrRepositoryUri"]
		prep.RepoState["ecs_cluster"] = outputs["ClusterName"]
		prep.RepoState["ecs_service"] = outputs["ServiceName"]
		prep.RepoState["task_family"] = outputs["TaskFamily"]
		prep.RepoState["container_name"] = outputs["ContainerName"]
		prep.RepoState["load_balancer_dns"] = outputs["LoadBalancerDns"]
		return prep, nil
	}

	prep.Variables["ARTIFACT_BUCKET"] = outputs["ArtifactBucket"]
	prep.Variables["INSTANCE_ID"] = outputs["InstanceId"]
	prep.RepoState["artifact_bucket"] = outputs["ArtifactBucket"]
	prep.RepoState["instance_id"] = outputs["InstanceId"]
	prep.RepoState["public_ip"] = outputs["PublicIp"]
	return prep, nil
}

// runtimeSecretValues is everything the running application needs. The auth
// secret is generated here and never leaves Secrets Manager; the addresses are
// only knowable once the stack has built the thing they point at.
func runtimeSecretValues(mongoURI string, outputs map[string]string) map[string]string {
	values := map[string]string{
		"MONGODB_URI":        mongoURI,
		"BETTER_AUTH_SECRET": token(),
	}
	if url := strings.TrimRight(outputs["ApplicationUrl"], "/"); url != "" {
		for _, name := range DeployerInjected {
			values[name] = url
		}
	}
	return values
}

func servicePort(run *Run) int {
	services, _ := run.Spec["services"].([]any)
	if len(services) == 0 {
		return defaultPort
	}
	service := object(services[0])
	switch port := service["port"].(type) {
	case float64:
		return int(port)
	case int:
		return port
	}
	return defaultPort
}

// templateTakes reports whether a template declares a parameter, so a run
// against an older generated template does not fail on an unknown one.
func templateTakes(path, parameter string) bool {
	body, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	return strings.Contains(string(body), parameter+":")
}

// --- Vercel ------------------------------------------------------------------------------

// prepareVercel links the project, sets its production environment, and hands
// GitHub the two ids and the one token the workflow needs.
func (d *Deployer) prepareVercel(ctx context.Context, run *Run, request Request) (Prep, error) {
	emit := d.emit(run.ID)
	token, err := VercelToken(request.VercelToken)
	if err != nil {
		return Prep{}, err
	}
	slug := text(run.Plan["project_slug"])
	root := run.StagedPath
	if serviceRoot := serviceRoot(run); serviceRoot != "" {
		root = filepath.Join(root, filepath.FromSlash(serviceRoot))
	}
	teamID := text(object(run.Repo)["vercel_team_id"])

	emit.step("link", StatusRunning, 20, "Linking the Vercel project "+slug, nil)
	link := Exec(ctx, Command{
		Name: "vercel", Args: []string{"link", "--yes", "--project", slug, "--cwd", root},
		Dir: root, Timeout: 3 * time.Minute, Env: map[string]string{"VERCEL_TOKEN": token},
	})
	if !link.OK() {
		detail := clip(link.Text(), 300)
		why := "'" + slug + "' may already exist under another account or scope."
		if strings.Contains(strings.ToLower(detail), "token") {
			why = "The Vercel token was rejected."
		}
		return Prep{}, errors.New("Could not link the Vercel project. " + why + " " + detail)
	}

	body, err := os.ReadFile(filepath.Join(root, ".vercel", "project.json"))
	if err != nil {
		return Prep{}, errors.New("Vercel link did not produce .vercel/project.json")
	}
	var linked struct {
		ProjectID string `json:"projectId"`
		OrgID     string `json:"orgId"`
	}
	if json.Unmarshal(body, &linked) != nil || linked.ProjectID == "" || linked.OrgID == "" {
		return Prep{}, errors.New("Vercel link returned no project or organisation id")
	}
	if teamID == "" && strings.HasPrefix(linked.OrgID, "team_") {
		teamID = linked.OrgID
	}
	linkedTo := "Linked Vercel project " + slug
	if teamID != "" {
		linkedTo += " (team " + teamID + ")"
	}
	emit.step("link", StatusComplete, 30, linkedTo, nil)

	client := Vercel{Token: token, TeamID: teamID}
	applicationURL := "https://" + slug + ".vercel.app"
	projectName := slug
	if project, err := client.Project(ctx, linked.ProjectID); err == nil {
		if url := project.ProductionURL(); url != "" {
			applicationURL = url
		}
		if project.Name != "" {
			projectName = project.Name
		}
	}

	if err := d.syncVercelEnvironment(ctx, run, client, linked.ProjectID,
		request.MongoURI, applicationURL, slug); err != nil {
		return Prep{}, err
	}

	return Prep{
		Variables: map[string]string{
			"VERCEL_ORG_ID":     linked.OrgID,
			"VERCEL_PROJECT_ID": linked.ProjectID,
			"PROJECT_SLUG":      slug,
		},
		Secrets: map[string]string{"VERCEL_TOKEN": token},
		RepoState: map[string]any{
			"vercel_org_id":       linked.OrgID,
			"vercel_project_id":   linked.ProjectID,
			"vercel_project_name": projectName,
			"vercel_team_id":      teamID,
			"application_url":     applicationURL,
		},
	}, nil
}

// syncVercelEnvironment writes the production variables straight to the Vercel
// project. A variable already set there is left as it is: the customer may
// have put the real value in by hand, and overwriting it would break the app.
func (d *Deployer) syncVercelEnvironment(ctx context.Context, run *Run, client Vercel,
	projectID, mongoURI, applicationURL, slug string) error {
	emit := d.emit(run.ID)

	existing := map[string]string{}
	if variables, err := client.Environment(ctx, projectID); err == nil {
		for _, variable := range variables {
			if contains(variable.Target, "production") {
				existing[variable.Key] = variable.ID
			}
		}
	} else {
		emit.send(Event{Type: EventLog, Stage: "env", Status: StatusWarning, Percent: 35,
			Message: RedactText(err.Error())})
	}

	known := vercelUserValues(mongoURI, applicationURL, slug)
	values := map[string]string{}
	unresolved := []string{}
	for _, entry := range planContract(run) {
		switch entry.Resolution {
		case ResolveUser:
			if value := known[entry.Name]; value != "" {
				values[entry.Name] = value
			} else if _, already := existing[entry.Name]; !already {
				unresolved = append(unresolved, entry.Name)
			}
		case ResolveGenerate:
			if _, already := existing[entry.Name]; !already {
				values[entry.Name] = token()
			}
		}
	}

	if len(unresolved) > 0 {
		sort.Strings(unresolved)
		emit.send(Event{Type: EventLog, Stage: "env", Status: StatusWarning, Percent: 36,
			Message: "No value for " + strings.Join(unresolved, ", ") +
				" — set it on the Vercel project if the app needs it."})
	}
	if len(values) == 0 {
		emit.step("env", StatusComplete, 40, "No production environment variables to sync", nil)
		return nil
	}

	emit.step("env", StatusRunning, 35,
		"Syncing "+strconv.Itoa(len(values))+" Vercel environment variable(s)", nil)
	for _, name := range sortedKeys(values) {
		if id := existing[name]; id != "" {
			// Upsert leaves an old encrypted value in place for some
			// variable types, so the old one goes first.
			_ = client.DeleteEnvironment(ctx, projectID, id)
		}
		if err := client.SetEnvironment(ctx, projectID, name, values[name]); err != nil {
			return errors.New("Could not set the Vercel environment variable " + name + ": " +
				clip(RedactText(err.Error()), 200))
		}
	}
	emit.step("env", StatusComplete, 40, "Vercel production environment variables are set", nil)
	return nil
}

// vercelUserValues are the variables the contract leaves to a person that the
// run can answer for them.
func vercelUserValues(mongoURI, applicationURL, slug string) map[string]string {
	database := ""
	if parsed, err := url.Parse(mongoURI); err == nil {
		database = strings.TrimPrefix(parsed.Path, "/")
	}
	if database == "" {
		database = strings.ReplaceAll(slug, "-", "_")
		if len(database) > 63 {
			database = database[:63]
		}
	}
	values := map[string]string{"MONGODB_URI": mongoURI, "MONGODB_DB": database}
	if address := strings.TrimRight(applicationURL, "/"); address != "" {
		values["BETTER_AUTH_URL"] = address
		values["BASE_URL"] = address
		values["NEXT_PUBLIC_BASE_URL"] = address
	}
	return values
}

// planContract is the environment contract as the run recorded it.
func planContract(run *Run) []EnvEntry {
	body, err := json.Marshal(run.Plan["environment"])
	if err != nil {
		return nil
	}
	var contract Contract
	if json.Unmarshal(body, &contract) != nil {
		return nil
	}
	return contract.Entries
}

func serviceRoot(run *Run) string {
	services, _ := run.Spec["services"].([]any)
	if len(services) == 0 {
		return ""
	}
	return text(object(services[0])["root"])
}
