// Package deploy takes a project the builder produced and puts it on the
// internet: it reads what the project actually is, plans the infrastructure,
// generates the files that describe it, runs the deployment and then watches
// what it deployed.
//
// It ran as a Python service on port 7834 until this port.
package deploy

import (
	"crypto/rand"
	"encoding/hex"
	"strconv"
	"time"
)

// State is where one deployment run has got to. Every transition is checked
// against Allowed: a run cannot go from DRAFT to LIVE because something
// forgot a step.
type State string

const (
	StateDraft         State = "DRAFT"
	StateAnalyzing     State = "ANALYZING"
	StateReviewReady   State = "REVIEW_READY"
	StateBootstrapping State = "BOOTSTRAPPING"
	StateCIRunning     State = "CI_RUNNING"
	StateDeploying     State = "DEPLOYING"
	StateValidating    State = "VALIDATING"
	StateLive          State = "LIVE"
	StateFailed        State = "FAILED"
	StateRolledBack    State = "ROLLED_BACK"
	StateDestroyed     State = "DESTROYED"
	StateCancelled     State = "CANCELLED"
)

// Allowed is the state machine. A missing entry means nothing may follow.
var Allowed = map[State]map[State]bool{
	StateDraft:         set(StateAnalyzing),
	StateAnalyzing:     set(StateReviewReady, StateFailed, StateCancelled),
	StateReviewReady:   set(StateBootstrapping, StateFailed, StateDestroyed, StateCancelled),
	StateBootstrapping: set(StateCIRunning, StateFailed, StateRolledBack, StateCancelled),
	StateCIRunning:     set(StateDeploying, StateValidating, StateFailed, StateRolledBack, StateCancelled),
	StateDeploying:     set(StateValidating, StateFailed, StateRolledBack, StateCancelled),
	StateValidating:    set(StateLive, StateFailed, StateRolledBack, StateCancelled),
	StateLive:          set(StateDeploying, StateFailed, StateRolledBack, StateDestroyed),
	StateFailed:        set(StateAnalyzing, StateBootstrapping, StateRolledBack, StateDestroyed),
	StateRolledBack:    set(StateBootstrapping, StateDestroyed),
	StateCancelled:     set(StateBootstrapping, StateDestroyed),
	StateDestroyed:     {},
}

func set(states ...State) map[State]bool {
	out := make(map[State]bool, len(states))
	for _, s := range states {
		out[s] = true
	}
	return out
}

// Active are the states where something is happening right now. A run in one
// of these is not safe to start a second deployment for.
var Active = set(StateBootstrapping, StateCIRunning, StateDeploying, StateValidating)

// Target is where a project is being put.
const (
	TargetEC2    = "aws_ec2"
	TargetECS    = "aws_ecs"
	TargetVercel = "vercel"
)

// Run is one deployment, from reading the project to watching it run.
type Run struct {
	ID          string `json:"id"`
	ProjectName string `json:"project_name"`
	ProjectPath string `json:"project_path"`
	StagedPath  string `json:"staged_path"`
	State       State  `json:"state"`

	Spec      map[string]any `json:"spec"`
	Plan      map[string]any `json:"plan"`
	Readiness map[string]any `json:"readiness"`
	Monitor   map[string]any `json:"monitor"`
	Repo      map[string]any `json:"repo"`

	Error     string `json:"error"`
	CreatedAt string `json:"created_at"`
	UpdatedAt string `json:"updated_at"`
}

// Event is one line of what happened. Every field here is read by the Studio's
// console and pipeline view, so none of them may be renamed: it groups by
// stage, filters by type, colours by status and fills the bar from percent.
type Event struct {
	EventID   int64          `json:"event_id"`
	RunID     string         `json:"run_id"`
	Type      string         `json:"type"`
	Stage     string         `json:"stage"`
	Status    string         `json:"status"`
	Percent   int            `json:"percent"`
	Message   string         `json:"message"`
	Data      map[string]any `json:"data"`
	Timestamp string         `json:"timestamp"`
}

// The event types the console separates into channels.
const (
	EventStep      = "step"
	EventState     = "state"
	EventLog       = "log"
	EventTerminal  = "terminal"
	EventPrompt    = "prompt"
	EventMonitor   = "monitor"
	EventChangeSet = "change_set"
	EventError     = "error"
)

// The statuses a step reports. The console reads "complete" as done and
// "failed" as an error; anything else is in progress.
const (
	StatusRunning  = "running"
	StatusComplete = "complete"
	StatusFailed   = "failed"
)

// Artifact is one file the run generated, with the hash of what was there
// before it — so nothing the agent wrote can be mistaken for the customer's
// own work, and every file it touched can be put back.
type Artifact struct {
	Path           string `json:"path"`
	Kind           string `json:"kind"`
	SHA256         string `json:"sha256"`
	Size           int64  `json:"size"`
	OriginalExists bool   `json:"original_exists"`
	OriginalSHA256 string `json:"original_sha256,omitempty"`
}

