package deploy

import (
	"regexp"
	"strings"

	"agentforge/agent/core"
)

// What the customer has told the Studio about deploying.
//
// The Settings panel writes these; the Deploy panel reads them back to decide
// whether it can offer the button at all. Nothing here is ever the value
// itself — a hint is enough to recognise what is saved, and a hint cannot be
// replayed by whoever ends up reading a support bundle.

// Keys the Studio writes into ~/.agentforge/settings.json.
const (
	KeyAWSProfile   = "aws_profile"
	KeyAWSRegion    = "aws_region"
	KeyAWSStartURL  = "aws_start_url"
	KeyAWSSSORegion = "aws_sso_region"
	KeyVercelToken  = "vercel_token"
	KeyDeployModel  = "deploy_model"

	// KeyProductionMongo is the database the deployed app will use. It is a
	// different setting from the one AgentForge uses for itself, which is
	// usually a MongoDB on this machine and unreachable from a cloud.
	KeyProductionMongo = "deploy_mongodb_uri"
	KeyLocalMongo      = "mongodb_uri"
)

// loopback matches a database that only exists on the machine that is running
// the Studio. Deploying one would produce an app that cannot reach its data.
var loopback = regexp.MustCompile(`(?i)(?:@|//)(?:127\.0\.0\.1|localhost|\[::1\]|0\.0\.0\.0)`)

// Settings is what is configured for deploying, and nothing that could be
// replayed.
type Settings struct {
	VercelCLISignedIn bool   `json:"vercel_cli_signed_in"`
	AWSProfile        string `json:"aws_profile"`
	AWSRegion         string `json:"aws_region"`
	AWSStartURL       string `json:"aws_start_url"`
	AWSSSORegion      string `json:"aws_sso_region"`
	VercelTokenSet    bool   `json:"vercel_token_set"`
	VercelTokenHint   string `json:"vercel_token_hint"`
	MongoURISet       bool   `json:"mongodb_uri_set"`
	MongoURIHint      string `json:"mongodb_uri_hint"`
	DeployModel       string `json:"deploy_model"`
}

// loadSettings is the settings file, read fresh. It is a variable so a test
// can point it somewhere else without a temporary home directory.
var loadSettings = core.LoadSettings

// SettingsSummary is what the Studio's deploy and settings panels read.
func SettingsSummary() Settings {
	saved := loadSettings()
	token := setting(saved, KeyVercelToken)
	database := ProductionMongoURI(saved)

	return Settings{
		VercelCLISignedIn: readVercelToken() != "",
		AWSProfile:        setting(saved, KeyAWSProfile),
		AWSRegion:         setting(saved, KeyAWSRegion),
		AWSStartURL:       setting(saved, KeyAWSStartURL),
		AWSSSORegion:      setting(saved, KeyAWSSSORegion),
		VercelTokenSet:    token != "",
		VercelTokenHint:   tail4(token),
		MongoURISet:       database != "",
		MongoURIHint:      RedactURI(database),
		DeployModel:       setting(saved, KeyDeployModel),
	}
}

// ProductionMongoURI is the database a deployment should use: the one saved
// for deploying, or the one AgentForge uses itself if that happens to be
// reachable from outside this machine. A loopback address is neither.
func ProductionMongoURI(saved map[string]any) string {
	if explicit := setting(saved, KeyProductionMongo); explicit != "" {
		return explicit
	}
	if shared := setting(saved, KeyLocalMongo); shared != "" && !loopback.MatchString(shared) {
		return shared
	}
	return ""
}

// RedactURI is a connection string with its credentials taken out, which is
// enough for a person to recognise which one is saved.
func RedactURI(uri string) string {
	at := strings.LastIndex(uri, "@")
	scheme := strings.Index(uri, "://")
	if at < 0 || scheme < 0 || at < scheme {
		return uri
	}
	return uri[:scheme+3] + "•••@" + uri[at+1:]
}

func setting(saved map[string]any, key string) string {
	value, _ := saved[key].(string)
	return strings.TrimSpace(value)
}

func tail4(value string) string {
	if len(value) < 4 {
		return ""
	}
	return "…" + value[len(value)-4:]
}
