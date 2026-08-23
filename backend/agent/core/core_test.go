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
