package deploy

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

// Where a deployment run is kept.
//
// The Python used SQLite because Python ships with it. Go does not, and the
// only pure-Go SQLite needs a newer toolchain than the launcher installs — so
// the runs live in files instead: one directory each, the run itself as JSON
// and its events as a line-per-event log. The volumes are tens of runs and
// thousands of lines, the data outlives a restart, and anyone debugging a
// deployment can read it with `cat`.

// Store holds every deployment run. It is safe for concurrent use.
type Store struct {
	root string
	mu   sync.RWMutex
}

// NewStore opens the store under one directory, creating it if needed.
func NewStore(root string) (*Store, error) {
	if err := os.MkdirAll(filepath.Join(root, "runs"), 0o755); err != nil {
		return nil, err
	}
	return &Store{root: root}, nil
}

func (s *Store) runDir(id string) string { return filepath.Join(s.root, "runs", id) }

// CreateRun opens a new run in DRAFT.
func (s *Store) CreateRun(id, projectName, projectPath, stagedPath string) (*Run, error) {
	now := NowISO()
	run := &Run{
		ID: id, ProjectName: projectName, ProjectPath: projectPath,
		StagedPath: stagedPath, State: StateDraft,
		Spec: map[string]any{}, Plan: map[string]any{}, Readiness: map[string]any{},
		Monitor: map[string]any{}, Repo: map[string]any{},
		CreatedAt: now, UpdatedAt: now,
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := os.MkdirAll(s.runDir(id), 0o755); err != nil {
		return nil, err
	}
	return run, s.writeRun(run)
}

// GetRun reads one run, or nil when there is none.
func (s *Store) GetRun(id string) (*Run, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.readRun(id)
}

func (s *Store) readRun(id string) (*Run, error) {
	body, err := os.ReadFile(filepath.Join(s.runDir(id), "run.json"))
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var run Run
	if err := json.Unmarshal(body, &run); err != nil {
		return nil, err
	}
	return &run, nil
}

func (s *Store) writeRun(run *Run) error {
	run.UpdatedAt = NowISO()
	body, err := json.MarshalIndent(run, "", "  ")
	if err != nil {
		return err
	}
	return writeAtomic(filepath.Join(s.runDir(run.ID), "run.json"), body)
}

// ListRuns is the most recently touched runs first, which is the order the
// Studio shows them in.
func (s *Store) ListRuns(limit int) ([]Run, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	entries, err := os.ReadDir(filepath.Join(s.root, "runs"))
	if err != nil {
		if os.IsNotExist(err) {
			return []Run{}, nil
		}
		return nil, err
	}
	out := []Run{}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		run, err := s.readRun(entry.Name())
		if err != nil || run == nil {
			continue
		}
		out = append(out, *run)
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].UpdatedAt > out[j].UpdatedAt })
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

// Update writes the named fields onto a run. Only the fields a run is allowed
// to change are accepted; anything else is a caller mistake and is ignored
// rather than silently corrupting a row.
func (s *Store) Update(id string, fields map[string]any) (*Run, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.update(id, fields)
}

func (s *Store) update(id string, fields map[string]any) (*Run, error) {
	run, err := s.readRun(id)
	if err != nil || run == nil {
		return nil, orMissing(err)
	}
	for key, value := range fields {
		// Everything stored is redacted first: a run record is read back by
		// the Studio, exported as evidence, and attached to a support bundle.
		value = Redact(value)
		switch key {
		case "project_name":
			run.ProjectName = text(value)
		case "project_path":
			run.ProjectPath = text(value)
		case "staged_path":
			run.StagedPath = text(value)
		case "state":
			run.State = State(text(value))
		case "error":
			run.Error = text(value)
		case "spec":
			run.Spec = object(value)
		case "plan":
			run.Plan = object(value)
		case "readiness":
			run.Readiness = object(value)
		case "monitor":
			run.Monitor = object(value)
		case "repo":
			run.Repo = object(value)
		}
	}
	return run, s.writeRun(run)
}

// Transition moves a run to a new state, refusing a move the state machine
// does not allow. Staying where it is is always allowed, so a stage that
// re-reports its own state is not an error.
func (s *Store) Transition(id string, target State, fields map[string]any) (*Run, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	run, err := s.readRun(id)
	if err != nil || run == nil {
		return nil, orMissing(err)
	}
	if target != run.State && !Allowed[run.State][target] {
		return nil, fmt.Errorf("invalid deployment state transition: %s -> %s", run.State, target)
	}
	if fields == nil {
		fields = map[string]any{}
	}
	fields["state"] = string(target)
	return s.update(id, fields)
}

