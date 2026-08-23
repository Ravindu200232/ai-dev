package deploy

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Every AWS call this agent makes goes through the customer's own `aws` CLI.
//
// That is deliberate. The CLI is already required — the Studio signs the
// customer in with it, the generated workflows run it, and a deployment
// without it cannot work — so using it here means the agent has no second
// credential path, no second retry policy and no second idea of what a profile
// is. It also means every call in this file is a line the customer can paste
// into a terminal to see for themselves what the agent did.
//
// Nothing here writes a credential anywhere. A profile is named; an SSO
// session's keys are passed to one process through its environment and are
// gone when it exits.

const (
	awsTimeout   = 2 * time.Minute
	stackTimeout = 30 * time.Minute
	pollEvery    = 5 * time.Second
)

// AWS is one account, in one region, reached one way.
type AWS struct {
	Profile string
	Region  string
	// Keys is a session from the in-app sign-in, for a machine with no
	// configured profile. It is held for the length of one deployment.
	Keys map[string]string
}

// Identity is who the agent is acting as.
type Identity struct {
	Account string `json:"Account"`
	Arn     string `json:"Arn"`
	UserID  string `json:"UserId"`
}

// Network is an existing VPC to deploy into, when a new one cannot be made.
type Network struct {
	VpcID   string `json:"vpc_id"`
	SubnetA string `json:"subnet_a"`
	SubnetB string `json:"subnet_b"`
}

// Available reports whether the machine can talk to AWS at all.
func (a AWS) Available() bool { return Have("aws") }

// exec runs one AWS command. Output is JSON unless the caller asked otherwise.
func (a AWS) exec(ctx context.Context, timeout time.Duration, args ...string) Output {
	return a.execWith(ctx, timeout, "", args...)
}

// execWith is exec with something on the command's standard input. A value
// passed as an argument is readable by every other process on the machine —
// /proc/<pid>/cmdline, ps — so anything secret goes this way instead.
func (a AWS) execWith(ctx context.Context, timeout time.Duration, stdin string, args ...string) Output {
	full := []string{}
	if a.Region != "" {
		full = append(full, "--region", a.Region)
	}
	if a.Profile != "" && len(a.Keys) == 0 {
		full = append(full, "--profile", a.Profile)
	}
	full = append(full, args...)
	full = append(full, "--output", "json", "--no-cli-pager")

	return Exec(ctx, Command{Name: "aws", Args: full, Timeout: timeout, Env: a.Keys, Stdin: stdin})
}

// call runs one AWS command and decodes what it printed.
func (a AWS) call(ctx context.Context, into any, args ...string) error {
	out := a.exec(ctx, awsTimeout, args...)
	if !out.OK() {
		return awsError(args, out)
	}
	if into == nil || strings.TrimSpace(out.Stdout) == "" {
		return nil
	}
	if err := json.Unmarshal([]byte(out.Stdout), into); err != nil {
		return errors.New("aws " + strings.Join(args[:2], " ") + " did not return JSON: " + err.Error())
	}
	return nil
}

// awsError is a failed call, said the way the console should show it: what was
// asked for, and what AWS said about it — never the whole invocation, which
// would put the account and region in every message.
func awsError(args []string, out Output) error {
	what := strings.Join(args, " ")
	if len(args) > 2 {
		what = strings.Join(args[:2], " ")
	}
	detail := out.Text()
	if out.TimedOut {
		detail = "the call did not finish in time"
	}
	if detail == "" {
		detail = "exit status " + strconv.Itoa(out.Code)
	}
	return errors.New("aws " + what + ": " + clip(detail, 600))
}

// notFound reports whether AWS said the thing does not exist, which is an
// answer rather than a failure in most of what follows.
func awsNotFound(err error) bool {
	if err == nil {
		return false
	}
	text := strings.ToLower(err.Error())
	return strings.Contains(text, "does not exist") ||
		strings.Contains(text, "not found") ||
		strings.Contains(text, "resourcenotfound")
}

// --- who we are ------------------------------------------------------------------------------

// Whoami is the account the deployment will be made in.
func (a AWS) Whoami(ctx context.Context) (Identity, error) {
	var identity Identity
	err := a.call(ctx, &identity, "sts", "get-caller-identity")
	return identity, err
}

// --- the GitHub OIDC provider ------------------------------------------------------------------

