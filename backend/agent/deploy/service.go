package deploy

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"agentforge/agent/core"
)

// The deployment agent's HTTP surface, as the Studio calls it.
//
// It ran on a port of its own until this port; now it is served in-process
// under /deploy/*, and the paths below are the same ones the Studio has always
// used. Nothing here does any work: each route reads a request, calls the
// thing that does, and writes what it said.

// Agent is the deployment agent, whole: the store everything is recorded in
// and the five things that read and write it.
type Agent struct {
	Store     *Store
	Analyzer  *Analyzer
	Deployer  *Deployer
	Monitor   *Monitor
	Exporter  Exporter
	SSO       *SSO
	Vault     *Vault
	Supervise *Supervisor
}

// NewAgent assembles the agent around one store, and one emitter that puts
// every stage's events into that store.
func NewAgent(root string, llm *core.LLM, hub *core.Hub) (*Agent, error) {
	store, err := NewStore(root)
	if err != nil {
		return nil, err
	}
	emit := func(runID string, event Event) {
		_ = store.AddEvent(runID, event)
		if hub != nil {
			// The Studio's console reads these live over the WebSocket as
			// well as by polling, so a long deployment is not silent.
			hub.Emit(map[string]any{
				"type": "deploy_event", "run_id": runID, "event": asMap(event),
			})
		}
	}

	vault := NewVault()
	service := &Agent{
		Store:    store,
		Analyzer: &Analyzer{Store: store, LLM: llm, Emit: emit},
		Deployer: NewDeployer(store, emit),
		Monitor:  &Monitor{Store: store, Emit: emit},
		Exporter: Exporter{Store: store},
		Vault:    vault,
		SSO:      NewSSO(vault),
	}
	service.Supervise = &Supervisor{Store: store, Monitor: service.Monitor,
		Deployer: service.Deployer, Emit: emit}
	return service, nil
}

// Watch recovers anything a previous process abandoned and then keeps looking.
func (s *Agent) Watch(ctx context.Context) {
	if recovered, err := s.Supervise.Once(ctx); err == nil {
		for _, item := range recovered {
			log.Printf("deploy: recovered run %s: %s → %s", shortID(item.RunID), item.From, item.To)
		}
	}
	s.Supervise.Watch(ctx)
}

// Handler is every route, on the mux the server mounts under /deploy.
func (s *Agent) Handler() http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("/api/health", func(w http.ResponseWriter, r *http.Request) {
		reply(w, 200, map[string]any{"status": "ok", "in_process": true})
	})
	mux.HandleFunc("/api/onboarding/status", func(w http.ResponseWriter, r *http.Request) {
		reply(w, 200, Onboarding(r.Context()))
	})
	mux.HandleFunc("/api/onboarding/login", s.login)
	mux.HandleFunc("/api/mongodb/check", s.mongodb)
	mux.HandleFunc("/api/runs", s.runs)
	mux.HandleFunc("/api/runs/analyze", s.analyze)
	mux.HandleFunc("/api/aws/", s.aws)
	mux.HandleFunc("/api/runs/", s.run)

	return mux
}

// --- runs ------------------------------------------------------------------------------------

func (s *Agent) runs(w http.ResponseWriter, r *http.Request) {
	runs, err := s.Store.ListRuns(0)
	if err != nil {
		fail(w, err)
		return
	}
	out := make([]map[string]any, 0, len(runs))
	for i := range runs {
		out = append(out, s.public(&runs[i]))
	}
	reply(w, 200, map[string]any{"runs": out})
}

func (s *Agent) analyze(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Path              string `json:"path"`
		Target            string `json:"target"`
		ValidateContainer *bool  `json:"validate_container"`
		CloudConsent      bool   `json:"cloud_consent"`
	}
	if !decode(w, r, &request) {
		return
	}
	if !request.CloudConsent {
		fail(w, badRequest("Ollama cloud source-context consent is required"))
		return
	}
	target := request.Target
	if target == "" {
		target = TargetEC2
	}
	validate := request.ValidateContainer == nil || *request.ValidateContainer

	runID, err := s.Analyzer.Start(context.WithoutCancel(r.Context()), request.Path, target, validate)
	if err != nil {
		fail(w, err)
		return
	}
	reply(w, 202, map[string]any{"run_id": runID, "state": string(StateAnalyzing)})
}

