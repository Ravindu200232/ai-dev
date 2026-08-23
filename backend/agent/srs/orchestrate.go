package srs

import (
	"context"
	"fmt"
	"strings"
)

// notFound and badRequest carry an HTTP meaning back to service.go without
// every operation having to know it is being served over HTTP.
type statusError struct {
	Status  int
	Message string
}

func (e statusError) Error() string { return e.Message }

func notFound(cause error, message string) error {
	if cause != nil {
		return statusError{Status: 404, Message: message + ": " + cause.Error()}
	}
	return statusError{Status: 404, Message: message}
}

func badRequest(message string) error { return statusError{Status: 400, Message: message} }

func conflict(message string) error { return statusError{Status: 409, Message: message} }

// The operations behind the endpoints: create a project, analyse it, run the
// interview, plan it, specify it, edit it. Each one ties a graph to the store,
// and each one is the whole of what its endpoint does — service.go only
// decodes the request and encodes the answer.

// CreateProject starts one idea on its way.
func (s *Service) CreateProject(ctx context.Context, idea, language string) (*Project, error) {
	project := NewProject(idea, language)
	if err := s.Repo.CreateProject(ctx, project); err != nil {
		return nil, err
	}
	s.emit(ctx, project.ID, "System", "Project created.", "success", 0, nil)
	return &project, nil
}

// AddSource records something the customer attached, already read into text.
func (s *Service) AddSource(ctx context.Context, projectID, mode, text, filename string,
	meta map[string]any) (Source, error) {

	src, err := s.Repo.AddSource(ctx, Source{
		ProjectID: projectID, Mode: mode, Filename: filename, Text: text, Meta: meta,
	})
	if err != nil {
		return src, err
	}
	label := "Added " + mode + " source"
	if filename != "" {
		label += " (" + filename + ")"
	}
	s.emit(ctx, projectID, "IntakeExtractorAgent",
		fmt.Sprintf("%s — %d chars extracted.", label, len(text)), "info", 0, nil)
	return src, nil
}

// Brief is the typed idea plus everything read out of what they attached.
func (s *Service) Brief(ctx context.Context, project *Project) string {
	sources, err := s.Repo.ListSources(ctx, project.ID)
	if err != nil {
		return project.RawIdea
	}
	return BuildBrief(project.RawIdea, sources)
}

// AnalysisResult is what POST /projects/{id}/analyze returns.
type AnalysisResult struct {
	Project             *Project       `json:"project"`
	Classification      map[string]any `json:"classification"`
	NeedsClarification  bool           `json:"needs_clarification"`
	ClarificationReason string         `json:"clarification_reason,omitempty"`
	Question            *Question      `json:"question"`
	Questions           []Question     `json:"questions"`
}

// Analyze reads the idea, works out what kind of app it is, and asks the first
// question. A failure here falls back to the deterministic classifier rather
// than leaving the customer with nothing.
func (s *Service) Analyze(ctx context.Context, projectID string) (*AnalysisResult, error) {
	project, err := s.Repo.GetProject(ctx, projectID)
	if err != nil || project == nil {
		return nil, notFound(err, "project not found")
	}
	_ = s.Repo.UpdateProject(ctx, projectID, Doc{"status": StatusAnalyzing})
	brief := s.Brief(ctx, project)

	state := &State{ProjectID: projectID, Project: project,
		RawIdea: project.RawIdea, Brief: brief,
		Language: firstNonEmpty(project.Language, "English")}

	result, err := s.RunAnalysis(ctx, state)
	if err != nil || result.Classification == nil {
		s.recordError(ctx, projectID, "AnalyzerWorkflow", err)
		s.warn(ctx, projectID, "AnalyzerWorkflow",
			"Analysis fell back to the deterministic classifier.", 50)
		result = fallbackAnalysis(state)
	}

	classification := result.Classification
	update := Doc{
		"classification": classification,
		"detected_domain": firstNonEmpty(firstText(classification["detected_domain"]),
			project.DetectedDomain, "Custom"),
		"domain_key": firstNonEmpty(firstText(classification["domain_key"]),
			project.DomainKey, GenericDomain),
		"language":             firstNonEmpty(result.Language, project.Language, "English"),
		"needs_clarification":  result.NeedsClarification,
		"clarification_reason": result.ClarificationReason,
		"status":               StatusQuestioning,
	}
	if result.Project != nil {
		update["complexity"] = result.Project.Complexity
		update["suggested_stack"] = result.Project.SuggestedStack
	}
	_ = s.Repo.UpdateProject(ctx, projectID, update)

	session := s.newSession(projectID, brief, classification, firstText(update["language"]))
	question := s.Ask(session, nextSlot(session), len(session.Questions)+1, session.Total)
	remember(session, &question)
	_ = s.Repo.SaveSession(ctx, *session)

	project, _ = s.Repo.GetProject(ctx, projectID)
	return &AnalysisResult{
		Project: project, Classification: classification,
		NeedsClarification:  result.NeedsClarification,
		ClarificationReason: result.ClarificationReason,
		Question:            &question, Questions: session.Questions,
	}, nil
}