// GitHubOIDC is the ARN of an existing GitHub OIDC provider in this account,
// or "" when there is none.
//
// An account may only have one provider per URL, so a second stack that tried
// to create it would fail. But a provider this stack owns must not be passed
// back to it as an existing one either, or CloudFormation would be told to
// adopt something it already manages.
func (a AWS) GitHubOIDC(ctx context.Context, stack string) string {
	var list struct {
		Providers []struct{ Arn string } `json:"OpenIDConnectProviderList"`
	}
	if a.call(ctx, &list, "iam", "list-open-id-connect-providers") != nil {
		return ""
	}
	arn := ""
	for _, provider := range list.Providers {
		var detail struct{ Url string }
		if a.call(ctx, &detail, "iam", "get-open-id-connect-provider",
			"--open-id-connect-provider-arn", provider.Arn) != nil {
			continue
		}
		if detail.Url == "token.actions.githubusercontent.com" {
			arn = provider.Arn
			break
		}
	}
	if arn == "" || stack == "" {
		return arn
	}

	var resources struct {
		Summaries []struct{ ResourceType string } `json:"StackResourceSummaries"`
	}
	if a.call(ctx, &resources, "cloudformation", "list-stack-resources", "--stack-name", stack) != nil {
		return arn
	}
	for _, resource := range resources.Summaries {
		if resource.ResourceType == "AWS::IAM::OIDCProvider" {
			return ""
		}
	}
	return arn
}

// --- the bootstrap stack -------------------------------------------------------------------

// StackChange is one resource CloudFormation says it is about to touch. The
// whole list is shown to the customer before it is executed.
type StackChange struct {
	Action            string `json:"action"`
	LogicalResourceID string `json:"logical_resource_id"`
	ResourceType      string `json:"resource_type"`
	Replacement       string `json:"replacement"`
}

// ApplyStack brings the bootstrap stack to what the template says, and returns
// the stack's outputs. It previews the change first: nothing is executed that
// the run has not emitted for the customer to see.
func (a AWS) ApplyStack(ctx context.Context, emit Emit, template, stack string,
	parameters map[string]string) (map[string]string, error) {
	exists, err := a.settleStack(ctx, stack)
	if err != nil {
		return nil, err
	}

	body, err := json.Marshal(parameterList(parameters))
	if err != nil {
		return nil, err
	}
	changeSet := "deployment-agent-" + strconv.FormatInt(time.Now().UTC().Unix(), 10)
	kind := "CREATE"
	if exists {
		kind = "UPDATE"
	}

	if err := a.call(ctx, nil, "cloudformation", "create-change-set",
		"--stack-name", stack,
		"--change-set-name", changeSet,
		"--change-set-type", kind,
		"--description", "Reviewed Deployment Agent bootstrap change set",
		"--template-body", "file://"+template,
		"--parameters", string(body),
		"--capabilities", "CAPABILITY_NAMED_IAM"); err != nil {
		return nil, err
	}
	emit.step("bootstrap", StatusRunning, 15, "CloudFormation change set created", nil)

	changes, empty, err := a.awaitChangeSet(ctx, stack, changeSet)
	if err != nil {
		return nil, err
	}
	if !empty {
		emit.send(Event{Type: EventChangeSet, Stage: "bootstrap", Status: "preview", Percent: 24,
			Message: "CloudFormation preview contains " + strconv.Itoa(len(changes)) +
				" reviewed resource change(s)",
			Data: map[string]any{"stack": stack, "changes": changes}})

		if err := a.call(ctx, nil, "cloudformation", "execute-change-set",
			"--stack-name", stack, "--change-set-name", changeSet); err != nil {
			return nil, err
		}
		waiter := "stack-create-complete"
		if exists {
			waiter = "stack-update-complete"
		}
		started := time.Now().UTC()
		if out := a.exec(ctx, stackTimeout, "cloudformation", "wait", waiter,
			"--stack-name", stack); !out.OK() {
			return nil, a.stackFailure(ctx, stack, out, started)
		}
	}

	outputs, err := a.StackOutputs(ctx, stack)
	if err != nil {
		return nil, err
	}
	emit.step("bootstrap", StatusComplete, 40, "AWS bootstrap stack is ready", nil)
	return outputs, nil
}