// run dispatches everything addressed to one run: /api/runs/{id}/{action}.
func (s *Agent) run(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/api/runs/")
	runID, action, _ := strings.Cut(strings.Trim(rest, "/"), "/")
	if runID == "" {
		fail(w, notFound("Run not found"))
		return
	}
	run, err := s.Store.GetRun(runID)
	if err != nil || run == nil {
		fail(w, notFound("Run not found"))
		return
	}

	if r.Method == http.MethodGet {
		s.readRun(w, r, run, action)
		return
	}
	s.actOnRun(w, r, run, action)
}

func (s *Agent) readRun(w http.ResponseWriter, r *http.Request, run *Run, action string) {
	query := r.URL.Query()

	switch action {
	case "":
		reply(w, 200, s.public(run))

	case "events":
		after, _ := strconv.ParseInt(query.Get("after"), 10, 64)
		events, err := s.Store.Events(run.ID, after, 0)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 200, map[string]any{"events": events})

	case "artifacts":
		records, err := s.Store.Artifacts(run.ID)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 200, map[string]any{"artifacts": records})

	case "diff":
		records, err := s.Store.Artifacts(run.ID)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 200, map[string]any{"diff": Diff(run.ProjectPath, run.StagedPath, records)})

	case "artifact":
		s.artifact(w, run, query.Get("path"))

	case "monitor":
		snapshot, err := s.Monitor.Snapshot(r.Context(), run.ID)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 200, snapshot)

	case "domains":
		domains, err := s.Deployer.Domains(r.Context(), run.ID)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 200, map[string]any{"domains": domains})

	case "evidence":
		items, err := s.Store.Evidence(run.ID)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 200, map[string]any{"evidence": items})

	case "evidence/file":
		id, _ := strconv.ParseInt(query.Get("id"), 10, 64)
		path, body, err := s.Exporter.EvidenceFile(run.ID, id)
		if err != nil {
			fail(w, err)
			return
		}
		send(w, body, contentType(path), "")

	case "export.zip":
		body, err := s.Exporter.Zip(run.ID)
		if err != nil {
			fail(w, err)
			return
		}
		send(w, body, "application/zip", "deployment-evidence-"+shortID(run.ID)+".zip")

	case "report.pdf":
		body, err := s.Exporter.PDF(run.ID)
		if err != nil {
			fail(w, err)
			return
		}
		send(w, body, "application/pdf", "deployment-report-"+shortID(run.ID)+".pdf")

	default:
		fail(w, notFound("Run action not found"))
	}
}

func (s *Agent) actOnRun(w http.ResponseWriter, r *http.Request, run *Run, action string) {
	var request struct {
		Approved            bool   `json:"approved"`
		Confirm             bool   `json:"confirm"`
		Force               bool   `json:"force"`
		Remove              bool   `json:"remove"`
		Verified            *bool  `json:"verified"`
		AWSProfile          string `json:"aws_profile"`
		Region              string `json:"region"`
		MongoURI            string `json:"mongodb_uri"`
		CredentialReference string `json:"credential_reference"`
		VercelToken         string `json:"vercel_token"`
		Domain              string `json:"domain"`
		Name                string `json:"name"`
		DataURL             string `json:"data_url"`
		ID                  int64  `json:"id"`
		ValidateContainer   *bool  `json:"validate_container"`
	}
	if !decode(w, r, &request) {
		return
	}
	// A deployment outlives the request that started it.
	ctx := context.WithoutCancel(r.Context())

	switch action {
	case "deploy":
		if !request.Approved {
			fail(w, badRequest("Deployment approval is required"))
			return
		}
		credentials, err := s.credentials(request.CredentialReference)
		if err != nil {
			fail(w, err)
			return
		}
		region := request.Region
		if region == "" {
			region = DefaultRegion
		}
		err = s.Deployer.Start(ctx, Request{
			RunID: run.ID, AWSProfile: request.AWSProfile, Region: region,
			MongoURI: strings.TrimSpace(request.MongoURI), Approved: true,
			Credentials: credentials, VercelToken: strings.TrimSpace(request.VercelToken),
		})
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 202, map[string]any{"accepted": true, "run_id": run.ID})

	case "cancel":
		if !request.Confirm {
			fail(w, badRequest("Cancellation confirmation is required"))
			return
		}
		result, err := s.Deployer.Cancel(ctx, run.ID)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 202, result)

	case "teardown":
		if !request.Confirm {
			fail(w, badRequest("Teardown confirmation is required"))
			return
		}
		credentials, err := s.credentials(request.CredentialReference)
		if err != nil {
			fail(w, err)
			return
		}
		result, err := s.Deployer.Teardown(ctx, run.ID, credentials, request.Force)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 202, result)

	case "rollback":
		// Rolling back talks to a machine over SSM, which takes longer than
		// the Studio waits, so the answer is that it started.
		go func() {
			if _, err := s.Deployer.Rollback(ctx, run.ID); err != nil {
				_, _ = s.Store.Update(run.ID, map[string]any{"error": err.Error()})
				s.Deployer.emit(run.ID).send(Event{Type: EventError, Stage: "rollback",
					Status: StatusFailed, Percent: 100, Message: err.Error()})
			}
		}()
		reply(w, 202, map[string]any{"accepted": true})

	case "domains":
		if request.Remove {
			result, err := s.Deployer.RemoveDomain(ctx, run.ID, request.Domain)
			if err != nil {
				fail(w, err)
				return
			}
			reply(w, 200, result)
			return
		}
		result, err := s.Deployer.AddDomain(ctx, run.ID, request.Domain)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 201, result)

	case "retry":
		validate := request.ValidateContainer == nil || *request.ValidateContainer
		runID, err := s.Analyzer.Start(ctx, run.ProjectPath, TargetOf(run), validate)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 202, map[string]any{"run_id": runID})

	case "evidence":
		name := request.Name
		if name == "" {
			name = "evidence"
		}
		saved, err := s.Exporter.SaveEvidence(run.ID, name, request.DataURL)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 201, saved)

	case "evidence/verify":
		verified := request.Verified == nil || *request.Verified
		if err := s.Store.VerifyEvidence(run.ID, request.ID, verified); err != nil {
			fail(w, err)
			return
		}
		reply(w, 200, map[string]any{"updated": true})

	default:
		fail(w, notFound("Run action not found"))
	}
}

