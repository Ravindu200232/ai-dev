package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"

	"agentforge/agent/core"
	"agentforge/agent/deploy"
)

// The deployment agent's seam into the server. It is part of this binary, so
// every route here hands the request straight to it: the /deploy/* passthrough
// the Studio polls, the project-shaped view its panel renders, and the one
// button that starts the whole thing.

// deployStart is the Studio's Deploy button: one project, one target, and
// everything else read from the settings the customer already filled in.
func (s *Server) deployStart(w http.ResponseWriter, r *http.Request, body []byte) {
	if s.Deploy == nil {
		writeJSON(w, 503, map[string]any{"error": "the deployment agent is not running"})
		return
	}
	var req struct {
		Project           string `json:"project"`
		Target            string `json:"target"`
		ValidateContainer *bool  `json:"validate_container"`
	}
	if err := json.Unmarshal(body, &req); err != nil || strings.TrimSpace(req.Project) == "" {
		writeJSON(w, 400, map[string]any{"error": "a deployment needs a project"})
		return
	}
	dir := s.Paths.Project(req.Project)
	if _, err := os.Stat(dir); err != nil {
		writeJSON(w, 404, map[string]any{"error": "no such project: " + req.Project})
		return
	}

	saved := core.LoadSettings()
	runID, err := s.Deploy.Deploy(context.WithoutCancel(r.Context()), deploy.StudioRequest{
		Path:          dir,
		Target:        req.Target,
		ValidateBuild: req.ValidateContainer == nil || *req.ValidateContainer,
		AWSProfile:    stringOf(saved, "aws_profile"),
		Region:        stringOf(saved, "aws_region"),
		MongoURI:      stringOf(saved, "mongodb_uri"),
		VercelToken:   stringOf(saved, "vercel_token"),
	})
	if err != nil {
		writeJSON(w, 400, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, 202, map[string]any{"run_id": runID, "state": "ANALYZING", "project": req.Project})
}

// serveDeploy hands a /deploy/* request to the deployment agent, which is part
// of this binary rather than a service on a port of its own.
func (s *Server) serveDeploy(w http.ResponseWriter, r *http.Request, path string) {
	if s.Deploy == nil {
		writeJSON(w, 503, map[string]any{"error": "the deployment agent is not running"})
		return
	}
	s.Deploy.Handler().ServeHTTP(w, rerouted(r, "/api"+path[len("/deploy"):]))
}

// deployJob runs one deployment request as a job, because the Studio polls
// rather than holding a request open for a deployment that takes minutes.
func (s *Server) deployJob(method, path string, body []byte) (int, any, error) {
	if s.Deploy == nil {
		return 503, map[string]any{"error": "the deployment agent is not running"}, nil
	}
	request, err := http.NewRequest(method, path, bytes.NewReader(body))
	if err != nil {
		return 400, map[string]any{"error": err.Error()}, nil
	}
	request.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.Deploy.Handler().ServeHTTP(recorder, request)

	var value any
	if err := json.Unmarshal(recorder.Body.Bytes(), &value); err != nil {
		value = map[string]any{"raw": recorder.Body.String()}
	}
	return recorder.Code, value, nil
}

// deployStatus is what the Studio shows about the agent itself. It is part of
// this process now, so it is running whenever this is.
func (s *Server) deployStatus() map[string]any {
	if s.Deploy == nil {
		return map[string]any{"state": "off", "running": false, "in_process": true}
	}
	return map[string]any{"state": "ready", "running": true, "in_process": true}
}

// deployResults is what the deploy panel reads on every render: whether the
// agent is up, what is deploying now, and what happened last time.
func (s *Server) deployResults(project string) map[string]any {
	dir := s.Paths.Project(project)
	if _, err := os.Stat(dir); err != nil {
		return map[string]any{"error": "no such project: " + project}
	}
	out := map[string]any{
		"project": project,
		"agent":   s.deployStatus(),
		"live":    nil,
		"have":    map[string]bool{"last": false},
	}
	if s.Deploy == nil {
		return out
	}
	live, last := s.Deploy.ForProject(dir)
	if live != nil {
		out["live"] = live
	}
	if last != nil {
		out["last"] = last
		out["have"] = map[string]bool{"last": true}
	}
	return out
}