// settleStack reports whether the stack already exists, first getting it out
// of any state it cannot be updated from. A stack left in ROLLBACK_COMPLETE
// has never successfully created anything and can only be deleted.
func (a AWS) settleStack(ctx context.Context, stack string) (bool, error) {
	status, err := a.stackStatus(ctx, stack)
	if awsNotFound(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}

	if status == "ROLLBACK_IN_PROGRESS" {
		status = a.awaitLeaving(ctx, stack, "ROLLBACK_IN_PROGRESS")
	}
	if status != "ROLLBACK_COMPLETE" {
		return status != "", nil
	}
	if err := a.call(ctx, nil, "cloudformation", "delete-stack", "--stack-name", stack); err != nil {
		return false, err
	}
	if out := a.exec(ctx, stackTimeout, "cloudformation", "wait", "stack-delete-complete",
		"--stack-name", stack); !out.OK() {
		return false, awsError([]string{"cloudformation", "wait"}, out)
	}
	return false, nil
}

func (a AWS) stackStatus(ctx context.Context, stack string) (string, error) {
	var described struct {
		Stacks []struct {
			StackStatus string
		}
	}
	if err := a.call(ctx, &described, "cloudformation", "describe-stacks", "--stack-name", stack); err != nil {
		return "", err
	}
	if len(described.Stacks) == 0 {
		return "", nil
	}
	return described.Stacks[0].StackStatus, nil
}

// awaitLeaving waits while a stack is in a state that is going to end by
// itself, and returns whatever it ended up in.
func (a AWS) awaitLeaving(ctx context.Context, stack, status string) string {
	for i := 0; i < 120; i++ {
		if !sleep(ctx, pollEvery) {
			return status
		}
		current, err := a.stackStatus(ctx, stack)
		if err != nil {
			return ""
		}
		if current != status {
			return current
		}
	}
	return status
}

// awaitChangeSet waits for the preview to be ready and returns what it says.
// "no changes" is a success with nothing in it: the account is already as the
// template describes, which is the normal case for a second deployment.
func (a AWS) awaitChangeSet(ctx context.Context, stack, changeSet string) ([]StackChange, bool, error) {
	for {
		var detail struct {
			Status       string
			StatusReason string
			Changes      []struct {
				ResourceChange struct {
					Action            string
					LogicalResourceId string
					ResourceType      string
					Replacement       string
				}
			}
		}
		if err := a.call(ctx, &detail, "cloudformation", "describe-change-set",
			"--stack-name", stack, "--change-set-name", changeSet); err != nil {
			return nil, false, err
		}

		switch detail.Status {
		case "CREATE_COMPLETE":
			changes := make([]StackChange, 0, len(detail.Changes))
			for _, change := range detail.Changes {
				changes = append(changes, StackChange{
					Action:            change.ResourceChange.Action,
					LogicalResourceID: change.ResourceChange.LogicalResourceId,
					ResourceType:      change.ResourceChange.ResourceType,
					Replacement:       change.ResourceChange.Replacement,
				})
			}
			return changes, false, nil
		case "FAILED":
			reason := detail.StatusReason
			if strings.Contains(reason, "didn't contain changes") || strings.Contains(reason, "No updates") {
				return nil, true, nil
			}
			return nil, false, errors.New("CloudFormation change set failed: " + reason)
		}
		if !sleep(ctx, 3*time.Second) {
			return nil, false, ctx.Err()
		}
	}
}

// stackFailure turns a failed wait into the reason CloudFormation gave, which
// is in the stack's events rather than in the command's own output.
//
// Only events from this operation count. A stack carries every failure it has
// ever had, and reporting an old one as the cause of a wait that simply ran out
// of time sends the customer to look at the wrong resource.
func (a AWS) stackFailure(ctx context.Context, stack string, out Output, since time.Time) error {
	if out.TimedOut {
		return errors.New("CloudFormation did not finish within " +
			stackTimeout.String() + "; the stack is still changing in the account")
	}
	var described struct {
		Events []struct {
			Timestamp            string
			LogicalResourceId    string
			ResourceStatus       string
			ResourceStatusReason string
		} `json:"StackEvents"`
	}
	if a.call(ctx, &described, "cloudformation", "describe-stack-events", "--stack-name", stack) == nil {
		scan := described.Events[:0:0]
		for _, event := range described.Events {
			if !after(event.Timestamp, since) {
				// Events come back newest first, so everything from here down
				// belongs to an earlier operation.
				break
			}
			scan = append(scan, event)
		}
		if len(scan) == 0 {
			// Not one event of this operation fell inside the window, which
			// means the two clocks disagree rather than that nothing failed.
			// An old reason is still a reason; no reason at all is not.
			scan = described.Events
		}
		for _, event := range scan {
			if strings.HasSuffix(event.ResourceStatus, "_FAILED") && event.ResourceStatusReason != "" {
				return errors.New("CloudFormation stopped at " + event.LogicalResourceId + ": " +
					clip(event.ResourceStatusReason, 400))
			}
		}
	}
	return awsError([]string{"cloudformation", "wait"}, out)
}

