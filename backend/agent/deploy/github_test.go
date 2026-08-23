package deploy

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// gitRepo is a real repository, because the thing under test is what git
// actually stages — not what a fake would say it staged.
func gitRepo(t *testing.T) string {
	t.Helper()
	if !Have("git") {
		t.Skip("git is not installed")
	}
	dir := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q"},
		{"config", "user.email", "test@example.com"},
		{"config", "user.name", "Test"},
		{"config", "commit.gpgsign", "false"},
	} {
		if out := Exec(context.Background(), Command{Name: "git", Args: args, Dir: dir}); !out.OK() {
			t.Fatalf("git %v: %s", args, out.Text())
		}
	}
	return dir
}

func TestAnEnvironmentFileIsNeverCommitted(t *testing.T) {
	// The path ending in "bearer" is the point: redaction used to rewrite it
	// and swallow the line after it, hiding the .env file from the guard that
	// exists to stop exactly this.
	source := gitRepo(t)
	write(t, source, "package.json", `{"name":"shop"}`)
	write(t, source, "a/bearer", "a file whose name ends in bearer\n")
	write(t, source, "b/.env.production.local",
		"MONGODB_URI=mongodb+srv://user:hunter2@cluster.mongodb.net/shop\n")
	write(t, source, ".env", "MONGODB_URI=mongodb+srv://user:hunter2@cluster/db\n")
	write(t, source, ".env.example", "MONGODB_URI=\n")

	github := GitHub{Dir: source}
	push, err := github.Commit(context.Background(), "run_test", "acme/shop", "main",
		[]string{".env.example"}, ProfileFor(TargetEC2))

	// Pushing has nowhere to go here; what matters is what reached the index.
	_ = push
	_ = err

	tracked := Exec(context.Background(), Command{
		Name: "git", Args: []string{"ls-files", "--cached"}, Dir: source, Plain: true})
	for _, name := range tracked.Lines() {
		base := filepath.Base(name)
		if strings.HasPrefix(base, ".env") && base != ".env.example" {
			t.Errorf("%s was committed", name)
		}
	}
	if !contains(tracked.Lines(), ".env.example") {
		t.Errorf("the example file is meant to be committed: %v", tracked.Lines())
	}
	if !contains(tracked.Lines(), "package.json") {
		t.Errorf("the project itself was not committed: %v", tracked.Lines())
	}

	// And nothing that holds a value is in the tree.
	body, err := os.ReadFile(filepath.Join(source, ".git", "COMMIT_EDITMSG"))
	if err == nil && strings.Contains(string(body), "hunter2") {
		t.Error("a value reached the commit message")
	}
}

func TestTheGuardSeesWhatGitActuallyStaged(t *testing.T) {
	// Even with the pathspec defeated — a file added to the index by hand —
	// the guard has to notice, which it can only do if it reads the real list.
	source := gitRepo(t)
	write(t, source, "a/bearer", "a name that redaction used to eat past\n")
	write(t, source, "b/.env.production.local", "MONGODB_URI=mongodb+srv://u:p@c/db\n")

	for _, args := range [][]string{
		{"add", "-f", "--", "a/bearer"},
		{"add", "-f", "--", "b/.env.production.local"},
	} {
		if out := Exec(context.Background(), Command{Name: "git", Args: args, Dir: source}); !out.OK() {
			t.Fatalf("git %v: %s", args, out.Text())
		}
	}

	staged := Exec(context.Background(), Command{
		Name: "git", Args: []string{"diff", "--cached", "--name-only"}, Dir: source, Plain: true})
	if leaked := environmentFiles(staged.Lines()); len(leaked) != 1 {
		t.Fatalf("the guard read %v from %q", leaked, staged.Stdout)
	}

	// The same listing, redacted the way it used to be, loses the file.
	if got := environmentFiles(strings.Split(RedactText(staged.Stdout), "\n")); len(got) != 1 {
		t.Logf("redaction still hides it: %q", RedactText(staged.Stdout))
	}
}

func TestRedactionNeverSpansALine(t *testing.T) {
	// A pattern that crosses a newline swallows whatever follows it.
	body := "a/bearer\nb/.env.production.local\npackage.json\n"
	if got := RedactText(body); !strings.Contains(got, ".env.production.local") {
		t.Errorf("redaction ate the next line: %q", got)
	}

	// A real bearer token on one line is still removed.
	token := "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345\n"
	if got := RedactText(token); strings.Contains(got, "abcdefghijklmnop") {
		t.Errorf("a token survived: %q", got)
	}
}

func TestRedactionNeverLosesALine(t *testing.T) {
	// A pattern that swallows a line boundary takes whatever follows it with
	// it, and something else is reading those lines: this is how a .env path
	// once vanished from the list the commit guard checks.
	cases := []string{
		"a/-----BEGIN PRIVATE KEY-----\nb/.env.production.local\nc/-----END PRIVATE KEY-----\npackage.json",
		"MY_KEY=\n.env.local\npackage.json",
		"a/bearer\nb/.env.production.local\npackage.json",
		"AUTH_TOKEN = ghp_" + strings.Repeat("a", 30) + "\nnext.config.mjs",
		"-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n-----END RSA PRIVATE KEY-----",
		"nothing to see here",
		"",
	}
	for _, body := range cases {
		got := RedactText(body)
		if strings.Count(got, "\n") != strings.Count(body, "\n") {
			t.Errorf("%q became %q — the line count changed", body, got)
		}
	}

	// And the secret is still gone.
	key := "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg==\n-----END PRIVATE KEY-----"
	if strings.Contains(RedactText(key), "MIIEvQIBADANBg") {
		t.Errorf("the key survived: %q", RedactText(key))
	}
	staged := RedactText("a/-----BEGIN PRIVATE KEY-----\nb/.env.production.local\nc/-----END PRIVATE KEY-----")
	if !strings.Contains(staged, redacted) {
		t.Errorf("staged = %q", staged)
	}
}

func TestTheAgentCommitsTheProjectAndNothingElse(t *testing.T) {
	source := gitRepo(t)
	write(t, source, "package.json", `{"name":"shop"}`)
	// An example this run did not write: before, it was excluded with the rest
	// of the .env family and never added back, so the project shipped without one.
	write(t, source, ".env.example", "MONGODB_URI=\n")
	// The project root is where the ** patterns used to miss.
	write(t, source, "node_modules/left-pad/index.js", "module.exports = 1\n")
	write(t, source, ".next/BUILD_ID", "abc\n")
	write(t, source, ".vercel/project.json", `{"projectId":"prj_1"}`)
	// This agent's own record of the deployment, which carries the account it
	// deployed into.
	write(t, source, ".agentforge/deploy/run.json", `{"repo":{"account_id":"123456789012"}}`)

	_, _ = GitHub{Dir: source}.Commit(context.Background(), "run_test", "acme/shop", "main",
		nil, ProfileFor(TargetEC2))

	tracked := Exec(context.Background(), Command{
		Name: "git", Args: []string{"ls-files", "--cached"}, Dir: source, Plain: true})
	staged := tracked.Lines()

	for _, name := range []string{"package.json", ".env.example"} {
		if !contains(staged, name) {
			t.Errorf("%s belongs in the commit: %v", name, staged)
		}
	}
	for _, name := range staged {
		for _, unwanted := range []string{"node_modules/", ".next/", ".vercel/", ".agentforge/"} {
			if strings.HasPrefix(name, unwanted) {
				t.Errorf("%s was committed", name)
			}
		}
	}
}
