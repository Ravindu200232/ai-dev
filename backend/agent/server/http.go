package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"agentforge/agent/app"
	"agentforge/agent/core"
	"agentforge/agent/deploy"
)

// The HTTP surface studio/lib/api.js talks to. Everything under
// /__agentforge/api is ours; anything else is the generated app's dev server.

const uiPrefix = "/__agentforge"

// QAPDFFunc renders a QA report as a PDF. cmd/agentforge supplies it.
type QAPDFFunc func(project string) ([]byte, error)

// ServeHTTP starts the API listener until the context is cancelled.
func (s *Server) ServeHTTP(ctx context.Context, addr string, qaPDF QAPDFFunc) error {
	s.qaPDF = qaPDF
	srv := &http.Server{
		Addr:              addr,
		Handler:           s.router(),
		ReadHeaderTimeout: 30 * time.Second,
	}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
	}()
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

func (s *Server) router() http.Handler {
	dev := devProxy()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodOptions {
			cors(w)
			w.WriteHeader(http.StatusOK)
			return
		}
		path := r.URL.Path
		if path != uiPrefix && !strings.HasPrefix(path, uiPrefix+"/") {
			dev.ServeHTTP(w, r) // the preview
			return
		}
		rest := strings.TrimPrefix(path, uiPrefix)
		if rest == "" {
			rest = "/"
		}
		if !strings.HasPrefix(rest, "/api/") {
			s.notTheUI(w)
			return
		}
		api := rest[len("/api"):]
		cors(w)
		switch r.Method {
		case http.MethodGet:
			s.apiGet(w, r, api)
		case http.MethodPost:
			s.apiPost(w, r, api)
		default:
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
		}
	})
}

func (s *Server) notTheUI(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusNotFound)
	_, _ = io.WriteString(w, `<!doctype html><meta charset=utf-8><title>AgentForge</title>`+
		`<body style="font:14px/1.6 system-ui;max-width:34rem;margin:12vh auto;padding:0 1.5rem">`+
		`<h1 style="font-size:1.1rem">AgentForge is not served on this port</h1>`+
		`<p>This is the backend API on :`+strconv.Itoa(core.UIPort)+`. The Studio runs separately — `+
		`<a href="http://localhost:3000/__agentforge">http://localhost:3000/__agentforge</a></p>`)
}

// --- GET ---------------------------------------------------------------------

func (s *Server) apiGet(w http.ResponseWriter, r *http.Request, path string) {
	switch {
	case path == "/projects":
		writeJSON(w, 200, s.listProjects())
	case path == "/models":
		writeJSON(w, 200, s.LLM.Catalog(r.Context()))
	case path == "/mongo":
		writeJSON(w, 200, s.Mongo.Status(r.Context()))
	case path == "/settings":
		writeJSON(w, 200, s.settingsSummary(r.Context()))
	case path == "/image-check":
		writeJSON(w, 200, s.Pictures.Check())
	case path == "/srs-status":
		writeJSON(w, 200, map[string]any{"running": true, "in_process": true})
	case path == "/deploy-status":
		writeJSON(w, 200, s.deployStatus())
	case strings.HasPrefix(path, "/files/"):
		writeJSON(w, 200, s.projectFiles(trimSeg(path, "/files/")))
	case strings.HasPrefix(path, "/qa/"):
		writeJSON(w, 200, s.qaResults(trimSeg(path, "/qa/")))
	case strings.HasPrefix(path, "/srs-results/"):
		writeJSON(w, 200, s.srsResults(trimSeg(path, "/srs-results/")))
	case strings.HasPrefix(path, "/deploy-results/"):
		writeJSON(w, 200, s.deployResults(trimSeg(path, "/deploy-results/")))
	case strings.HasPrefix(path, "/qa-pdf/"):
		s.sendQAPDF(w, trimSeg(path, "/qa-pdf/"))
	case strings.HasPrefix(path, "/srs-pdf/"):
		project := trimSeg(path, "/srs-pdf/")
		s.sendFile(w, filepath.Join(s.Paths.Meta(project), "srs", "SRS_latest.pdf"),
			"application/pdf", `inline; filename="SRS.pdf"`, "no SRS PDF for this project")
	case strings.HasPrefix(path, "/jobs/"):
		writeJSON(w, 200, s.jobs.Poll(trimSeg(path, "/jobs/")))
	case strings.HasPrefix(path, "/deploy/jobs/"):
		writeJSON(w, 200, s.jobs.Poll(trimSeg(path, "/deploy/jobs/")))
	case strings.HasPrefix(path, "/srs/"):
		s.serveSRS(w, r, path)
	case strings.HasPrefix(path, "/deploy/"):
		s.serveDeploy(w, r, path)
	default:
		writeJSON(w, 404, map[string]any{"error": "unknown endpoint " + path})
	}
}