// credentials are the vaulted keys behind a reference, in the form a process
// takes them. No reference means the customer's own configured profile.
func (s *Agent) credentials(reference string) (map[string]string, error) {
	if strings.TrimSpace(reference) == "" {
		return nil, nil
	}
	values, err := s.Vault.Get(reference)
	if err != nil {
		return nil, err
	}
	return Environment(values), nil
}

// artifact serves one generated file, and only one that is on the manifest —
// the path comes from the Studio, and a run's staging directory holds the
// customer's whole project.
func (s *Agent) artifact(w http.ResponseWriter, run *Run, relative string) {
	records, err := s.Store.Artifacts(run.ID)
	if err != nil {
		fail(w, err)
		return
	}
	known := false
	for _, record := range records {
		if record.Path == relative {
			known = true
			break
		}
	}
	if !known {
		fail(w, badRequest("Artifact path is not part of the reviewed manifest"))
		return
	}

	path := filepath.Join(run.StagedPath, filepath.FromSlash(relative))
	if !within(run.StagedPath, path) {
		fail(w, badRequest("Invalid artifact path"))
		return
	}
	body, err := os.ReadFile(path)
	if err != nil {
		fail(w, notFound("Artifact is no longer on disk"))
		return
	}
	send(w, body, contentType(path), "")
}

// public is a run as the Studio reads it: redacted, masked, and told whether
// its artifacts were made by this version of the agent.
func (s *Agent) public(run *Run) map[string]any {
	value := object(mask(Redact(run)))
	value["id"] = run.ID

	version := 0
	if body, err := os.ReadFile(filepath.Join(run.StagedPath, "deployment-manifest.json")); err == nil {
		var manifest struct {
			Version int `json:"version"`
		}
		if json.Unmarshal(body, &manifest) == nil {
			version = manifest.Version
		}
	}
	value["artifact_schema_version"] = version
	value["artifacts_current"] = version == ArtifactVersion
	return value
}

// --- accounts --------------------------------------------------------------------------------

func (s *Agent) login(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Tool    string `json:"tool"`
		Profile string `json:"profile"`
		Region  string `json:"region"`
	}
	if !decode(w, r, &request) {
		return
	}
	profile, err := StartLogin(request.Tool, request.Profile, request.Region)
	if err != nil {
		fail(w, err)
		return
	}
	reply(w, 202, map[string]any{"started": true, "tool": request.Tool, "profile": profile})
}

