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
// reaches anything that stores or displays it — the one exception being a
// caller that asks for it Plain because it is deciding with it rather than
// showing it.

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

// Output is what one command left behind. Both streams arrive redacted, unless
// the Command asked for them Plain — in which case they are exactly what the
// program wrote and must not be stored or displayed as they are.
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

	// Plain keeps the output exactly as the program wrote it. Set it only
	// when the caller is deciding something with the output rather than
	// showing it to anybody: redaction rewrites text, and a decision made on
	// rewritten text is a decision made on something that never happened.
	// Whatever a Plain command returns is secret until it has been through
	// RedactText.
	Plain bool
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
	out := Output{Stdout: stdout.String(), Stderr: stderr.String()}
	if !cmd.Plain {
		out.Stdout, out.Stderr = RedactText(out.Stdout), RedactText(out.Stderr)
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
			out.Stderr = err.Error()
			if !cmd.Plain {
				out.Stderr = RedactText(out.Stderr)
			}
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

// --- what the machine has, and how to sign in --------------------------------------------

// ToolStatus is which of the command-line programs a deployment needs are
// installed, and what version. The Studio's setup panel is built from this.
func ToolStatus(ctx context.Context) map[string]any {
	status := map[string]any{}
	for _, name := range sortedKeys(tools) {
		path := Resolve(name)
		item := map[string]any{"installed": path != "", "path": path, "version": ""}
		if path != "" {
			out := Exec(ctx, Command{Name: tools[name][0], Args: tools[name][1:],
				Timeout: 8 * time.Second})
			if line := firstLine(out.Text()); line != "" {
				item["version"] = clip(line, 160)
			}
			if name == "node" {
				item["ready"] = out.OK()
			}
		}
		status[name] = item
	}
	status["ports"] = map[string]any{"7834_available": true}
	return status
}

func firstLine(text string) string {
	line, _, _ := strings.Cut(strings.TrimSpace(text), "\n")
	return strings.TrimSpace(line)
}

// logins are the sign-in commands the Studio can start for the customer. Each
// one opens the tool's own flow — the agent never handles a password.
var logins = map[string][]string{
	"aws-configure":     {"aws", "configure", "sso"},
	"aws-login":         {"aws", "sso", "login"},
	"aws-console-login": {"aws", "login", "--no-cli-pager"},
	"vercel":            {"vercel", "login"},
	"github": {"gh", "auth", "login", "--hostname", "github.com", "--web",
		"--clipboard", "--git-protocol", "https"},
	"ollama": {"ollama", "signin"},
}

// StartLogin opens a sign-in in a window of its own and returns immediately:
// the customer finishes it in the tool, and the Studio polls to find out when
// they have.
func StartLogin(tool, profile, region string) (string, error) {
	args, known := logins[tool]
	if !known {
		return "", badRequest("Unsupported login tool")
	}
	args = append([]string{}, args...)

	switch tool {
	case "aws-configure", "aws-login":
		if profile != "" {
			args = append(args, "--profile", profile)
		}
	case "aws-console-login":
		name := profile
		if name == "" {
			name = "deployment-agent"
		}
		where := region
		if where == "" {
			where = DefaultRegion
		}
		args = append(args, "--profile", name, "--region", where)
	}

	name := Resolve(args[0])
	if name == "" {
		return "", badRequest(args[0] + " is not installed")
	}
	command := exec.Command(name, args[1:]...)
	command.Env = append(os.Environ(), "PATH="+commandPath())
	detach(command)
	if err := command.Start(); err != nil {
		return "", err
	}
	// Nothing waits for it: the customer's browser is where this finishes.
	go func() { _ = command.Wait() }()

	if profile == "" {
		profile = "deployment-agent"
	}
	return profile, nil
}
