package deploy

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Vercel is the one target that is somebody else's platform rather than the
// customer's own account, so this is the only file in the package that talks
// to a third-party API directly.
//
// The token is the uncomfortable part and is treated accordingly: it is read
// from wherever `vercel login` put it, held for the length of one call, and
// never written to the run record, an event, or an artifact.

const vercelAPI = "https://api.vercel.com"

// SignInHint is what the Studio shows when there is no token to be found.
const SignInHint = "The Vercel CLI is not signed in. Open the Vercel Connection tab and run " +
	"the sign-in, or paste a Vercel access token there."

// Vercel is one authenticated caller.
type Vercel struct {
	Token  string
	TeamID string
}

// VercelToken is the token to use: the one that was pasted, or the one the CLI
// signed in with.
func VercelToken(supplied string) (string, error) {
	if token := strings.TrimSpace(supplied); token != "" {
		return token, nil
	}
	if token := readVercelToken(); token != "" {
		return token, nil
	}
	return "", badRequest(SignInHint)
}

// readVercelToken looks where the CLI stores its sign-in, most specific place
// first. Every platform puts it somewhere different.
func readVercelToken() string {
	candidates := []string{}
	if override := strings.TrimSpace(os.Getenv("VERCEL_DIR")); override != "" {
		candidates = append(candidates, filepath.Join(override, "auth.json"))
	}
	home := home()
	candidates = append(candidates, filepath.Join(home, ".vercel", "auth.json"))
	for _, variable := range []string{"APPDATA", "LOCALAPPDATA"} {
		base := strings.TrimSpace(os.Getenv(variable))
		if base == "" {
			continue
		}
		candidates = append(candidates,
			filepath.Join(base, "xdg.data", "com.vercel.cli", "auth.json"),
			filepath.Join(base, "com.vercel.cli", "auth.json"))
	}
	candidates = append(candidates,
		filepath.Join(home, "Library", "Application Support", "com.vercel.cli", "auth.json"))
	share := strings.TrimSpace(os.Getenv("XDG_DATA_HOME"))
	if share == "" {
		share = filepath.Join(home, ".local", "share")
	}
	candidates = append(candidates, filepath.Join(share, "com.vercel.cli", "auth.json"))

	for _, path := range candidates {
		body, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var file struct {
			Token string `json:"token"`
		}
		if json.Unmarshal(body, &file) != nil {
			continue
		}
		if token := strings.TrimSpace(file.Token); token != "" {
			return token
		}
	}
	return ""
}

// call is one Vercel API request. Every error it returns is already redacted,
// because an API error can quote the request that caused it.
func (v Vercel) call(ctx context.Context, method, path string,
	query map[string]string, body, into any) error {
	address, err := url.Parse(vercelAPI + path)
	if err != nil {
		return err
	}
	values := address.Query()
	for key, value := range query {
		values.Set(key, value)
	}
	if v.TeamID != "" {
		values.Set("teamId", v.TeamID)
	}
	address.RawQuery = values.Encode()

	var payload io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return err
		}
		payload = bytes.NewReader(encoded)
	}

	request, err := http.NewRequestWithContext(ctx, method, address.String(), payload)
	if err != nil {
		return err
	}
	request.Header.Set("Authorization", "Bearer "+v.Token)
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}

	response, err := (&http.Client{Timeout: 30 * time.Second}).Do(request)
	if err != nil {
		return errors.New("Vercel API " + method + " " + path + " failed: " + RedactText(err.Error()))
	}
	defer response.Body.Close()

	answer, _ := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if response.StatusCode >= 400 {
		return errors.New("Vercel API " + method + " " + path + " failed (" +
			strconv.Itoa(response.StatusCode) + "): " + RedactText(vercelMessage(answer)))
	}
	if into == nil || len(bytes.TrimSpace(answer)) == 0 {
		return nil
	}
	return json.Unmarshal(answer, into)
}