func (s *Agent) mongodb(w http.ResponseWriter, r *http.Request) {
	var request struct {
		URI string `json:"uri"`
	}
	if !decode(w, r, &request) {
		return
	}
	reply(w, 200, CheckMongo(r.Context(), request.URI))
}

// aws is the in-app account work: signing in, and asking the account whether
// this deployment could succeed.
func (s *Agent) aws(w http.ResponseWriter, r *http.Request) {
	action := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/aws/"), "/")
	var request struct {
		StartURL         string  `json:"start_url"`
		Region           string  `json:"region"`
		DeploymentRegion string  `json:"deployment_region"`
		FlowID           string  `json:"flow_id"`
		AccountID        string  `json:"account_id"`
		RoleName         string  `json:"role_name"`
		Profile          string  `json:"profile"`
		PersistProfile   bool    `json:"persist_profile"`
		Target           string  `json:"target"`
		AWSProfile       string  `json:"aws_profile"`
		CredentialRef    string  `json:"credential_reference"`
		ServiceCode      string  `json:"service_code"`
		QuotaCode        string  `json:"quota_code"`
		Desired          float64 `json:"desired"`
		PrincipalArn     string  `json:"principal_arn"`
		Token            string  `json:"token"`
	}
	if !decode(w, r, &request) {
		return
	}

	switch action {
	case "sso/start":
		started, err := s.SSO.Start(r.Context(), request.StartURL, request.Region)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 202, started)

	case "sso/poll":
		result, err := s.SSO.Poll(r.Context(), request.FlowID)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 200, result)

	case "sso/roles":
		roles, err := s.SSO.Roles(r.Context(), request.FlowID, request.AccountID)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 200, map[string]any{"roles": roles})

	case "sso/select":
		result, err := s.SSO.Select(r.Context(), request.FlowID, request.AccountID, request.RoleName)
		if err != nil {
			fail(w, err)
			return
		}
		if request.PersistProfile {
			name, err := PersistProfile(request.Profile, s.SSO.StartURL(request.FlowID),
				text(result["region"]), text(result["account_id"]),
				text(result["role_name"]), request.DeploymentRegion)
			if err != nil {
				fail(w, err)
				return
			}
			result["profile"] = name
		}
		reply(w, 200, result)

	case "preflight":
		aws, err := s.account(request.AWSProfile, request.Region, request.CredentialRef)
		if err != nil {
			fail(w, err)
			return
		}
		target := request.Target
		if _, known := Profiles[target]; !known {
			target = TargetEC2
		}
		reply(w, 200, map[string]any{
			"target":      target,
			"quotas":      aws.CheckQuotas(r.Context(), target),
			"permissions": aws.CheckPermissions(r.Context(), target),
		})

	case "quota/request":
		aws, err := s.account(request.AWSProfile, request.Region, request.CredentialRef)
		if err != nil {
			fail(w, err)
			return
		}
		result, err := aws.RequestQuota(r.Context(), request.ServiceCode, request.QuotaCode, request.Desired)
		if err != nil {
			fail(w, err)
			return
		}
		reply(w, 202, result)

	case "vercel/status":
		reply(w, 200, VercelConnection(r.Context(), request.Token))

	case "bootstrap-role-template":
		reply(w, 200, map[string]any{"template": BootstrapRoleTemplate(request.PrincipalArn)})

	default:
		fail(w, notFound("AWS action not found"))
	}
}

func (s *Agent) account(profile, region, reference string) (AWS, error) {
	credentials, err := s.credentials(reference)
	if err != nil {
		return AWS{}, err
	}
	return AWS{Profile: profile, Region: region, Keys: credentials}, nil
}

// --- what the machine has --------------------------------------------------------------------

// Onboarding is what the Studio's account panel shows: which tools are
// installed, which AWS profiles work, and whether GitHub is signed in.
func Onboarding(ctx context.Context) map[string]any {
	status := ToolStatus(ctx)
	profiles := []string{}
	identities := map[string]any{}

	if installed(status, "aws") {
		if out := Exec(ctx, Command{Name: "aws", Args: []string{"configure", "list-profiles"},
			Timeout: 10 * time.Second}); out.OK() {
			for _, line := range out.Lines() {
				if name := strings.TrimSpace(line); name != "" {
					profiles = append(profiles, name)
				}
			}
		}
		for index, profile := range profiles {
			if index == 20 {
				break
			}
			if identity := profileIdentity(ctx, profile); identity != nil {
				identities[profile] = identity
			}
		}
	}

	github, account := false, ""
	if installed(status, "gh") {
		github = Exec(ctx, Command{Name: "gh", Args: []string{"auth", "status"},
			Timeout: 15 * time.Second}).OK()
		if github {
			if out := Exec(ctx, Command{Name: "gh", Args: []string{"api", "user", "--jq", ".login"},
				Timeout: 15 * time.Second}); out.OK() {
				account = strings.TrimSpace(out.Stdout)
			}
		}
	}

	return map[string]any{
		"tools":                status,
		"aws_profiles":         profiles,
		"aws_authenticated":    len(identities) > 0,
		"aws_identities":       identities,
		"github_authenticated": github,
		"github_account":       account,
		"cloud_notice": "Selected source context is sent to Ollama Cloud for deployment " +
			"planning. Credentials and detected secret values are always redacted.",
	}
}

