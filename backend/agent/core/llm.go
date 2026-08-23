package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/tmc/langchaingo/llms"
	"github.com/tmc/langchaingo/llms/ollama"
)

// Model routing, settings and the JSON repair loop. Ollama is reached through
// langchaingo; cloud models go to ollama.com with the saved API key, local ones
// to the daemon.

const (
	DefaultLocalHost = "http://localhost:11434"
	CloudHost        = "https://ollama.com"
	DefaultModel     = "qwen2.5-coder:14b"
	LocalDefaultCtx  = 16384
	CloudDefaultCtx  = 131072

	llmAttempts   = 4
	llmBaseDelay  = 2500 * time.Millisecond
	llmMaxDelay   = 30 * time.Second
	repairRounds  = 3
	requestExpiry = 30 * time.Minute
)

// FallbackCloud is what the picker offers when ollama.com cannot be listed.
var FallbackCloud = []string{
	"qwen3-coder:480b-cloud", "deepseek-v3.1:671b-cloud",
	"gpt-oss:120b-cloud", "kimi-k2:1t-cloud", "glm-4.6:cloud",
	"minimax-m2:cloud",
}

// SettingsPath is the file the Studio's settings modal writes through us. The
// SRS and deployment sidecars read the same file.
func SettingsPath() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return filepath.Join(".agentforge", "settings.json")
	}
	return filepath.Join(home, ".agentforge", "settings.json")
}

// LoadSettings reads ~/.agentforge/settings.json. A missing file is empty.
func LoadSettings() map[string]any {
	out := map[string]any{}
	data, err := os.ReadFile(SettingsPath())
	if err != nil {
		return out
	}
	_ = json.Unmarshal(data, &out)
	return out
}

// SaveSettings merge-writes the settings file, the way the Python backend did.
func SaveSettings(patch map[string]any) error {
	current := LoadSettings()
	for k, v := range patch {
		current[k] = v
	}
	path := SettingsPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(current, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o600)
}

func settingString(s map[string]any, key string) string {
	if v, ok := s[key]; ok {
		if str, ok := v.(string); ok {
			return strings.TrimSpace(str)
		}
	}
	return ""
}

// IsCloud reports whether a model id names an Ollama Cloud model.
func IsCloud(model string) bool {
	m := strings.ToLower(strings.TrimSpace(model))
	return strings.HasSuffix(m, "-cloud") || strings.HasSuffix(m, ":cloud")
}

// LocalHost is the Ollama daemon URL: env, then settings, then the default.
func LocalHost() string {
	host := strings.TrimSpace(os.Getenv("OLLAMA_HOST"))
	if host == "" {
		host = settingString(LoadSettings(), "ollama_host")
	}
	if host == "" {
		host = DefaultLocalHost
	}
	if !strings.HasPrefix(host, "http") {
		host = "http://" + host
	}
	return strings.TrimRight(host, "/")
}

// APIKey is the Ollama Cloud key: env first, then settings.
func APIKey() string {
	if k := strings.TrimSpace(os.Getenv("OLLAMA_API_KEY")); k != "" {
		return k
	}
	return settingString(LoadSettings(), "ollama_api_key")
}

// NumCtx is the context window to request for a model.
func NumCtx(model string) int {
	if IsCloud(model) {
		return CloudDefaultCtx
	}
	raw := strings.TrimSpace(os.Getenv("AGENTFORGE_NUM_CTX"))
	if raw == "" {
		s := LoadSettings()
		if v, ok := s["local_num_ctx"]; ok {
			raw = fmt.Sprintf("%v", v)
			raw = strings.TrimSuffix(raw, ".0")
		}
	}
	if n, err := strconv.Atoi(raw); err == nil && n > 0 {
		return max(4096, n)
	}
	return LocalDefaultCtx
}

// Roles the Studio lets a user assign a model to.
const (
	RolePlanner = "planner"
	RoleDesign  = "design"
	RoleBuilder = "builder"
	RoleQA      = "builder" // QA writes code, so it follows the builder model
)

// LLM holds one langchaingo client per model and applies the retry and repair
// policy every node depends on.
type LLM struct {
	mu     sync.Mutex
	byName map[string]*ollama.LLM
}

func NewLLM() *LLM { return &LLM{byName: map[string]*ollama.LLM{}} }

// ModelFor resolves the model for a role, falling back to the shared agent
// model and finally to the built-in default.
func (l *LLM) ModelFor(role string) string {
	s := LoadSettings()
	if m := settingString(s, role+"_model"); m != "" {
		return m
	}
	if m := settingString(s, "agent_model"); m != "" {
		return m
	}
	return DefaultModel
}

