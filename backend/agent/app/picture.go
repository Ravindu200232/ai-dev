package app

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"agentforge/agent/core"
)

// Pictures are drawn by Fooocus, a local image generator with a Gradio API. We
// do not ship it: if one is answering we use it, and if none is we say so
// plainly rather than pretending the button works.

const (
	generateTimeout = 10 * time.Minute
	configTimeout   = 10 * time.Second
	maxUploadBytes  = 8 << 20
)

// defaultHosts are the ports a local Fooocus listens on out of the box.
var defaultHosts = []string{"http://127.0.0.1:7865", "http://127.0.0.1:7860"}

// aspects are Fooocus's own resolution labels.
var aspects = map[string]string{
	"banner": "1664*576", "wide": "1344*768", "landscape": "1152*896",
	"square": "1024*1024", "portrait": "896*1152", "poster": "832*1216",
}

// Gradio labels the three inputs we override.
const (
	aspectLabel = "Aspect Ratios"
	countLabel  = "Image Number"
	seedLabel   = "Seed"
)

// Pictures talks to whichever Fooocus is running.
type Pictures struct {
	Paths core.Paths

	mu       sync.Mutex
	host     string
	template []any          // every input's default, in order
	labels   map[string]int // label -> position in that list
	fnIndex  int
	loaded   bool
}

func NewPictures(paths core.Paths) *Pictures { return &Pictures{Paths: paths} }

// settingHost is the machine the user pointed us at, if any.
func settingHost() string {
	if v, ok := core.LoadSettings()["image_host"].(string); ok {
		return strings.TrimSpace(v)
	}
	return ""
}

func imagesEnabled() bool {
	if v, ok := core.LoadSettings()["images_enabled"].(bool); ok {
		return v
	}
	return true // a Fooocus that is running should just work
}

// Host returns the first Fooocus that answers, or "".
func (p *Pictures) Host() string {
	p.mu.Lock()
	cached := p.host
	p.mu.Unlock()
	if cached != "" && alive(cached) {
		return cached
	}

	candidates := defaultHosts
	if configured := settingHost(); configured != "" {
		candidates = nil
		for _, h := range regexp.MustCompile(`[,\s;]+`).Split(configured, -1) {
			if h = strings.TrimRight(strings.TrimSpace(h), "/"); h != "" {
				candidates = append(candidates, h)
			}
		}
	}
	for _, url := range candidates {
		if alive(url) {
			p.mu.Lock()
			p.host = url
			p.mu.Unlock()
			return url
		}
	}
	p.mu.Lock()
	p.host = ""
	p.mu.Unlock()
	return ""
}

// alive reports whether a Gradio app is answering here.
func alive(url string) bool {
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url+"/config", nil)
	if err != nil {
		return false
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false
	}
	head, _ := io.ReadAll(io.LimitReader(resp.Body, 400))
	return strings.Contains(string(head), "components")
}

// Check answers /image-check.
func (p *Pictures) Check() map[string]any {
	host := ""
	if imagesEnabled() {
		host = p.Host()
	}
	launcher := p.launcher()
	reason := ""
	if host == "" {
		reason = "no Fooocus is answering — start it, or set image_host in settings " +
			"to the machine that runs it"
	}
	return map[string]any{
		"enabled":    imagesEnabled(),
		"available":  host != "",
		"host":       host,
		"launcher":   launcher,
		"can_start":  launcher != "" && host == "",
		"lan_url":    "",
		"lan_access": false,
		"reason":     reason,
	}
}

// launcher is a Fooocus start script the user pointed us at.
func (p *Pictures) launcher() string {
	v, _ := core.LoadSettings()["image_launcher"].(string)
	if v = strings.TrimSpace(v); v == "" {
		return ""
	}
	if info, err := os.Stat(v); err == nil && !info.IsDir() {
		return v
	}
	return ""
}

// Start runs the configured Fooocus launcher and waits for it to answer.
func (p *Pictures) Start(ctx context.Context) map[string]any {
	launcher := p.launcher()
	if launcher == "" {
		return map[string]any{"ok": false,
			"error": "no Fooocus launcher is configured — set image_launcher in settings"}
	}
	cmd := exec.Command(launcher)
	cmd.Dir = filepath.Dir(launcher)
	if err := cmd.Start(); err != nil {
		return map[string]any{"ok": false, "error": err.Error()}
	}
	go func() { _ = cmd.Wait() }()

	deadline := time.Now().Add(3 * time.Minute)
	for time.Now().Before(deadline) {
		if ctx.Err() != nil {
			break
		}
		if host := p.Host(); host != "" {
			return map[string]any{"ok": true, "host": host}
		}
		time.Sleep(2 * time.Second)
	}
	return map[string]any{"ok": false, "error": "Fooocus was started but never answered"}
}

