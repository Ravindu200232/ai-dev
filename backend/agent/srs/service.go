package srs

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

// The SRS API. It used to run in its own Python process on port 7826 and be
// reverse-proxied; it is now served in-process from the same binary, on the
// same paths, so the Studio's calls did not have to change.

// MaxUploadBytes caps one attachment. A browser can post a data: URL of any
// size, and a 40 MB photo would be decoded into memory before anything else
// could refuse it.
const MaxUploadBytes = 7_500_000

// svgInlineLimit is how large an SVG may be before the Studio is given a path
// instead of the drawing itself.
const svgInlineLimit = 400_000

// Handler is the whole SRS surface, mounted under /projects.
func (s *Service) Handler() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("POST /projects", s.handleCreateProject)
	mux.HandleFunc("GET /projects", s.handleListProjects)
	mux.HandleFunc("GET /projects/{id}", s.handleProjectDetail)
	mux.HandleFunc("POST /projects/{id}/approve", s.handleApproveProject)
	mux.HandleFunc("POST /projects/{id}/discard", s.handleDiscardProject)

	mux.HandleFunc("POST /projects/{id}/inputs", s.handleInputs)
	mux.HandleFunc("POST /projects/{id}/inputs-json", s.handleInputsJSON)

	mux.HandleFunc("POST /projects/{id}/analyze", s.handleAnalyze)
	mux.HandleFunc("GET /projects/{id}/questions", s.handleQuestions)
	mux.HandleFunc("GET /projects/{id}/interview", s.handleInterview)
	mux.HandleFunc("POST /projects/{id}/interview/answer", s.handleInterviewAnswer)

	mux.HandleFunc("POST /projects/{id}/plan", s.handleGeneratePlan)
	mux.HandleFunc("GET /projects/{id}/plan", s.handleGetPlan)
	mux.HandleFunc("POST /projects/{id}/plan/approve", s.handleApprovePlan)

	mux.HandleFunc("POST /projects/{id}/generate-srs", s.handleGenerateSRS)
	mux.HandleFunc("POST /projects/{id}/customize", s.handleCustomize)

	mux.HandleFunc("GET /projects/{id}/srs-json", s.handleSRSJSON)
	mux.HandleFunc("GET /projects/{id}/requirements", s.handleRequirements)
	mux.HandleFunc("GET /projects/{id}/diagrams", s.handleDiagrams)
	mux.HandleFunc("GET /projects/{id}/ambiguities", s.handleAmbiguities)
	mux.HandleFunc("GET /projects/{id}/risks", s.handleRisks)
	mux.HandleFunc("GET /projects/{id}/builder-handoff", s.handleBuilderHandoff)
	mux.HandleFunc("GET /projects/{id}/builder-prompt", s.handleBuilderPrompt)

	mux.HandleFunc("GET /projects/{id}/download/json", s.handleDownloadJSON)
	mux.HandleFunc("GET /projects/{id}/download/pdf", s.handleDownloadPDF)

	mux.HandleFunc("GET /projects/{id}/events", s.handleEvents)
	mux.HandleFunc("GET /projects/{id}/traces", s.handleTraces)
	mux.HandleFunc("GET /projects/{id}/errors", s.handleErrors)

	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSONResponse(w, http.StatusOK, map[string]any{"ok": true, "service": "srs"})
	})
	return mux
}

// --- projects ------------------------------------------------------------------------------

func (s *Service) handleCreateProject(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Idea     string `json:"idea"`
		Language string `json:"language"`
	}
	if !decode(w, r, &body) {
		return
	}
	if strings.TrimSpace(body.Idea) == "" {
		fail(w, badRequest("an idea is required"))
		return
	}
	project, err := s.CreateProject(r.Context(), body.Idea, body.Language)
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, map[string]any{"project": project})
}

func (s *Service) handleListProjects(w http.ResponseWriter, r *http.Request) {
	projects, err := s.Repo.ListProjects(r.Context())
	if err != nil {
		fail(w, err)
		return
	}
	if projects == nil {
		projects = []Project{}
	}
	writeJSONResponse(w, http.StatusOK, map[string]any{"projects": projects})
}

func (s *Service) handleProjectDetail(w http.ResponseWriter, r *http.Request) {
	detail, err := s.ProjectDetail(r.Context(), r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, detail)
}