// fallbackAnalysis is the last-resort net: no model, no graph, just the
// keyword classifier. A customer who cannot reach a model still gets an
// interview.
func fallbackAnalysis(state *State) *State {
	brief := state.Brief
	nonsense, reason := LooksLikeNonsense(contentOnly(brief))
	key, confidence := ClassifyDomain(brief)
	domain := GetDomain(key)

	out := *state
	out.Classification = map[string]any{
		"domain_key": key, "detected_domain": domain.Label,
		"app_type": domain.AppTypePrimary, "confidence": confidence,
		"similar_patterns": similarPatterns(key),
		"reasoning":        "Deterministic keyword classification (the model was not reachable).",
	}
	out.NeedsClarification, out.ClarificationReason = nonsense, reason
	out.Language = firstNonEmpty(state.Language, DetectLanguage(brief))
	if out.Project != nil {
		project := *out.Project
		project.Complexity = complexityFor(key, brief)
		project.SuggestedStack = stackFor(key)
		out.Project = &project
	}
	return &out
}

// newSession opens the interview, with the app type the idea reads as already
// guessed so the first question can offer it.
func (s *Service) newSession(projectID, brief string, classification map[string]any,
	language string) *Session {

	guessed, confidence, why := GuessAppType(brief)
	if picked := strings.ToLower(strings.TrimSpace(firstText(classification["build_category"]))); picked != "" {
		if _, ok := Knowledge().AppTypes[picked]; ok {
			guessed = picked
			if given := classification["build_category_confidence"]; given != nil {
				if value, ok := given.(float64); ok {
					confidence = value
				}
			}
			why = firstText(classification["build_category_why"])
		}
	}
	return &Session{
		ID: NewID("qs_"), ProjectID: projectID, Mode: "interview",
		RawIdea: brief, Language: firstNonEmpty(language, "English"),
		GuessedAppType: guessed, GuessedAppTypeConfidence: roundTo(confidence, 2),
		GuessedAppTypeWhy: why,
		Answers:           map[string]AnswerEntry{}, Asked: []string{},
		Questions: []Question{}, CreatedAt: NowISO(),
	}
}

func roundTo(value float64, places int) float64 {
	scale := 1.0
	for i := 0; i < places; i++ {
		scale *= 10
	}
	return float64(int64(value*scale+0.5)) / scale
}

