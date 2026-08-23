package deploy

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
)

// Stopping, undoing and deleting.
//
// These are the operations that have to work when everything else has gone
// wrong, so each one says plainly what it did and — more importantly — what it
// did not. Cancelling does not delete anything. Teardown deletes everything and
// cannot be undone. A customer reading the console should never have to guess
// which of those just happened.

// cancellable are the states a run can be stopped from. A run that is only
// being analysed can be stopped too: it is still doing work.
var cancellable = set(StateBootstrapping, StateCIRunning, StateDeploying, StateValidating,
	StateAnalyzing, StateReviewReady)

// Cancel stops a run that is still in flight, and the GitHub Actions run it
// started if one is live.
func (d *Deployer) Cancel(ctx context.Context, runID string) (map[string]any, error) {
	run, err := d.Store.GetRun(runID)
	if err != nil || run == nil {
		return nil, notFound("Run not found")
	}
	if !cancellable[run.State] {
		return nil, conflict("This deployment is not running (it is " + string(run.State) +
			"), so there is nothing to cancel")
	}

	workflow := d.cancelWorkflow(ctx, run)
	if _, err := d.Store.Transition(runID, StateCancelled, map[string]any{"error": ""}); err != nil {
		return nil, err
	}

	message := "Deployment cancelled"
	if workflow != "" {
		message += "; GitHub Actions run " + workflow + " was stopped too"
	}
	message += ". Cloud resources were not deleted — use Delete for those."
	d.emit(runID).step("cancel", StatusComplete, 100, message, nil)

	return map[string]any{
		"cancelled": true, "state": string(StateCancelled), "workflow_cancelled": workflow,
	}, nil
}

// liveWorkflow are the GitHub Actions statuses worth cancelling.
var liveWorkflow = map[string]bool{
	"queued": true, "in_progress": true, "waiting": true, "requested": true, "pending": true,
}

// cancelWorkflow stops the workflow this deployment started. Failing to is not
// an error: the run is being cancelled either way, and a workflow that finishes
// on its own does no harm.
func (d *Deployer) cancelWorkflow(ctx context.Context, run *Run) string {
	repository := text(object(run.Repo)["repository"])
	if repository == "" || !Have("gh") {
		return ""
	}
	github := GitHub{Emit: d.emit(run.ID), Dir: run.ProjectPath}

	out := github.gh(ctx, "run", "list", "--repo", repository, "--limit", "10",
		"--json", "databaseId,status")
	if !out.OK() {
		return ""
	}
	var runs []struct {
		DatabaseID int64  `json:"databaseId"`
		Status     string `json:"status"`
	}
	if json.Unmarshal([]byte(out.Stdout), &runs) != nil {
		return ""
	}
	for _, workflow := range runs {
		if !liveWorkflow[workflow.Status] {
			continue
		}
		id := strconv.FormatInt(workflow.DatabaseID, 10)
		if cancelled := github.gh(ctx, "run", "cancel", id, "--repo", repository); cancelled.OK() {
			return id
		}
		return ""
	}
	return ""
}

// --- rollback -------------------------------------------------------------------------------

// rollbackScript puts the instance back on the release before the current one.
// It runs on the instance through SSM, so it is written to be safe to re-run
// and to fail loudly rather than leave a half-swapped symlink.
const rollbackScript = `set -euo pipefail
cd /opt/app/releases
current=$(readlink -f /opt/app/current || true)
previous=$(ls -1dt /opt/app/releases/*/ | grep -v "^${current}/$" | head -n 1)
test -n "$previous"
ln -sfn "${previous%/}" /opt/app/current.new
mv -Tf /opt/app/current.new /opt/app/current
systemctl restart nextjs
basename "${previous%/}" > /opt/app/shared/current-sha
echo "rolled back to ${previous%/}"`

