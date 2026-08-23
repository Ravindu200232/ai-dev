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
