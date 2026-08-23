package core

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestExtractJSON(t *testing.T) {
	cases := []struct{ name, in, want string }{
		{"bare object", `{"a":1}`, `{"a":1}`},
		{"leading prose", `Sure! Here it is:\n{"a":1}`, `{"a":1}`},
		{"fenced", "```json\n{\"a\":1}\n```", `{"a":1}`},
		{"fenced no lang", "```\n{\"a\":1}\n```", `{"a":1}`},
		{"nested", `{"a":{"b":[1,2]},"c":3}`, `{"a":{"b":[1,2]},"c":3}`},
		{"array", `[{"a":1}]`, `[{"a":1}]`},
		{"brace inside string", `{"a":"} not the end","b":2}`, `{"a":"} not the end","b":2}`},
		{"escaped quote in string", `{"a":"say \"} \"","b":2}`, `{"a":"say \"} \"","b":2}`},
		{"trailing prose", `{"a":1}\nHope that helps!`, `{"a":1}`},
		{"no json", `there is no object here`, ``},
		{"unbalanced", `{"a":1`, ``},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := ExtractJSON(c.in); got != c.want {
				t.Fatalf("ExtractJSON(%q) = %q, want %q", c.in, got, c.want)
			}
		})
	}
}

func TestSafeName(t *testing.T) {
	cases := map[string]string{
		"my-app":           "my-app",
		"../../etc/passwd": "passwd",
		"a/b/c":            "c",
		"..":               "",
		".hidden":          "hidden",
		"Weird Name!":      "Weird-Name",
		`c:\windows\bad`:   "bad",
	}
	for in, want := range cases {
		if got := SafeName(in); got != want {
			t.Errorf("SafeName(%q) = %q, want %q", in, got, want)
		}
	}
}

// A tool handed ../ must refuse rather than read outside the project.
func TestShellRefusesEscape(t *testing.T) {
	root := t.TempDir()
	project := filepath.Join(root, "app")
	if err := os.MkdirAll(project, 0o755); err != nil {
		t.Fatal(err)
	}
	secret := filepath.Join(root, "secret.txt")
	if err := os.WriteFile(secret, []byte("private"), 0o644); err != nil {
		t.Fatal(err)
	}
	s := NewShell(project)

	for _, bad := range []string{"../secret.txt", "../../secret.txt", secret} {
		if _, _, err := s.Read(bad); err == nil {
			t.Errorf("Read(%q) should have been refused", bad)
		}
		if err := s.Write(bad, "x"); err == nil {
			t.Errorf("Write(%q) should have been refused", bad)
		}
	}
	if data, err := os.ReadFile(secret); err != nil || string(data) != "private" {
		t.Fatalf("the file outside the project was modified: %v %q", err, data)
	}
}