// --- POST --------------------------------------------------------------------

func (s *Server) apiPost(w http.ResponseWriter, r *http.Request, path string) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 64<<20))
	if err != nil {
		writeJSON(w, 400, map[string]any{"error": "could not read the request"})
		return
	}

	// The instruction endpoints are the HTTP fallback for the socket, so they
	// share one decoder and one dispatcher with studio/lib/ws.js.
	if kind, ok := fallbackKind[path]; ok {
		msg, err := ParseMessage(body)
		if err != nil || msg.Type == "" {
			msg.Type = kind
			msg.Raw = map[string]any{}
			_ = json.Unmarshal(body, &msg.Raw)
			msg.Raw["type"] = kind
		}
		if err := s.Dispatch(msg); err != nil {
			writeJSON(w, 409, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "started": msg.Type})
		return
	}

	switch path {
	case "/build/cancel":
		writeJSON(w, 200, map[string]any{"ok": true, "stopped": s.CancelActive()})

	case "/settings":
		var patch map[string]any
		if err := json.Unmarshal(body, &patch); err != nil {
			writeJSON(w, 400, map[string]any{"error": "settings must be an object"})
			return
		}
		if err := core.SaveSettings(patch); err != nil {
			writeJSON(w, 500, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, 200, s.settingsSummary(r.Context()))

	case "/save-file":
		var req struct{ Project, Path, Content string }
		if err := json.Unmarshal(body, &req); err != nil {
			writeJSON(w, 400, map[string]any{"error": "expected project, path and content"})
			return
		}
		sh := core.NewShell(s.Paths.Project(req.Project))
		if err := sh.Write(req.Path, req.Content); err != nil {
			writeJSON(w, 400, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "path": req.Path})

	case "/delete-project":
		var req struct{ Project string }
		_ = json.Unmarshal(body, &req)
		dir := s.Paths.Project(req.Project)
		if core.SafeName(req.Project) == "" {
			writeJSON(w, 400, map[string]any{"error": "name the project to delete"})
			return
		}
		if err := os.RemoveAll(dir); err != nil {
			writeJSON(w, 500, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "project": core.SafeName(req.Project)})

	case "/undo":
		var req struct{ Project, ID string }
		_ = json.Unmarshal(body, &req)
		restored, err := s.undo(req.Project, req.ID)
		if err != nil {
			writeJSON(w, 400, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "files": restored})

	case "/upload-project":
		code, payload := s.uploadProject(body)
		writeJSON(w, code, payload)

	case "/mongo/prefetch":
		// Installing mongod takes minutes, so it answers immediately and the
		// Studio watches /mongo for the progress.
		go s.Mongo.Prefetch(context.Background())
		writeJSON(w, 200, s.Mongo.Status(r.Context()))

	case "/discard-srs":
		var req struct {
			SRSID string `json:"srs_id"`
		}
		_ = json.Unmarshal(body, &req)
		s.serveSRS(w, withBody(r, body), "/srs/projects/"+req.SRSID+"/discard")

	case "/tune":
		s.tune(w, r, body)

	case "/jobs":
		var req struct {
			Path   string          `json:"path"`
			Method string          `json:"method"`
			Body   json.RawMessage `json:"body"`
		}
		if err := json.Unmarshal(body, &req); err != nil || req.Path == "" {
			writeJSON(w, 400, map[string]any{"error": "a job needs a path"})
			return
		}
		if !localJobPaths[req.Path] {
			writeJSON(w, 400, map[string]any{"error": req.Path + " cannot be run as a job"})
			return
		}
		started := s.jobs.Start(req.Path, func() (int, any, error) {
			return s.localJob(req.Path, req.Body)
		})
		writeJSON(w, 200, map[string]any{"job_id": started.ID, "status": "running", "path": req.Path})

	case "/deploy/jobs":
		var req struct {
			Path   string          `json:"path"`
			Method string          `json:"method"`
			Body   json.RawMessage `json:"body"`
		}
		if err := json.Unmarshal(body, &req); err != nil || req.Path == "" {
			writeJSON(w, 400, map[string]any{"error": "a job needs a path"})
			return
		}
		method := req.Method
		if method == "" {
			method = http.MethodPost
		}
		started := s.jobs.Start(req.Path, func() (int, any, error) {
			return s.deployJob(method, "/api"+req.Path, req.Body)
		})
		writeJSON(w, 200, map[string]any{"job_id": started.ID, "status": "running", "path": req.Path})

	// The Deploy button. It used to proxy to a route with no handler, which
	// is why it did nothing at all.
	case "/deploy-start":
		s.deployStart(w, r, body)

	case "/image-start":
		writeJSON(w, 200, s.Pictures.Start(r.Context()))

	case "/image-upload":
		var req app.UploadRequest
		if err := json.Unmarshal(body, &req); err != nil {
			writeJSON(w, 400, map[string]any{"error": "expected an uploaded file"})
			return
		}
		saved, err := s.Pictures.SaveUpload(req)
		if err != nil {
			writeJSON(w, 400, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, 200, saved)

	case "/attach":
		var req app.UploadRequest
		if err := json.Unmarshal(body, &req); err != nil {
			writeJSON(w, 400, map[string]any{"error": "expected an attachment"})
			return
		}
		got, err := app.Attach(req)
		if err != nil {
			writeJSON(w, 400, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, 200, got)

	default:
		if strings.HasPrefix(path, "/open/") {
			writeJSON(w, 200, s.openProject(trimSeg(path, "/open/")))
			return
		}
		if strings.HasPrefix(path, "/srs/") {
			s.serveSRS(w, withBody(r, body), path)
			return
		}
		if strings.HasPrefix(path, "/deploy/") {
			s.serveDeploy(w, withBody(r, body), path)
			return
		}
		writeJSON(w, 404, map[string]any{"error": "unknown endpoint " + path})
	}
}

// fallbackKind mirrors HTTP_FALLBACK in studio/lib/api.js.
var fallbackKind = map[string]string{
	"/agent-build":  "agent_build",
	"/agent-update": "agent_update",
	"/resume":       "agent_resume",
	"/feature":      "feature",
	"/element-edit": "element_edit",
	"/pencil-edit":  "pencil_edit",
	"/image-edit":   "image_edit",
	"/image-swap":   "image_swap",
}

var localJobPaths = map[string]bool{
	"/tune": true, "/image": true, "/logo-prompt": true, "/themes": true,
}

// localJob runs one of our own endpoints in the background for the poller.
func (s *Server) localJob(path string, body json.RawMessage) (int, any, error) {
	switch path {
	case "/tune":
		var req struct {
			Prompt  string `json:"prompt"`
			Project string `json:"project"`
			Route   string `json:"route"`
		}
		_ = json.Unmarshal(body, &req)
		out, err := s.tuned(context.Background(), req.Prompt, req.Route)
		if err != nil {
			return 200, map[string]any{"prompt": req.Prompt}, nil // fall back to what was typed
		}
		return 200, map[string]any{"prompt": out}, nil

	case "/image":
		var req app.ImageRequest
		if err := json.Unmarshal(body, &req); err != nil {
			return 400, map[string]any{"error": "expected a prompt"}, nil
		}
		drawn, err := s.Pictures.Generate(context.Background(), req)
		if err != nil {
			return 502, map[string]any{"error": err.Error()}, nil
		}
		return 200, drawn, nil

	case "/logo-prompt":
		var req struct {
			Prompt string `json:"prompt"`
		}
		_ = json.Unmarshal(body, &req)
		out, err := app.LogoPrompt(context.Background(), s.LLM, req.Prompt)
		if err != nil {
			return 502, map[string]any{"error": err.Error()}, nil
		}
		return 200, map[string]any{"prompt": out}, nil

	case "/themes":
		var req app.ThemeRequest
		_ = json.Unmarshal(body, &req)
		brief, err := s.designBrief(context.Background(), req)
		if err != nil {
			return 400, map[string]any{"error": err.Error()}, nil
		}
		drawn, err := app.Themes(context.Background(), s.LLM, brief, req)
		if err != nil {
			return 502, map[string]any{"error": err.Error()}, nil
		}
		return 200, drawn, nil
	}
	return 404, map[string]any{"error": "unknown job " + path}, nil
}

// designBrief is what the theme drawings are based on: the approved plan when
// there is one, and otherwise what the user typed.
func (s *Server) designBrief(ctx context.Context, req app.ThemeRequest) (string, error) {
	if id := strings.TrimSpace(req.SRSID); id != "" {
		if plan := s.srsPlan(ctx, id); plan != "" {
			return plan, nil
		}
	}
	if brief := strings.TrimSpace(req.Prompt); brief != "" {
		return brief, nil
	}
	return "", errors.New("describe the app, or approve a specification first")
}

// srsPlan reads the approved plan in prose. The SRS service is part of this
// binary, so this is a call rather than a request.
func (s *Server) srsPlan(ctx context.Context, srsID string) string {
	if s.SRS == nil {
		return ""
	}
	envelope, err := s.SRS.PlanState(ctx, srsID)
	if err != nil || envelope == nil {
		return ""
	}
	return strings.TrimSpace(envelope.Markdown)
}

// serveSRS hands one request to the in-process SRS service. The Studio calls
// it under /srs/*, which is where the reverse proxy to port 7826 used to be.
// rerouted is the same request with a different path — the one the in-process
// service knows itself by. The query string comes with it: an artifact is
// asked for by ?path=, and losing that turns a file into a 400.
func rerouted(r *http.Request, path string) *http.Request {
	inner := r.Clone(r.Context())
	inner.URL = &url.URL{Path: path, RawQuery: r.URL.RawQuery}
	inner.RequestURI = ""
	return inner
}

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

func (s *Server) serveSRS(w http.ResponseWriter, r *http.Request, path string) {
	if s.SRS == nil {
		writeJSON(w, 503, map[string]any{"error": "the SRS service is not running"})
		return
	}
	s.SRS.Handler().ServeHTTP(w, rerouted(r, path[len("/srs"):]))
}

// tune rewords an edit request into something the builder can act on. The
// Studio shows the result and lets the user accept or keep their own wording.
func (s *Server) tune(w http.ResponseWriter, r *http.Request, body []byte) {
	var req struct {
		Prompt string `json:"prompt"`
		Route  string `json:"route"`
	}
	_ = json.Unmarshal(body, &req)
	out, err := s.tuned(r.Context(), req.Prompt, req.Route)
	if err != nil {
		writeJSON(w, 200, map[string]any{"prompt": req.Prompt}) // never block the edit
		return
	}
	writeJSON(w, 200, map[string]any{"prompt": out})
}

func (s *Server) tuned(ctx context.Context, prompt, route string) (string, error) {
	prompt = strings.TrimSpace(prompt)
	if prompt == "" {
		return "", errors.New("nothing to reword")
	}
	system := "You restate one UI change request as a single precise instruction " +
		"for a code agent. Keep the user's intent exactly. Name the visible " +
		"outcome, not the implementation. Answer with the instruction only — no " +
		"preamble, no list, no quotes."
	user := prompt
	if route != "" {
		user = "Route: " + route + "\nRequest: " + prompt
	}
	out, err := s.LLM.Text(ctx, core.RoleBuilder, system, user)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(strings.Trim(strings.TrimSpace(out), `"`)), nil
}

// --- project files -----------------------------------------------------------

const (
	maxListedFiles = 400
	maxFileBytes   = 512 << 10
)

func (s *Server) listProjects() []map[string]any {
	entries, err := os.ReadDir(s.Paths.Projects)
	if err != nil {
		return []map[string]any{}
	}
	out := []map[string]any{}
	for _, e := range entries {
		if !e.IsDir() || strings.HasPrefix(e.Name(), ".") {
			continue
		}
		dir := filepath.Join(s.Paths.Projects, e.Name())
		info, err := e.Info()
		if err != nil {
			continue
		}
		files, _ := core.NewShell(dir).Tree(".", 0)
		title := e.Name()
		if pkg, err := os.ReadFile(filepath.Join(dir, "package.json")); err == nil {
			var parsed struct {
				Name string `json:"name"`
			}
			if json.Unmarshal(pkg, &parsed) == nil && parsed.Name != "" {
				title = parsed.Name
			}
		}
		_, deployed := os.Stat(filepath.Join(dir, ".agentforge", "deploy", "run.json"))
		out = append(out, map[string]any{
			"name": e.Name(), "title": title,
			"mtime":      info.ModTime().Unix(),
			"file_count": len(files),
			"stack":      detectStack(dir),
			"unfinished": 0,
			"deployed":   deployed == nil,
		})
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i]["mtime"].(int64) > out[j]["mtime"].(int64)
	})
	return out
}