// --- events -----------------------------------------------------------------------------

// AddEvent appends one line to the run's log.
func (s *Store) AddEvent(id string, event Event) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if event.Timestamp == "" {
		event.Timestamp = NowISO()
	}
	if event.RunID == "" {
		event.RunID = id
	}
	if event.Percent < 0 {
		event.Percent = 0
	}
	if event.Percent > 100 {
		event.Percent = 100
	}
	event.Data = object(Redact(event.Data))
	event.Message = text(Redact(event.Message))

	path := filepath.Join(s.runDir(id), "events.jsonl")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer file.Close()

	body, err := json.Marshal(event)
	if err != nil {
		return err
	}
	_, err = file.Write(append(body, '\n'))
	return err
}

// Events are the run's log after the given id, which is how the Studio polls
// for what happened since it last looked.
func (s *Store) Events(id string, after int64, limit int) ([]Event, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	body, err := os.ReadFile(filepath.Join(s.runDir(id), "events.jsonl"))
	if os.IsNotExist(err) {
		return []Event{}, nil
	}
	if err != nil {
		return nil, err
	}
	out := []Event{}
	for i, line := range strings.Split(string(body), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var event Event
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			continue
		}
		// The line number is the id: it is stable, ordered, and needs nothing
		// stored alongside it.
		event.EventID = int64(i + 1)
		if event.EventID <= after {
			continue
		}
		out = append(out, event)
		if limit > 0 && len(out) >= limit {
			break
		}
	}
	return out, nil
}

// --- artifacts and evidence ------------------------------------------------------------

// SetArtifacts replaces the record of what this run generated.
func (s *Store) SetArtifacts(id string, records []Artifact) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	sort.SliceStable(records, func(i, j int) bool { return records[i].Path < records[j].Path })
	return s.writeJSON(id, "artifacts.json", records)
}

func (s *Store) Artifacts(id string) ([]Artifact, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []Artifact{}
	err := s.readJSON(id, "artifacts.json", &out)
	return out, err
}

// AddEvidence records one proof that the deployment works.
func (s *Store) AddEvidence(id, name, path string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	rows := []Evidence{}
	if err := s.readJSON(id, "evidence.json", &rows); err != nil {
		return err
	}
	next := int64(len(rows) + 1)
	rows = append(rows, Evidence{ID: next, Name: name, Path: path, CreatedAt: NowISO()})
	return s.writeJSON(id, "evidence.json", rows)
}

func (s *Store) Evidence(id string) ([]Evidence, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []Evidence{}
	err := s.readJSON(id, "evidence.json", &out)
	return out, err
}

// VerifyEvidence marks one piece of evidence as checked by a person.
func (s *Store) VerifyEvidence(id string, evidenceID int64, verified bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	rows := []Evidence{}
	if err := s.readJSON(id, "evidence.json", &rows); err != nil {
		return err
	}
	for i := range rows {
		if rows[i].ID == evidenceID {
			rows[i].Verified = verified
		}
	}
	return s.writeJSON(id, "evidence.json", rows)
}

func (s *Store) writeJSON(id, name string, value any) error {
	body, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(s.runDir(id), 0o755); err != nil {
		return err
	}
	return writeAtomic(filepath.Join(s.runDir(id), name), body)
}

func (s *Store) readJSON(id, name string, into any) error {
	body, err := os.ReadFile(filepath.Join(s.runDir(id), name))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if len(body) == 0 {
		return nil
	}
	return json.Unmarshal(body, into)
}

// writeAtomic writes through a temporary file, so a crash mid-write leaves the
// previous record rather than half of the new one.
func writeAtomic(path string, body []byte) error {
	temp := path + ".tmp"
	if err := os.WriteFile(temp, body, 0o644); err != nil {
		return err
	}
	return os.Rename(temp, path)
}

func orMissing(err error) error {
	if err != nil {
		return err
	}
	return fmt.Errorf("run not found")
}

func text(value any) string {
	if value == nil {
		return ""
	}
	if s, ok := value.(string); ok {
		return s
	}
	return fmt.Sprint(value)
}

// object turns whatever was handed in into a plain JSON object, so a typed
// struct and a map are stored the same way.
func object(value any) map[string]any {
	if value == nil {
		return map[string]any{}
	}
	if m, ok := value.(map[string]any); ok {
		return m
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return map[string]any{}
	}
	out := map[string]any{}
	if err := json.Unmarshal(raw, &out); err != nil {
		return map[string]any{}
	}
	return out
}