func (s *Service) handleApproveProject(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	project, err := s.Repo.GetProject(r.Context(), id)
	if err != nil || project == nil {
		fail(w, notFound(err, "project not found"))
		return
	}
	if err := s.Repo.UpdateProject(r.Context(), id, Doc{"status": StatusApproved}); err != nil {
		fail(w, err)
		return
	}
	project, _ = s.Repo.GetProject(r.Context(), id)
	writeJSONResponse(w, http.StatusOK, map[string]any{"project": project})
}

// handleDiscardProject throws away an unapproved specification. The Studio has
// always had this button; the Python service never had the route behind it.
func (s *Service) handleDiscardProject(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	project, err := s.Repo.GetProject(r.Context(), id)
	if err != nil || project == nil {
		fail(w, notFound(err, "project not found"))
		return
	}
	if project.Status == StatusApproved {
		fail(w, conflict("this specification is approved; it cannot be discarded"))
		return
	}
	if err := s.Repo.DiscardSpecification(r.Context(), id); err != nil {
		fail(w, err)
		return
	}
	if err := s.Repo.UpdateProject(r.Context(), id, Doc{
		"status": StatusIntake, "current_version": "0.0.0"}); err != nil {
		fail(w, err)
		return
	}
	_ = os.RemoveAll(s.projectDir(id))
	project, _ = s.Repo.GetProject(r.Context(), id)
	writeJSONResponse(w, http.StatusOK, map[string]any{"project": project, "discarded": true})
}

// --- what the customer attached --------------------------------------------------------------

var unsafeFileChars = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

// publicName is a file name a built app can serve out of public/. The name
// ends up in the finished app's HTML, so a dash left stranded against the
// extension is worth removing.
func publicName(filename string) string {
	stem := strings.Trim(unsafeFileChars.ReplaceAllString(filepath.Base(filename), "-"), "-.")
	stem = strings.ReplaceAll(stem, "-.", ".")
	if stem == "" {
		return "upload"
	}
	return stem
}