// remember parks a question on the session as both "what to show" and
// transcript, so a reload puts the customer back where they were.
func remember(session *Session, question *Question) {
	if question == nil {
		session.Current = -1
		return
	}
	for i, existing := range session.Questions {
		if existing.ID == question.ID {
			session.Questions[i] = *question
			session.Current = i
			session.Total = maxInt(session.Total, question.Total)
			return
		}
	}
	session.Questions = append(session.Questions, *question)
	session.Current = len(session.Questions) - 1
	session.Total = maxInt(question.Total, len(session.Questions))
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// currentQuestion is the one on screen, or nil.
func currentQuestion(session *Session) *Question {
	if session == nil || session.Current < 0 || session.Current >= len(session.Questions) {
		return nil
	}
	return &session.Questions[session.Current]
}

// nextSlot is the next unanswered question in the queue, or an empty slot when
// the interview is done.
func nextSlot(session *Session) Slot {
	topics, err := LoadTopics()
	if err != nil {
		return Slot{}
	}
	queue := QuestionBudget(BuildQueue(topics, session))
	for _, slot := range queue {
		if _, answered := session.Answers[slot.Key]; !answered {
			return slot
		}
	}
	return Slot{}
}

// queueLength is how many questions this session will ask in total.
func queueLength(session *Session) int {
	topics, err := LoadTopics()
	if err != nil {
		return len(session.Questions)
	}
	return len(QuestionBudget(BuildQueue(topics, session)))
}

// --- the interview ------------------------------------------------------------------------

// InterviewState is the question on screen right now, plus what has been
// answered so far.
type InterviewState struct {
	Question   *Question  `json:"question"`
	Transcript []Question `json:"transcript"`
	Answers    []Answer   `json:"answers"`
	Done       bool       `json:"done"`
	AppType    string     `json:"app_type,omitempty"`
}

func (s *Service) InterviewState(ctx context.Context, projectID string) (*InterviewState, error) {
	session, err := s.Repo.GetSession(ctx, projectID)
	if err != nil {
		return nil, err
	}
	if session == nil {
		return &InterviewState{Transcript: []Question{}, Answers: []Answer{}}, nil
	}

	question := currentQuestion(session)
	if question == nil && !session.Complete {
		question = s.nextQuestion(ctx, session)
		_ = s.Repo.SaveSession(ctx, *session)
	}
	return &InterviewState{
		Question: question, Transcript: session.Questions,
		Answers: FlatAnswers(session),
		Done:    session.Complete || question == nil,
		AppType: session.AppType,
	}, nil
}

// nextQuestion is a pending read-back if there is one, otherwise the next
// interview question. A clarification always jumps the queue: the answer it is
// about cannot be used until it is settled.
func (s *Service) nextQuestion(ctx context.Context, session *Session) *Question {
	if _, pending := PendingClarification(session); pending != nil {
		question := pending.ClarifyQuestion()
		remember(session, question)
		return question
	}
	slot := nextSlot(session)
	if slot.Key == "" {
		remember(session, nil)
		return nil
	}
	question := s.Ask(session, slot, len(session.Answers)+1, queueLength(session))
	remember(session, &question)
	return &question
}

// AnswerResult is what POST /interview/answer returns.
type AnswerResult struct {
	Question      *Question `json:"question"`
	Done          bool      `json:"done"`
	CoverageScore float64   `json:"coverage_score,omitempty"`
}

// InterviewAnswer records one answer and returns whatever should be asked next.
func (s *Service) InterviewAnswer(ctx context.Context, projectID, key string, value any,
	text string, selected any, custom string, attachments []string) (*AnswerResult, error) {

	project, err := s.Repo.GetProject(ctx, projectID)
	if err != nil || project == nil {
		return nil, notFound(err, "project not found")
	}
	session, err := s.Repo.GetSession(ctx, projectID)
	if err != nil {
		return nil, err
	}
	if session == nil {
		return nil, notFound(nil, "no interview in progress")
	}

	words := customerContext("", session, project)
	attached := s.sourcesByID(ctx, projectID, attachments)

	if pendingKey, pending := PendingClarification(session); pending != nil {
		s.AnswerClarification(ctx, pending, value, text, confirmedTyped(session), words)
		storeClarification(session, pendingKey, pending)
		if pending.Ready() {
			applyClarification(session, pending)
		}
	} else {
		typed := strings.TrimSpace(firstNonEmpty(custom, text))
		Record(session, key, firstNonNilValue(value, selected), typed, attached)

		var options []Option
		if question := currentQuestion(session); question != nil {
			options = question.Options
		}
		if NeedsClarification(typed, options) {
			question := currentQuestion(session)
			asked := ""
			if question != nil {
				asked = question.Question
			}
			state := s.StartClarification(ctx, key, typed, strings.SplitN(key, ":", 2)[0],
				asked, confirmedTyped(session), options, projectID, words,
				firstNonEmpty(project.Language, "English"))
			storeClarification(session, key, state)
			if state.Ready() {
				applyClarification(session, state)
			}
		}
	}

	_ = s.Repo.SaveAnswers(ctx, projectID, FlatAnswers(session))
	question := s.nextQuestion(ctx, session)
	if question != nil {
		_ = s.Repo.SaveSession(ctx, *session)
		return &AnswerResult{Question: question}, nil
	}

	session.Complete = true
	coverage := ComputeCoverage(s.Brief(ctx, project), FlatAnswers(session))
	score, _ := coverage["score"].(float64)
	session.CoverageScore, session.Coverage = score, coverage
	_ = s.Repo.SaveSession(ctx, *session)
	_ = s.Repo.UpdateProject(ctx, projectID, Doc{
		"coverage_score": score, "coverage": coverage, "status": StatusPlanning})
	s.emit(ctx, projectID, "InterviewAgent",
		fmt.Sprintf("That is everything we need — coverage %.0f%%.", score),
		"success", 100, map[string]any{"coverage": coverage})
	return &AnswerResult{Done: true, CoverageScore: score}, nil
}

func firstNonNilValue(value, selected any) any {
	if value != nil {
		return value
	}
	return selected
}

func (s *Service) sourcesByID(ctx context.Context, projectID string, ids []string) []string {
	if len(ids) == 0 {
		return nil
	}
	sources, err := s.Repo.ListSources(ctx, projectID)
	if err != nil {
		return nil
	}
	byID := map[string]Source{}
	for _, src := range sources {
		byID[src.ID] = src
	}
	// The attachment's text is what the interview carries forward, in the
	// order the customer attached them.
	var out []string
	for _, id := range ids {
		if src, ok := byID[id]; ok && strings.TrimSpace(src.Text) != "" {
			label := src.Filename
			if label == "" {
				label = src.Mode
			}
			out = append(out, label+": "+src.Text)
		}
	}
	return out
}

// confirmedTyped is every typed answer already read back and confirmed — the
// list a new one is checked against for duplicates.
func confirmedTyped(session *Session) []string {
	var out []string
	for _, key := range keysSorted(clarifyStates(session)) {
		if resolved := clarifyStates(session)[key].Resolve(); resolved != nil && resolved.Text != "" {
			out = append(out, resolved.Text)
		}
	}
	return out
}

// applyClarification folds a confirmed reading back into the answer it came
// from, so the specification carries what they meant rather than what they
// first typed.
func applyClarification(session *Session, state *Clarification) {
	resolved := state.Resolve()
	if resolved == nil {
		return
	}
	entry, ok := session.Answers[state.QuestionID]
	if !ok {
		return
	}
	answer := firstNonEmpty(resolved.Answer, resolved.Text)
	if resolved.Text != "" && resolved.Text != resolved.Raw {
		entry.Text = answer
		switch value := entry.Value.(type) {
		case []any:
			for i, item := range value {
				if firstText(item) == resolved.Raw {
					value[i] = answer
				}
			}
			entry.Value = value
		case []string:
			for i, item := range value {
				if item == resolved.Raw {
					value[i] = answer
				}
			}
			entry.Value = value
		default:
			if firstText(entry.Value) == resolved.Raw {
				entry.Value = answer
			}
		}
	}
	session.Answers[state.QuestionID] = entry
}

// --- planning --------------------------------------------------------------------------

// GeneratePlan builds plan v1, or a new version answering a change request.
func (s *Service) GeneratePlan(ctx context.Context, projectID, revision string) (*PlanEnvelope, error) {
	project, err := s.Repo.GetProject(ctx, projectID)
	if err != nil || project == nil {
		return nil, notFound(err, "project not found")
	}
	session, _ := s.Repo.GetSession(ctx, projectID)
	if session == nil {
		session = &Session{ProjectID: projectID, Answers: map[string]AnswerEntry{}}
	}
	brief := s.Brief(ctx, project)

	latest, _ := s.Repo.LatestPlan(ctx, projectID)
	var previous *Plan
	version := 1
	if latest != nil {
		version = latest.Version + 1
		if revision != "" {
			plan := latest.Plan
			previous = &plan
		}
	}

	coverage := session.Coverage
	if len(coverage) == 0 {
		coverage = ComputeCoverage(brief, FlatAnswers(session))
	}

	plan, err := s.WritePlan(ctx, project, session, brief, previous, revision, coverage)
	if err != nil {
		return nil, err
	}

	// A rename in the plan is the customer renaming their app, so it reaches
	// the interview state and the project title too.
	asked := strings.TrimSpace(firstText(answerOf(session, "app_name")))
	named := strings.TrimSpace(plan.AppName)
	appName := firstNonEmpty(named, asked, project.Title)
	if named != "" && named != asked {
		entry := session.Answers["app_name"]
		entry.Value = named
		if session.Answers == nil {
			session.Answers = map[string]AnswerEntry{}
		}
		session.Answers["app_name"] = entry
		_ = s.Repo.SaveSession(ctx, *session)
		_ = s.Repo.UpdateProject(ctx, projectID, Doc{"title": named})
	}

	doc := PlanRecordDoc{
		ProjectID: projectID, Version: version, Plan: *plan,
		Markdown:      RenderPlanMarkdown(plan, appName),
		ContentHash:   ContentHash(plan),
		ChangeRequest: revision,
	}
	if latest != nil && revision != "" {
		doc.RevisionOf = latest.Version
	}
	if _, err := s.Repo.SavePlan(ctx, doc); err != nil {
		return nil, err
	}
	_ = s.Repo.UpdateProject(ctx, projectID, Doc{"status": StatusPlanning})
	return s.PlanState(ctx, projectID)
}

// ApprovePlanAndAnnounce approves and says so on the console.
func (s *Service) ApprovePlanAndAnnounce(ctx context.Context, projectID string, version *int) (*Approval, error) {
	verdict, err := s.ApprovePlan(ctx, projectID, version)
	if err != nil || verdict == nil || !verdict.Approved {
		return verdict, err
	}
	s.emit(ctx, projectID, "PlanGeneratorAgent", fmt.Sprintf(
		"Plan v%d approved — this is now the record of what gets specified.", *verdict.Version),
		"success", 100, nil)
	return verdict, nil
}

// --- specifying ---------------------------------------------------------------------------

// GenerationResult is what POST /generate-srs returns.
type GenerationResult struct {
	Project *Project  `json:"project"`
	Version string    `json:"version"`
	Summary Summary   `json:"summary"`
	SRS     *Envelope `json:"srs,omitempty"`
}

// GenerateSRS writes the specification from the approved plan.
func (s *Service) GenerateSRS(ctx context.Context, projectID string) (*GenerationResult, error) {
	project, err := s.Repo.GetProject(ctx, projectID)
	if err != nil || project == nil {
		return nil, notFound(err, "project not found")
	}
	session, _ := s.Repo.GetSession(ctx, projectID)
	if session == nil {
		session = &Session{ProjectID: projectID, Answers: map[string]AnswerEntry{}}
	}
	// The specification is composed from the approved plan and nothing else.
	// Falling back to the latest plan wrote one from something the customer
	// had not agreed to — and with no plan at all, from nothing at all.
	planDoc, _ := s.ApprovedPlan(ctx, projectID)
	if planDoc == nil {
		if latest, _ := s.Repo.LatestPlan(ctx, projectID); latest != nil {
			return nil, conflict("this plan has not been approved yet — approve it, " +
				"and the specification is written from it")
		}
		return nil, badRequest("there is no plan to write a specification from — " +
			"answer the interview first")
	}

	state := &State{
		ProjectID: projectID, Project: project, RawIdea: project.RawIdea,
		Brief: s.Brief(ctx, project), Language: firstNonEmpty(project.Language, "English"),
		Classification: project.Classification, Session: session,
	}
	if planDoc != nil {
		plan := planDoc.Plan
		state.Plan, state.PlanMarkdown = &plan, planDoc.Markdown
	}

	result, err := s.RunGeneration(ctx, state)
	if err != nil {
		return nil, err
	}

	version := "1.0.0"
	existing, _ := s.Repo.LatestVersion(ctx, projectID)
	label := "Initial generation"
	if existing != nil {
		version = bumpMinor(existing.Version)
		label = "Rewritten from a changed plan"
	}
	result.Document.Version = version

	if err := s.SaveSRS(projectID, version, result.Document); err != nil {
		s.recordError(ctx, projectID, "System", err)
	}
	s.storeVersion(ctx, projectID, version, label, result, nil)
	_ = s.Repo.UpdateProject(ctx, projectID, Doc{
		"status": StatusGenerated, "current_version": version})

	project, _ = s.Repo.GetProject(ctx, projectID)
	return &GenerationResult{Project: project, Version: version,
		Summary: Summarize(result.Document)}, nil
}

// CustomizationResult is what POST /customize returns.
type CustomizationResult struct {
	Project     *Project `json:"project"`
	Version     string   `json:"version"`
	DiffSummary []string `json:"diff_summary"`
	Summary     Summary  `json:"summary"`
}

// Customize applies one plain-English edit to the current specification.
func (s *Service) Customize(ctx context.Context, projectID, prompt string) (*CustomizationResult, error) {
	project, err := s.Repo.GetProject(ctx, projectID)
	if err != nil || project == nil {
		return nil, notFound(err, "project not found")
	}
	latest, _ := s.Repo.LatestVersion(ctx, projectID)
	if latest == nil {
		return nil, badRequest("no SRS to customize yet; generate first")
	}
	session, _ := s.Repo.GetSession(ctx, projectID)
	if session == nil {
		session = &Session{ProjectID: projectID, Answers: map[string]AnswerEntry{}}
	}
	planDoc, _ := s.ApprovedPlan(ctx, projectID)
	if planDoc == nil {
		planDoc, _ = s.Repo.LatestPlan(ctx, projectID)
	}

	document := latest.SRS.Document
	state := &State{
		ProjectID: projectID, Project: project, Document: &document,
		Brief: s.Brief(ctx, project), Session: session,
		Language:            firstNonEmpty(project.Language, "English"),
		CustomizationPrompt: prompt,
	}
	if planDoc != nil {
		plan := planDoc.Plan
		state.Plan, state.PlanMarkdown = &plan, planDoc.Markdown
	}

	result, err := s.RunCustomization(ctx, state)
	if err != nil {
		return nil, err
	}
	version := bumpMinor(latest.Version)
	result.Document.Version = version

	// The edit changed the specification, so the builder's contract is rebuilt
	// from it rather than left describing the document before the edit.
	plan := planFromDoc(result.Document.ApprovedPlan)
	if len(result.Document.EffectivePlan) > 0 {
		plan = planFromDoc(result.Document.EffectivePlan)
	}
	pack := packOf(session)
	handoff := BuildHandoff(plan, result.Document, pack, AuthOn(pack, plan))
	handoff.SourceDocumentLanguage, handoff.PromptLanguage = "English", "English"
	result.Document.BuilderHandoff = asDoc(handoff)

	if err := s.SaveSRS(projectID, version, result.Document); err != nil {
		s.recordError(ctx, projectID, "System", err)
	}
	s.storeVersion(ctx, projectID, version,
		"Customized: "+truncate(prompt, 60), result, result.DiffSummary)
	_ = s.Repo.UpdateProject(ctx, projectID, Doc{
		"status": StatusCustomized, "current_version": version})

	project, _ = s.Repo.GetProject(ctx, projectID)
	return &CustomizationResult{Project: project, Version: version,
		DiffSummary: result.DiffSummary, Summary: Summarize(result.Document)}, nil
}

// storeVersion keeps this version's document and diagrams before the next
// revision overwrites the files on disk.
func (s *Service) storeVersion(ctx context.Context, projectID, version, label string,
	result *State, diff []string) {

	snapshot := *result.Document
	snapshot.Diagrams = s.SnapshotDiagrams(projectID, version, result.Diagrams)
	if diff == nil {
		diff = []string{}
	}
	_, _ = s.Repo.SaveVersion(ctx, Version{
		ProjectID: projectID, Version: version, Label: label,
		SRS: Envelope{Document: snapshot}, DiffSummary: diff,
	})
	_ = s.Repo.SaveDiagrams(ctx, projectID, result.Diagrams)
}

// bumpMinor is how a regenerated specification is numbered: 1.0.0 → 1.1.0.
// Anything that is not three numbers restarts at 1.1.0 rather than producing
// something no version comparison can read.
func bumpMinor(version string) string {
	parts := strings.Split(firstNonEmpty(version, "1.0.0"), ".")
	for len(parts) < 3 {
		parts = append(parts, "0")
	}
	for _, part := range parts[:3] {
		if part == "" || digitsOnly(part) != part {
			return "1.1.0"
		}
	}
	return parts[0] + "." + itoa(atoi(parts[1])+1) + ".0"
}

// --- reading it back -----------------------------------------------------------------------

// ProjectDetail is what GET /projects/{id} returns.
type ProjectDetail struct {
	Project  *Project  `json:"project"`
	SRS      *Envelope `json:"srs"`
	Summary  *Summary  `json:"summary"`
	Versions []Version `json:"versions"`
}

func (s *Service) ProjectDetail(ctx context.Context, projectID string) (*ProjectDetail, error) {
	project, err := s.Repo.GetProject(ctx, projectID)
	if err != nil || project == nil {
		return nil, notFound(err, "project not found")
	}
	latest, _ := s.Repo.LatestVersion(ctx, projectID)
	versions, _ := s.Repo.ListVersions(ctx, projectID)
	out := &ProjectDetail{Project: project, Versions: versions}
	if latest != nil {
		srs := latest.SRS
		summary := Summarize(&srs.Document)
		out.SRS, out.Summary = &srs, &summary
	}
	return out, nil
}

// LatestSRS is the current specification, or nil when none has been written.
func (s *Service) LatestSRS(ctx context.Context, projectID string) *Envelope {
	latest, err := s.Repo.LatestVersion(ctx, projectID)
	if err != nil || latest == nil {
		return nil
	}
	srs := latest.SRS
	return &srs
}

// LiveHandoff is the stored handoff rebuilt against the current builder
// contract, so an SRS written before a contract change still hands over.
func (s *Service) LiveHandoff(ctx context.Context, projectID string) (map[string]any, error) {
	srs := s.LatestSRS(ctx, projectID)
	if srs == nil {
		return nil, notFound(nil, "no SRS generated yet")
	}
	doc := srs.Document
	if doc.BuilderHandoff == nil {
		return nil, notFound(nil,
			"no builder handoff — this SRS predates the approved-plan flow")
	}

	planDoc, _ := s.ApprovedPlan(ctx, projectID)
	if planDoc == nil {
		planDoc, _ = s.Repo.LatestPlan(ctx, projectID)
	}
	if planDoc == nil {
		return doc.BuilderHandoff, nil
	}
	plan := planDoc.Plan
	plan.Workflows = journeysForEveryRole(&plan)
	pack := &Pack{AppLabel: firstText(doc.BuilderHandoff["appType"], doc.SystemCategory)}
	fresh := BuildHandoff(&plan, &doc, pack, AuthOn(pack, &plan))
	if len(fresh.Prompt) <= 200 {
		return doc.BuilderHandoff, nil
	}
	// A localized SRS stores a separately translated English prompt; refresh
	// the structured contract but keep the approved wording.
	body := asDoc(fresh)
	if firstText(doc.BuilderHandoff["prompt_language"]) == "English" {
		if stored := firstText(doc.BuilderHandoff["prompt"]); stored != "" {
			body["prompt"] = stored
			body["prompt_language"] = "English"
			body["source_document_language"] = firstNonEmpty(
				firstText(doc.BuilderHandoff["source_document_language"]), "English")
		}
	}
	return body, nil
}
