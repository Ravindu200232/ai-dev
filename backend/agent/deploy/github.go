package deploy

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Everything that touches the customer's repository is here.
//
// Two rules run through all of it. The agent never commits a value: the git
// index is checked for .env files before anything is committed, and refuses.
// And the agent never overwrites something it did not put there: a file it is
// about to replace has to still hash to what it hashed at review time, or the
// run stops and asks for the analysis to be done again.

const (
	ghTimeout   = 60 * time.Second
	gitTimeout2 = 10 * time.Minute
	watchFor    = 15 * time.Minute
)

// githubRemote pulls owner/name out of any form of GitHub remote URL.
var githubRemote = regexp.MustCompile(`github\.com[/:]([^/\s]+/[^/\s]+?)(?:\.git)?$`)

// Push is what happened when the deployment was pushed.
type Push struct {
	Mode            string `json:"mode"`
	Branch          string `json:"branch"`
	URL             string `json:"url,omitempty"`
	HeadSHA         string `json:"head_sha"`
	PreviousRunURL  string `json:"previous_workflow_url,omitempty"`
	CommittedFiles  int    `json:"committed_files"`
	AlreadyUpToDate bool   `json:"already_up_to_date,omitempty"`
}

// GitHub is the customer's repository, reached through their own gh CLI so it
// uses the account they signed in with and no token the agent holds.
type GitHub struct {
	Emit Emit
	Dir  string // the customer's project, which is the git working tree
}

func (g GitHub) gh(ctx context.Context, args ...string) Output {
	return Exec(ctx, Command{Name: "gh", Args: args, Dir: g.Dir, Timeout: ghTimeout})
}

func (g GitHub) git(ctx context.Context, args ...string) Output {
	return Exec(ctx, Command{Name: "git", Args: args, Dir: g.Dir, Timeout: gitTimeout2})
}

// credentialArgs make git use the gh sign-in for a push, so nothing prompts
// and no token is written to a file.
func credentialArgs() []string {
	command := Resolve("gh")
	if command == "" {
		command = "gh"
	}
	return []string{
		"-c", "credential.https://github.com.helper=",
		"-c", "credential.https://github.com.helper=!'" + filepath.ToSlash(command) + "' auth git-credential",
	}
}

// EnsureRepository is the GitHub repository this project pushes to, made if
// the project has never had one.
func (g GitHub) EnsureRepository(ctx context.Context, slug string) (string, error) {
	if !isDir(filepath.Join(g.Dir, ".git")) {
		if out := g.git(ctx, "init"); !out.OK() {
			return "", errors.New("git init: " + out.Text())
		}
		if out := g.git(ctx, "branch", "-M", "main"); !out.OK() {
			return "", errors.New("git branch: " + out.Text())
		}
	}

	if out := g.git(ctx, "remote", "get-url", "origin"); out.OK() {
		if remote := strings.TrimSpace(out.Stdout); remote != "" {
			name := RepositoryName(remote)
			if name == "" {
				return "", errors.New("The existing origin is not a GitHub repository")
			}
			return name, nil
		}
	}

	who := g.gh(ctx, "api", "user", "--jq", ".login")
	if !who.OK() {
		return "", errors.New("gh api user: " + who.Text())
	}
	repo := strings.TrimSpace(who.Stdout) + "/" + slug
	if out := g.gh(ctx, "repo", "create", repo, "--private"); !out.OK() {
		// The name is taken. A dated suffix is better than deploying into
		// somebody else's repository of the same name.
		repo += "-" + time.Now().UTC().Format("200601021504")
		if retry := g.gh(ctx, "repo", "create", repo, "--private"); !retry.OK() {
			return "", errors.New("gh repo create: " + retry.Text())
		}
	}
	if out := g.git(ctx, "remote", "add", "origin", "https://github.com/"+repo+".git"); !out.OK() {
		return "", errors.New("git remote add: " + out.Text())
	}
	return repo, nil
}

// RepositoryName is owner/name from a remote URL, or "".
func RepositoryName(remote string) string {
	match := githubRemote.FindStringSubmatch(strings.TrimSpace(remote))
	if match == nil {
		return ""
	}
	return strings.TrimSuffix(match[1], ".git")
}

// RepoIdentity is what GitHub says about the repository, and what the OIDC
// trust policy has to be written against.
type RepoIdentity struct {
	ID    int64 `json:"id"`
	Owner struct {
		ID int64 `json:"id"`
	} `json:"owner"`
	DefaultBranch string `json:"default_branch"`
}

