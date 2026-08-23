package core

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// The agent inspects a project the way a person would: list a directory, read a
// file, grep for a symbol, run a command. Those four are the whole tool surface
// and every phase re-runs them rather than trusting what it already believes.
//
// ls/read/grep/tree are implemented against the filesystem instead of exec'ing
// /bin/ls, because AgentForge ships on Windows through start-agentforge.bat and
// coreutils are not there. Real processes — npm, npx, node — are exec'd for real.

// Skip lists the directories no survey should ever walk into.
var Skip = map[string]bool{
	"node_modules": true, ".next": true, ".git": true, "dist": true,
	"build": true, ".turbo": true, "coverage": true, ".agentforge": true,
	"playwright-report": true, "test-results": true, ".vercel": true,
}

const (
	defaultExecTimeout = 10 * time.Minute
	maxCapturedOutput  = 256 << 10 // 256 KiB of a command's output is plenty
	maxReadBytes       = 192 << 10
	maxSurveyFiles     = 4000
)

// ErrOutsideProject means a tool was handed a path that escapes the project.
var ErrOutsideProject = errors.New("path is outside the project")

// Shell runs the agent's tools against one project directory.
type Shell struct {
	Dir string
	// Emit, when set, echoes each executed command to the Studio console.
	Emit func(line string)
}

func NewShell(dir string) *Shell { return &Shell{Dir: dir} }

// resolve turns a project-relative path into an absolute one, refusing
// anything that would escape the project directory.
func (s *Shell) resolve(rel string) (string, error) {
	rel = strings.TrimSpace(rel)
	rel = strings.TrimPrefix(filepath.ToSlash(rel), "./")
	if rel == "" || rel == "." {
		return s.Dir, nil
	}
	if filepath.IsAbs(rel) {
		return "", fmt.Errorf("%w: %s", ErrOutsideProject, rel)
	}
	abs := filepath.Join(s.Dir, filepath.FromSlash(rel))
	base := filepath.Clean(s.Dir)
	if abs != base && !strings.HasPrefix(abs, base+string(os.PathSeparator)) {
		return "", fmt.Errorf("%w: %s", ErrOutsideProject, rel)
	}
	return abs, nil
}

// Rel turns an absolute path back into the project-relative form the agent and
// the Studio both use.
func (s *Shell) Rel(abs string) string {
	rel, err := filepath.Rel(s.Dir, abs)
	if err != nil {
		return filepath.ToSlash(abs)
	}
	return filepath.ToSlash(rel)
}

// Entry is one row of an `ls`.
type Entry struct {
	Name  string `json:"name"`
	Path  string `json:"path"`
	Dir   bool   `json:"dir"`
	Size  int64  `json:"size"`
	Lines int    `json:"lines,omitempty"`
}

// Ls lists one directory. This is the tool the builder calls at the start of
// every phase to see what is really on disk.
func (s *Shell) Ls(rel string) ([]Entry, error) {
	abs, err := s.resolve(rel)
	if err != nil {
		return nil, err
	}
	items, err := os.ReadDir(abs)
	if err != nil {
		return nil, err
	}
	out := make([]Entry, 0, len(items))
	for _, it := range items {
		if Skip[it.Name()] {
			continue
		}
		e := Entry{Name: it.Name(), Dir: it.IsDir(), Path: s.Rel(filepath.Join(abs, it.Name()))}
		if info, err := it.Info(); err == nil {
			e.Size = info.Size()
		}
		out = append(out, e)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Dir != out[j].Dir {
			return out[i].Dir
		}
		return out[i].Name < out[j].Name
	})
	return out, nil
}

// Tree walks the project, skipping the directories in Skip. depth <= 0 walks
// the whole tree.
func (s *Shell) Tree(rel string, depth int) ([]string, error) {
	root, err := s.resolve(rel)
	if err != nil {
		return nil, err
	}
	var files []string
	rootDepth := strings.Count(filepath.ToSlash(root), "/")
	err = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil // an unreadable corner is not a reason to abandon the walk
		}
		if d.IsDir() {
			if path != root && Skip[d.Name()] {
				return filepath.SkipDir
			}
			if depth > 0 && strings.Count(filepath.ToSlash(path), "/")-rootDepth >= depth {
				return filepath.SkipDir
			}
			return nil
		}
		if len(files) >= maxSurveyFiles {
			return io.EOF
		}
		files = append(files, s.Rel(path))
		return nil
	})
	if err != nil && !errors.Is(err, io.EOF) {
		return files, err
	}
	sort.Strings(files)
	return files, nil
}

