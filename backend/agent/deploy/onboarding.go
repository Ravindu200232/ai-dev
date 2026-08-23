package deploy

import (
	"context"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Before a deployment is attempted, the account is asked whether it could
// succeed: does it have room for what the stack creates, and is this identity
// allowed to create it?
//
// Both questions are worth asking because their failures are the worst kind —
// they happen halfway through, with resources already made, and the message
// AWS gives back names one API call rather than the thing the customer needs
// to change.

// Quota is one service limit the deployment consumes.
type Quota struct {
	Key         string `json:"key"`
	Label       string `json:"label"`
	ServiceCode string `json:"service_code"`
	QuotaCode   string `json:"quota_code"`
	Needed      int    `json:"needed"`
	Limit       *int   `json:"limit"`
	Used        *int   `json:"used"`
	Status      string `json:"status"`
	Message     string `json:"message"`
}

// ec2Quotas are what a single-instance deployment needs room for.
var ec2Quotas = []Quota{
	{Key: "vpc", Label: "VPCs per Region", ServiceCode: "vpc", QuotaCode: "L-F678F1CE", Needed: 1},
	{Key: "eip", Label: "Elastic IPs per Region", ServiceCode: "ec2", QuotaCode: "L-0263D0A3", Needed: 1},
	{Key: "vcpu", Label: "Running On-Demand Standard instances (vCPU)",
		ServiceCode: "ec2", QuotaCode: "L-1216C47A", Needed: 2},
}

// ecsQuotas are the same for the container deployment.
var ecsQuotas = []Quota{
	{Key: "vpc", Label: "VPCs per Region", ServiceCode: "vpc", QuotaCode: "L-F678F1CE", Needed: 1},
	{Key: "fargate_vcpu", Label: "Fargate On-Demand vCPU",
		ServiceCode: "fargate", QuotaCode: "L-3032A538", Needed: 1},
	{Key: "alb", Label: "Application Load Balancers per Region",
		ServiceCode: "elasticloadbalancing", QuotaCode: "L-53DA6B97", Needed: 1},
}

// sharedActions are what both AWS targets need permission for.
var sharedActions = []string{
	"sts:GetCallerIdentity",
	"cloudformation:CreateChangeSet", "cloudformation:ExecuteChangeSet",
	"cloudformation:DescribeChangeSet", "cloudformation:DescribeStacks",
	"cloudformation:DescribeStackEvents", "cloudformation:DeleteStack",
	"ec2:DescribeVpcs", "ec2:DescribeSubnets", "ec2:CreateVpc", "ec2:CreateSubnet",
	"ec2:CreateInternetGateway", "ec2:CreateSecurityGroup",
	"ec2:AuthorizeSecurityGroupIngress", "ec2:CreateTags",
	"iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:PassRole",
	"iam:CreateOpenIDConnectProvider", "iam:ListOpenIDConnectProviders",
	"secretsmanager:CreateSecret", "secretsmanager:PutSecretValue",
	"logs:CreateLogGroup", "logs:PutRetentionPolicy", "logs:FilterLogEvents",
}

// ec2Actions are what the instance deployment needs on top of the shared ones.
var ec2Actions = append(append([]string{}, sharedActions...),
	"ec2:DescribeInstances", "ec2:AllocateAddress", "ec2:AssociateAddress", "ec2:RunInstances",
	"iam:CreateInstanceProfile", "iam:AddRoleToInstanceProfile",
	"s3:CreateBucket", "s3:PutEncryptionConfiguration", "s3:PutBucketVersioning",
	"s3:PutBucketPublicAccessBlock", "s3:PutLifecycleConfiguration",
	"ssm:GetParameters", "ssm:SendCommand", "ssm:GetCommandInvocation",
	"ssm:ListCommandInvocations",
)

// ecsActions are the container equivalents.
var ecsActions = append(append([]string{}, sharedActions...),
	"ecr:CreateRepository", "ecr:DescribeRepositories", "ecr:GetAuthorizationToken",
	"ecr:PutLifecyclePolicy", "ecr:ListImages", "ecr:BatchDeleteImage", "ecr:DeleteRepository",
	"ecs:CreateCluster", "ecs:CreateService", "ecs:RegisterTaskDefinition", "ecs:UpdateService",
	"ecs:DescribeServices", "ecs:DescribeClusters", "ecs:DeleteService", "ecs:DeleteCluster",
	"elasticloadbalancing:CreateLoadBalancer", "elasticloadbalancing:CreateTargetGroup",
	"elasticloadbalancing:CreateListener", "elasticloadbalancing:DescribeLoadBalancers",
	"elasticloadbalancing:DeleteLoadBalancer",
)

// ActionsFor are the permissions a preflight should check for a target.
func ActionsFor(target string) []string {
	switch target {
	case TargetECS:
		return ecsActions
	case TargetVercel:
		// Vercel is not the customer's AWS account, so there is nothing here
		// to have permission for.
		return nil
	}
	return ec2Actions
}

// QuotasFor are the limits a target consumes.
func QuotasFor(target string) []Quota {
	switch target {
	case TargetECS:
		return append([]Quota{}, ecsQuotas...)
	case TargetVercel:
		return nil
	}
	return append([]Quota{}, ec2Quotas...)
}

// --- quotas ---------------------------------------------------------------------------------

// CheckQuotas compares what the deployment needs against what the account has
// left. A quota that cannot be read is reported as unknown rather than as a
// failure: some identities cannot see their own limits.
func (a AWS) CheckQuotas(ctx context.Context, target string) []Quota {
	checks := QuotasFor(target)
	if len(checks) == 0 {
		return []Quota{}
	}
	used := a.quotaUsage(ctx)

	out := make([]Quota, 0, len(checks))
	for _, check := range checks {
		check.Status = "unknown"
		if value, known := used[check.Key]; known {
			count := value
			check.Used = &count
		}

		var quota struct {
			Quota struct{ Value float64 }
		}
		if err := a.call(ctx, &quota, "service-quotas", "get-service-quota",
			"--service-code", check.ServiceCode, "--quota-code", check.QuotaCode); err != nil {
			check.Message = RedactText(err.Error())
			out = append(out, check)
			continue
		}
		limit := int(quota.Quota.Value)
		check.Limit = &limit

		switch {
		case limit < check.Needed:
			check.Status = GateFailed
			check.Message = "Quota is " + strconv.Itoa(limit) + "; the deployment needs at least " +
				strconv.Itoa(check.Needed) + "."
		case check.Used != nil && limit-*check.Used < check.Needed:
			check.Status = GateFailed
			check.Message = strconv.Itoa(*check.Used) + " of " + strconv.Itoa(limit) +
				" already in use; no headroom for this deployment."
		default:
			check.Status = GatePassed
		}
		out = append(out, check)
	}
	return out
}

// quotaUsage is what the account is already using, where that is countable.
func (a AWS) quotaUsage(ctx context.Context) map[string]int {
	used := map[string]int{}
	var vpcs struct{ Vpcs []struct{} }
	if a.call(ctx, &vpcs, "ec2", "describe-vpcs") == nil {
		used["vpc"] = len(vpcs.Vpcs)
	}
	var addresses struct{ Addresses []struct{} }
	if a.call(ctx, &addresses, "ec2", "describe-addresses") == nil {
		used["eip"] = len(addresses.Addresses)
	}
	return used
}

// RequestQuota asks AWS for more of something. The answer is a case number,
// not an increase: these take days.
func (a AWS) RequestQuota(ctx context.Context, serviceCode, quotaCode string,
	desired float64) (map[string]any, error) {
	var requested struct {
		RequestedQuota struct {
			Id           string
			Status       string
			DesiredValue float64
			QuotaCode    string
		}
	}
	if err := a.call(ctx, &requested, "service-quotas", "request-service-quota-increase",
		"--service-code", serviceCode, "--quota-code", quotaCode,
		"--desired-value", strconv.FormatFloat(desired, 'f', -1, 64)); err != nil {
		return nil, err
	}
	return map[string]any{
		"id":         requested.RequestedQuota.Id,
		"status":     requested.RequestedQuota.Status,
		"desired":    requested.RequestedQuota.DesiredValue,
		"quota_code": requested.RequestedQuota.QuotaCode,
	}, nil
}

// --- permissions -----------------------------------------------------------------------------

// Permissions is what this identity may and may not do.
type Permissions struct {
	Status    string   `json:"status"`
	Principal string   `json:"principal"`
	Allowed   []string `json:"allowed"`
	Denied    []string `json:"denied"`
	Message   string   `json:"message"`
}

// simulateBatch is how many actions AWS will evaluate in one call.
const simulateBatch = 25

// CheckPermissions asks IAM to evaluate each action this deployment performs
// against the identity that would perform it — without performing any of them.
//
// An identity that cannot run the simulation is not a failure: many can
// deploy perfectly well without being allowed to introspect themselves, and
// the deployment itself will say what is missing if anything is.
func (a AWS) CheckPermissions(ctx context.Context, target string) Permissions {
	actions := ActionsFor(target)
	if len(actions) == 0 {
		return Permissions{Status: GatePassed}
	}

	identity, err := a.Whoami(ctx)
	if err != nil {
		return Permissions{Status: "unknown", Allowed: []string{}, Denied: []string{},
			Message: RedactText("Permission simulation is unavailable for this identity; " +
				"deployment will surface any missing permission directly.")}
	}
	source := principalOf(identity)

	allowed, denied := []string{}, []string{}
	for from := 0; from < len(actions); from += simulateBatch {
		to := from + simulateBatch
		if to > len(actions) {
			to = len(actions)
		}
		var simulated struct {
			EvaluationResults []struct {
				EvalActionName string
				EvalDecision   string
			}
		}
		args := append([]string{"iam", "simulate-principal-policy",
			"--policy-source-arn", source, "--action-names"}, actions[from:to]...)
		if err := a.call(ctx, &simulated, args...); err != nil {
			return Permissions{Status: "unknown", Principal: source,
				Allowed: []string{}, Denied: []string{},
				Message: RedactText("Permission simulation is unavailable for this identity; " +
					"deployment will surface any missing permission directly.")}
		}
		for _, result := range simulated.EvaluationResults {
			if result.EvalDecision == "allowed" {
				allowed = append(allowed, result.EvalActionName)
			} else {
				denied = append(denied, result.EvalActionName)
			}
		}
	}

	sort.Strings(allowed)
	sort.Strings(denied)
	permissions := Permissions{Status: GatePassed, Principal: source,
		Allowed: allowed, Denied: denied}
	if len(denied) > 0 {
		permissions.Status = GateFailed
		permissions.Message = strconv.Itoa(len(denied)) +
			" required action(s) are denied for this identity."
	}
	return permissions
}

// principalOf is the ARN a policy simulation is run against. An assumed role
// arrives as a session ARN, which IAM will not simulate; the role behind it
// will.
func principalOf(identity Identity) string {
	const marker = ":assumed-role/"
	at := strings.Index(identity.Arn, marker)
	if at < 0 {
		return identity.Arn
	}
	role, _, _ := strings.Cut(identity.Arn[at+len(marker):], "/")
	return "arn:aws:iam::" + identity.Account + ":role/" + role
}

// --- the one-time permission stack ---------------------------------------------------------

// BootstrapRoleTemplate is a CloudFormation template the customer can apply by
// hand to give the agent a role with exactly the permissions it uses — for an
// account where handing over a broad identity is not acceptable.
func BootstrapRoleTemplate(principal string) string {
	lines := make([]string, 0, len(ec2Actions))
	actions := append([]string{}, ec2Actions...)
	sort.Strings(actions)
	for _, action := range actions {
		lines = append(lines, strings.Repeat(" ", 26)+"- "+action)
	}
	trusted := strings.TrimSpace(principal)
	if trusted == "" {
		trusted = "''"
	}
	body := assetText("bootstrap-role.yml")
	body = strings.ReplaceAll(body, "__PRINCIPAL__", trusted)
	return strings.ReplaceAll(body, "__ACTIONS__", strings.Join(lines, "\n"))
}

// --- the profile ------------------------------------------------------------------------------

// PersistProfile writes an sso-session profile to ~/.aws/config, so the
// customer's own aws CLI can use the sign-in they just did here.
//
// The file is edited rather than rewritten: it is the customer's, and it
// probably has other profiles in it.
func PersistProfile(name, startURL, region, accountID, roleName, deploymentRegion string) (string, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		name = "deployment-agent"
	}
	path := filepath.Join(home(), ".aws", "config")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return "", err
	}
	existing, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return "", err
	}

	if deploymentRegion == "" {
		deploymentRegion = region
	}
	sections := map[string][]string{
		"sso-session " + name: {
			"sso_start_url = " + startURL,
			"sso_region = " + region,
			"sso_registration_scopes = " + ssoScope,
		},
		"profile " + name: {
			"sso_session = " + name,
			"sso_account_id = " + accountID,
			"sso_role_name = " + roleName,
			"region = " + deploymentRegion,
		},
	}
	updated := writeSections(string(existing), sections)
	if err := os.WriteFile(path, []byte(updated), 0o600); err != nil {
		return "", err
	}
	return name, nil
}

