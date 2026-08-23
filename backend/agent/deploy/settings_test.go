package deploy

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// settingsFile writes the file the Studio saves, where the agent reads it.
func settingsFile(t *testing.T, body string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	dir := filepath.Join(home, ".agentforge")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "settings.json"), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestTheProductionDatabaseIsNotThisMachines(t *testing.T) {
	cases := []struct {
		name, body, want string
	}{
		{"the one saved for deploying",
			`{"deploy_mongodb_uri":"mongodb+srv://u:p@cluster.mongodb.net/shop",
			  "mongodb_uri":"mongodb://127.0.0.1:27017"}`,
			"mongodb+srv://u:p@cluster.mongodb.net/shop"},

		{"a shared one that is reachable",
			`{"mongodb_uri":"mongodb+srv://u:p@cluster.mongodb.net/shop"}`,
			"mongodb+srv://u:p@cluster.mongodb.net/shop"},

		// The deployed app runs in a cloud. A database on this laptop is not
		// one it can reach, and baking it in produces an app that starts and
		// then fails on its first request.
		{"never a loopback one", `{"mongodb_uri":"mongodb://127.0.0.1:27017/shop"}`, ""},
		{"nor localhost", `{"mongodb_uri":"mongodb://localhost:27017/shop"}`, ""},
		{"nor one with credentials on loopback",
			`{"mongodb_uri":"mongodb://user:pass@127.0.0.1:27017/shop"}`, ""},
		{"nothing at all", `{}`, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			settingsFile(t, c.body)
			if got := ProductionMongoURI(loadSettings()); got != c.want {
				t.Errorf("ProductionMongoURI = %q, want %q", got, c.want)
			}
		})
	}
}

func TestTheSettingsSummaryCarriesNoValues(t *testing.T) {
	settingsFile(t, `{
	  "aws_profile": "deployment-agent",
	  "aws_region": "ap-south-1",
	  "aws_start_url": "https://acme.awsapps.com/start",
	  "aws_sso_region": "us-east-1",
	  "vercel_token": "vercel_live_abcdefghijkl",
	  "deploy_mongodb_uri": "mongodb+srv://user:hunter2@cluster.mongodb.net/shop",
	  "deploy_model": "gemma4:31b-cloud"
	}`)

	got := SettingsSummary()
	if got.AWSProfile != "deployment-agent" || got.AWSRegion != "ap-south-1" {
		t.Errorf("aws = %+v", got)
	}
	if got.AWSStartURL == "" || got.AWSSSORegion == "" {
		t.Errorf("the sign-in details are what the panel re-fills its form from: %+v", got)
	}
	if !got.VercelTokenSet || got.VercelTokenHint != "…ijkl" {
		t.Errorf("vercel = %v %q", got.VercelTokenSet, got.VercelTokenHint)
	}
	if !got.MongoURISet {
		t.Error("the database is set")
	}
	if got.DeployModel != "gemma4:31b-cloud" {
		t.Errorf("model = %q", got.DeployModel)
	}

	body := SafeJSON(got)
	for _, secret := range []string{"hunter2", "vercel_live_abcdefghijkl"} {
		if strings.Contains(body, secret) {
			t.Errorf("the summary carries a value: %s", body)
		}
	}
	if !strings.Contains(got.MongoURIHint, "cluster.mongodb.net") {
		t.Errorf("the hint does not say which database: %q", got.MongoURIHint)
	}
	if strings.Contains(got.MongoURIHint, "hunter2") {
		t.Errorf("the hint carries the password: %q", got.MongoURIHint)
	}
}

func TestRedactURI(t *testing.T) {
	cases := map[string]string{
		"mongodb+srv://u:p@cluster/db": "mongodb+srv://•••@cluster/db",
		"mongodb://host:27017/db":      "mongodb://host:27017/db",
		"":                             "",
	}
	for uri, want := range cases {
		if got := RedactURI(uri); got != want {
			t.Errorf("RedactURI(%q) = %q, want %q", uri, got, want)
		}
	}
}