// Read returns a file's contents, truncated so one large file cannot swamp a
// prompt. The second value reports whether it was cut short.
func (s *Shell) Read(rel string) (string, bool, error) {
	abs, err := s.resolve(rel)
	if err != nil {
		return "", false, err
	}
	data, err := os.ReadFile(abs)
	if err != nil {
		return "", false, err
	}
	if len(data) > maxReadBytes {
		return string(data[:maxReadBytes]), true, nil
	}
	return string(data), false, nil
}

// Write saves a file inside the project, creating parent directories.
func (s *Shell) Write(rel, content string) error {
	abs, err := s.resolve(rel)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		return err
	}
	return os.WriteFile(abs, []byte(content), 0o644)
}

// Exists reports whether a project-relative path is on disk.
func (s *Shell) Exists(rel string) bool {
	abs, err := s.resolve(rel)
	if err != nil {
		return false
	}
	_, err = os.Stat(abs)
	return err == nil
}

// Hit is one grep match.
type Hit struct {
	Path string `json:"path"`
	Line int    `json:"line"`
	Text string `json:"text"`
}

// Grep searches the project for a pattern, optionally limited to files whose
// path contains one of the given suffixes.
func (s *Shell) Grep(pattern string, suffixes []string, limit int) ([]Hit, error) {
	re, err := regexp.Compile(pattern)
	if err != nil {
		return nil, err
	}
	if limit <= 0 {
		limit = 200
	}
	files, err := s.Tree(".", 0)
	if err != nil {
		return nil, err
	}
	var hits []Hit
	for _, f := range files {
		if len(suffixes) > 0 && !hasSuffix(f, suffixes) {
			continue
		}
		abs, err := s.resolve(f)
		if err != nil {
			continue
		}
		fh, err := os.Open(abs)
		if err != nil {
			continue
		}
		scanner := bufio.NewScanner(fh)
		scanner.Buffer(make([]byte, 0, 64<<10), 1<<20)
		for n := 1; scanner.Scan(); n++ {
			line := scanner.Text()
			if re.MatchString(line) {
				hits = append(hits, Hit{Path: f, Line: n, Text: strings.TrimSpace(line)})
				if len(hits) >= limit {
					fh.Close()
					return hits, nil
				}
			}
		}
		fh.Close()
	}
	return hits, nil
}

func hasSuffix(path string, suffixes []string) bool {
	for _, s := range suffixes {
		if strings.HasSuffix(path, s) {
			return true
		}
	}
	return false
}

// Result is what one finished command left behind.
type Result struct {
	Cmd      string
	Code     int
	Stdout   string
	Stderr   string
	Duration time.Duration
	TimedOut bool
}

// OK reports a clean exit.
func (r Result) OK() bool { return r.Code == 0 && !r.TimedOut }

// Tail returns the last n lines of combined output — what a failure diagnosis
// actually needs.
func (r Result) Tail(n int) string {
	combined := strings.TrimSpace(r.Stdout + "\n" + r.Stderr)
	lines := strings.Split(combined, "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return strings.Join(lines, "\n")
}

// Exec runs a command to completion inside the project directory.
func (s *Shell) Exec(ctx context.Context, timeout time.Duration, name string, args ...string) (Result, error) {
	if timeout <= 0 {
		timeout = defaultExecTimeout
	}
	line := name + " " + strings.Join(args, " ")
	if s.Emit != nil {
		s.Emit(strings.TrimSpace(line))
	}
	runCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	cmd := exec.CommandContext(runCtx, name, args...)
	cmd.Dir = s.Dir
	cmd.Env = s.env()
	var out, errb capped
	out.limit, errb.limit = maxCapturedOutput, maxCapturedOutput
	cmd.Stdout, cmd.Stderr = &out, &errb

	started := time.Now()
	err := cmd.Run()
	res := Result{
		Cmd: strings.TrimSpace(line), Stdout: out.String(), Stderr: errb.String(),
		Duration: time.Since(started),
	}
	if runCtx.Err() == context.DeadlineExceeded {
		res.TimedOut = true
		res.Code = -1
		return res, fmt.Errorf("%s timed out after %s", name, timeout)
	}
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			res.Code = ee.ExitCode()
			return res, nil // a non-zero exit is an answer, not a failure to run
		}
		res.Code = -1
		return res, err
	}
	return res, nil
}