// Identity reads the repository, and refuses a repository whose OIDC subject
// template has been customised — the trust policy the agent writes assumes the
// default one, and a custom one would either not match or match too much.
func (g GitHub) Identity(ctx context.Context, repo string) (RepoIdentity, error) {
	var identity RepoIdentity
	out := g.gh(ctx, "api", "repos/"+repo)
	if !out.OK() {
		return identity, errors.New("gh api repos: " + out.Text())
	}
	if err := json.Unmarshal([]byte(out.Stdout), &identity); err != nil {
		return identity, errors.New("GitHub repository identity response was not valid JSON")
	}

	if custom := g.gh(ctx, "api", "repos/"+repo+"/actions/oidc/customization/sub"); custom.OK() {
		var customization struct {
			UseDefault *bool `json:"use_default"`
		}
		if json.Unmarshal([]byte(custom.Stdout), &customization) == nil &&
			customization.UseDefault != nil && !*customization.UseDefault {
			return identity, errors.New("This repository uses a custom GitHub OIDC subject " +
				"template; reset it to the default before deployment")
		}
	}
	return identity, nil
}

// DefaultBranch is the branch the workflows will run on.
func (g GitHub) DefaultBranch(ctx context.Context, repo, fallback string) string {
	out := g.gh(ctx, "repo", "view", repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name")
	if branch := strings.TrimSpace(out.Stdout); out.OK() && branch != "" {
		return branch
	}
	if fallback != "" {
		return fallback
	}
	return "main"
}

// OIDCSubjects are exactly which workflow runs may assume the deploy role: this
// repository, on this branch, and nothing else. The second form is the same
// thing by immutable id, so a repository that is renamed keeps working and one
// that is deleted and recreated under the same name does not.
func OIDCSubjects(repo, branch string, identity RepoIdentity) []string {
	owner, name, _ := strings.Cut(repo, "/")
	subjects := []string{"repo:" + owner + "/" + name + ":ref:refs/heads/" + branch}
	if identity.Owner.ID != 0 && identity.ID != 0 {
		subjects = append(subjects,
			"repo:"+owner+"@"+strconv.FormatInt(identity.Owner.ID, 10)+
				"/"+name+"@"+strconv.FormatInt(identity.ID, 10)+":ref:refs/heads/"+branch)
	}
	return subjects
}

// SetVariables writes what the workflows read. A missing value is a bug in the
// bootstrap stack, not something to paper over: the workflow would fail later
// with nothing to point at.
func (g GitHub) SetVariables(ctx context.Context, repo string, values map[string]string) error {
	for _, name := range sortedKeys(values) {
		if values[name] == "" {
			return errors.New("Bootstrap output is missing " + name)
		}
		if out := g.gh(ctx, "variable", "set", name, "--body", values[name], "--repo", repo); !out.OK() {
			return errors.New("gh variable set " + name + ": " + out.Text())
		}
	}
	return nil
}

// SetSecrets writes Actions secrets with the value on stdin, so it is never an
// argument and never reaches a process list or a shell history.
func (g GitHub) SetSecrets(ctx context.Context, repo string, values map[string]string) error {
	for _, name := range sortedKeys(values) {
		if values[name] == "" {
			continue
		}
		out := Exec(ctx, Command{
			Name: "gh", Args: []string{"secret", "set", name, "--repo", repo},
			Dir: g.Dir, Timeout: ghTimeout, Stdin: values[name],
		})
		if !out.OK() {
			return errors.New("gh secret set " + name + ": " + out.Text())
		}
	}
	return nil
}

// --- putting the reviewed files into the project ------------------------------------------

// Apply copies the reviewed artifacts out of the staging copy and into the
// customer's own project. This is the first moment the run writes anything
// outside its workspace, and every file it touches was shown on the review
// screen first.
func Apply(emit Emit, source, staged string, records []Artifact) ([]string, error) {
	backups := filepath.Join(filepath.Dir(staged), "backup")
	applied := []string{}

	for _, record := range records {
		target := filepath.Join(source, filepath.FromSlash(record.Path))
		from := filepath.Join(staged, filepath.FromSlash(record.Path))
		if !within(source, target) || !within(staged, from) {
			return nil, errors.New("Reviewed artifact path escapes the project workspace: " + record.Path)
		}
		if _, err := os.Stat(from); err != nil {
			return nil, errors.New("Reviewed artifact disappeared: " + record.Path)
		}

		current := SHA256File(target)
		if current == record.SHA256 {
			// Already applied — a re-run of the same deployment.
			applied = append(applied, record.Path)
			continue
		}
		switch {
		case record.OriginalExists:
			if current == "" || current != record.OriginalSHA256 {
				return nil, errors.New(
					"Source file changed after review; re-run analysis: " + record.Path)
			}
			if err := copyFile(target, filepath.Join(backups, filepath.FromSlash(record.Path))); err != nil {
				return nil, err
			}
		case current != "":
			return nil, errors.New("A new conflicting file appeared after review: " + record.Path)
		}

		if err := copyFile(from, target); err != nil {
			return nil, err
		}
		applied = append(applied, record.Path)
	}

	emit.step("apply", StatusComplete, 58,
		"Applied "+strconv.Itoa(len(applied))+" reviewed files to the source repository", nil)
	return applied, nil
}

// within reports whether a path stays inside a directory.
func within(root, path string) bool {
	rootAbs, err := filepath.Abs(root)
	if err != nil {
		return false
	}
	pathAbs, err := filepath.Abs(path)
	if err != nil {
		return false
	}
	rel, err := filepath.Rel(rootAbs, pathAbs)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

// --- committing and pushing ------------------------------------------------------------------

// excluded are never committed by the agent, whatever the project's own
// .gitignore says. The .env rules are the important ones: a deployment that
// committed a customer's environment file would publish their database.
var excluded = []string{
	":(exclude)**/.env", ":(exclude)**/.env.local", ":(exclude)**/.env.production",
	":(exclude)**/.env.development", ":(exclude)**/node_modules/**", ":(exclude)**/.next/**",
	":(exclude)**/.vercel/**",
}

// Commit stages the deployment, refuses to commit anything that holds a value,
// and pushes. A branch that cannot be pushed to — protected, or not the
// customer's to write — becomes a pull request instead of a failure.
func (g GitHub) Commit(ctx context.Context, runID, repo, branch string,
	applied []string, profile Profile) (Push, error) {
	previous := g.latestRunURL(ctx, repo)

	if out := g.git(ctx, append([]string{"add", "-A", "--", "."}, excluded...)...); !out.OK() {
		return Push{}, errors.New("git add: " + out.Text())
	}
	// .env.example is the one .env file that exists to be committed.
	for _, relative := range applied {
		if strings.HasSuffix(relative, ".env.example") {
			if out := g.git(ctx, "add", "-f", "--", relative); !out.OK() {
				return Push{}, errors.New("git add: " + out.Text())
			}
		}
	}

	staged := g.git(ctx, "diff", "--cached", "--name-only")
	if !staged.OK() {
		return Push{}, errors.New("git diff: " + staged.Text())
	}
	names := staged.Lines()
	if leaked := environmentFiles(names); len(leaked) > 0 {
		return Push{}, errors.New("Refusing to commit environment value files: " +
			strings.Join(leaked, ", "))
	}

	if len(names) > 0 {
		subject := strings.ReplaceAll(profile.CommitSubject, "{run}", shortID(runID))
		if out := g.git(ctx, "commit", "-m", subject); !out.OK() {
			return Push{}, errors.New(out.Text())
		}
	}

	head := strings.TrimSpace(g.git(ctx, "rev-parse", "HEAD").Stdout)
	push := g.git(ctx, append(credentialArgs(), "push", "origin", "HEAD:"+branch)...)
	if push.OK() {
		if len(names) == 0 {
			// Nothing changed, so no workflow will fire by itself.
			if out := g.gh(ctx, "workflow", "run", "deploy.yml", "--repo", repo, "--ref", branch); !out.OK() {
				return Push{}, errors.New("gh workflow run: " + out.Text())
			}
		}
		g.Emit.step("github", StatusComplete, 68,
			"Pushed reviewed deployment commit to "+repo+":"+branch, nil)
		return Push{
			Mode: "direct", Branch: branch, HeadSHA: head,
			PreviousRunURL: previous, CommittedFiles: len(names),
			AlreadyUpToDate: len(names) == 0,
		}, nil
	}

	fallback := "deployment-agent/" + shortID(runID)
	if out := g.git(ctx, append(credentialArgs(), "push", "origin", "HEAD:"+fallback)...); !out.OK() {
		return Push{}, errors.New("git push: " + out.Text())
	}
	pr := g.gh(ctx, "pr", "create", "--repo", repo, "--base", branch, "--head", fallback,
		"--title", profile.PullRequestTitle,
		"--body", "Generated and reviewed by Deployment Agent run `"+runID+"`.")
	if !pr.OK() {
		return Push{}, errors.New("gh pr create: " + pr.Text())
	}
	return Push{
		Mode: "pull_request", Branch: fallback, URL: strings.TrimSpace(pr.Stdout),
		HeadSHA: head, CommittedFiles: len(names),
	}, nil
}

// environmentFiles are staged files that hold values. .env.example is the
// documented exception and holds only names.
func environmentFiles(names []string) []string {
	out := []string{}
	for _, name := range names {
		base := filepath.Base(strings.TrimSpace(name))
		if strings.HasPrefix(base, ".env") && base != ".env.example" {
			out = append(out, name)
		}
		if len(out) == 10 {
			break
		}
	}
	return out
}

func shortID(runID string) string {
	if len(runID) <= 8 {
		return runID
	}
	return runID[:8]
}

// --- watching the workflow ---------------------------------------------------------------

// WorkflowRun is one GitHub Actions run, as gh reports it.
type WorkflowRun struct {
	Status       string `json:"status"`
	Conclusion   string `json:"conclusion"`
	URL          string `json:"url"`
	DisplayTitle string `json:"displayTitle"`
	CreatedAt    string `json:"createdAt"`
	HeadSHA      string `json:"headSha"`
}

func (g GitHub) latestRunURL(ctx context.Context, repo string) string {
	out := g.gh(ctx, "run", "list", "--repo", repo, "--workflow", "deploy.yml",
		"--limit", "1", "--json", "url")
	if !out.OK() {
		return ""
	}
	var runs []WorkflowRun
	if json.Unmarshal([]byte(out.Stdout), &runs) != nil || len(runs) == 0 {
		return ""
	}
	return runs[0].URL
}

// Watch follows the deployment workflow until it finishes, and returns its
// conclusion — or "running" if it is still going when the wait runs out, or
// "not_found" if no run ever appeared.
func (g GitHub) Watch(ctx context.Context, repo, headSHA, previousURL string) string {
	deadline := time.Now().Add(watchFor)
	seen := false
	last := ""

	for time.Now().Before(deadline) {
		out := g.gh(ctx, "run", "list", "--repo", repo, "--workflow", "deploy.yml",
			"--limit", "10", "--json", "status,conclusion,url,displayTitle,createdAt,headSha")
		if out.OK() {
			var runs []WorkflowRun
			if json.Unmarshal([]byte(out.Stdout), &runs) == nil {
				if current, ok := SelectRun(runs, headSHA, previousURL); ok {
					seen = true
					if signature := current.Status + ":" + current.Conclusion; signature != last {
						last = signature
						status := current.Status
						if status == "" {
							status = StatusRunning
						}
						title := current.DisplayTitle
						if title == "" {
							title = "deployment"
						}
						g.Emit.send(Event{Type: EventMonitor, Stage: "github", Status: status,
							Percent: 76, Message: "GitHub Actions: " + title, Data: asMap(current)})
					}
					if current.Status == "completed" {
						if current.Conclusion == "" {
							return "unknown"
						}
						return current.Conclusion
					}
				}
			}
		}
		wait := 5 * time.Second
		if seen {
			wait = 10 * time.Second
		}
		if !sleep(ctx, wait) {
			break
		}
	}
	if seen {
		return "running"
	}
	return "not_found"
}

// SelectRun is which of the repository's recent workflow runs belongs to this
// deployment: the one for the commit that was just pushed, or — when the push
// was a fast-forward of somebody else's commit — the newest one that is not
// the run that was already there before.
func SelectRun(runs []WorkflowRun, headSHA, previousURL string) (WorkflowRun, bool) {
	if headSHA != "" {
		// A commit that has no run of its own has no run: taking the newest
		// one instead would report somebody else's push as this deployment.
		for _, run := range runs {
			if run.HeadSHA == headSHA {
				return run, true
			}
		}
		return WorkflowRun{}, false
	}
	for _, run := range runs {
		if run.URL != "" && run.URL == previousURL {
			continue
		}
		return run, true
	}
	return WorkflowRun{}, false
}