// profileIdentity is who a configured profile is, and whether the account it
// belongs to can actually be deployed into.
func profileIdentity(ctx context.Context, profile string) map[string]any {
	region := DefaultRegion
	if out := Exec(ctx, Command{Name: "aws",
		Args: []string{"configure", "get", "region", "--profile", profile}, Timeout: 10 * time.Second}); out.OK() {
		if configured := strings.TrimSpace(out.Stdout); configured != "" {
			region = configured
		}
	}
	aws := AWS{Profile: profile, Region: region}

	identity, err := aws.Whoami(ctx)
	if err != nil {
		return nil
	}

	// One real call, because an account that has never been activated
	// authenticates perfectly well and then refuses everything.
	ready, problem := true, ""
	if err := aws.call(ctx, nil, "cloudformation", "list-stacks", "--max-items", "1"); err != nil {
		ready = false
		problem = serviceProblem(err.Error())
	}
	return map[string]any{
		"account": identity.Account, "arn": identity.Arn, "user_id": identity.UserID,
		"region": region, "service_ready": ready, "service_error": problem,
	}
}

func serviceProblem(text string) string {
	lowered := strings.ToLower(text)
	switch {
	case strings.Contains(lowered, "optinrequired"), strings.Contains(lowered, "not subscribed"),
		strings.Contains(lowered, "needs a subscription"):
		return "AWS account services are not activated (OptInRequired)"
	case strings.Contains(lowered, "expired"):
		return "AWS login session expired"
	case strings.Contains(lowered, "accessdenied"), strings.Contains(lowered, "not authorized"):
		return "CloudFormation access is denied for this identity"
	}
	return "CloudFormation preflight failed"
}

func installed(status map[string]any, name string) bool {
	return object(status[name])["installed"] == true
}

// --- writing a reply ---------------------------------------------------------------------