func detectStack(dir string) string {
	if _, err := os.Stat(filepath.Join(dir, "next.config.mjs")); err == nil {
		return "next"
	}
	if _, err := os.Stat(filepath.Join(dir, "next.config.js")); err == nil {
		return "next"
	}
	if _, err := os.Stat(filepath.Join(dir, "package.json")); err == nil {
		return "node"
	}
	return "static"
}

// projectFiles answers the code pane: every source file with its size.
func (s *Server) projectFiles(project string) map[string]any {
	dir := s.Paths.Project(project)
	out := map[string]any{}
	if _, err := os.Stat(dir); err != nil {
		return out
	}
	sh := core.NewShell(dir)
	files, _ := sh.Tree(".", 0)

	// Show the files a person opens first, then everything else.
	priority := []string{"package.json", "app/page.jsx", "app/layout.jsx", "app/globals.css"}
	ordered := append([]string{}, priority...)
	for _, f := range files {
		if !contains(priority, f) {
			ordered = append(ordered, f)
		}
	}
	for _, rel := range ordered {
		if len(out) >= maxListedFiles {
			break
		}
		abs := filepath.Join(dir, filepath.FromSlash(rel))
		info, err := os.Stat(abs)
		if err != nil || info.IsDir() || info.Size() > maxFileBytes {
			continue
		}
		data, err := os.ReadFile(abs)
		if err != nil {
			continue
		}
		out[rel] = map[string]any{"content": string(data), "size": humanSize(len(data))}
	}
	return out
}