// Rollback puts the previous release back.
func (d *Deployer) Rollback(ctx context.Context, runID string) (map[string]any, error) {
	run, err := d.Store.GetRun(runID)
	if err != nil || run == nil || len(run.Repo) == 0 {
		return nil, badRequest("No deployed environment is available for rollback")
	}
	if TargetOf(run) == TargetVercel {
		return d.rollbackVercel(ctx, run)
	}

	instance := text(run.Repo["instance_id"])
	if instance == "" {
		return nil, badRequest("No EC2 instance is recorded for this deployment")
	}
	aws := AWS{Profile: text(run.Repo["aws_profile"]), Region: regionOf(run)}

	commands, err := json.Marshal(strings.Split(rollbackScript, "\n"))
	if err != nil {
		return nil, err
	}
	var sent struct {
		Command struct{ CommandId string }
	}
	if err := aws.call(ctx, &sent, "ssm", "send-command",
		"--instance-ids", instance,
		"--document-name", "AWS-RunShellScript",
		"--comment", "Deployment Agent rollback",
		"--timeout-seconds", "600",
		"--parameters", `{"commands":`+string(commands)+`}`); err != nil {
		return nil, err
	}

	if _, err := d.Store.Transition(runID, StateRolledBack, nil); err != nil {
		return nil, err
	}
	d.emit(runID).step("rollback", StatusComplete, 100,
		"Rollback to the previous release dispatched", nil)
	return map[string]any{"command_id": sent.Command.CommandId, "instance_id": instance}, nil
}

// rollbackVercel promotes the most recent healthy deployment that is not the
// one running now.
func (d *Deployer) rollbackVercel(ctx context.Context, run *Run) (map[string]any, error) {
	projectID, teamID, err := vercelProject(run)
	if err != nil {
		return nil, err
	}
	token, err := VercelToken("")
	if err != nil {
		return nil, err
	}
	client := Vercel{Token: token, TeamID: teamID}

	deployments, err := client.Deployments(ctx, projectID, 20)
	if err != nil {
		return nil, err
	}
	ready := []VercelDeployment{}
	for _, deployment := range deployments {
		if deployment.Ready() {
			ready = append(ready, deployment)
		}
	}
	if len(ready) < 2 {
		return nil, conflict("No previous healthy Vercel deployment is available to roll back to")
	}

	previous := ready[1]
	if err := client.Promote(ctx, projectID, previous.Identifier()); err != nil {
		return nil, err
	}
	if _, err := d.Store.Transition(run.ID, StateRolledBack, nil); err != nil {
		return nil, err
	}
	d.emit(run.ID).step("rollback", StatusComplete, 100,
		"Promoted the previous Vercel deployment", nil)
	return map[string]any{"deployment_id": previous.Identifier(), "url": previous.URL}, nil
}

// --- teardown --------------------------------------------------------------------------------

// Teardown deletes everything this run created. It is the one operation with
// no undo, so a run that is still going has to be stopped first — or the
// customer has to say to do both at once.
func (d *Deployer) Teardown(ctx context.Context, runID string,
	credentials map[string]string, force bool) (map[string]any, error) {
	run, err := d.Store.GetRun(runID)
	if err != nil || run == nil {
		return nil, notFound("Run not found")
	}
	if run.State == StateDestroyed {
		return nil, conflict("This deployment has already been torn down")
	}
	if Active[run.State] {
		if !force {
			return nil, conflict("This deployment is still running. Stop it first with Cancel, " +
				"or delete it anyway — that stops it and removes its resources in one go.")
		}
		if _, err := d.Cancel(ctx, runID); err != nil {
			return nil, err
		}
		if current, _ := d.Store.GetRun(runID); current != nil {
			run = current
		}
		d.emit(runID).step("teardown", StatusRunning, 5,
			"Deployment stopped; deleting what it created", nil)
	}

	slug := text(run.Plan["project_slug"])
	if slug == "" || len(run.Repo) == 0 {
		if _, err := d.Store.Transition(runID, StateDestroyed, map[string]any{"error": ""}); err != nil {
			return nil, err
		}
		d.emit(runID).step("teardown", StatusComplete, 100,
			"No cloud resources were created by this run", nil)
		return map[string]any{"deleted": []string{}, "slug": slug}, nil
	}

	if TargetOf(run) == TargetVercel {
		go d.teardownVercel(ctx, run, slug)
	} else {
		go d.teardownAWS(ctx, run, slug, credentials)
	}
	return map[string]any{"accepted": true, "slug": slug}, nil
}