// writeSections replaces the named sections of an INI file and leaves every
// other line exactly as it was, including comments and spacing.
func writeSections(body string, sections map[string][]string) string {
	out := []string{}
	skipping := false

	for _, line := range strings.Split(strings.ReplaceAll(body, "\r\n", "\n"), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			name := strings.TrimSpace(trimmed[1 : len(trimmed)-1])
			_, replacing := sections[name]
			skipping = replacing
		}
		if !skipping {
			out = append(out, line)
		}
	}

	// Trailing blank lines would pile up every time this is written.
	for len(out) > 0 && strings.TrimSpace(out[len(out)-1]) == "" {
		out = out[:len(out)-1]
	}
	for _, name := range sortedKeys(sections) {
		if len(out) > 0 {
			out = append(out, "")
		}
		out = append(out, "["+name+"]")
		out = append(out, sections[name]...)
	}
	return strings.Join(out, "\n") + "\n"
}

// --- what the machine has --------------------------------------------------------------------

// Onboarding is what the Studio's account panel shows: which tools are
// installed, which AWS profiles work, and whether GitHub is signed in.
func Onboarding(ctx context.Context) map[string]any {
	status := ToolStatus(ctx)
	profiles := []string{}
	identities := map[string]any{}

	if installed(status, "aws") {
		if out := Exec(ctx, Command{Name: "aws", Args: []string{"configure", "list-profiles"},
			Timeout: 10 * time.Second}); out.OK() {
			for _, line := range out.Lines() {
				if name := strings.TrimSpace(line); name != "" {
					profiles = append(profiles, name)
				}
			}
		}
		for index, profile := range profiles {
			if index == 20 {
				break
			}
			if identity := profileIdentity(ctx, profile); identity != nil {
				identities[profile] = identity
			}
		}
	}

	github, account := false, ""
	if installed(status, "gh") {
		github = Exec(ctx, Command{Name: "gh", Args: []string{"auth", "status"},
			Timeout: 15 * time.Second}).OK()
		if github {
			if out := Exec(ctx, Command{Name: "gh", Args: []string{"api", "user", "--jq", ".login"},
				Timeout: 15 * time.Second}); out.OK() {
				account = strings.TrimSpace(out.Stdout)
			}
		}
	}

	return map[string]any{
		"tools":                status,
		"aws_profiles":         profiles,
		"aws_authenticated":    len(identities) > 0,
		"aws_identities":       identities,
		"github_authenticated": github,
		"github_account":       account,
		"cloud_notice": "Selected source context is sent to Ollama Cloud for deployment " +
			"planning. Credentials and detected secret values are always redacted.",
	}
}

