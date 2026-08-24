package deploy

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestActionsAndQuotasFollowTheTarget(t *testing.T) {
	if len(ActionsFor(TargetVercel)) != 0 || len(QuotasFor(TargetVercel)) != 0 {
		t.Error("Vercel is not the customer's AWS account")
	}
	ec2 := ActionsFor(TargetEC2)
	ecs := ActionsFor(TargetECS)
	if !contains(ec2, "ssm:SendCommand") || contains(ec2, "ecs:CreateService") {
		t.Errorf("ec2 actions = %v", ec2)
	}
	if !contains(ecs, "ecs:CreateService") || contains(ecs, "ssm:SendCommand") {
		t.Errorf("ecs actions = %v", ecs)
	}
	// Both need to be able to make the stack and read it back.
	for _, shared := range []string{"cloudformation:CreateChangeSet", "sts:GetCallerIdentity"} {
		if !contains(ec2, shared) || !contains(ecs, shared) {
			t.Errorf("%s is missing from one of them", shared)
		}
	}
	if len(QuotasFor(TargetEC2)) != 3 || len(QuotasFor(TargetECS)) != 3 {
		t.Error("both AWS targets consume three quotas")
	}
}

func TestPrincipalOfAnAssumedRole(t *testing.T) {
	cases := map[string]Identity{
		"arn:aws:iam::123456789012:role/deployer": {
			Account: "123456789012",
			Arn:     "arn:aws:sts::123456789012:assumed-role/deployer/session-name",
		},
		"arn:aws:iam::123456789012:user/ravindu": {
			Account: "123456789012",
			Arn:     "arn:aws:iam::123456789012:user/ravindu",
		},
	}
	for want, identity := range cases {
		if got := principalOf(identity); got != want {
			t.Errorf("principalOf(%q) = %q, want %q", identity.Arn, got, want)
		}
	}
}

func TestBootstrapRoleTemplateListsWhatItUses(t *testing.T) {
	template := BootstrapRoleTemplate("arn:aws:iam::123456789012:user/ravindu")
	if !strings.Contains(template, "Default: arn:aws:iam::123456789012:user/ravindu") {
		t.Error("the trusted principal was not filled in")
	}
	for _, action := range []string{"cloudformation:CreateChangeSet", "ssm:SendCommand"} {
		if !strings.Contains(template, "- "+action) {
			t.Errorf("%s is missing from the role", action)
		}
	}
	if strings.Contains(template, "__ACTIONS__") || strings.Contains(template, "__PRINCIPAL__") {
		t.Error("a placeholder survived")
	}
	// No principal is a template the customer fills in themselves.
	if !strings.Contains(BootstrapRoleTemplate(""), "Default: ''") {
		t.Error("an empty principal is an empty default")
	}
}

func TestPersistProfileKeepsTheRestOfTheFile(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home) // the one os.UserHomeDir reads on Windows
	config := filepath.Join(home, ".aws", "config")
	if err := os.MkdirAll(filepath.Dir(config), 0o755); err != nil {
		t.Fatal(err)
	}
	existing := "[profile work]\nregion = eu-west-1\n# a comment nobody should lose\n"
	if err := os.WriteFile(config, []byte(existing), 0o600); err != nil {
		t.Fatal(err)
	}

	name, err := PersistProfile("", "https://acme.awsapps.com/start", "us-east-1",
		"123456789012", "AdministratorAccess", "ap-south-1")
	if err != nil {
		t.Fatal(err)
	}
	if name != "deployment-agent" {
		t.Errorf("name = %q", name)
	}

	body, err := os.ReadFile(config)
	if err != nil {
		t.Fatal(err)
	}
	text := string(body)
	for _, want := range []string{
		"[profile work]", "# a comment nobody should lose",
		"[sso-session deployment-agent]", "sso_start_url = https://acme.awsapps.com/start",
		"[profile deployment-agent]", "sso_account_id = 123456789012",
		"region = ap-south-1",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("%q missing from:\n%s", want, text)
		}
	}

	// Writing it again replaces its own sections rather than repeating them.
	if _, err := PersistProfile("deployment-agent", "https://acme.awsapps.com/start",
		"us-east-1", "999999999999", "ReadOnly", ""); err != nil {
		t.Fatal(err)
	}
	body, _ = os.ReadFile(config)
	text = string(body)
	if strings.Count(text, "[profile deployment-agent]") != 1 {
		t.Errorf("the profile was written twice:\n%s", text)
	}
	if strings.Contains(text, "123456789012") || !strings.Contains(text, "999999999999") {
		t.Errorf("the profile was not updated:\n%s", text)
	}
	if !strings.Contains(text, "region = us-east-1") {
		t.Error("with no deployment region, the sign-in region is used")
	}

	// The customer's own file is only ever read from ~/.aws, never elsewhere.
	info, err := os.Stat(config)
	if err != nil {
		t.Fatal(err)
	}
	// Windows has no POSIX mode bits; Go reports 0666 for anything writable.
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Errorf("mode = %v", info.Mode().Perm())
	}
}