func vercelMessage(body []byte) string {
	var failure struct {
		Error struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	if json.Unmarshal(body, &failure) == nil && failure.Error.Message != "" {
		return failure.Error.Message
	}
	return clip(string(body), 200)
}

// --- who the token belongs to ------------------------------------------------------------

// VercelStatus is the connection summary the Studio's account panel shows.
type VercelStatus struct {
	Connected bool   `json:"connected"`
	Source    string `json:"source"`
	Message   string `json:"message,omitempty"`
	Username  string `json:"username,omitempty"`
	Email     string `json:"email,omitempty"`
	Name      string `json:"name,omitempty"`
	ID        string `json:"id,omitempty"`
}

// VercelConnection never fails: the panel asks it on every render, and "not
// signed in" is an answer.
func VercelConnection(ctx context.Context, supplied string) VercelStatus {
	token := strings.TrimSpace(supplied)
	source := "pasted token"
	if token == "" {
		token, source = readVercelToken(), "vercel CLI"
	}
	if token == "" {
		return VercelStatus{Source: "", Message: SignInHint}
	}

	var identity struct {
		User struct {
			Username string `json:"username"`
			Email    string `json:"email"`
			Name     string `json:"name"`
			ID       string `json:"id"`
		} `json:"user"`
	}
	if err := (Vercel{Token: token}).call(ctx, http.MethodGet, "/v2/user", nil, nil, &identity); err != nil {
		message := clip(err.Error(), 200)
		if strings.Contains(err.Error(), "(403)") {
			message = "The Vercel token was rejected. Sign in again from the Vercel Connection tab."
		}
		return VercelStatus{Source: source, Message: message}
	}
	return VercelStatus{
		Connected: true, Source: source,
		Username: identity.User.Username, Email: identity.User.Email,
		Name: identity.User.Name, ID: identity.User.ID,
	}
}

// --- the project -------------------------------------------------------------------------

// VercelProject is what the API says about a linked project.
type VercelProject struct {
	Name    string `json:"name"`
	Targets struct {
		Production struct {
			Alias []string `json:"alias"`
			URL   string   `json:"url"`
		} `json:"production"`
	} `json:"targets"`
}

// ProductionURL is where the app actually answers: an alias if it has one,
// because that address survives the next deployment.
func (p VercelProject) ProductionURL() string {
	for _, alias := range p.Targets.Production.Alias {
		if alias != "" {
			return "https://" + alias
		}
	}
	if p.Targets.Production.URL != "" {
		return "https://" + p.Targets.Production.URL
	}
	if p.Name != "" {
		return "https://" + p.Name + ".vercel.app"
	}
	return ""
}

func (v Vercel) Project(ctx context.Context, projectID string) (VercelProject, error) {
	var project VercelProject
	err := v.call(ctx, http.MethodGet, "/v9/projects/"+projectID, nil, nil, &project)
	return project, err
}

func (v Vercel) DeleteProject(ctx context.Context, projectID string) error {
	return v.call(ctx, http.MethodDelete, "/v9/projects/"+projectID, nil, nil, nil)
}

// VercelDeployment is one production deployment.
type VercelDeployment struct {
	UID        string `json:"uid"`
	ID         string `json:"id"`
	URL        string `json:"url"`
	State      string `json:"state"`
	ReadyState string `json:"readyState"`
	Created    int64  `json:"created"`
}

// Ready reports whether this deployment is one that could be promoted.
func (d VercelDeployment) Ready() bool { return d.ReadyState == "READY" || d.State == "READY" }

// Identifier is the id the promote endpoint takes.
func (d VercelDeployment) Identifier() string {
	if d.UID != "" {
		return d.UID
	}
	return d.ID
}

func (v Vercel) Deployments(ctx context.Context, projectID string, limit int) ([]VercelDeployment, error) {
	var payload struct {
		Deployments []VercelDeployment `json:"deployments"`
	}
	err := v.call(ctx, http.MethodGet, "/v6/deployments", map[string]string{
		"projectId": projectID, "limit": strconv.Itoa(limit), "target": "production",
	}, nil, &payload)
	return payload.Deployments, err
}

func (v Vercel) Promote(ctx context.Context, projectID, deploymentID string) error {
	return v.call(ctx, http.MethodPost, "/v9/projects/"+projectID+"/promote/"+deploymentID, nil, nil, nil)
}

// LogLine is one line of a build log, shaped for the Studio's log tab.
type LogLine struct {
	Timestamp any    `json:"timestamp"`
	Message   string `json:"message"`
}

func (v Vercel) DeploymentLogs(ctx context.Context, deploymentID string, limit int) ([]LogLine, error) {
	var events []struct {
		Created any    `json:"created"`
		Date    any    `json:"date"`
		Text    string `json:"text"`
		Payload struct {
			Text string `json:"text"`
		} `json:"payload"`
	}
	err := v.call(ctx, http.MethodGet, "/v3/deployments/"+deploymentID+"/events",
		map[string]string{"limit": strconv.Itoa(limit), "direction": "backward"}, nil, &events)
	if err != nil {
		return nil, err
	}
	rows := []LogLine{}
	for _, event := range events {
		text := event.Text
		if text == "" {
			text = event.Payload.Text
		}
		if text == "" {
			continue
		}
		at := event.Created
		if at == nil {
			at = event.Date
		}
		rows = append(rows, LogLine{Timestamp: at, Message: RedactText(text)})
	}
	if len(rows) > limit {
		rows = rows[len(rows)-limit:]
	}
	return rows, nil
}

// --- the environment ---------------------------------------------------------------------

// VercelEnv is one variable on the project. The value is never requested: this
// is used to find out what is already set, not what it is set to.
type VercelEnv struct {
	ID     string   `json:"id"`
	Key    string   `json:"key"`
	Target []string `json:"target"`
}

func (v Vercel) Environment(ctx context.Context, projectID string) ([]VercelEnv, error) {
	var payload struct {
		Envs []VercelEnv `json:"envs"`
	}
	err := v.call(ctx, http.MethodGet, "/v9/projects/"+projectID+"/env", nil, nil, &payload)
	return payload.Envs, err
}

func (v Vercel) SetEnvironment(ctx context.Context, projectID, key, value string) error {
	return v.call(ctx, http.MethodPost, "/v10/projects/"+projectID+"/env",
		map[string]string{"upsert": "true"},
		map[string]any{"key": key, "value": value, "type": "encrypted",
			"target": []string{"production"}}, nil)
}

func (v Vercel) DeleteEnvironment(ctx context.Context, projectID, envID string) error {
	return v.call(ctx, http.MethodDelete, "/v9/projects/"+projectID+"/env/"+envID, nil, nil, nil)
}

// --- domains ------------------------------------------------------------------------------

func (v Vercel) Domains(ctx context.Context, projectID string) ([]map[string]any, error) {
	var payload struct {
		Domains []map[string]any `json:"domains"`
	}
	err := v.call(ctx, http.MethodGet, "/v9/projects/"+projectID+"/domains", nil, nil, &payload)
	if payload.Domains == nil {
		payload.Domains = []map[string]any{}
	}
	return payload.Domains, err
}

// AddDomain returns whatever DNS record the customer now has to create.
func (v Vercel) AddDomain(ctx context.Context, projectID, domain string) (map[string]any, error) {
	result := map[string]any{}
	err := v.call(ctx, http.MethodPost, "/v10/projects/"+projectID+"/domains", nil,
		map[string]string{"name": domain}, &result)
	return result, err
}

func (v Vercel) RemoveDomain(ctx context.Context, projectID, domain string) error {
	return v.call(ctx, http.MethodDelete, "/v9/projects/"+projectID+"/domains/"+domain, nil, nil, nil)
}

// CheckDomain is the rule for what a customer may type into the domain box.
func CheckDomain(domain string) (string, error) {
	domain = strings.ToLower(strings.TrimSpace(domain))
	if domain == "" || strings.ContainsAny(domain, "/ ") {
		return "", badRequest("Enter a bare hostname, for example app.example.com")
	}
	return domain, nil
}