func (d *Deployer) teardownVercel(ctx context.Context, run *Run, slug string) {
	emit := d.emit(run.ID)
	fail := func(err error) {
		emit.send(Event{Type: EventError, Stage: "teardown", Status: StatusFailed, Percent: 100,
			Message: "Teardown failed; the Vercel project was NOT deleted: " + RedactText(err.Error())})
	}

	projectID, teamID, err := vercelProject(run)
	if err != nil {
		fail(err)
		return
	}
	token, err := VercelToken("")
	if err != nil {
		fail(err)
		return
	}
	emit.step("teardown", StatusRunning, 40, "Deleting the Vercel project "+slug, nil)
	if err := (Vercel{Token: token, TeamID: teamID}).DeleteProject(ctx, projectID); err != nil {
		fail(err)
		return
	}
	if _, err := d.Store.Transition(run.ID, StateDestroyed, map[string]any{"error": ""}); err != nil {
		fail(err)
		return
	}
	emit.step("teardown", StatusComplete, 100,
		"Teardown complete; the Vercel project and its deployments were deleted",
		map[string]any{"deleted": []string{slug}})
}

func (d *Deployer) teardownAWS(ctx context.Context, run *Run, slug string, credentials map[string]string) {
	emit := d.emit(run.ID)
	aws := AWS{Profile: text(run.Repo["aws_profile"]), Region: regionOf(run), Keys: credentials}
	deleted := []string{}

	for index, stack := range []string{slug + "-service", slug + "-bootstrap"} {
		if _, err := aws.stackStatus(ctx, stack); err != nil {
			if awsNotFound(err) {
				continue
			}
			emit.send(Event{Type: EventError, Stage: "teardown", Status: StatusFailed, Percent: 100,
				Message: "Teardown failed; resources were NOT deleted: " + RedactText(err.Error())})
			return
		}
		emit.step("teardown", StatusRunning, 20+index*40, "Deleting stack "+stack, nil)
		d.drain(ctx, aws, run.ID, stack)

		if err := aws.DeleteStack(ctx, stack); err != nil {
			emit.send(Event{Type: EventError, Stage: "teardown", Status: StatusFailed, Percent: 100,
				Message: "Teardown failed; resources were NOT deleted: " + RedactText(err.Error())})
			return
		}
		deleted = append(deleted, stack)
		emit.step("teardown", StatusRunning, 40+index*40, "Deleted "+stack, nil)
	}

	if _, err := d.Store.Transition(run.ID, StateDestroyed, map[string]any{"error": ""}); err != nil {
		emit.send(Event{Type: EventError, Stage: "teardown", Status: StatusFailed, Percent: 100,
			Message: RedactText(err.Error())})
		return
	}
	message := "No stacks remained to delete"
	if len(deleted) > 0 {
		message = "Teardown complete; deleted " + strconv.Itoa(len(deleted)) + " stack(s)"
	}
	emit.step("teardown", StatusComplete, 100, message, map[string]any{"deleted": deleted})
}

// drain empties the resources CloudFormation refuses to delete while they hold
// anything: a bucket with objects in it, a registry with images in it. Failing
// to empty one is a warning — the stack delete will say so itself, and stopping
// here would leave more behind than carrying on.
func (d *Deployer) drain(ctx context.Context, aws AWS, runID, stack string) {
	emit := d.emit(runID)
	var described struct {
		Resources []struct {
			ResourceType       string
			PhysicalResourceId string
		} `json:"StackResources"`
	}
	if aws.call(ctx, &described, "cloudformation", "describe-stack-resources",
		"--stack-name", stack) != nil {
		return
	}

	for _, resource := range described.Resources {
		if resource.PhysicalResourceId == "" {
			continue
		}
		var err error
		switch resource.ResourceType {
		case "AWS::ECR::Repository":
			if err = emptyRegistry(ctx, aws, resource.PhysicalResourceId); err == nil {
				emit.step("teardown", StatusRunning, 30,
					"Emptied ECR repository "+resource.PhysicalResourceId, nil)
			}
		case "AWS::S3::Bucket":
			if err = emptyBucket(ctx, aws, resource.PhysicalResourceId); err == nil {
				emit.step("teardown", StatusRunning, 30,
					"Emptied S3 bucket "+resource.PhysicalResourceId, nil)
			}
		default:
			continue
		}
		if err != nil {
			emit.send(Event{Type: EventLog, Stage: "teardown", Status: StatusWarning, Percent: 30,
				Message: "Could not empty " + resource.ResourceType + " " +
					resource.PhysicalResourceId + ": " + RedactText(err.Error())})
		}
	}
}