func TestVaultForgets(t *testing.T) {
	vault := NewVault()
	reference := vault.Put(map[string]string{
		"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE", "empty": "",
	})
	values, err := vault.Get(reference)
	if err != nil {
		t.Fatal(err)
	}
	if values["aws_access_key_id"] != "AKIAIOSFODNN7EXAMPLE" {
		t.Errorf("values = %v", sortedKeys(values))
	}
	if _, kept := values["empty"]; kept {
		t.Error("an empty credential is not a credential")
	}
	// What comes back is a copy: a caller cannot reach into the vault.
	values["aws_access_key_id"] = "changed"
	again, _ := vault.Get(reference)
	if again["aws_access_key_id"] != "AKIAIOSFODNN7EXAMPLE" {
		t.Error("the vault handed out its own map")
	}

	vault.Clear(reference)
	if _, err := vault.Get(reference); err == nil {
		t.Error("a cleared reference is gone")
	}
	if _, err := vault.Get("vault_nothere"); err == nil {
		t.Error("an unknown reference is an error, not empty credentials")
	}
}

func TestEnvironmentIsWhatAProcessTakes(t *testing.T) {
	got := Environment(map[string]string{
		"aws_access_key_id": "AKIA", "aws_secret_access_key": "secret",
		"aws_session_token": "token", "region": "ap-south-1", "access_token": "not this one",
	})
	if got["AWS_ACCESS_KEY_ID"] != "AKIA" || got["AWS_REGION"] != "ap-south-1" {
		t.Errorf("environment = %v", got)
	}
	if len(got) != 4 {
		t.Errorf("only what a process reads: %v", sortedKeys(got))
	}
}

func TestSSORefusesAnIncompleteSignIn(t *testing.T) {
	sso := NewSSO(NewVault())
	if _, err := sso.Start(t.Context(), "acme.awsapps.com/start", "us-east-1"); err == nil {
		t.Error("a start URL has to be https")
	}
	if _, err := sso.Start(t.Context(), "https://acme.awsapps.com/start", ""); err == nil {
		t.Error("a region is required")
	}
	if _, err := sso.Poll(t.Context(), "sso_nothere"); err == nil {
		t.Error("an unknown flow is an expired flow")
	}
	if _, err := sso.Accounts(t.Context(), "sso_nothere"); err == nil {
		t.Error("an unknown flow has no accounts")
	}
	if url := sso.StartURL("sso_nothere"); url != "" {
		t.Errorf("start url = %q", url)
	}
	// Forgetting something that was never there is not an error.
	sso.Forget("sso_nothere")
}

func TestOIDCErrorsAreRecognised(t *testing.T) {
	pending := oidcError{Code: "AuthorizationPendingException"}
	if !isOIDC(pending, "AuthorizationPending") {
		t.Error("a pending authorisation was not recognised")
	}
	if isOIDC(pending, "ExpiredToken") {
		t.Error("pending is not expired")
	}
	if isOIDC(nil, "AuthorizationPending") {
		t.Error("no error is not an error")
	}
	if got := (oidcError{Code: "InvalidRequest", Message: "bad"}).Error(); got != "InvalidRequest: bad" {
		t.Errorf("error = %q", got)
	}
}