// StackOutputs are what the stack says about what it built.
func (a AWS) StackOutputs(ctx context.Context, stack string) (map[string]string, error) {
	var described struct {
		Stacks []struct {
			Outputs []struct {
				OutputKey   string
				OutputValue string
			}
		}
	}
	if err := a.call(ctx, &described, "cloudformation", "describe-stacks", "--stack-name", stack); err != nil {
		return nil, err
	}
	outputs := map[string]string{}
	if len(described.Stacks) == 0 {
		return outputs, nil
	}
	for _, output := range described.Stacks[0].Outputs {
		outputs[output.OutputKey] = output.OutputValue
	}
	return outputs, nil
}

// DeleteStack takes the whole deployment down.
func (a AWS) DeleteStack(ctx context.Context, stack string) error {
	if err := a.call(ctx, nil, "cloudformation", "delete-stack", "--stack-name", stack); err != nil {
		return err
	}
	if out := a.exec(ctx, stackTimeout, "cloudformation", "wait", "stack-delete-complete",
		"--stack-name", stack); !out.OK() {
		return awsError([]string{"cloudformation", "wait"}, out)
	}
	return nil
}

// parameterList is the CLI's JSON form for stack parameters. Shorthand would
// do for most of them, but the OIDC subjects are comma-separated and would be
// read as several parameters.
func parameterList(parameters map[string]string) []map[string]string {
	out := make([]map[string]string, 0, len(parameters))
	for _, key := range sortedKeys(parameters) {
		out = append(out, map[string]string{"ParameterKey": key, "ParameterValue": parameters[key]})
	}
	return out
}

// --- secrets ---------------------------------------------------------------------------------

// PutSecret writes the running application's whole environment into Secrets
// Manager. It is the only place a deployment ever puts a value.
//
// The bundle never becomes an argument: it holds the customer's database
// password, and an argument is readable by every other process on the machine
// for as long as the command runs.
func (a AWS) PutSecret(ctx context.Context, id string, values map[string]string) error {
	body, err := json.Marshal(values)
	if err != nil {
		return err
	}
	parameter, stdin, cleanup, err := secretParameter(runtime.GOOS, string(body))
	if err != nil {
		return err
	}
	defer cleanup()

	out := a.execWith(ctx, awsTimeout, stdin,
		"secretsmanager", "put-secret-value",
		"--secret-id", id, "--secret-string", parameter)
	if !out.OK() {
		return awsError([]string{"secretsmanager", "put-secret-value"}, out)
	}
	return nil
}

// secretParameter is how the bundle reaches the CLI: what to pass as
// --secret-string, what to put on standard input, and what to clean up after.
//
// On Unix that is standard input, which the CLI reads through /dev/stdin.
// Windows has no /dev/stdin — the CLI would try to open a path that does not
// exist and every deployment from a Windows machine would stop here — so there
// the bundle goes into a file in the user's own temporary directory and is
// deleted the moment the command returns. A file that exists for a second is
// still out of every process list; an argument is not.
func secretParameter(goos, body string) (parameter, stdin string, cleanup func(), err error) {
	if goos != "windows" {
		return "file:///dev/stdin", body, func() {}, nil
	}
	file, err := os.CreateTemp("", "agentforge-secret-*.json")
	if err != nil {
		return "", "", func() {}, err
	}
	path := file.Name()
	cleanup = func() { _ = os.Remove(path) }
	// Windows keeps the temporary directory per-user already; this narrows it
	// further wherever the filesystem honours it.
	_ = file.Chmod(0o600)

	if _, err := file.WriteString(body); err != nil {
		_ = file.Close()
		cleanup()
		return "", "", func() {}, err
	}
	if err := file.Close(); err != nil {
		cleanup()
		return "", "", func() {}, err
	}
	return "file://" + filepath.ToSlash(path), "", cleanup, nil
}