// profileIdentity is who a configured profile is, and whether the account it
// belongs to can actually be deployed into.
func profileIdentity(ctx context.Context, profile string) map[string]any {
	region := DefaultRegion
	if out := Exec(ctx, Command{Name: "aws",
		Args: []string{"configure", "get", "region", "--profile", profile}, Timeout: 10 * time.Second}); out.OK() {
		if configured := strings.TrimSpace(out.Stdout); configured != "" {
			region = configured
		}
	}
	aws := AWS{Profile: profile, Region: region}

	identity, err := aws.Whoami(ctx)
	if err != nil {
		return nil
	}

	// One real call, because an account that has never been activated
	// authenticates perfectly well and then refuses everything.
	ready, problem := true, ""
	if err := aws.call(ctx, nil, "cloudformation", "list-stacks", "--max-items", "1"); err != nil {
		ready = false
		problem = serviceProblem(err.Error())
	}
	return map[string]any{
		"account": identity.Account, "arn": identity.Arn, "user_id": identity.UserID,
		"region": region, "service_ready": ready, "service_error": problem,
	}
}

func serviceProblem(text string) string {
	lowered := strings.ToLower(text)
	switch {
	case strings.Contains(lowered, "optinrequired"), strings.Contains(lowered, "not subscribed"),
		strings.Contains(lowered, "needs a subscription"):
		return "AWS account services are not activated (OptInRequired)"
	case strings.Contains(lowered, "expired"):
		return "AWS login session expired"
	case strings.Contains(lowered, "accessdenied"), strings.Contains(lowered, "not authorized"):
		return "CloudFormation access is denied for this identity"
	}
	return "CloudFormation preflight failed"
}

func installed(status map[string]any, name string) bool {
	return object(status[name])["installed"] == true
}
