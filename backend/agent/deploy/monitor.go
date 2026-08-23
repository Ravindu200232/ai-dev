package deploy

import (
	"context"
	"encoding/json"
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// What is actually running, asked of the things that would know.
//
// The snapshot is the only part of the agent that reports rather than acts. It
// asks GitHub what the workflow did, the provider what exists, and the
// deployed application itself whether it answers — and then scores that out of
// 100 so the Studio can say "live" and mean it.
//
// Everything it collects is redacted and then masked: an account number or an
// image digest in a support bundle is nobody's business but the customer's.

// terminalFailure are the workflow conclusions a run does not recover from.
var terminalFailure = map[string]bool{
	"failure": true, "cancelled": true, "timed_out": true,
	"action_required": true, "startup_failure": true,
}

// inFlight are the workflow statuses that mean it is still going.
var inFlight = map[string]bool{
	"queued": true, "in_progress": true, "waiting": true, "pending": true, "requested": true,
}

// accountNumber is a bare twelve-digit AWS account id.
var accountNumber = regexp.MustCompile(`(^|[^\d])(\d{12})($|[^\d])`)

// imageDigest is the long hash in a container image reference.
var imageDigest = regexp.MustCompile(`(sha256:)[a-f0-9]{20,}`)

// Monitor reports on a run that has been deployed.
type Monitor struct {
	Store *Store
	Emit  func(runID string, event Event)
}

func (m *Monitor) emit(runID string) Emit {
	return func(event Event) {
		if m.Emit != nil {
			m.Emit(runID, event)
		}
	}
}

// Snapshot is everything known about the deployment right now. It also moves
// the run's state: this is the only thing watching once the deployer's own
// worker has finished, so a workflow that failed after the agent stopped
// looking is noticed here.
func (m *Monitor) Snapshot(ctx context.Context, runID string) (map[string]any, error) {
	run, err := m.Store.GetRun(runID)
	if err != nil || run == nil {
		return nil, notFound("Run not found")
	}
	repo := object(run.Repo)
	target := TargetOf(run)

	snapshot := map[string]any{
		"captured_at": NowISO(),
		"github":      map[string]any{},
		"aws":         map[string]any{},
		"vercel":      map[string]any{},
		"logs":        []any{},
		"api":         []any{},
		"errors":      []string{},
	}
	problems := []string{}
	note := func(err error) {
		if err != nil {
			problems = append(problems, RedactText(err.Error()))
		}
	}

	repository := text(repo["repository"])
	if repository != "" && Have("gh") {
		snapshot["github"] = m.github(ctx, run, repository, object(repo["push"]))
	}

	switch {
	case target == TargetVercel:
		if text(repo["vercel_project_id"]) != "" {
			note(m.vercel(ctx, run, snapshot))
		}
	case target == TargetECS:
		note(m.ecs(ctx, run, snapshot))
	case text(repo["region"]) != "":
		note(m.ec2(ctx, run, snapshot))
	}

	snapshot["api"] = probeApplication(ctx, text(repo["application_url"]))
	snapshot["workflow"] = m.settleWorkflow(ctx, run, snapshot, object(repo["push"]))
	if latest, _ := m.Store.GetRun(runID); latest != nil {
		run = latest
	}

	readiness := Score(run, snapshot)
	scored := asMap(readiness)
	// The gates are evidence of what the review proved — that the project
	// builds, that its artifacts are sound — and a snapshot does not re-prove
	// any of it. Overwriting them would make a redeploy of a failed run ask
	// for a build validation that already passed.
	if gates := object(run.Readiness)["gates"]; gates != nil {
		scored["gates"] = gates
	}
	snapshot["readiness"] = scored
	snapshot["errors"] = problems

	sanitized := object(mask(Redact(snapshot)))
	if _, err := m.Store.Update(runID, map[string]any{
		"monitor": sanitized, "readiness": scored,
	}); err != nil {
		return nil, err
	}

	m.settleLive(run, readiness, snapshot)
	return sanitized, nil
}

// settleLive is the last transition: a validating deployment whose score is
// high enough and whose own pages answer is live.
func (m *Monitor) settleLive(run *Run, readiness Readiness, snapshot map[string]any) {
	latest, _ := m.Store.GetRun(run.ID)
	if latest == nil || (latest.State != StateValidating && latest.State != StateLive) {
		return
	}
	if readiness.Score < 90 || !allProbesPassed(snapshot["api"]) {
		return
	}
	if latest.State != StateLive {
		m.emit(run.ID).step("validation", StatusComplete, 98,
			"Homepage and health API validation passed", nil)
		// The dashboard capture the Python agent attempted could never
		// succeed — it navigated to a page this server does not serve — so
		// the run says plainly that there is no image rather than pretending.
		m.emit(run.ID).send(Event{Type: EventLog, Stage: "evidence", Status: StatusComplete,
			Percent: 99, Message: "No evidence image was captured; the .zip and .pdf still " +
				"export everything else this run recorded."})
	}
	_, _ = m.Store.Transition(run.ID, StateLive, nil)
}

func allProbesPassed(value any) bool {
	probes, ok := value.([]Probe)
	if !ok || len(probes) == 0 {
		return false
	}
	for _, probe := range probes {
		if !probe.Passed {
			return false
		}
	}
	return true
}

// --- GitHub -----------------------------------------------------------------------------------

func (m *Monitor) github(ctx context.Context, run *Run, repository string, push map[string]any) map[string]any {
	github := GitHub{Emit: m.emit(run.ID), Dir: run.ProjectPath}
	out := github.gh(ctx, "run", "list", "--repo", repository, "--limit", "10", "--json",
		"databaseId,name,displayTitle,status,conclusion,url,headSha,createdAt,updatedAt")
	if !out.OK() {
		return map[string]any{"repository": repository, "runs": []any{}, "error": out.Stderr}
	}

	runs := []map[string]any{}
	if json.Unmarshal([]byte(out.Stdout), &runs) != nil {
		runs = []map[string]any{}
	}
	successful, failed := 0, 0
	for _, item := range runs {
		switch text(item["conclusion"]) {
		case "success":
			successful++
		case "failure":
			failed++
		}
	}
	value := map[string]any{
		"repository": repository, "runs": runs,
		"successful": successful, "failed": failed,
	}

	// A deployment that went out as a pull request is waiting on a person, so
	// the snapshot has to show them where.
	if text(push["mode"]) == "pull_request" && text(push["url"]) != "" {
		pr := github.gh(ctx, "pr", "view", text(push["url"]), "--repo", repository, "--json",
			"state,mergeStateStatus,statusCheckRollup,url,headRefName,baseRefName,mergedAt")
		if pr.OK() {
			detail := map[string]any{}
			if json.Unmarshal([]byte(pr.Stdout), &detail) == nil {
				value["pull_request"] = detail
			}
		} else {
			value["pull_request"] = map[string]any{"url": text(push["url"]), "error": pr.Stderr}
		}
	}
	return value
}

// settleWorkflow finds this deployment's own workflow run among the
// repository's recent ones, and moves the run's state to match what it did.
func (m *Monitor) settleWorkflow(ctx context.Context, run *Run,
	snapshot map[string]any, push map[string]any) map[string]any {
	github := object(snapshot["github"])
	listed, _ := github["runs"].([]map[string]any)

	deploys := []WorkflowRun{}
	raw := []map[string]any{}
	for _, item := range listed {
		if !strings.Contains(strings.ToLower(text(item["name"])), "deploy") {
			continue
		}
		raw = append(raw, item)
		deploys = append(deploys, WorkflowRun{
			Status: text(item["status"]), Conclusion: text(item["conclusion"]),
			URL: text(item["url"]), DisplayTitle: text(item["displayTitle"]),
			CreatedAt: text(item["createdAt"]), HeadSHA: text(item["headSha"]),
		})
	}

	head := text(push["head_sha"])
	current, found := SelectRun(deploys, head, "")
	if !found {
		return map[string]any{}
	}
	index := 0
	for i, item := range deploys {
		if item.URL == current.URL {
			index = i
			break
		}
	}
	detail := raw[index]

	running := inFlight[current.Status]
	switch {
	case run.State == StateCIRunning || run.State == StateDeploying:
		if running {
			_, _ = m.Store.Transition(run.ID, StateDeploying, nil)
		} else if current.Conclusion == "success" {
			_, _ = m.Store.Transition(run.ID, StateValidating, nil)
		}
	}
	if !running && terminalFailure[current.Conclusion] && Active[run.State] {
		_, _ = m.Store.Transition(run.ID, StateFailed, map[string]any{
			"error": "GitHub deployment workflow concluded with " + current.Conclusion + ": " + current.URL,
		})
		m.emit(run.ID).send(Event{Type: EventError, Stage: "deploy", Status: StatusFailed,
			Percent: 100, Message: "GitHub deployment workflow concluded with " + current.Conclusion,
			Data: detail})
	}
	return detail
}

// --- the provider -------------------------------------------------------------------------

// stacks is the bootstrap stack, what it built, and what it last did. The
// events are what a failed deployment is diagnosed from.
func (m *Monitor) stacks(ctx context.Context, aws AWS, slug string) []map[string]any {
	stack := slug + "-bootstrap"
	status, err := aws.stackStatus(ctx, stack)
	if err != nil {
		return []map[string]any{{"name": stack, "status": "NOT_FOUND", "error": RedactText(err.Error())}}
	}
	outputs, _ := aws.StackOutputs(ctx, stack)

	var described struct {
		Events []struct {
			Timestamp            string
			LogicalResourceId    string
			ResourceType         string
			ResourceStatus       string
			ResourceStatusReason string
		} `json:"StackEvents"`
	}
	events := []map[string]any{}
	if err := aws.call(ctx, &described, "cloudformation", "describe-stack-events",
		"--stack-name", stack); err != nil {
		events = append(events, map[string]any{"status": "UNAVAILABLE", "reason": RedactText(err.Error())})
	}
	for _, event := range described.Events {
		if len(events) >= 80 {
			break
		}
		events = append(events, map[string]any{
			"timestamp":           event.Timestamp,
			"logical_resource_id": event.LogicalResourceId,
			"resource_type":       event.ResourceType,
			"status":              event.ResourceStatus,
			"reason":              RedactText(event.ResourceStatusReason),
		})
	}
	return []map[string]any{{
		"name": stack, "status": status, "outputs": outputs, "events": events,
	}}
}

func (m *Monitor) ec2(ctx context.Context, run *Run, snapshot map[string]any) error {
	repo := object(run.Repo)
	aws := AWS{Profile: text(repo["aws_profile"]), Region: text(repo["region"])}
	slug := text(run.Plan["project_slug"])
	instance := text(repo["instance_id"])

	value := map[string]any{
		"region":          text(repo["region"]),
		"instance_id":     instance,
		"artifact_bucket": text(repo["artifact_bucket"]),
		"stacks":          m.stacks(ctx, aws, slug),
		"instances":       []map[string]any{},
		"releases":        []map[string]any{},
	}

	var problem error
	if instance != "" {
		var described struct {
			Reservations []struct {
				Instances []struct {
					InstanceID       string `json:"InstanceId"`
					InstanceType     string
					PublicIPAddress  string `json:"PublicIpAddress"`
					PrivateIPAddress string `json:"PrivateIpAddress"`
					LaunchTime       string
					State            struct{ Name string }
					Placement        struct{ AvailabilityZone string }
				}
			}
		}
		if err := aws.call(ctx, &described, "ec2", "describe-instances",
			"--instance-ids", instance); err != nil {
			problem = err
		}
		instances := []map[string]any{}
		for _, reservation := range described.Reservations {
			for _, item := range reservation.Instances {
				instances = append(instances, map[string]any{
					"instance_id":       item.InstanceID,
					"state":             item.State.Name,
					"instance_type":     item.InstanceType,
					"public_ip":         item.PublicIPAddress,
					"private_ip":        item.PrivateIPAddress,
					"availability_zone": item.Placement.AvailabilityZone,
					"launched_at":       item.LaunchTime,
				})
			}
		}
		value["instances"] = instances

		var invocations struct {
			CommandInvocations []struct {
				CommandId         string
				Status            string
				Comment           string
				RequestedDateTime string
			}
		}
		if aws.call(ctx, &invocations, "ssm", "list-command-invocations",
			"--instance-id", instance, "--max-results", "20") == nil {
			releases := []map[string]any{}
			for _, item := range invocations.CommandInvocations {
				releases = append(releases, map[string]any{
					"command_id":   item.CommandId,
					"status":       item.Status,
					"comment":      item.Comment,
					"requested_at": item.RequestedDateTime,
				})
			}
			// Newest first: the release the deployment just made is the one
			// the customer is looking for.
			sort.SliceStable(releases, func(i, j int) bool {
				return text(releases[i]["requested_at"]) > text(releases[j]["requested_at"])
			})
			value["releases"] = releases
		}
	}

	snapshot["aws"] = value
	m.cloudwatch(ctx, aws, run, snapshot)
	return problem
}

func (m *Monitor) ecs(ctx context.Context, run *Run, snapshot map[string]any) error {
	repo := object(run.Repo)
	aws := AWS{Profile: text(repo["aws_profile"]), Region: text(repo["region"])}
	slug := text(run.Plan["project_slug"])
	cluster, service := text(repo["ecs_cluster"]), text(repo["ecs_service"])

	value := map[string]any{
		"region":            text(repo["region"]),
		"ecr_repository":    text(repo["ecr_repository"]),
		"ecs_cluster":       cluster,
		"ecs_service":       service,
		"load_balancer_dns": text(repo["load_balancer_dns"]),
		"stacks":            m.stacks(ctx, aws, slug),
		"services":          []map[string]any{},
		"tasks":             []map[string]any{},
		"releases":          []map[string]any{},
	}
	if cluster == "" || service == "" {
		snapshot["aws"] = value
		m.cloudwatch(ctx, aws, run, snapshot)
		return nil
	}

	var problem error
	var described struct {
		Services []struct {
			ServiceName  string
			Status       string
			DesiredCount int
			RunningCount int
			PendingCount int
			LaunchType   string
			Deployments  []struct {
				Id                 string
				Status             string
				RolloutState       string
				RolloutStateReason string
				TaskDefinition     string
				RunningCount       int
				CreatedAt          string
			}
		}
	}
	if err := aws.call(ctx, &described, "ecs", "describe-services",
		"--cluster", cluster, "--services", service); err != nil {
		problem = err
	}

	services := []map[string]any{}
	releases := []map[string]any{}
	for _, item := range described.Services {
		primary := struct {
			Id                 string
			Status             string
			RolloutState       string
			RolloutStateReason string
			TaskDefinition     string
			RunningCount       int
			CreatedAt          string
		}{}
		if len(item.Deployments) > 0 {
			primary = item.Deployments[0]
		}
		services = append(services, map[string]any{
			"name": item.ServiceName, "status": item.Status,
			"desired_count": item.DesiredCount, "running_count": item.RunningCount,
			"pending_count": item.PendingCount, "launch_type": item.LaunchType,
			"task_definition": primary.TaskDefinition,
			"image":           taskImage(ctx, aws, primary.TaskDefinition),
			"rollout_state":   primary.RolloutState,
			"rollout_reason":  primary.RolloutStateReason,
		})
		for index, deployment := range item.Deployments {
			if index == 8 {
				break
			}
			releases = append(releases, map[string]any{
				"id": deployment.Id, "status": deployment.Status,
				"rollout_state":   deployment.RolloutState,
				"task_definition": deployment.TaskDefinition,
				"running_count":   deployment.RunningCount,
				"created_at":      deployment.CreatedAt,
			})
		}
	}
	value["services"] = services
	value["releases"] = releases
	value["tasks"] = runningTasks(ctx, aws, cluster, service)

	snapshot["aws"] = value
	m.cloudwatch(ctx, aws, run, snapshot)
	return problem
}

// taskImage is the image the service is actually running, which is how a
// deployment is checked to be the commit that was pushed.
func taskImage(ctx context.Context, aws AWS, taskDefinition string) string {
	if taskDefinition == "" {
		return ""
	}
	var described struct {
		TaskDefinition struct {
			ContainerDefinitions []struct{ Image string }
		}
	}
	if aws.call(ctx, &described, "ecs", "describe-task-definition",
		"--task-definition", taskDefinition) != nil {
		return ""
	}
	if len(described.TaskDefinition.ContainerDefinitions) == 0 {
		return ""
	}
	return described.TaskDefinition.ContainerDefinitions[0].Image
}

func runningTasks(ctx context.Context, aws AWS, cluster, service string) []map[string]any {
	var listed struct{ TaskArns []string }
	if aws.call(ctx, &listed, "ecs", "list-tasks",
		"--cluster", cluster, "--service-name", service) != nil || len(listed.TaskArns) == 0 {
		return []map[string]any{}
	}
	arns := listed.TaskArns
	if len(arns) > 10 {
		arns = arns[:10]
	}

	var described struct {
		Tasks []struct {
			TaskArn       string
			LastStatus    string
			DesiredStatus string
			HealthStatus  string
			CPU           string `json:"cpu"`
			Memory        string `json:"memory"`
			StartedAt     string
		}
	}
	if aws.call(ctx, &described, append([]string{"ecs", "describe-tasks",
		"--cluster", cluster, "--tasks"}, arns...)...) != nil {
		return []map[string]any{}
	}
	tasks := []map[string]any{}
	for _, task := range described.Tasks {
		tasks = append(tasks, map[string]any{
			"task_arn": task.TaskArn, "last_status": task.LastStatus,
			"desired_status": task.DesiredStatus, "health_status": task.HealthStatus,
			"cpu": task.CPU, "memory": task.Memory, "started_at": task.StartedAt,
		})
	}
	return tasks
}

// cloudwatch is the application's own log lines — the ones its code wrote,
// which is what a person actually wants when something is wrong.
func (m *Monitor) cloudwatch(ctx context.Context, aws AWS, run *Run, snapshot map[string]any) {
	group := text(object(run.Repo)["log_group"])
	if group == "" {
		group = "/deployment-agent/" + text(run.Plan["project_slug"])
	}
	var filtered struct {
		Events []struct {
			Timestamp int64
			Message   string
		}
	}
	if err := aws.call(ctx, &filtered, "logs", "filter-log-events",
		"--log-group-name", group, "--limit", "100", "--interleaved"); err != nil {
		if problems, ok := snapshot["errors"].([]string); ok {
			snapshot["errors"] = append(problems, RedactText(err.Error()))
		}
		return
	}

	events := filtered.Events
	if len(events) > 100 {
		events = events[len(events)-100:]
	}
	lines := []map[string]any{}
	for _, event := range events {
		lines = append(lines, map[string]any{
			"timestamp": event.Timestamp, "message": RedactText(event.Message),
		})
	}
	snapshot["logs"] = lines
}

// --- Vercel ------------------------------------------------------------------------------

func (m *Monitor) vercel(ctx context.Context, run *Run, snapshot map[string]any) error {
	repo := object(run.Repo)
	projectID := text(repo["vercel_project_id"])
	token, err := VercelToken("")
	if err != nil {
		return err
	}
	client := Vercel{Token: token, TeamID: text(repo["vercel_team_id"])}

	value := map[string]any{
		"project_id":      projectID,
		"project_name":    text(repo["vercel_project_name"]),
		"org_id":          text(repo["vercel_org_id"]),
		"application_url": text(repo["application_url"]),
		"deployments":     []map[string]any{},
		"domains":         []map[string]any{},
	}

	deployments, err := client.Deployments(ctx, projectID, 20)
	if err != nil {
		snapshot["vercel"] = value
		return err
	}
	rows := []map[string]any{}
	for _, deployment := range deployments {
		address := ""
		if deployment.URL != "" {
			address = "https://" + deployment.URL
		}
		// uid/sha are what the Studio's table reads; id/commit_sha are what
		// the run record has always carried. Both are written so neither a
		// stored snapshot nor the live view has a blank column.
		rows = append(rows, map[string]any{
			"id": deployment.Identifier(), "uid": deployment.Identifier(),
			"ready_state":    readyState(deployment),
			"url":            address,
			"commit_sha":     deployment.Meta.CommitSHA,
			"sha":            deployment.Meta.CommitSHA,
			"commit_message": deployment.Meta.CommitMessage,
			"created_at":     millis(deployment.Created),
		})
	}
	value["deployments"] = rows

	var problem error
	if domains, err := client.Domains(ctx, projectID); err != nil {
		problem = err
	} else {
		listed := []map[string]any{}
		for _, domain := range domains {
			listed = append(listed, map[string]any{
				"name": text(domain["name"]), "verified": domain["verified"] == true,
			})
		}
		value["domains"] = listed
	}
	snapshot["vercel"] = value

	if len(rows) > 0 {
		if id := text(rows[0]["id"]); id != "" {
			if logs, err := client.DeploymentLogs(ctx, id, 100); err == nil {
				snapshot["logs"] = logs
			} else if problem == nil {
				problem = err
			}
		}
	}
	return problem
}

func readyState(deployment VercelDeployment) string {
	if deployment.ReadyState != "" {
		return deployment.ReadyState
	}
	return deployment.State
}

// millis is a Vercel timestamp, which is in milliseconds.
func millis(value int64) string {
	if value <= 0 {
		return ""
	}
	return time.UnixMilli(value).UTC().Format(time.RFC3339)
}

// --- does it answer? ----------------------------------------------------------------------

// Probe is one request made to the deployed application.
type Probe struct {
	Name      string `json:"name"`
	Method    string `json:"method"`
	Path      string `json:"path"`
	Status    int    `json:"status"`
	ElapsedMS int64  `json:"elapsed_ms,omitempty"`
	Passed    bool   `json:"passed"`
	Error     string `json:"error,omitempty"`
}

// probeApplication asks the deployed site the two questions that matter: does
// the homepage load, and does the health route answer. Nothing else in the
// snapshot is evidence that the deployment actually works.
func probeApplication(ctx context.Context, base string) []Probe {
	if base == "" {
		return []Probe{}
	}
	client := &http.Client{Timeout: 12 * time.Second}
	probes := []Probe{}

	for _, page := range []struct{ path, name string }{
		{"/", "Homepage"},
		{DefaultHealthPath, "Health API"},
	} {
		address := strings.TrimRight(base, "/") + page.path
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
		if err != nil {
			probes = append(probes, Probe{Name: page.name, Method: "GET", Path: page.path,
				Passed: false, Error: RedactText(err.Error())})
			continue
		}
		started := time.Now()
		response, err := client.Do(request)
		if err != nil {
			probes = append(probes, Probe{Name: page.path, Method: "GET", Path: page.path,
				Passed: false, Error: RedactText(err.Error())})
			continue
		}
		response.Body.Close()
		probes = append(probes, Probe{
			Name: page.name, Method: "GET", Path: page.path,
			Status:    response.StatusCode,
			ElapsedMS: time.Since(started).Milliseconds(),
			Passed:    response.StatusCode >= 200 && response.StatusCode < 400,
		})
	}
	return probes
}

// --- the score ---------------------------------------------------------------------------

// Score is how ready the deployment is, out of 100. Each category is evidence
// of something: that CI passed, that the provider is running what was pushed,
// that logs are arriving, and that the site answers.
func Score(run *Run, snapshot map[string]any) Readiness {
	review := object(object(run.Readiness)["categories"])

	categories := Categories{
		Build:    number(review["build"]),
		Security: 20,
	}
	if value, set := review["security"]; set {
		categories.Security = number(value)
	}
	if text(object(snapshot["workflow"])["conclusion"]) == "success" {
		categories.CICD = 20
	}
	if providerHealthy(run, snapshot) {
		categories.Provider = 25
	}
	if logs, ok := snapshot["logs"].([]map[string]any); ok && len(logs) > 0 {
		categories.Monitoring = 10
	} else if lines, ok := snapshot["logs"].([]LogLine); ok && len(lines) > 0 {
		categories.Monitoring = 10
	}

	phase := "deploying"
	if allProbesPassed(snapshot["api"]) {
		categories.API = 10
		phase = "live"
	}
	return Readiness{Score: categories.Total(), Categories: categories, Phase: phase}
}

// providerHealthy is the question each target answers differently: is the
// thing that is running the thing that was deployed, and is it up?
func providerHealthy(run *Run, snapshot map[string]any) bool {
	head := text(object(object(run.Repo)["push"])["head_sha"])

	switch TargetOf(run) {
	case TargetVercel:
		deployments, _ := object(snapshot["vercel"])["deployments"].([]map[string]any)
		if len(deployments) == 0 {
			return false
		}
		newest := deployments[0]
		if text(newest["ready_state"]) != "READY" {
			return false
		}
		return head == "" || text(newest["commit_sha"]) == head

	case TargetECS:
		services, _ := object(snapshot["aws"])["services"].([]map[string]any)
		if len(services) == 0 {
			return false
		}
		newest := services[0]
		desired := number(newest["desired_count"])
		if desired == 0 {
			desired = 1
		}
		if number(newest["running_count"]) < desired {
			return false
		}
		if rollout := text(newest["rollout_state"]); rollout != "COMPLETED" && rollout != "" {
			return false
		}
		return head == "" || strings.Contains(text(newest["image"]), head)

	default:
		aws := object(snapshot["aws"])
		instances, _ := aws["instances"].([]map[string]any)
		releases, _ := aws["releases"].([]map[string]any)
		if len(instances) == 0 || len(releases) == 0 {
			return false
		}
		for _, instance := range instances {
			if text(instance["state"]) != "running" {
				return false
			}
		}
		return text(releases[0]["status"]) == "Success"
	}
}

// --- masking -----------------------------------------------------------------------------

// mask removes the identifiers that are not secrets but are nobody else's
// business: the account number, and the digest of an image.
func mask(value any) any {
	switch item := value.(type) {
	case string:
		item = accountNumber.ReplaceAllString(item, "${1}***ACCOUNT***${3}")
		return imageDigest.ReplaceAllString(item, "${1}***masked***")
	case map[string]any:
		out := make(map[string]any, len(item))
		for key, nested := range item {
			out[key] = mask(nested)
		}
		return out
	case []any:
		out := make([]any, 0, len(item))
		for _, nested := range item {
			out = append(out, mask(nested))
		}
		return out
	}
	return value
}

// number reads a count out of decoded JSON, whatever numeric shape it took.
func number(value any) int {
	switch item := value.(type) {
	case int:
		return item
	case int64:
		return int(item)
	case float64:
		return int(item)
	case string:
		if parsed, err := strconv.Atoi(item); err == nil {
			return parsed
		}
	}
	return 0
}