func reply(w http.ResponseWriter, status int, value any) {
	body, err := json.Marshal(value)
	if err != nil {
		body = []byte(`{"error":"the response could not be encoded"}`)
		status = 500
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// fail turns an error into the status it already knows it wants.
func fail(w http.ResponseWriter, err error) {
	status := 500
	var known statusError
	if errors.As(err, &known) {
		status = known.Status
	}
	reply(w, status, map[string]any{"error": RedactText(err.Error())})
}

func decode(w http.ResponseWriter, r *http.Request, into any) bool {
	if r.Body == nil || r.Method == http.MethodGet {
		return true
	}
	body, err := readBody(r)
	if err != nil {
		fail(w, badRequest(err.Error()))
		return false
	}
	if len(strings.TrimSpace(string(body))) == 0 {
		return true
	}
	if err := json.Unmarshal(body, into); err != nil {
		fail(w, badRequest("JSON request body must be an object"))
		return false
	}
	return true
}

// bodyLimit is the largest request the agent reads. Evidence images are the
// only thing that comes close.
const bodyLimit = 16 << 20

func readBody(r *http.Request) ([]byte, error) {
	defer r.Body.Close()
	body := make([]byte, 0, 4096)
	buffer := make([]byte, 32<<10)
	for {
		read, err := r.Body.Read(buffer)
		body = append(body, buffer[:read]...)
		if len(body) > bodyLimit {
			return nil, errors.New("Request body exceeds 16 MB")
		}
		if err != nil {
			return body, nil
		}
	}
}

func send(w http.ResponseWriter, body []byte, kind, filename string) {
	w.Header().Set("Content-Type", kind)
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	if filename != "" {
		w.Header().Set("Content-Disposition", `attachment; filename="`+filename+`"`)
	}
	w.WriteHeader(200)
	_, _ = w.Write(body)
}

func contentType(path string) string {
	if kind := mime.TypeByExtension(strings.ToLower(filepath.Ext(path))); kind != "" {
		return kind
	}
	return "text/plain; charset=utf-8"
}

// --- the Studio's one button ---------------------------------------------------------------

// StudioRequest is what the Deploy button sends, plus what the Studio has
// already collected in Settings: the account to deploy into, the database the
// app will use, and the token for the one target that needs one.
type StudioRequest struct {
	Path          string
	Target        string
	ValidateBuild bool

	AWSProfile  string
	Region      string
	MongoURI    string
	VercelToken string
}

// Deploy is the whole flow behind one button: read the project, plan it,
// generate and check the files, and — only if all of that passed — deploy it.
//
// The button is the approval. Everything the review would have shown is still
// written down and still has to pass; what the customer does not have to do is
// approve the same deployment twice.
func (s *Agent) Deploy(ctx context.Context, request StudioRequest) (string, error) {
	target := request.Target
	if target == "" {
		target = TargetEC2
	}
	runID, err := s.Analyzer.Start(ctx, request.Path, target, request.ValidateBuild)
	if err != nil {
		return "", err
	}

	go s.deployWhenReviewed(ctx, runID, request)
	return runID, nil
}

// deployWhenReviewed waits for the analysis to finish and then deploys what it
// produced. An analysis that failed stops here: the run says why, and the
// console already showed it.
func (s *Agent) deployWhenReviewed(ctx context.Context, runID string, request StudioRequest) {
	run, ok := s.awaitReview(ctx, runID)
	if !ok {
		return
	}
	region := strings.TrimSpace(request.Region)
	if region == "" {
		region = DefaultRegion
	}

	err := s.Deployer.Start(ctx, Request{
		RunID: run.ID, AWSProfile: strings.TrimSpace(request.AWSProfile), Region: region,
		MongoURI: strings.TrimSpace(request.MongoURI), Approved: true,
		VercelToken: strings.TrimSpace(request.VercelToken),
	})
	if err == nil {
		return
	}
	_, _ = s.Store.Transition(runID, StateFailed, map[string]any{"error": err.Error()})
	s.Deployer.emit(runID).send(Event{Type: EventError, Stage: "deploy", Status: StatusFailed,
		Percent: 100, Message: err.Error()})
}

// reviewPatience is how long the analysis is given to reach the review screen.
// It installs and builds the project, so it is measured in tens of minutes.
const reviewPatience = 45 * time.Minute

// awaitReview waits for the run to leave ANALYZING, and reports whether what
// it left behind is deployable.
func (s *Agent) awaitReview(ctx context.Context, runID string) (*Run, bool) {
	deadline := time.Now().Add(reviewPatience)
	for time.Now().Before(deadline) {
		if !sleep(ctx, time.Second) {
			return nil, false
		}
		run, err := s.Store.GetRun(runID)
		if err != nil || run == nil {
			return nil, false
		}
		switch run.State {
		case StateDraft, StateAnalyzing:
			continue
		case StateReviewReady:
			return run, true
		default:
			return nil, false
		}
	}
	return nil, false
}

// --- one project's deployments -----------------------------------------------------------

// Terminal are the states a run does not come back from. The Studio has the
// same list, and stops polling when it sees one.
var Terminal = set(StateLive, StateFailed, StateRolledBack, StateDestroyed, StateCancelled)

// ForProject is what the Studio's deploy panel shows for one project: the run
// happening now, if any, and the last one that finished.
func (s *Agent) ForProject(path string) (live, last map[string]any) {
	wanted, err := filepath.Abs(path)
	if err != nil {
		return nil, nil
	}
	wanted = strings.ToLower(wanted)

	runs, err := s.Store.ListRuns(250)
	if err != nil {
		return nil, nil
	}
	for i := range runs {
		run := &runs[i]
		mine, err := filepath.Abs(run.ProjectPath)
		if err != nil || strings.ToLower(mine) != wanted {
			continue
		}
		// ListRuns is newest first, so the first match of each kind is the
		// one the panel wants.
		record := s.public(run)
		record["run_id"] = run.ID
		record["state"] = string(run.State)
		record["target"] = TargetOf(run)
		if last == nil {
			last = record
		}
		if live == nil && !Terminal[run.State] {
			live = record
		}
		if live != nil && last != nil {
			break
		}
	}
	return live, last
}
