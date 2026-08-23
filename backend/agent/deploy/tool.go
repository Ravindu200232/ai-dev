package deploy

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// Everything the deployment agent runs on the machine goes through here.
//
// It is separate from core.Shell because that one resolves every path inside
// one generated project: this runs git, npm, aws and gh against the customer's
// own folder and against a staging workspace outside it. What the two share is
// the rule that output is captured, never inherited, and redacted before it
// reaches anything that stores or displays it.

// tools are the command-line programs a deployment depends on. The Studio's
// setup panel reports each one.
var tools = map[string][]string{
	"git":    {"git", "--version"},
	"gh":     {"gh", "--version"},
	"aws":    {"aws", "--version"},
	"node":   {"node", "--version"},
	"vercel": {"vercel", "--version"},
	"ollama": {"ollama", "--version"},
}

// windowsToolDirs are where the Windows installers put things they forgot to
// add to PATH. A machine that has just installed git or the AWS CLI has a
// stale PATH in every process that was already running, this one included.
func windowsToolDirs() []string {
	if runtime.GOOS != "windows" {
		return nil
	}
	programFiles := env("ProgramFiles", `C:\Program Files`)
	localAppData := env("LOCALAPPDATA", filepath.Join(home(), "AppData", "Local"))
	appData := env("APPDATA", filepath.Join(home(), "AppData", "Roaming"))
	return []string{
		filepath.Join(programFiles, "Git", "cmd"),
		filepath.Join(programFiles, "GitHub CLI"),
		filepath.Join(programFiles, "Amazon", "AWSCLIV2"),
		filepath.Join(programFiles, "nodejs"),
		filepath.Join(localAppData, "Programs", "Ollama"),
		filepath.Join(localAppData, "Programs", "nodejs"),
		filepath.Join(localAppData, "GitHubDesktop", "bin"),
		filepath.Join(appData, "npm"),
	}
}

func env(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func home() string {
	dir, err := os.UserHomeDir()
	if err != nil {
		return "."
	}
	return dir
}

// commandPath returns the PATH commands are looked up and run with.
func commandPath() string {
	current := os.Getenv("PATH")
	extra := []string{}
	for _, dir := range windowsToolDirs() {
		if info, err := os.Stat(dir); err == nil && info.IsDir() {
			extra = append(extra, dir)
		}
	}
	if len(extra) == 0 {
		return current
	}
	if current != "" {
		extra = append(extra, current)
	}
	return strings.Join(extra, string(os.PathListSeparator))
}

// Resolve finds a command, looking in the places Windows installers use as
// well as on PATH. It returns "" when the machine does not have it.
func Resolve(name string) string {
	path := commandPath()
	if path == os.Getenv("PATH") {
		found, err := exec.LookPath(name)
		if err != nil {
			return ""
		}
		return found
	}
	restore := os.Getenv("PATH")
	_ = os.Setenv("PATH", path)
	found, err := exec.LookPath(name)
	_ = os.Setenv("PATH", restore)
	if err != nil {
		return ""
	}
	return found
}

// Have reports whether the machine has a command.
func Have(name string) bool { return Resolve(name) != "" }

// Output is what one command left behind. Both streams are already redacted.
type Output struct {
	Code     int
	Stdout   string
	Stderr   string
	TimedOut bool
}

// OK is a command that ran and succeeded.
func (o Output) OK() bool { return o.Code == 0 && !o.TimedOut }

// Text is whichever stream said something, for a message to the console.
func (o Output) Text() string {
	if out := strings.TrimSpace(o.Stdout); out != "" {
		return out
	}
	return strings.TrimSpace(o.Stderr)
}

// Lines are the non-empty lines of stdout.
func (o Output) Lines() []string {
	out := []string{}
	for _, line := range strings.Split(o.Stdout, "\n") {
		if line = strings.TrimRight(line, "\r"); strings.TrimSpace(line) != "" {
			out = append(out, line)
		}
	}
	return out
}

// Command is one program to run: where, for how long, and with what added to
// its environment.
type Command struct {
	Name    string
	Args    []string
	Dir     string
	Timeout time.Duration
	Env     map[string]string
	Stdin   string
}

// Exec runs a command and captures both streams. A program the machine does
// not have, one that fails, and one that runs too long are all outcomes, not
// errors: the caller decides which of them matters.
func Exec(ctx context.Context, cmd Command) Output {
	timeout := cmd.Timeout
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	name := Resolve(cmd.Name)
	if name == "" {
		return Output{Code: 127, Stderr: cmd.Name + " is not installed"}
	}

	args := cmd.Args
	// git refuses to read a repository owned by another user unless it is
	// told the directory is safe. Staging runs as whoever launched the app,
	// against a folder that may have been made by an installer.
	if cmd.Dir != "" && baseName(name) == "git" {
		if abs, err := filepath.Abs(cmd.Dir); err == nil {
			args = append([]string{"-c", "safe.directory=" + abs}, args...)
		}
	}

	var stdout, stderr bytes.Buffer
	run := exec.CommandContext(ctx, name, args...)
	run.Dir = cmd.Dir
	run.Stdout = &stdout
	run.Stderr = &stderr
	if cmd.Stdin != "" {
		run.Stdin = strings.NewReader(cmd.Stdin)
	}
	if len(cmd.Env) > 0 {
		run.Env = mergeEnv(cmd.Env)
	} else {
		run.Env = append(os.Environ(), "PATH="+commandPath())
	}

	err := run.Run()
	out := Output{
		Stdout: RedactText(stdout.String()),
		Stderr: RedactText(stderr.String()),
	}
	if run.ProcessState != nil {
		out.Code = run.ProcessState.ExitCode()
	}
	if ctx.Err() == context.DeadlineExceeded {
		out.TimedOut = true
		if out.Code == 0 {
			out.Code = 124
		}
		return out
	}
	if err != nil && out.Code == 0 {
		out.Code = 1
		if out.Stderr == "" {
			out.Stderr = RedactText(err.Error())
		}
	}
	return out
}

func mergeEnv(extra map[string]string) []string {
	merged := map[string]string{}
	for _, entry := range os.Environ() {
		if key, value, ok := strings.Cut(entry, "="); ok {
			merged[key] = value
		}
	}
	for key, value := range extra {
		merged[key] = value
	}
	merged["PATH"] = commandPath()
	out := make([]string, 0, len(merged))
	for key, value := range merged {
		out = append(out, key+"="+value)
	}
	return out
}

func baseName(path string) string {
	name := strings.ToLower(filepath.Base(path))
	return strings.TrimSuffix(name, ".exe")
}