// client returns the langchaingo LLM for a model, building it once.
func (l *LLM) client(model string) (*ollama.LLM, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if c, ok := l.byName[model]; ok {
		return c, nil
	}
	base := LocalHost()
	httpClient := &http.Client{Timeout: requestExpiry}
	if IsCloud(model) {
		key := APIKey()
		if key == "" {
			return nil, fmt.Errorf("%s is a cloud model but no Ollama API key is saved", model)
		}
		base = CloudHost
		httpClient.Transport = bearer{key: key, next: http.DefaultTransport}
	}
	c, err := ollama.New(
		ollama.WithServerURL(base),
		ollama.WithModel(model),
		ollama.WithHTTPClient(httpClient),
		ollama.WithRunnerNumCtx(NumCtx(model)),
	)
	if err != nil {
		return nil, err
	}
	l.byName[model] = c
	return c, nil
}

// bearer attaches the cloud key without langchaingo needing to know about it.
type bearer struct {
	key  string
	next http.RoundTripper
}

func (b bearer) RoundTrip(req *http.Request) (*http.Response, error) {
	clone := req.Clone(req.Context())
	clone.Header.Set("Authorization", "Bearer "+b.key)
	return b.next.RoundTrip(clone)
}

// Text asks for prose and retries while the daemon is merely busy.
func (l *LLM) Text(ctx context.Context, role, system, user string) (string, error) {
	return l.generate(ctx, role, system, user, false, nil)
}

// Stream asks for prose and hands each token to onToken as it arrives.
func (l *LLM) Stream(ctx context.Context, role, system, user string, onToken func(string)) (string, error) {
	return l.generate(ctx, role, system, user, false, onToken)
}

// JSON asks for one JSON object and decodes it into `into`. When the model
// returns something unparseable it is shown the error and asked again, which is
// the single most effective accuracy fix for small local models.
func (l *LLM) JSON(ctx context.Context, role, system, user string, into any) error {
	return l.JSONValid(ctx, role, system, user, into, nil)
}

// JSONValid is JSON with a check the decoded value has to pass. A failed check
// goes back to the model through the same repair loop a parse error does,
// which is what gets a small local model to a depth floor it missed the first
// time. The check reads whatever `into` points at.
func (l *LLM) JSONValid(ctx context.Context, role, system, user string, into any, check func() error) error {
	messages := []llms.MessageContent{
		llms.TextParts(llms.ChatMessageTypeSystem, system),
		llms.TextParts(llms.ChatMessageTypeHuman, user),
	}
	var lastErr error
	for round := 0; round < repairRounds; round++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		text, err := l.chat(ctx, role, messages, true, nil)
		if err != nil {
			return err
		}
		body := ExtractJSON(text)
		if body == "" {
			lastErr = errors.New("the reply contained no JSON object")
		} else if err := json.Unmarshal([]byte(body), into); err != nil {
			lastErr = err
		} else if check == nil {
			return nil
		} else if err := check(); err != nil {
			lastErr = err
		} else {
			return nil
		}
		messages = append(messages,
			llms.TextParts(llms.ChatMessageTypeAI, truncate(text, 3000)),
			llms.TextParts(llms.ChatMessageTypeHuman, fmt.Sprintf(
				"That did not parse: %v.\nReply with ONLY one valid JSON object — "+
					"no prose, no markdown fences. Keep everything that was already "+
					"correct and fix just the problem named above.", lastErr)))
	}
	return fmt.Errorf("model could not produce valid JSON after %d attempts: %w", repairRounds, lastErr)
}

func (l *LLM) generate(ctx context.Context, role, system, user string, jsonMode bool, onToken func(string)) (string, error) {
	messages := []llms.MessageContent{
		llms.TextParts(llms.ChatMessageTypeSystem, system),
		llms.TextParts(llms.ChatMessageTypeHuman, user),
	}
	return l.chat(ctx, role, messages, jsonMode, onToken)
}

// chat is the one place a request actually leaves the process, so the retry
// policy lives here and nowhere else.
func (l *LLM) chat(ctx context.Context, role string, messages []llms.MessageContent, jsonMode bool, onToken func(string)) (string, error) {
	model := l.ModelFor(role)
	client, err := l.client(model)
	if err != nil {
		return "", err
	}
	opts := []llms.CallOption{llms.WithTemperature(0.2), llms.WithModel(model)}
	if jsonMode {
		opts = append(opts, llms.WithJSONMode())
	}
	if onToken != nil {
		opts = append(opts, llms.WithStreamingFunc(func(_ context.Context, chunk []byte) error {
			onToken(string(chunk))
			return nil
		}))
	}

	delay := llmBaseDelay
	var lastErr error
	for attempt := 1; attempt <= llmAttempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return "", err
		}
		resp, err := client.GenerateContent(ctx, messages, opts...)
		if err == nil {
			if len(resp.Choices) == 0 {
				return "", fmt.Errorf("%s returned no choices", model)
			}
			return resp.Choices[0].Content, nil
		}
		lastErr = err
		if !IsTransient(err) || attempt == llmAttempts {
			break
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(delay):
		}
		if delay *= 2; delay > llmMaxDelay {
			delay = llmMaxDelay
		}
	}
	return "", fmt.Errorf("%s failed: %w", model, lastErr)
}