var publicImageExt = []string{".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
var imageExt = []string{".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
var audioExt = []string{".webm", ".wav", ".mp3", ".m4a", ".ogg", ".flac"}

func hasExt(name string, exts []string) bool {
	low := strings.ToLower(name)
	for _, ext := range exts {
		if strings.HasSuffix(low, ext) {
			return true
		}
	}
	return false
}

// ingest saves an upload, reads it, and records it as a source.
func (s *Service) ingest(r *http.Request, projectID, mode string, data []byte,
	filename, contentType, purpose string) (map[string]any, error) {

	uploads := filepath.Join(s.projectDir(projectID), "uploads")
	if err := os.MkdirAll(uploads, 0o755); err == nil {
		_ = os.WriteFile(filepath.Join(uploads, filepath.Base(filename)), data, 0o644)
	}
	purpose = truncate(strings.Join(strings.Fields(purpose), " "), 300)

	// An image the customer supplied is already part of their app, so it is
	// put where the built app will serve it and the brief names that path.
	publicURL := ""
	if hasExt(filename, publicImageExt) || strings.HasPrefix(contentType, "image/") {
		public := filepath.Join(s.projectDir(projectID), "public", "uploads")
		if err := os.MkdirAll(public, 0o755); err == nil {
			safe := publicName(filename)
			if err := os.WriteFile(filepath.Join(public, safe), data, 0o644); err == nil {
				publicURL = "/uploads/" + safe
			}
		}
	}

	ctx := r.Context()
	var result Extraction
	resolved := "text"
	switch {
	case mode == "pdf" || hasExt(filename, []string{".pdf"}) || strings.Contains(contentType, "pdf"):
		result, resolved = s.ReadPDF(ctx, data, filename), "pdf"
	case mode == "image" || strings.HasPrefix(contentType, "image/") || hasExt(filename, imageExt):
		result, resolved = s.ReadImage(ctx, data, filename), "image"
	case mode == "voice" || strings.HasPrefix(contentType, "audio/") || hasExt(filename, audioExt):
		result, resolved = TranscribeAudio(ctx, data, filename), "voice"
	default:
		result = Extraction{Text: string(data), Engine: "raw"}
	}

	meta := asDoc(result)
	delete(meta, "text")
	if publicURL != "" {
		meta["url"] = publicURL
	}
	if purpose != "" {
		meta["purpose"] = purpose
	}

	src, err := s.AddSource(ctx, projectID, resolved, result.Text, filename, meta)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"source": src, "extraction": meta,
		"note": firstNonEmpty(result.Warning, result.Error),
		"url":  publicURL, "purpose": purpose,
	}, nil
}

func (s *Service) handleInputs(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if project, err := s.Repo.GetProject(r.Context(), id); err != nil || project == nil {
		fail(w, notFound(err, "project not found"))
		return
	}
	if err := r.ParseMultipartForm(MaxUploadBytes); err != nil {
		fail(w, badRequest("the upload could not be read: "+err.Error()))
		return
	}
	mode := firstNonEmpty(r.FormValue("mode"), "text")

	file, header, err := r.FormFile("file")
	if err != nil {
		src, err := s.AddSource(r.Context(), id, mode, r.FormValue("text"), "", nil)
		if err != nil {
			fail(w, err)
			return
		}
		writeJSONResponse(w, http.StatusOK, map[string]any{"source": src})
		return
	}
	defer file.Close()

	data := make([]byte, 0, header.Size)
	buf := make([]byte, 32*1024)
	for int64(len(data)) <= MaxUploadBytes {
		n, err := file.Read(buf)
		data = append(data, buf[:n]...)
		if err != nil {
			break
		}
	}
	if len(data) > MaxUploadBytes {
		fail(w, statusError{Status: http.StatusRequestEntityTooLarge,
			Message: "attachment is larger than " + itoa(MaxUploadBytes/1_000_000) + " MB"})
		return
	}

	out, err := s.ingest(r, id, mode, data, firstNonEmpty(header.Filename, "upload"),
		strings.ToLower(header.Header.Get("Content-Type")), r.FormValue("purpose"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, out)
}

func (s *Service) handleInputsJSON(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if project, err := s.Repo.GetProject(r.Context(), id); err != nil || project == nil {
		fail(w, notFound(err, "project not found"))
		return
	}
	var body struct {
		Mode        string `json:"mode"`
		Text        string `json:"text"`
		Filename    string `json:"filename"`
		ContentType string `json:"content_type"`
		Purpose     string `json:"purpose"`
		DataBase64  string `json:"data_base64"`
	}
	if !decode(w, r, &body) {
		return
	}
	mode := firstNonEmpty(body.Mode, "text")

	if strings.TrimSpace(body.DataBase64) == "" {
		src, err := s.AddSource(r.Context(), id, mode, body.Text, "", nil)
		if err != nil {
			fail(w, err)
			return
		}
		writeJSONResponse(w, http.StatusOK, map[string]any{"source": src})
		return
	}

	raw := body.DataBase64
	// A browser posts a data: URL; the payload is what follows the comma.
	if head := raw; len(head) > 64 {
		head = head[:64]
		if strings.HasPrefix(strings.TrimSpace(head), "data:") && strings.Contains(head, ",") {
			raw = strings.SplitN(raw, ",", 2)[1]
		}
	}
	if len(raw) > MaxUploadBytes*4/3+8 {
		fail(w, statusError{Status: http.StatusRequestEntityTooLarge,
			Message: "attachment is larger than " + itoa(MaxUploadBytes/1_000_000) + " MB"})
		return
	}
	data, err := base64.StdEncoding.DecodeString(raw)
	if err != nil {
		if data, err = base64.RawStdEncoding.DecodeString(raw); err != nil {
			fail(w, badRequest("attachment is not valid base64: "+err.Error()))
			return
		}
	}
	if len(data) > MaxUploadBytes {
		fail(w, statusError{Status: http.StatusRequestEntityTooLarge,
			Message: "attachment is larger than " + itoa(MaxUploadBytes/1_000_000) + " MB"})
		return
	}

	out, err := s.ingest(r, id, mode, data, firstNonEmpty(body.Filename, "upload"),
		strings.ToLower(body.ContentType), body.Purpose)
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, out)
}

// --- the interview ---------------------------------------------------------------------------

func (s *Service) handleAnalyze(w http.ResponseWriter, r *http.Request) {
	result, err := s.Analyze(r.Context(), r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, result)
}

func (s *Service) handleQuestions(w http.ResponseWriter, r *http.Request) {
	session, err := s.Repo.GetSession(r.Context(), r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	if session == nil {
		writeJSONResponse(w, http.StatusOK, map[string]any{
			"session": nil, "questions": []Question{}})
		return
	}
	writeJSONResponse(w, http.StatusOK, map[string]any{
		"session": session, "questions": session.Questions})
}

func (s *Service) handleInterview(w http.ResponseWriter, r *http.Request) {
	state, err := s.InterviewState(r.Context(), r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, state)
}

func (s *Service) handleInterviewAnswer(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Key         string   `json:"key"`
		Value       any      `json:"value"`
		Text        string   `json:"text"`
		Selected    any      `json:"selected"`
		Custom      string   `json:"custom"`
		Attachments []string `json:"attachments"`
	}
	if !decode(w, r, &body) {
		return
	}
	result, err := s.InterviewAnswer(r.Context(), r.PathValue("id"), body.Key, body.Value,
		body.Text, body.Selected, body.Custom, body.Attachments)
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, result)
}