// Start launches a long-running process (the dev server) and streams its output
// to onLine until the process exits or the context is cancelled.
func (s *Shell) Start(ctx context.Context, onLine func(string), name string, args ...string) (*exec.Cmd, error) {
	line := name + " " + strings.Join(args, " ")
	if s.Emit != nil {
		s.Emit(strings.TrimSpace(line))
	}
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Dir = s.Dir
	cmd.Env = s.env()
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	pump := func(r io.Reader) {
		scanner := bufio.NewScanner(r)
		scanner.Buffer(make([]byte, 0, 64<<10), 1<<20)
		for scanner.Scan() {
			if onLine != nil {
				onLine(scanner.Text())
			}
		}
	}
	go pump(stdout)
	go pump(stderr)
	return cmd, nil
}

// env gives child processes the node toolchain the launcher resolved.
func (s *Shell) env() []string {
	env := os.Environ()
	if node := strings.TrimSpace(os.Getenv("AGENTFORGE_NODE")); node != "" {
		env = append(env, "PATH="+filepath.Dir(node)+string(os.PathListSeparator)+os.Getenv("PATH"))
	}
	return env
}

// NPM and NPX are the launcher-resolved binaries, falling back to whatever is
// on PATH. On Windows npm ships as npm.cmd.
func NPM() string { return nodeBin("AGENTFORGE_NPM", "npm") }
func NPX() string { return nodeBin("AGENTFORGE_NPX", "npx") }

func nodeBin(envKey, base string) string {
	if v := strings.TrimSpace(os.Getenv(envKey)); v != "" {
		return v
	}
	if found, err := exec.LookPath(base); err == nil {
		return found
	}
	return base
}

// Survey is the structure snapshot every phase takes before it decides
// anything. It is a tree walk plus the classification the planner cares about.
func (s *Shell) Survey() (*Structure, error) {
	files, err := s.Tree(".", 0)
	if err != nil && len(files) == 0 {
		return &Structure{TakenAt: time.Now()}, err
	}
	st := &Structure{Files: files, TakenAt: time.Now()}
	seen := map[string]bool{}
	for _, f := range files {
		if dir := filepath.ToSlash(filepath.Dir(f)); dir != "." && !seen[dir] {
			seen[dir] = true
			st.Dirs = append(st.Dirs, dir)
		}
		switch {
		case isTestFile(f):
			st.Tests = append(st.Tests, f)
		case strings.HasPrefix(f, "app/api/") && isRouteFile(f):
			st.APIs = append(st.APIs, apiRoute(f))
		case strings.HasPrefix(f, "app/") && isPageFile(f):
			st.Routes = append(st.Routes, pageRoute(f))
		}
	}
	sort.Strings(st.Dirs)
	sort.Strings(st.Routes)
	sort.Strings(st.APIs)
	return st, nil
}

func isTestFile(f string) bool {
	return strings.Contains(f, ".test.") || strings.Contains(f, ".spec.") ||
		strings.HasPrefix(f, "tests/") || strings.HasPrefix(f, "e2e/")
}

func isRouteFile(f string) bool {
	base := filepath.Base(f)
	return base == "route.js" || base == "route.ts" || base == "route.jsx" || base == "route.tsx"
}

func isPageFile(f string) bool {
	base := filepath.Base(f)
	return base == "page.js" || base == "page.ts" || base == "page.jsx" || base == "page.tsx"
}

// apiRoute turns app/api/orders/route.js into /api/orders.
func apiRoute(f string) string {
	return "/" + strings.TrimSuffix(strings.TrimPrefix(filepath.ToSlash(filepath.Dir(f)), "app/"), "/")
}

// pageRoute turns app/orders/[id]/page.jsx into /orders/[id].
func pageRoute(f string) string {
	dir := strings.TrimPrefix(filepath.ToSlash(filepath.Dir(f)), "app")
	if dir == "" {
		return "/"
	}
	return dir
}

// capped is a writer that keeps only the first limit bytes. Build output can be
// enormous and only the head and the exit code decide anything.
type capped struct {
	buf   []byte
	limit int
}

func (c *capped) Write(p []byte) (int, error) {
	if room := c.limit - len(c.buf); room > 0 {
		if len(p) <= room {
			c.buf = append(c.buf, p...)
		} else {
			c.buf = append(c.buf, p[:room]...)
		}
	}
	return len(p), nil
}

func (c *capped) String() string { return string(c.buf) }