func TestShellTreeSkipsHeavyDirs(t *testing.T) {
	project := t.TempDir()
	write := func(rel, body string) {
		p := filepath.Join(project, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("app/page.jsx", "export default function P(){}")
	write("node_modules/react/index.js", "// huge")
	write(".next/build.json", "{}")
	write(".git/config", "[core]")

	files, err := NewShell(project).Tree(".", 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 1 || files[0] != "app/page.jsx" {
		t.Fatalf("Tree walked into a skipped directory: %v", files)
	}
}

func TestSurveyClassifies(t *testing.T) {
	project := t.TempDir()
	for _, rel := range []string{
		"app/page.jsx",
		"app/orders/[id]/page.jsx",
		"app/api/orders/route.js",
		"tests/orders.test.js",
		"lib/db.js",
	} {
		p := filepath.Join(project, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte("//"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	st, err := NewShell(project).Survey()
	if err != nil {
		t.Fatal(err)
	}
	if !has(st.Routes, "/") || !has(st.Routes, "/orders/[id]") {
		t.Errorf("routes = %v", st.Routes)
	}
	if !has(st.APIs, "/api/orders") {
		t.Errorf("apis = %v", st.APIs)
	}
	if !has(st.Tests, "tests/orders.test.js") {
		t.Errorf("tests = %v", st.Tests)
	}
	if !st.Has("lib/db.js") || st.Has("lib/missing.js") {
		t.Errorf("Has is wrong for %v", st.Files)
	}
}

func TestExecCapturesExitCode(t *testing.T) {
	s := NewShell(t.TempDir())
	ctx := context.Background()

	res, err := s.Exec(ctx, 20*time.Second, "sh", "-c", "echo hello; exit 3")
	if err != nil {
		t.Fatalf("a non-zero exit should be reported, not returned as an error: %v", err)
	}
	if res.Code != 3 {
		t.Errorf("Code = %d, want 3", res.Code)
	}
	if !strings.Contains(res.Stdout, "hello") {
		t.Errorf("Stdout = %q", res.Stdout)
	}
	if res.OK() {
		t.Error("OK() should be false after a non-zero exit")
	}
}

func TestExecTimesOut(t *testing.T) {
	s := NewShell(t.TempDir())
	res, err := s.Exec(context.Background(), 150*time.Millisecond, "sh", "-c", "sleep 5")
	if err == nil {
		t.Fatal("a command past its deadline should report an error")
	}
	if !res.TimedOut {
		t.Error("TimedOut should be set")
	}
}

func TestCappedWriterStopsGrowing(t *testing.T) {
	c := &capped{limit: 10}
	n, err := c.Write([]byte(strings.Repeat("x", 100)))
	if err != nil || n != 100 {
		t.Fatalf("Write should claim the whole slice: %d %v", n, err)
	}
	if got := c.String(); len(got) != 10 {
		t.Fatalf("kept %d bytes, want 10", len(got))
	}
}

func TestRunCancel(t *testing.T) {
	r := NewRun(context.Background(), NewHub(), Paths{Projects: t.TempDir()}, NewLLM(), "demo", "build")
	if err := r.Check(); err != nil {
		t.Fatalf("a fresh run should not be cancelled: %v", err)
	}
	r.Cancel()
	if err := r.Check(); err != ErrCancelled {
		t.Fatalf("Check() = %v, want ErrCancelled", err)
	}
}

func TestIsTransient(t *testing.T) {
	if !IsTransient(errString("ollama returned 503 service unavailable")) {
		t.Error("503 should be retried")
	}
	if !IsTransient(errString("dial tcp: connection refused")) {
		t.Error("a refused dial should be retried")
	}
	if IsTransient(errString("model 'nope' not found")) {
		t.Error("a missing model is a real answer, not a transient failure")
	}
	if IsTransient(context.Canceled) {
		t.Error("a cancelled run must not be retried")
	}
}

// The file writer parses a token stream, so markers arrive split across
// arbitrary boundaries. Feeding the same output one byte at a time must produce
// exactly the same files as feeding it whole.
func TestFileWriterStreamsFiles(t *testing.T) {
	output := "Here you go:\n" +
		"<<<FILE app/page.jsx\n" +
		"export default function Page() {\n  return <div>hi</div>\n}\n" +
		">>>END\n" +
		"<<<FILE lib/db.js\n" +
		"export const db = 1\n" +
		">>>END\n"

	for _, chunkSize := range []int{1, 3, 17, len(output)} {
		t.Run("chunk "+itoa(chunkSize), func(t *testing.T) {
			run := NewRun(context.Background(), NewHub(),
				Paths{Projects: t.TempDir()}, NewLLM(), "demo", "build")
			if err := os.MkdirAll(run.Dir, 0o755); err != nil {
				t.Fatal(err)
			}
			w := NewFileWriter(run)
			for i := 0; i < len(output); i += chunkSize {
				end := min(i+chunkSize, len(output))
				w.Feed(output[i:end])
			}
			w.Finish()

			if got := w.Written(); len(got) != 2 || got[0] != "app/page.jsx" || got[1] != "lib/db.js" {
				t.Fatalf("written = %v", got)
			}
			body, _, err := run.Shell.Read("app/page.jsx")
			if err != nil {
				t.Fatal(err)
			}
			want := "export default function Page() {\n  return <div>hi</div>\n}\n"
			if body != want {
				t.Errorf("app/page.jsx =\n%q\nwant\n%q", body, want)
			}
			if strings.Contains(body, "<<<FILE") || strings.Contains(body, ">>>END") {
				t.Error("a marker leaked into the file body")
			}
			if strings.Contains(body, "Here you go") {
				t.Error("prose before the first marker leaked into the file")
			}
		})
	}
}

// Small models sometimes stop without closing the last file. What they did emit
// should still land on disk rather than being thrown away.
func TestFileWriterClosesAnUnterminatedFile(t *testing.T) {
	run := NewRun(context.Background(), NewHub(), Paths{Projects: t.TempDir()}, NewLLM(), "demo", "build")
	if err := os.MkdirAll(run.Dir, 0o755); err != nil {
		t.Fatal(err)
	}
	w := NewFileWriter(run)
	w.Feed("<<<FILE app/page.jsx\nexport default function Page() {}\n")
	w.Finish()

	if got := w.Written(); len(got) != 1 {
		t.Fatalf("written = %v", got)
	}
	body, _, _ := run.Shell.Read("app/page.jsx")
	if !strings.Contains(body, "export default function Page() {}") {
		t.Errorf("the unterminated file lost its content: %q", body)
	}
}

// A reply with no markers at all must write nothing, not create a stray file.
func TestFileWriterIgnoresProseOnlyReplies(t *testing.T) {
	run := NewRun(context.Background(), NewHub(), Paths{Projects: t.TempDir()}, NewLLM(), "demo", "build")
	w := NewFileWriter(run)
	w.Feed("I cannot fix this without seeing lib/db.js.")
	w.Finish()
	if got := w.Written(); len(got) != 0 {
		t.Errorf("written = %v, want nothing", got)
	}
}

func TestReadFilesRespectsBudget(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "big.js"), []byte(strings.Repeat("x", 5000)), 0o644); err != nil {
		t.Fatal(err)
	}
	run := NewRun(context.Background(), NewHub(), Paths{Projects: filepath.Dir(dir)}, NewLLM(), filepath.Base(dir), "build")
	block := ReadFiles(run, []string{"big.js", "missing.js"}, 1000)

	if len(block) > 1400 {
		t.Errorf("the budget was not applied: %d bytes", len(block))
	}
	if !strings.Contains(block, "truncated") {
		t.Error("a truncated file should say so")
	}
	if strings.Contains(block, "missing.js") {
		t.Error("a file that is not there should be skipped silently")
	}
}

func has(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

type errString string

func (e errString) Error() string { return string(e) }

// Listing has to be the real command, not our own directory read: the whole
// point is that the agent sees what the machine reports.
func TestLsRunsTheRealCommand(t *testing.T) {
	project := t.TempDir()
	for _, rel := range []string{"app/page.jsx", "lib/db.js", ".env.local", "node_modules/react/index.js"} {
		p := filepath.Join(project, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	var ran []string
	sh := NewShell(project)
	sh.Emit = func(line string) { ran = append(ran, line) }

	entries, err := sh.Ls(".")
	if err != nil {
		t.Fatal(err)
	}
	if len(ran) == 0 {
		t.Fatal("the listing command was never echoed to the console")
	}
	if !strings.HasPrefix(ran[0], "ls ") && !strings.HasPrefix(ran[0], "cmd ") {
		t.Errorf("echoed %q, expected the platform listing command", ran[0])
	}

	byName := map[string]Entry{}
	for _, e := range entries {
		byName[e.Name] = e
	}
	// -A keeps dotfiles, and node_modules is skipped after the command runs.
	if _, ok := byName[".env.local"]; !ok {
		t.Errorf("a dotfile was not listed: %v", entries)
	}
	if _, ok := byName["node_modules"]; ok {
		t.Errorf("node_modules should be skipped: %v", entries)
	}
	if e := byName["app"]; !e.Dir {
		t.Errorf("app should be reported as a directory: %+v", e)
	}
	if e := byName["lib"]; e.Path != "lib" {
		t.Errorf("path = %q, want a project-relative path", e.Path)
	}
}

// Tree is that same listing, one directory at a time, so it must agree with it.
func TestTreeIsBuiltFromRealListings(t *testing.T) {
	project := t.TempDir()
	want := []string{"app/orders/page.jsx", "app/page.jsx", "lib/db.js"}
	for _, rel := range append(append([]string{}, want...), "node_modules/react/index.js", ".next/build.json") {
		p := filepath.Join(project, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	got, err := NewShell(project).Tree(".", 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != len(want) {
		t.Fatalf("Tree = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("Tree[%d] = %q, want %q", i, got[i], want[i])
		}
	}

	// depth 1 stops before descending.
	shallow, err := NewShell(project).Tree(".", 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(shallow) != 0 {
		t.Errorf("depth 1 has no files at the root, got %v", shallow)
	}
}

// A listing command that cannot run must fall back rather than report nothing.
func TestListingFallsBackWhenTheCommandFails(t *testing.T) {
	project := t.TempDir()
	if err := os.WriteFile(filepath.Join(project, "a.js"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	names, err := readDirNames(project)
	if err != nil {
		t.Fatal(err)
	}
	if len(names) != 1 || names[0] != "a.js" {
		t.Fatalf("readDirNames = %v", names)
	}
}

func TestLsRefusesAFile(t *testing.T) {
	project := t.TempDir()
	if err := os.WriteFile(filepath.Join(project, "a.js"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := NewShell(project).Ls("a.js"); err == nil {
		t.Error("listing a file should be refused")
	}
	if _, err := NewShell(project).Ls("../"); err == nil {
		t.Error("listing outside the project should be refused")
	}
}