// --- the network ------------------------------------------------------------------------------

// vpcQuota is the default per-region limit. A stack that would create the
// sixth VPC fails, so at that point an existing one has to be reused.
const vpcQuota = 5

// SelectNetwork is nil when the account has room for the dedicated VPC the
// template creates, and an existing VPC to reuse when it does not.
func (a AWS) SelectNetwork(ctx context.Context, emit Emit) (*Network, error) {
	var described struct {
		Vpcs []struct {
			VpcID     string `json:"VpcId"`
			IsDefault bool
			State     string
		}
	}
	if err := a.call(ctx, &described, "ec2", "describe-vpcs"); err != nil {
		return nil, err
	}
	if len(described.Vpcs) < vpcQuota {
		return nil, nil
	}

	candidates := []string{}
	for _, vpc := range described.Vpcs {
		if vpc.IsDefault && vpc.VpcID != "" {
			candidates = append(candidates, vpc.VpcID)
		}
	}
	if len(candidates) == 0 {
		for _, vpc := range described.Vpcs {
			if vpc.State == "available" && vpc.VpcID != "" {
				candidates = append(candidates, vpc.VpcID)
			}
		}
	}

	for _, vpcID := range candidates {
		network, ok := a.publicSubnets(ctx, vpcID)
		if !ok {
			continue
		}
		emit.send(Event{Type: EventLog, Stage: "bootstrap", Status: StatusWarning, Percent: 8,
			Message: "Regional VPC quota is full; reusing two existing public subnets safely",
			Data:    map[string]any{"vpc_id": vpcID, "subnets": []string{network.SubnetA, network.SubnetB}}})
		return &network, nil
	}
	return nil, errors.New(
		"AWS VPC quota is exhausted and no pair of public subnets is available for safe reuse")
}

// publicSubnets are two subnets in different availability zones, which is what
// a load balancer needs and what an instance can be reached through.
func (a AWS) publicSubnets(ctx context.Context, vpcID string) (Network, bool) {
	var described struct {
		Subnets []struct {
			SubnetID            string `json:"SubnetId"`
			AvailabilityZone    string
			MapPublicIpOnLaunch bool
		}
	}
	if a.call(ctx, &described, "ec2", "describe-subnets",
		"--filters", "Name=vpc-id,Values="+vpcID, "Name=state,Values=available") != nil {
		return Network{}, false
	}

	usable := described.Subnets[:0:0]
	for _, subnet := range described.Subnets {
		if subnet.MapPublicIpOnLaunch {
			usable = append(usable, subnet)
		}
	}
	if len(usable) < 2 {
		usable = described.Subnets
	}
	sort.SliceStable(usable, func(i, j int) bool {
		if usable[i].AvailabilityZone != usable[j].AvailabilityZone {
			return usable[i].AvailabilityZone < usable[j].AvailabilityZone
		}
		return usable[i].SubnetID < usable[j].SubnetID
	})

	seen := map[string]bool{}
	chosen := []string{}
	for _, subnet := range usable {
		if seen[subnet.AvailabilityZone] {
			continue
		}
		seen[subnet.AvailabilityZone] = true
		chosen = append(chosen, subnet.SubnetID)
		if len(chosen) == 2 {
			return Network{VpcID: vpcID, SubnetA: chosen[0], SubnetB: chosen[1]}, true
		}
	}
	return Network{}, false
}

// after reports whether a CloudFormation event timestamp is at or past a moment
// this run recorded. An unparseable timestamp counts as recent: it is better to
// report a possibly-old reason than none at all.
func after(timestamp string, since time.Time) bool {
	at, err := time.Parse(time.RFC3339Nano, timestamp)
	if err != nil {
		at, err = time.Parse("2006-01-02T15:04:05.000Z0700", timestamp)
	}
	if err != nil {
		return true
	}
	// The timestamp is AWS's clock and `since` is this machine's, so the slack
	// covers ordinary drift between them as well as the gap between recording
	// the moment and the change set actually executing. An event a minute old
	// is still almost certainly this operation's.
	return !at.Before(since.Add(-time.Minute))
}

// sleep waits, and reports false if the run was cancelled while it waited.
func sleep(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
