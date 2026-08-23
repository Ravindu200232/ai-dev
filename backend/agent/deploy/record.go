package deploy

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
)

// A deployment leaves its record inside the project it deployed.
//
// The store is the authority while a run is happening, but the project folder
// is what a person keeps, copies and looks at a year later — so when a run
// finishes, what it did is written down beside the code it deployed. The
// Studio's project list reads it too: that is how a project knows it has been
// deployed before.

// recordDir is where a project keeps its deployment record.
func recordDir(projectPath string) string {
	return filepath.Join(projectPath, ".agentforge", "deploy")
}

// Adopt writes a finished run into the project it deployed.
func Adopt(store *Store, run *Run) error {
	if run == nil || run.ProjectPath == "" {
		return nil
	}
	dir := recordDir(run.ProjectPath)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}

	events, err := store.Events(run.ID, 0, 0)
	if err != nil {
		events = []Event{}
	}
	// Masked as well as redacted. This record sits in the customer's project
	// folder, which is the folder that gets committed and pushed, so it holds
	// no more than what the Studio itself is shown: no account number, no
	// image digest.
	files := map[string]any{
		"run.json":     mask(Redact(run)),
		"events.json":  mask(Redact(map[string]any{"events": events})),
		"monitor.json": mask(Redact(run.Monitor)),
		"link.json": map[string]any{
			"run_id":     run.ID,
			"adopted_at": NowISO(),
			"state":      string(run.State),
			"target":     TargetOf(run),
		},
	}
	for name, value := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(indented(value)), 0o644); err != nil {
			return err
		}
	}
	return nil
}

// Retire moves a torn-down deployment's record out of the way, keeping every
// byte of it: a customer who deletes a deployment is deleting cloud resources,
// not the evidence of what was there.
func Retire(projectPath, runID string) error {
	live := recordDir(projectPath)
	if info, err := os.Stat(live); err != nil || !info.IsDir() {
		return nil
	}
	name := runID
	if name == "" {
		name = "unknown"
	}

	archive := filepath.Join(projectPath, ".agentforge", "deploy-archive", name)
	if err := os.MkdirAll(filepath.Dir(archive), 0o755); err != nil {
		return err
	}
	for suffix := 0; ; suffix++ {
		candidate := archive
		if suffix > 0 {
			candidate = archive + "-" + strconv.Itoa(suffix)
		}
		if _, err := os.Stat(candidate); os.IsNotExist(err) {
			archive = candidate
			break
		}
		if suffix > 50 {
			return nil
		}
	}
	if err := os.Rename(live, archive); err != nil {
		return err
	}

	return os.WriteFile(
		filepath.Join(projectPath, ".agentforge", "deploy-deleted.json"),
		[]byte(indented(map[string]any{
			"run_id":     runID,
			"deleted_at": NowISO(),
			"archive":    filepath.Base(archive),
		})), 0o644)
}

// Deleted is the note Retire left, for a project whose deployment was torn
// down and which therefore has no record any more.
func Deleted(projectPath string) map[string]any {
	body, err := os.ReadFile(filepath.Join(projectPath, ".agentforge", "deploy-deleted.json"))
	if err != nil {
		return nil
	}
	value := map[string]any{}
	if json.Unmarshal(body, &value) != nil || len(value) == 0 {
		return nil
	}
	return value
}

// HasRecord reports whether a project has been deployed before, which is what
// the Studio's project list shows a badge for.
func HasRecord(projectPath string) bool {
	info, err := os.Stat(filepath.Join(recordDir(projectPath), "run.json"))
	return err == nil && !info.IsDir()
}