func emptyRegistry(ctx context.Context, aws AWS, repository string) error {
	var listed struct {
		ImageIDs []map[string]string `json:"imageIds"`
	}
	if err := aws.call(ctx, &listed, "ecr", "list-images", "--repository-name", repository); err != nil {
		return err
	}
	if len(listed.ImageIDs) == 0 {
		return nil
	}
	body, err := json.Marshal(listed.ImageIDs)
	if err != nil {
		return err
	}
	return aws.call(ctx, nil, "ecr", "batch-delete-image",
		"--repository-name", repository, "--image-ids", string(body))
}

// deleteBatch is the most objects one S3 delete call takes.
const deleteBatch = 1000

func emptyBucket(ctx context.Context, aws AWS, bucket string) error {
	var listed struct {
		Versions []struct {
			Key       string
			VersionId string
		}
		DeleteMarkers []struct {
			Key       string
			VersionId string
		}
	}
	if err := aws.call(ctx, &listed, "s3api", "list-object-versions", "--bucket", bucket); err != nil {
		return err
	}

	objects := []map[string]string{}
	for _, version := range listed.Versions {
		objects = append(objects, map[string]string{"Key": version.Key, "VersionId": version.VersionId})
	}
	for _, marker := range listed.DeleteMarkers {
		objects = append(objects, map[string]string{"Key": marker.Key, "VersionId": marker.VersionId})
	}

	for from := 0; from < len(objects); from += deleteBatch {
		to := from + deleteBatch
		if to > len(objects) {
			to = len(objects)
		}
		body, err := json.Marshal(map[string]any{"Objects": objects[from:to]})
		if err != nil {
			return err
		}
		if err := aws.call(ctx, nil, "s3api", "delete-objects",
			"--bucket", bucket, "--delete", string(body)); err != nil {
			return err
		}
	}
	return nil
}

// --- domains --------------------------------------------------------------------------------

// Domains are the addresses a Vercel deployment answers on.
func (d *Deployer) Domains(ctx context.Context, runID string) ([]map[string]any, error) {
	client, projectID, err := d.vercelClient(runID)
	if err != nil {
		return nil, err
	}
	return client.Domains(ctx, projectID)
}

// AddDomain points a customer's own hostname at the deployment.
func (d *Deployer) AddDomain(ctx context.Context, runID, domain string) (map[string]any, error) {
	domain, err := CheckDomain(domain)
	if err != nil {
		return nil, err
	}
	client, projectID, err := d.vercelClient(runID)
	if err != nil {
		return nil, err
	}
	result, err := client.AddDomain(ctx, projectID, domain)
	if err != nil {
		return nil, err
	}
	d.emit(runID).step("domains", StatusComplete, 100, "Added the domain "+domain, nil)
	return result, nil
}

// RemoveDomain takes one back off.
func (d *Deployer) RemoveDomain(ctx context.Context, runID, domain string) (map[string]any, error) {
	client, projectID, err := d.vercelClient(runID)
	if err != nil {
		return nil, err
	}
	if err := client.RemoveDomain(ctx, projectID, strings.TrimSpace(domain)); err != nil {
		return nil, err
	}
	d.emit(runID).step("domains", StatusComplete, 100, "Removed the domain "+domain, nil)
	return map[string]any{"removed": domain}, nil
}

func (d *Deployer) vercelClient(runID string) (Vercel, string, error) {
	run, err := d.Store.GetRun(runID)
	if err != nil || run == nil {
		return Vercel{}, "", notFound("Run not found")
	}
	projectID, teamID, err := vercelProject(run)
	if err != nil {
		return Vercel{}, "", err
	}
	token, err := VercelToken("")
	if err != nil {
		return Vercel{}, "", err
	}
	return Vercel{Token: token, TeamID: teamID}, projectID, nil
}

// vercelProject is the linked project, and the reasons there might not be one.
func vercelProject(run *Run) (projectID, teamID string, err error) {
	if TargetOf(run) != TargetVercel {
		return "", "", badRequest("This deployment does not target Vercel")
	}
	projectID = text(object(run.Repo)["vercel_project_id"])
	if projectID == "" {
		return "", "", conflict("This run has no linked Vercel project yet; deploy it first")
	}
	return projectID, text(object(run.Repo)["vercel_team_id"]), nil
}

func regionOf(run *Run) string {
	if region := text(object(run.Repo)["region"]); region != "" {
		return region
	}
	return DefaultRegion
}