func humanSize(n int) string {
	if n >= 1024 {
		return fmt.Sprintf("%.1fKB", float64(n)/1024)
	}
	return strconv.Itoa(n) + "B"
}

func (s *Server) openProject(project string) map[string]any {
	dir := s.Paths.Project(project)
	if _, err := os.Stat(dir); err != nil {
		return map[string]any{"error": "no such project: " + project}
	}
	var cmd *exec.Cmd
	switch {
	case fileExists("/usr/bin/xdg-open"):
		cmd = exec.Command("xdg-open", dir)
	case fileExists("/usr/bin/open"):
		cmd = exec.Command("open", dir)
	default:
		cmd = exec.Command("explorer", dir)
	}
	if err := cmd.Start(); err != nil {
		return map[string]any{"ok": false, "path": dir, "error": err.Error()}
	}
	go func() { _ = cmd.Wait() }()
	return map[string]any{"ok": true, "path": dir}
}

// uploadProject imports a folder the user dropped into the Studio.
func (s *Server) uploadProject(body []byte) (int, map[string]any) {
	var req struct {
		Name  string            `json:"name"`
		Title string            `json:"title"`
		Files map[string]string `json:"files"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		return 400, map[string]any{"error": "expected name and files"}
	}
	name := core.SafeName(req.Name)
	if name == "" {
		return 400, map[string]any{"error": "the import needs a name"}
	}
	sh := core.NewShell(s.Paths.Project(name))
	written := 0
	for rel, content := range req.Files {
		if err := sh.Write(rel, content); err == nil {
			written++
		}
	}
	return 200, map[string]any{"ok": true, "project": name, "files": written}
}

// undo restores the files saved at an undo point.
func (s *Server) undo(project, id string) ([]string, error) {
	if core.SafeName(project) == "" || id == "" {
		return nil, errors.New("undo needs a project and a point")
	}
	dir := filepath.Join(s.Paths.Meta(project), "undo", core.SafeName(id))
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, errors.New("that undo point is gone")
	}
	var manifest struct {
		Files []string `json:"files"`
	}
	_ = core.ReadJSON(filepath.Join(dir, "manifest.json"), &manifest)
	sh := core.NewShell(s.Paths.Project(project))
	var restored []string
	for i, rel := range manifest.Files {
		data, err := os.ReadFile(filepath.Join(dir, strconv.Itoa(i)+".bak"))
		if err != nil {
			continue
		}
		if err := sh.Write(rel, string(data)); err == nil {
			restored = append(restored, rel)
		}
	}
	if len(restored) == 0 && len(entries) > 0 {
		return nil, errors.New("nothing in that undo point could be restored")
	}
	return restored, nil
}

// --- results -----------------------------------------------------------------

func (s *Server) qaResults(project string) map[string]any {
	dir := s.Paths.Project(project)
	if _, err := os.Stat(dir); err != nil {
		return map[string]any{"error": "no such project: " + project}
	}
	qaDir := filepath.Join(s.Paths.Meta(project), "qa")
	out := map[string]any{"project": project}
	have := map[string]bool{}

	for key, file := range map[string]string{
		"vitest": "vitest.json", "manifest": "manifest.json", "report": "report.json",
	} {
		var value any
		if core.ReadJSON(filepath.Join(qaDir, file), &value) == nil && value != nil {
			out[key] = value
			have[key] = true
		} else {
			out[key] = nil
			have[key] = false
		}
	}

	history := []any{}
	if data, err := os.ReadFile(filepath.Join(qaDir, "history.jsonl")); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if strings.TrimSpace(line) == "" {
				continue
			}
			var row any
			if json.Unmarshal([]byte(line), &row) == nil {
				history = append(history, row)
			}
		}
	}
	out["history"] = history
	have["history"] = len(history) > 0

	var perf any
	if core.ReadJSON(filepath.Join(s.Paths.Meta(project), "performance.json"), &perf) == nil && perf != nil {
		out["performance"] = perf
		have["performance"] = true
	} else {
		have["performance"] = false
	}

	tests := map[string]string{}
	sh := core.NewShell(dir)
	for _, sub := range []string{"tests/unit", "tests/e2e", "tests/api"} {
		files, err := sh.Tree(sub, 0)
		if err != nil {
			continue
		}
		for _, rel := range files {
			if body, _, err := sh.Read(rel); err == nil {
				tests[rel] = body
			}
		}
	}
	out["tests"] = tests
	have["tests"] = len(tests) > 0

	out["have"] = have
	return out
}

func (s *Server) srsResults(project string) map[string]any {
	dir := s.Paths.Project(project)
	if _, err := os.Stat(dir); err != nil {
		return map[string]any{"error": "no such project: " + project}
	}
	srsDir := filepath.Join(s.Paths.Meta(project), "srs")
	out := map[string]any{"project": project}
	have := map[string]bool{}

	var link, handoff, interview map[string]any
	_ = core.ReadJSON(filepath.Join(srsDir, "link.json"), &link)
	out["link"] = orEmpty(link)

	var latest map[string]any
	_ = core.ReadJSON(filepath.Join(srsDir, "srs_latest.json"), &latest)
	document := latest
	if doc, ok := latest["srs_document"].(map[string]any); ok {
		document = doc
	}
	out["document"] = orEmpty(document)
	have["document"] = len(document) > 0

	plan, _ := os.ReadFile(filepath.Join(srsDir, "plan.md"))
	out["plan"] = string(plan)
	have["plan"] = strings.TrimSpace(string(plan)) != ""

	_ = core.ReadJSON(filepath.Join(srsDir, "handoff.json"), &handoff)
	out["handoff"] = orEmpty(handoff)
	have["handoff"] = handoff["prompt"] != nil

	_ = core.ReadJSON(filepath.Join(srsDir, "interview.json"), &interview)
	out["interview"] = orEmpty(interview)
	have["interview"] = interview["transcript"] != nil

	diagrams := []map[string]any{}
	if entries, err := os.ReadDir(filepath.Join(srsDir, "diagrams")); err == nil {
		for _, e := range entries {
			if !strings.HasSuffix(e.Name(), ".mmd") {
				continue
			}
			stem := strings.TrimSuffix(e.Name(), ".mmd")
			mermaid, _ := os.ReadFile(filepath.Join(srsDir, "diagrams", e.Name()))
			svg, _ := os.ReadFile(filepath.Join(srsDir, "diagrams", stem+".svg"))
			if len(svg) > 400_000 {
				svg = nil
			}
			diagrams = append(diagrams, map[string]any{
				"name": stem, "mermaid": string(mermaid), "svg": string(svg),
				"png": fileExists(filepath.Join(srsDir, "diagrams", stem+".png")),
			})
		}
	}
	out["diagrams"] = diagrams
	have["diagrams"] = len(diagrams) > 0
	have["pdf"] = fileExists(filepath.Join(srsDir, "SRS_latest.pdf"))

	out["have"] = have
	return out
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

func (s *Server) sendQAPDF(w http.ResponseWriter, project string) {
	if s.qaPDF == nil {
		writeJSON(w, 501, map[string]any{"error": "the PDF report is not available in this build"})
		return
	}
	data, err := s.qaPDF(project)
	if err != nil {
		writeJSON(w, 404, map[string]any{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("Content-Disposition", `attachment; filename="`+project+`-test-report.pdf"`)
	w.WriteHeader(200)
	_, _ = w.Write(data)
}

func (s *Server) sendFile(w http.ResponseWriter, path, mime, disposition, missing string) {
	data, err := os.ReadFile(path)
	if err != nil {
		writeJSON(w, 404, map[string]any{"error": missing})
		return
	}
	w.Header().Set("Content-Type", mime)
	w.Header().Set("Content-Disposition", disposition)
	w.WriteHeader(200)
	_, _ = w.Write(data)
}

// settingsSummary is what the settings modal reads back.
func (s *Server) settingsSummary(ctx context.Context) map[string]any {
	saved := core.LoadSettings()
	key := core.APIKey()
	hint := ""
	if len(key) >= 4 {
		hint = "…" + key[len(key)-4:]
	}
	uri, _ := saved["mongodb_uri"].(string)
	redacted := ""
	if uri != "" {
		redacted = redactURI(uri)
	}
	return map[string]any{
		"ollama_host":      core.LocalHost(),
		"cloud_enabled":    key != "",
		"api_key_hint":     hint,
		"local_num_ctx":    core.NumCtx("llama3.1:8b"),
		"agent_model":      s.LLM.ModelFor("agent"),
		"planner_model":    s.LLM.ModelFor(core.RolePlanner),
		"design_model":     s.LLM.ModelFor(core.RoleDesign),
		"builder_model":    s.LLM.ModelFor(core.RoleBuilder),
		"mongodb_uri_set":  uri != "",
		"mongodb_uri_hint": redacted,
		"mongo":            s.Mongo.Status(ctx),
		"deploy":           s.deployStatus(),
		"images":           s.Pictures.Check(),
		"images_enabled":   s.Pictures.Check()["enabled"],
		"image_host":       stringOf(saved, "image_host"),
	}
}

// redactURI keeps the shape of a connection string without the password.
func redactURI(uri string) string {
	at := strings.LastIndex(uri, "@")
	scheme := strings.Index(uri, "://")
	if at < 0 || scheme < 0 || at < scheme {
		return uri
	}
	return uri[:scheme+3] + "•••@" + uri[at+1:]
}

// --- small helpers -----------------------------------------------------------

func cors(w http.ResponseWriter) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
}

func writeJSON(w http.ResponseWriter, code int, payload any) {
	data, err := json.Marshal(payload)
	if err != nil {
		data = []byte(`{"error":"the response could not be encoded"}`)
		code = 500
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	w.WriteHeader(code)
	_, _ = w.Write(data)
}

func jsonUnmarshal(data []byte, into any) error { return json.Unmarshal(data, into) }

// withBody rewinds a request so a proxy can resend a body we already read.
func withBody(r *http.Request, body []byte) *http.Request {
	out := r.Clone(r.Context())
	out.Body = io.NopCloser(strings.NewReader(string(body)))
	out.ContentLength = int64(len(body))
	return out
}

func trimSeg(path, prefix string) string {
	return strings.Trim(strings.TrimPrefix(path, prefix), "/")
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func contains(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

// stringOf reads one string out of the settings map.
func stringOf(settings map[string]any, key string) string {
	if v, ok := settings[key].(string); ok {
		return strings.TrimSpace(v)
	}
	return ""
}

func orEmpty(m map[string]any) map[string]any {
	if m == nil {
		return map[string]any{}
	}
	return m
}