// --- the plan ---------------------------------------------------------------------------------

func (s *Service) handleGeneratePlan(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Revision string `json:"revision"`
	}
	_ = json.NewDecoder(r.Body).Decode(&body) // an empty body means "first plan"
	envelope, err := s.GeneratePlan(r.Context(), r.PathValue("id"), body.Revision)
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, envelope)
}

func (s *Service) handleGetPlan(w http.ResponseWriter, r *http.Request) {
	envelope, err := s.PlanState(r.Context(), r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, envelope)
}

func (s *Service) handleApprovePlan(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Version *int `json:"version"`
	}
	_ = json.NewDecoder(r.Body).Decode(&body)
	verdict, err := s.ApprovePlanAndAnnounce(r.Context(), r.PathValue("id"), body.Version)
	if err != nil {
		fail(w, err)
		return
	}
	if !verdict.Approved {
		fail(w, conflict(firstNonEmpty(verdict.Reason, "plan cannot be approved")))
		return
	}
	writeJSONResponse(w, http.StatusOK, verdict)
}

// --- the specification -------------------------------------------------------------------------

func (s *Service) handleGenerateSRS(w http.ResponseWriter, r *http.Request) {
	result, err := s.GenerateSRS(r.Context(), r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, result)
}

func (s *Service) handleCustomize(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Prompt string `json:"prompt"`
	}
	if !decode(w, r, &body) {
		return
	}
	if strings.TrimSpace(body.Prompt) == "" {
		fail(w, badRequest("an edit needs a prompt"))
		return
	}
	result, err := s.Customize(r.Context(), r.PathValue("id"), body.Prompt)
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, result)
}

// document is the current specification, or a 404 with a reason.
func (s *Service) document(r *http.Request) (*Document, error) {
	srs := s.LatestSRS(r.Context(), r.PathValue("id"))
	if srs == nil {
		return nil, notFound(nil, "no SRS generated yet")
	}
	return &srs.Document, nil
}

func (s *Service) handleSRSJSON(w http.ResponseWriter, r *http.Request) {
	srs := s.LatestSRS(r.Context(), r.PathValue("id"))
	if srs == nil {
		fail(w, notFound(nil, "no SRS generated yet"))
		return
	}
	writeJSONResponse(w, http.StatusOK, srs)
}

func (s *Service) handleRequirements(w http.ResponseWriter, r *http.Request) {
	doc, err := s.document(r)
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, map[string]any{
		"functional_requirements":     doc.FunctionalRequirements,
		"non_functional_requirements": doc.NonFunctionalRequirements,
	})
}

func (s *Service) handleDiagrams(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	stored, _ := s.Repo.ListDiagrams(r.Context(), id)
	if len(stored) == 0 {
		doc, err := s.document(r)
		if err != nil {
			fail(w, err)
			return
		}
		stored = doc.Diagrams
	}
	out := make([]Diagram, 0, len(stored))
	for _, d := range stored {
		out = append(out, withInlineSVG(d))
	}
	writeJSONResponse(w, http.StatusOK, map[string]any{"diagrams": out})
}

// withInlineSVG puts a small drawing in the response itself, so the Studio can
// show it without a second request for a file it cannot reach.
func withInlineSVG(d Diagram) Diagram {
	if d.SVG != "" || d.SvgPath == "" {
		return d
	}
	body, err := os.ReadFile(d.SvgPath)
	if err != nil || len(body) == 0 || len(body) > svgInlineLimit {
		return d
	}
	d.SVG = string(body)
	return d
}

func (s *Service) handleAmbiguities(w http.ResponseWriter, r *http.Request) {
	doc, err := s.document(r)
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, map[string]any{
		"ambiguities": doc.Ambiguities, "assumptions": doc.Assumptions})
}

