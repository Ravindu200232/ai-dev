package deploy

import "strings"

// A target is where a deployment goes, and it decides more than a name: which
// tools have to be installed, which generated files have to exist before the
// run may proceed, whether GitHub needs permission to assume an AWS role, and
// what the commit that carries it all says.
//
// Everything that differs between targets is in this one table, so adding a
// provider is a row here plus the files that generate for it — not a search
// for every `if target ==` in the package.

// Profile is one target's facts.
type Profile struct {
	Target            string
	Label             string
	RequiredTools     []string
	RequiredArtifacts []string
	ArtifactKind      string
	RuntimeStrategy   string
	NeedsOIDC         bool
	SupportsTeardown  bool
	CommitSubject     string
	PullRequestTitle  string
}

// Profiles is every target the agent can deploy to.
var Profiles = map[string]Profile{
	TargetEC2: {
		Target:        TargetEC2,
		Label:         "AWS EC2",
		RequiredTools: []string{"git", "gh", "aws"},
		RequiredArtifacts: []string{
			".github/workflows/ci.yml",
			".github/workflows/deploy.yml",
			"infra/bootstrap.yml",
			"deploy/release.sh",
		},
		ArtifactKind:     "aws",
		RuntimeStrategy:  "nextjs-standalone",
		NeedsOIDC:        true,
		SupportsTeardown: true,
		CommitSubject:    "chore(deploy): add generated AWS deployment for {run}",
		PullRequestTitle: "Generated AWS deployment configuration",
	},
	TargetECS: {
		Target:        TargetECS,
		Label:         "AWS ECS Fargate",
		RequiredTools: []string{"git", "gh", "aws"},
		RequiredArtifacts: []string{
			".github/workflows/ci.yml",
			".github/workflows/deploy.yml",
			"infra/bootstrap.yml",
			"Dockerfile",
			"deploy/task-definition.json",
		},
		ArtifactKind:     "aws",
		RuntimeStrategy:  "nextjs-docker-standalone",
		NeedsOIDC:        true,
		SupportsTeardown: true,
		CommitSubject:    "chore(deploy): add generated AWS ECS deployment for {run}",
		PullRequestTitle: "Generated AWS ECS deployment configuration",
	},
	TargetVercel: {
		Target:        TargetVercel,
		Label:         "Vercel",
		RequiredTools: []string{"git", "gh", "vercel"},
		RequiredArtifacts: []string{
			".github/workflows/ci.yml",
			".github/workflows/deploy.yml",
			"deploy/vercel-environment.json",
		},
		ArtifactKind:     "vercel",
		RuntimeStrategy:  "vercel-managed",
		NeedsOIDC:        false,
		SupportsTeardown: true,
		CommitSubject:    "chore(deploy): add generated Vercel deployment for {run}",
		PullRequestTitle: "Generated Vercel deployment configuration",
	},
}

// ProfileFor resolves a target name. Anything unrecognised — an old record, a
// typo in a request — is EC2, which is the target every other default assumes.
func ProfileFor(target string) Profile {
	if profile, ok := Profiles[strings.TrimSpace(strings.ToLower(target))]; ok {
		return profile
	}
	return Profiles[TargetEC2]
}

// TargetOf is the target a stored run belongs to.
func TargetOf(run *Run) string {
	if run == nil {
		return TargetEC2
	}
	return ProfileFor(text(run.Plan["target"])).Target
}