// loadTemplate reads Fooocus's UI description once and remembers every input's
// default, so a generate request only has to override the three that matter.
func (p *Pictures) loadTemplate(ctx context.Context, host string) error {
	p.mu.Lock()
	loaded := p.loaded
	p.mu.Unlock()
	if loaded {
		return nil
	}

	reqCtx, cancel := context.WithTimeout(ctx, configTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, host+"/config", nil)
	if err != nil {
		return err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	var cfg struct {
		Components []struct {
			ID    int    `json:"id"`
			Type  string `json:"type"`
			Props struct {
				Value any    `json:"value"`
				Label string `json:"label"`
			} `json:"props"`
		} `json:"components"`
		Dependencies []struct {
			Inputs []int `json:"inputs"`
		} `json:"dependencies"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 32<<20)).Decode(&cfg); err != nil {
		return err
	}

	type prop struct {
		Value any
		Label string
	}
	byID := map[int]prop{}
	for _, c := range cfg.Components {
		byID[c.ID] = prop{c.Props.Value, c.Props.Label}
	}

	// The generate function is the one taking by far the most inputs.
	best, bestLen := -1, 0
	for i, d := range cfg.Dependencies {
		if len(d.Inputs) > bestLen {
			best, bestLen = i, len(d.Inputs)
		}
	}
	if best < 0 || bestLen < 50 {
		return fmt.Errorf("this does not look like Fooocus — its largest function takes %d inputs", bestLen)
	}

	values := make([]any, 0, bestLen)
	labels := map[string]int{}
	for pos, id := range cfg.Dependencies[best].Inputs {
		c := byID[id]
		values = append(values, c.Value)
		if c.Label != "" {
			if _, seen := labels[c.Label]; !seen {
				labels[c.Label] = pos
			}
		}
	}

	p.mu.Lock()
	p.template, p.labels, p.fnIndex, p.loaded = values, labels, best, true
	p.mu.Unlock()
	return nil
}

func (p *Pictures) slot(label string, fallback int) int {
	p.mu.Lock()
	defer p.mu.Unlock()
	if pos, ok := p.labels[label]; ok {
		return pos
	}
	return fallback
}

// ImageRequest is what the Studio asks for.
type ImageRequest struct {
	Prompt  string `json:"prompt"`
	Name    string `json:"name"`
	Project string `json:"project"`
	Aspect  string `json:"aspect"`
	Seed    int    `json:"seed"`
	Force   bool   `json:"force"`
}

// Generate draws one picture and saves it where the project can serve it.
func (p *Pictures) Generate(ctx context.Context, req ImageRequest) (map[string]any, error) {
	prompt := strings.TrimSpace(req.Prompt)
	if prompt == "" {
		return nil, fmt.Errorf("a picture needs a prompt")
	}
	if !imagesEnabled() {
		return nil, fmt.Errorf("picture generation is switched off in settings")
	}
	host := p.Host()
	if host == "" {
		return nil, fmt.Errorf("no Fooocus is answering — start it, or set image_host " +
			"in settings to the machine that runs it")
	}
	if err := p.loadTemplate(ctx, host); err != nil {
		return nil, fmt.Errorf("could not read the Fooocus interface: %w", err)
	}

	name := core.SafeName(req.Name)
	if name == "" {
		name = slug(prompt)
	}
	out := p.imagePath(req.Project, name)
	if !req.Force {
		if info, err := os.Stat(out); err == nil && info.Size() > 1024 {
			return p.result(req.Project, name, out), nil
		}
	}

	p.mu.Lock()
	args := append([]any{}, p.template...)
	fnIndex := p.fnIndex
	p.mu.Unlock()

	if len(args) > 2 {
		args[2] = prompt
	}
	if i := p.slot(aspectLabel, 6); i < len(args) {
		args[i] = resolveAspect(args[i], req.Aspect)
	}
	if i := p.slot(countLabel, 7); i < len(args) {
		args[i] = 1
	}
	if i := p.slot(seedLabel, 9); i < len(args) {
		seed := req.Seed
		if seed == 0 {
			seed = rand.Intn(1<<31-1) + 1
		}
		args[i] = strconv.Itoa(seed)
	}

	data, err := p.predict(ctx, host, fnIndex, args)
	if err != nil {
		return nil, err
	}
	raw := firstImage(host, data)
	if len(raw) == 0 {
		return nil, fmt.Errorf("Fooocus returned no image")
	}
	if err := os.MkdirAll(filepath.Dir(out), 0o755); err != nil {
		return nil, err
	}
	if err := os.WriteFile(out, raw, 0o644); err != nil {
		return nil, err
	}
	return p.result(req.Project, name, out), nil
}

// resolveAspect swaps the leading WIDTH*HEIGHT of Fooocus's own label.
func resolveAspect(current any, want string) any {
	size, ok := aspects[strings.ToLower(strings.TrimSpace(want))]
	if !ok {
		size = aspects["landscape"]
	}
	text, isString := current.(string)
	if !isString {
		return current
	}
	leading := regexp.MustCompile(`^\d+[*×]\d+`)
	if leading.MatchString(text) {
		return leading.ReplaceAllString(text, strings.ReplaceAll(size, "*", "×"))
	}
	return text
}

// predict runs the Gradio queue: join, then read the event stream until the
// job reports it is done.
func (p *Pictures) predict(ctx context.Context, host string, fnIndex int, args []any) (any, error) {
	session := fmt.Sprintf("agentforge%d", time.Now().UnixNano())
	payload, err := json.Marshal(map[string]any{
		"fn_index": fnIndex, "data": args, "session_hash": session,
	})
	if err != nil {
		return nil, err
	}

	joinCtx, cancelJoin := context.WithTimeout(ctx, 30*time.Second)
	defer cancelJoin()
	joinReq, err := http.NewRequestWithContext(joinCtx, http.MethodPost,
		host+"/queue/join", strings.NewReader(string(payload)))
	if err != nil {
		return nil, err
	}
	joinReq.Header.Set("Content-Type", "application/json")
	joinResp, err := http.DefaultClient.Do(joinReq)
	if err != nil {
		return nil, fmt.Errorf("Fooocus would not take the job: %w", err)
	}
	body, _ := io.ReadAll(io.LimitReader(joinResp.Body, 4096))
	joinResp.Body.Close()
	if joinResp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Fooocus answered %d: %s", joinResp.StatusCode, strings.TrimSpace(string(body)))
	}

	streamCtx, cancelStream := context.WithTimeout(ctx, generateTimeout)
	defer cancelStream()
	streamReq, err := http.NewRequestWithContext(streamCtx, http.MethodGet,
		host+"/queue/data?session_hash="+session, nil)
	if err != nil {
		return nil, err
	}
	streamResp, err := http.DefaultClient.Do(streamReq)
	if err != nil {
		return nil, fmt.Errorf("Fooocus did not answer: %w", err)
	}
	defer streamResp.Body.Close()

	decoder := json.NewDecoder(newSSEReader(streamResp.Body))
	for {
		var event struct {
			Msg    string `json:"msg"`
			Output struct {
				Data  any    `json:"data"`
				Error string `json:"error"`
			} `json:"output"`
		}
		if err := decoder.Decode(&event); err != nil {
			if err == io.EOF {
				return nil, fmt.Errorf("Fooocus closed the stream before finishing")
			}
			return nil, err
		}
		if event.Msg == "process_completed" {
			if event.Output.Error != "" {
				return nil, fmt.Errorf("Fooocus failed: %s", event.Output.Error)
			}
			return event.Output.Data, nil
		}
	}
}

// sseReader turns "data: {...}" lines into a plain stream of JSON objects.
type sseReader struct {
	src     io.Reader
	pending []byte
	buf     []byte
}

func newSSEReader(src io.Reader) *sseReader {
	return &sseReader{src: src, buf: make([]byte, 32<<10)}
}

func (r *sseReader) Read(out []byte) (int, error) {
	for len(r.pending) == 0 {
		n, err := r.src.Read(r.buf)
		if n > 0 {
			for _, line := range strings.Split(string(r.buf[:n]), "\n") {
				line = strings.TrimSpace(line)
				if payload, ok := strings.CutPrefix(line, "data:"); ok {
					r.pending = append(r.pending, strings.TrimSpace(payload)...)
					r.pending = append(r.pending, '\n')
				}
			}
		}
		if err != nil {
			if len(r.pending) == 0 {
				return 0, err
			}
			break
		}
	}
	n := copy(out, r.pending)
	r.pending = r.pending[n:]
	return n, nil
}

// firstImage digs the first picture out of whatever shape Gradio returned.
func firstImage(host string, node any) []byte {
	switch value := node.(type) {
	case string:
		if strings.HasPrefix(value, "data:image") {
			if _, encoded, ok := strings.Cut(value, ","); ok {
				if raw, err := base64.StdEncoding.DecodeString(encoded); err == nil {
					return raw
				}
			}
			return nil
		}
		lower := strings.ToLower(value)
		for _, ext := range []string{".png", ".jpg", ".jpeg", ".webp"} {
			if strings.HasSuffix(lower, ext) {
				return fetchImage(host, value)
			}
		}
	case map[string]any:
		for _, key := range []string{"value", "name", "path", "url", "data"} {
			if inner, ok := value[key]; ok {
				if got := firstImage(host, inner); len(got) > 0 {
					return got
				}
			}
		}
	case []any:
		for _, item := range value {
			if got := firstImage(host, item); len(got) > 0 {
				return got
			}
		}
	}
	return nil
}

// fetchImage reads a picture Fooocus reported by path — from disk if it is on
// this machine, otherwise over its own file endpoint.
func fetchImage(host, ref string) []byte {
	if raw, err := os.ReadFile(ref); err == nil && looksLikeImage(raw) {
		return raw
	}
	for _, path := range []string{"/file=" + ref, "/file/" + ref} {
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, host+path, nil)
		if err != nil {
			cancel()
			continue
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			cancel()
			continue
		}
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
		resp.Body.Close()
		cancel()
		if resp.StatusCode == http.StatusOK && looksLikeImage(raw) {
			return raw
		}
	}
	return nil
}

func looksLikeImage(raw []byte) bool {
	if len(raw) < 4 {
		return false
	}
	switch {
	case raw[0] == 0x89 && raw[1] == 'P' && raw[2] == 'N' && raw[3] == 'G':
		return true
	case raw[0] == 0xFF && raw[1] == 0xD8:
		return true
	case string(raw[:4]) == "RIFF":
		return true
	case string(raw[:3]) == "GIF":
		return true
	}
	return false
}

// --- uploads -----------------------------------------------------------------

// UploadRequest is a picture or attachment the user chose.
type UploadRequest struct {
	Name       string `json:"name"`
	Filename   string `json:"filename"`
	Project    string `json:"project"`
	Purpose    string `json:"purpose"`
	DataBase64 string `json:"data_base64"`
}

// SaveUpload writes an uploaded picture where a generated one would go, so the
// two are interchangeable everywhere downstream.
func (p *Pictures) SaveUpload(req UploadRequest) (map[string]any, error) {
	raw, err := decodeUpload(req.DataBase64)
	if err != nil {
		return nil, err
	}
	if !looksLikeImage(raw) {
		return nil, fmt.Errorf("that file is not a picture")
	}
	name := core.SafeName(req.Name)
	if name == "" {
		name = core.SafeName(strings.TrimSuffix(req.Filename, filepath.Ext(req.Filename)))
	}
	if name == "" {
		name = "upload"
	}
	out := p.imagePath(req.Project, name)
	if err := os.MkdirAll(filepath.Dir(out), 0o755); err != nil {
		return nil, err
	}
	if err := os.WriteFile(out, raw, 0o644); err != nil {
		return nil, err
	}
	return p.result(req.Project, name, out), nil
}

// Attach reads one file the user dropped into an editing chat and returns it as
// something the prompt can carry.
func Attach(req UploadRequest) (map[string]any, error) {
	raw, err := decodeUpload(req.DataBase64)
	if err != nil {
		return nil, err
	}
	name := req.Filename
	if name == "" {
		name = "attachment"
	}
	if looksLikeImage(raw) {
		return map[string]any{
			"ok": true, "name": name, "kind": "image",
			"data_uri": "data:image/png;base64," + base64.StdEncoding.EncodeToString(raw),
			"text":     fmt.Sprintf("\n\n[attached picture: %s]", name),
		}, nil
	}
	text := string(raw)
	if len(text) > 40000 {
		text = text[:40000] + "\n…(truncated)"
	}
	return map[string]any{
		"ok": true, "name": name, "kind": "text",
		"text": fmt.Sprintf("\n\n--- %s ---\n%s\n", name, text),
	}, nil
}

func decodeUpload(encoded string) ([]byte, error) {
	if strings.TrimSpace(encoded) == "" {
		return nil, fmt.Errorf("nothing was uploaded")
	}
	if strings.HasPrefix(encoded, "data:") {
		if _, payload, ok := strings.Cut(encoded, ","); ok {
			encoded = payload
		}
	}
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(encoded))
	if err != nil {
		return nil, fmt.Errorf("the upload could not be decoded")
	}
	if len(raw) > maxUploadBytes {
		return nil, fmt.Errorf("that file is larger than %d MB", maxUploadBytes>>20)
	}
	return raw, nil
}

// imagePath is public/generated inside a project, or a scratch folder when the
// picture does not belong to one yet.
func (p *Pictures) imagePath(project, name string) string {
	if project = core.SafeName(project); project != "" {
		return filepath.Join(p.Paths.Project(project), "public", "generated", name+".png")
	}
	return filepath.Join(p.Paths.Logs, "images", name+".png")
}

// result is the shape studio/components/LogoPanel.jsx reads back.
func (p *Pictures) result(project, name, out string) map[string]any {
	url := ""
	if core.SafeName(project) != "" {
		url = "/generated/" + name + ".png"
	}
	return map[string]any{
		"ok": true, "file": out, "name": name,
		"data_uri": previewURI(out), "url": url,
	}
}

// previewURI inlines a saved picture so the Studio can show it immediately.
func previewURI(path string) string {
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) == 0 {
		return ""
	}
	return "data:image/png;base64," + base64.StdEncoding.EncodeToString(raw)
}

var slugPattern = regexp.MustCompile(`[^a-z0-9]+`)

// slug turns a prompt into a stable filename.
func slug(text string) string {
	s := strings.Trim(slugPattern.ReplaceAllString(strings.ToLower(text), "-"), "-")
	if len(s) > 40 {
		s = strings.TrimRight(s[:40], "-")
	}
	if s == "" {
		return "image"
	}
	return s
}

// --- placing a picture in the app --------------------------------------------

// PictureRequest is an image_edit or image_swap instruction from the preview.
type PictureRequest struct {
	Swap       bool           // true for image_swap: the user supplied the file
	Prompt     string         // for image_edit: what to draw
	Filename   string         // for image_swap
	DataBase64 string         // for image_swap
	Route      string         // the page the element is on
	Element    map[string]any // what the user clicked
}

// Place puts a picture into the project and then points the page at it. The
// second half is the ordinary edit flow, so selection, pencil and pictures all
// change code the same way.
func Place(ctx context.Context, run *core.Run, pics *Pictures, req PictureRequest) (string, error) {
	if run.Project == "" {
		return "", fmt.Errorf("open a project first")
	}

	var saved map[string]any
	var err error
	if req.Swap {
		run.Info("🖼️  Saving the uploaded picture")
		saved, err = pics.SaveUpload(UploadRequest{
			Filename: req.Filename, Project: run.Project, DataBase64: req.DataBase64,
		})
	} else {
		if strings.TrimSpace(req.Prompt) == "" {
			return "", fmt.Errorf("describe the picture you want")
		}
		run.Info("🎨 Drawing the picture")
		saved, err = pics.Generate(ctx, ImageRequest{
			Prompt: req.Prompt, Project: run.Project,
			Aspect: aspectFor(req.Element), Force: true,
		})
	}
	if err != nil {
		return "", err
	}

	url, _ := saved["url"].(string)
	_ = saved["name"]
	if url == "" {
		return "", fmt.Errorf("the picture was not saved into the project")
	}
	run.Info("🖼️  " + url)
	run.File(strings.TrimPrefix("public"+url, "/"), 0, "")

	instruction := fmt.Sprintf(
		"Show the picture at %s in place of the picture the user selected. "+
			"Use a Next.js <Image> or a plain <img> with a meaningful alt, sized to "+
			"fit where the old one was. Do not change anything else on the page.", url)
	if !req.Swap {
		instruction += " The picture was drawn from: " + strings.TrimSpace(req.Prompt)
	}

	return Edit(ctx, run, Request{
		Kind:    KindSelect,
		Prompt:  instruction,
		Route:   req.Route,
		Element: req.Element,
	})
}

// aspectFor guesses a shape from the box the user clicked, so a wide banner is
// not filled with a square picture.
func aspectFor(element map[string]any) string {
	width, hasW := numberOf(element, "width")
	height, hasH := numberOf(element, "height")
	if !hasW || !hasH || height <= 0 {
		return "landscape"
	}
	switch ratio := width / height; {
	case ratio >= 2.4:
		return "banner"
	case ratio >= 1.5:
		return "wide"
	case ratio >= 1.15:
		return "landscape"
	case ratio >= 0.85:
		return "square"
	default:
		return "portrait"
	}
}

func numberOf(element map[string]any, key string) (float64, bool) {
	if element == nil {
		return 0, false
	}
	if v, ok := element[key].(float64); ok {
		return v, true
	}
	// The picker sometimes reports the box as a nested rect.
	if rect, ok := element["rect"].(map[string]any); ok {
		if v, ok := rect[key].(float64); ok {
			return v, true
		}
	}
	return 0, false
}