// transientText matches the daemon being busy rather than giving a real answer.
var transientText = []string{
	"overload", "temporarily", "try again", "too many requests", "unavailable",
	"connection reset", "connection aborted", "broken pipe", "timed out",
	"timeout", "eof", "no such host", "connection refused",
}

// IsTransient reports whether a failure is worth retrying.
func IsTransient(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false
	}
	msg := strings.ToLower(err.Error())
	for _, code := range []string{"429", "500", "502", "503", "504", "529"} {
		if strings.Contains(msg, code) {
			return true
		}
	}
	for _, frag := range transientText {
		if strings.Contains(msg, frag) {
			return true
		}
	}
	return false
}

// ExtractJSON pulls the first balanced JSON object or array out of a reply,
// tolerating markdown fences and leading prose.
func ExtractJSON(text string) string {
	text = strings.TrimSpace(text)
	if i := strings.Index(text, "```"); i >= 0 {
		rest := text[i+3:]
		if nl := strings.IndexByte(rest, '\n'); nl >= 0 {
			rest = rest[nl+1:]
		}
		if end := strings.Index(rest, "```"); end >= 0 {
			rest = rest[:end]
		}
		text = strings.TrimSpace(rest)
	}
	start := strings.IndexAny(text, "{[")
	if start < 0 {
		return ""
	}
	open := text[start]
	close := byte('}')
	if open == '[' {
		close = ']'
	}
	depth, inString, escaped := 0, false, false
	for i := start; i < len(text); i++ {
		c := text[i]
		switch {
		case escaped:
			escaped = false
		case c == '\\' && inString:
			escaped = true
		case c == '"':
			inString = !inString
		case inString:
			// characters inside a string never change nesting
		case c == open:
			depth++
		case c == close:
			if depth--; depth == 0 {
				return text[start : i+1]
			}
		}
	}
	return ""
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// ModelInfo is one row of the model picker.
type ModelInfo struct {
	ID     string `json:"id"`
	Label  string `json:"label,omitempty"`
	Ctx    int    `json:"ctx,omitempty"`
	Vision bool   `json:"vision,omitempty"`
}

// Catalog answers GET /models in the shape studio/lib/models.js parses.
func (l *LLM) Catalog(ctx context.Context) map[string]any {
	key := APIKey()
	local := listTags(ctx, LocalHost(), "")
	installed := make([]string, 0, len(local))
	for _, m := range local {
		installed = append(installed, m.ID)
	}

	var cloud []ModelInfo
	via := "none"
	if key != "" {
		via = "key"
		cloud = listTags(ctx, CloudHost, key)
		if len(cloud) == 0 {
			for _, id := range FallbackCloud {
				cloud = append(cloud, ModelInfo{ID: id, Ctx: CloudDefaultCtx})
			}
		}
	}
	return map[string]any{
		"cloud":         cloud,
		"local_models":  local,
		"local":         installed,
		"cloud_enabled": key != "",
		"cloud_via":     via,
	}
}

// listTags asks an Ollama endpoint what it can serve.
func listTags(ctx context.Context, base, key string) []ModelInfo {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(base, "/")+"/api/tags", nil)
	if err != nil {
		return nil
	}
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	client := &http.Client{Timeout: 8 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil
	}
	var body struct {
		Models []struct {
			Name    string `json:"name"`
			Model   string `json:"model"`
			Details struct {
				Family string `json:"family"`
			} `json:"details"`
		} `json:"models"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil
	}
	out := make([]ModelInfo, 0, len(body.Models))
	for _, m := range body.Models {
		id := m.Name
		if id == "" {
			id = m.Model
		}
		if id == "" {
			continue
		}
		out = append(out, ModelInfo{
			ID:     id,
			Ctx:    NumCtx(id),
			Vision: strings.Contains(strings.ToLower(id), "vl") || m.Details.Family == "mllama",
		})
	}
	return out
}

// Health reports whether the configured models can actually be reached.
func (l *LLM) Health(ctx context.Context) map[string]any {
	local := listTags(ctx, LocalHost(), "")
	return map[string]any{
		"available": len(local) > 0,
		"base_url":  LocalHost(),
		"models":    local,
		"cloud":     APIKey() != "",
	}
}