func (s *Service) handleRisks(w http.ResponseWriter, r *http.Request) {
	doc, err := s.document(r)
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, map[string]any{"risk_priority": doc.RiskPriority})
}

func (s *Service) handleBuilderHandoff(w http.ResponseWriter, r *http.Request) {
	handoff, err := s.LiveHandoff(r.Context(), r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSONResponse(w, http.StatusOK, handoff)
}

func (s *Service) handleBuilderPrompt(w http.ResponseWriter, r *http.Request) {
	handoff, err := s.LiveHandoff(r.Context(), r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	prompt := firstText(handoff["prompt"])
	if prompt == "" {
		fail(w, notFound(nil, "no builder prompt — this SRS predates the approved-plan flow"))
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	_, _ = w.Write([]byte(prompt))
}

// --- downloads ------------------------------------------------------------------------------

// downloadName is the project's title, safe to put in a filename.
func downloadName(project *Project) string {
	name := "srs"
	if project != nil && strings.TrimSpace(project.Title) != "" {
		name = strings.ReplaceAll(project.Title, " ", "_")
	}
	return truncate(unsafeFileChars.ReplaceAllString(name, "_"), 40)
}

func (s *Service) handleDownloadJSON(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	srs := s.LatestSRS(r.Context(), id)
	if srs == nil {
		fail(w, notFound(nil, "no SRS generated yet"))
		return
	}
	project, _ := s.Repo.GetProject(r.Context(), id)
	body, err := json.MarshalIndent(srs, "", "  ")
	if err != nil {
		fail(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Disposition",
		`attachment; filename="`+downloadName(project)+`_SRS.json"`)
	_, _ = w.Write(body)
}

func (s *Service) handleDownloadPDF(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	project, err := s.Repo.GetProject(r.Context(), id)
	if err != nil || project == nil {
		fail(w, notFound(err, "project not found"))
		return
	}
	srs := s.LatestSRS(r.Context(), id)
	if srs == nil {
		fail(w, notFound(nil, "no SRS generated yet"))
		return
	}

	status := "Draft"
	if project.Status == StatusApproved || project.Status == StatusCustomized {
		status = "Approved"
	}
	version := firstNonEmpty(project.CurrentVersion, "1.0.0")
	path := s.PDFPath(id, version)
	doc := srs.Document
	if err := PDF(&doc, path, status); err != nil {
		fail(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("Content-Disposition",
		`attachment; filename="`+downloadName(project)+`_SRS_v`+version+`.pdf"`)
	http.ServeFile(w, r, path)
}

// --- the console ----------------------------------------------------------------------------

func (s *Service) handleEvents(w http.ResponseWriter, r *http.Request) {
	limit := 0
	if raw := r.URL.Query().Get("limit"); raw != "" {
		limit, _ = strconv.Atoi(raw)
	}
	events, err := s.Repo.ListEvents(r.Context(), r.PathValue("id"), limit)
	if err != nil {
		fail(w, err)
		return
	}
	if events == nil {
		events = []Event{}
	}
	writeJSONResponse(w, http.StatusOK, map[string]any{"events": events})
}

func (s *Service) handleTraces(w http.ResponseWriter, r *http.Request) {
	s.records(w, r, CollTraces, "traces")
}

func (s *Service) handleErrors(w http.ResponseWriter, r *http.Request) {
	s.records(w, r, CollErrors, "errors")
}

func (s *Service) records(w http.ResponseWriter, r *http.Request, collection, key string) {
	rows, err := s.Repo.ListRecords(r.Context(), collection, r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	if rows == nil {
		rows = []Doc{}
	}
	writeJSONResponse(w, http.StatusOK, map[string]any{key: rows})
}

// --- plumbing --------------------------------------------------------------------------------

func decode(w http.ResponseWriter, r *http.Request, into any) bool {
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 12<<20)).Decode(into); err != nil {
		fail(w, badRequest("the request body could not be read: "+err.Error()))
		return false
	}
	return true
}

func writeJSONResponse(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

// fail answers with the status the operation asked for, and 500 for anything
// that did not name one.
func fail(w http.ResponseWriter, err error) {
	status := http.StatusInternalServerError
	var known statusError
	if errors.As(err, &known) {
		status = known.Status
	}
	writeJSONResponse(w, status, map[string]any{"detail": err.Error()})
}