// Evidence is something recorded as proof the deployment really works.
type Evidence struct {
	ID        int64  `json:"id"`
	Name      string `json:"name"`
	Path      string `json:"path"`
	Verified  bool   `json:"verified"`
	CreatedAt string `json:"created_at"`
}

// EnvVar is one variable the project needs, and where it may come from.
type EnvVar struct {
	Name     string   `json:"name"`
	Required bool     `json:"required"`
	Secret   bool     `json:"secret"`
	Scope    string   `json:"scope"`
	Sources  []string `json:"sources"`

	Resolution      string `json:"resolution,omitempty"`
	ValuePresent    bool   `json:"value_present,omitempty"`
	Public          bool   `json:"public,omitempty"`
	DevelopmentOnly bool   `json:"development_only,omitempty"`
}

// Service is one thing in the project that can be started: its framework, how
// it is built, how it is run, and what it needs.
type Service struct {
	Name           string              `json:"name"`
	Root           string              `json:"root"`
	Framework      string              `json:"framework"`
	Version        string              `json:"version"`
	PackageManager string              `json:"package_manager"`
	InstallCommand string              `json:"install_command"`
	BuildCommand   string              `json:"build_command"`
	StartCommand   string              `json:"start_command"`
	Port           int                 `json:"port"`
	HealthPath     string              `json:"health_path"`
	Routes         []map[string]string `json:"routes"`
	Environment    []EnvVar            `json:"environment"`
	Dependencies   []string            `json:"dependencies"`
	Scripts        map[string]string   `json:"scripts"`
	Lockfile       string              `json:"lockfile"`
	HasMongoDB     bool                `json:"has_mongodb"`
	HasBetterAuth  bool                `json:"has_better_auth"`
}

// Repository is what git says about the project.
type Repository struct {
	IsGit      bool     `json:"is_git"`
	Branch     string   `json:"branch"`
	Remote     string   `json:"remote"`
	DirtyFiles []string `json:"dirty_files"`
}

// Spec is what the project actually is, read from its own files.
type Spec struct {
	Name       string     `json:"name"`
	SourcePath string     `json:"source_path"`
	StagedPath string     `json:"staged_path"`
	Services   []Service  `json:"services"`
	Repository Repository `json:"repository"`
	Warnings   []string   `json:"warnings"`
}

// Plan is how the project will be deployed. Almost none of it is the model's
// to decide — see plan.go — but it is written in one place so the whole
// decision can be read at once.
type Plan struct {
	ProjectSlug    string `json:"project_slug"`
	PrimaryService string `json:"primary_service"`
	Region         string `json:"region"`
	Infrastructure string `json:"infrastructure"`
	Topology       string `json:"topology"`
	Database       string `json:"database"`

	SourcePatches   []map[string]string `json:"source_patches"`
	Risks           []string            `json:"risks"`
	Recommendations []string            `json:"recommendations"`

	ServiceRoot    string `json:"service_root"`
	PackageManager string `json:"package_manager"`
	InstallCommand string `json:"install_command"`
	BuildCommand   string `json:"build_command"`
	StartCommand   string `json:"start_command"`
	Port           int    `json:"port"`
	HealthPath     string `json:"health_path"`

	EnvironmentContract []EnvVar       `json:"environment_contract"`
	RuntimeStrategy     string         `json:"runtime_strategy"`
	GitHubJobs          []string       `json:"github_jobs"`
	AWSSizing           map[string]any `json:"aws_sizing"`
	RequiredPatches     []string       `json:"required_patches"`
	RepairActions       []string       `json:"repair_actions"`

	Model       string         `json:"model"`
	ModelUsed   bool           `json:"model_used"`
	Target      string         `json:"target"`
	Environment map[string]any `json:"environment"`
}

// Gate is one check that has to pass before a deployment is called done.
type Gate struct {
	ID       string         `json:"id"`
	Required bool           `json:"required"`
	Status   string         `json:"status"`
	Message  string         `json:"message"`
	Evidence map[string]any `json:"evidence,omitempty"`
}

const (
	GatePassed = "passed"
	GateFailed = "failed"
)

// statusError is a failure that already knows what the HTTP surface should
// say about it, so a rule lives with the code that enforces it rather than
// being restated in every handler.
type statusError struct {
	Status  int
	Message string
}

func (e statusError) Error() string { return e.Message }

func badRequest(message string) error { return statusError{Status: 400, Message: message} }

func notFound(message string) error { return statusError{Status: 404, Message: message} }

func conflict(message string) error { return statusError{Status: 409, Message: message} }

// NowISO is the timestamp every row is stamped with.
func NowISO() string { return time.Now().UTC().Format(time.RFC3339Nano) }

// NewRunID is the identifier one deployment is known by.
func NewRunID() string {
	var raw [12]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "run_" + hex.EncodeToString([]byte(NowISO()))[:24]
	}
	return "run_" + hex.EncodeToString(raw[:])
}

func itoa(n int) string { return strconv.Itoa(n) }
