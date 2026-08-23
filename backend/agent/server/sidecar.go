package server

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"agentforge/agent/core"
)

// The SRS and deployment agents stay in Python. The old backend imported them
// as threads; a Go parent cannot, so each runs as a subprocess on the port it
// always used and every request is proxied straight through. The Studio sees
// no difference.

type sidecarState struct {
	mu      sync.RWMutex
	name    string
	port    int
	state   string // off, starting, running, crashed, exited
	err     string
	cmd     *exec.Cmd
	proxy   *httputil.ReverseProxy
	restart int
}

func (s *sidecarState) set(state, errText string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.state, s.err = state, errText
}

// Status answers /srs-status and /deploy-status. `listening` is measured, not
// believed, because a crashed child can leave a stale state behind.
func (s *sidecarState) Status() map[string]any {
	s.mu.RLock()
	state, errText, port := s.state, s.err, s.port
	s.mu.RUnlock()
	return map[string]any{
		"state": state, "port": port, "error": errText,
		"listening": listening(port),
	}
}

func listening(port int) bool {
	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+strconv.Itoa(port), 500*time.Millisecond)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

// Sidecars supervises both Python services.
type Sidecars struct {
	Paths core.Paths
	// Python is the interpreter and any leading arguments it needs. On Windows
	// the launcher hands us "py -3", which is a command plus an argument, not a
	// path — so this stays a slice rather than a string.
	Python []string

	SRS    *sidecarState
	Deploy *sidecarState
}

func NewSidecars(paths core.Paths) *Sidecars {
	return &Sidecars{
		Paths:  paths,
		Python: pythonBin(),
		SRS:    newSidecar("srs", core.SRSPort),
		Deploy: newSidecar("deploy", core.DeployPort),
	}
}

func newSidecar(name string, port int) *sidecarState {
	s := &sidecarState{name: name, port: port, state: "off"}
	if u, err := url.Parse("http://127.0.0.1:" + strconv.Itoa(port)); err == nil {
		s.proxy = httputil.NewSingleHostReverseProxy(u)
		s.proxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, err error) {
			writeJSON(w, http.StatusBadGateway, map[string]any{
				"error": fmt.Sprintf("the %s agent is unreachable: %v", name, err),
				name:    s.Status(),
			})
		}
		s.proxy.FlushInterval = 100 * time.Millisecond // the SRS streams SSE
	}
	return s
}

func pythonBin() []string {
	// The launcher passes what it verified works, which may include arguments.
	if p := strings.TrimSpace(os.Getenv("AGENTFORGE_PYTHON")); p != "" {
		return strings.Fields(p)
	}
	for _, name := range []string{"python3", "python"} {
		if found, err := exec.LookPath(name); err == nil {
			return []string{found}
		}
	}
	if runtime.GOOS == "windows" {
		return []string{"py", "-3"}
	}
	return []string{"python3"}
}

// Start launches both sidecars and keeps them up until ctx is cancelled.
func (s *Sidecars) Start(ctx context.Context, mongoURI string) {
	go s.supervise(ctx, s.SRS,
		filepath.Join(s.Paths.Base, "srs-agent"),
		"from srs_agent import mount; mount.serve()", mongoURI)
	go s.supervise(ctx, s.Deploy,
		filepath.Join(s.Paths.Base, "deployment-agent"),
		"from deploy_agent import mount; mount.serve()", mongoURI)
}

// supervise restarts a sidecar that dies, backing off so a service that cannot
// start does not spin.
func (s *Sidecars) supervise(ctx context.Context, sc *sidecarState, dir, entry, mongoURI string) {
	if _, err := os.Stat(dir); err != nil {
		sc.set("import-failed", dir+" is not present")
		return
	}
	backoff := 2 * time.Second
	for {
		if ctx.Err() != nil {
			return
		}
		sc.set("starting", "")
		args := append(append([]string{}, s.Python[1:]...), "-c", entry)
		cmd := exec.CommandContext(ctx, s.Python[0], args...)
		cmd.Dir = dir
		cmd.Env = append(os.Environ(),
			"PYTHONUNBUFFERED=1",
			"AGENTFORGE_PROJECTS="+s.Paths.Projects,
			"AGENTFORGE_MONGO_URI="+mongoURI,
		)
		cmd.Stdout = prefixWriter{tag: sc.name}
		cmd.Stderr = prefixWriter{tag: sc.name}

		if err := cmd.Start(); err != nil {
			sc.set("crashed", err.Error())
			if !sleepCtx(ctx, backoff) {
				return
			}
			backoff = nextBackoff(backoff)
			continue
		}
		sc.mu.Lock()
		sc.cmd = cmd
		sc.mu.Unlock()

		// It is up once the port answers; until then the state stays "starting".
		go func() {
			for i := 0; i < 120; i++ {
				if ctx.Err() != nil || listening(sc.port) {
					if listening(sc.port) {
						sc.set("running", "")
					}
					return
				}
				time.Sleep(500 * time.Millisecond)
			}
		}()

		err := cmd.Wait()
		if ctx.Err() != nil {
			sc.set("stopped", "")
			return
		}
		sc.mu.Lock()
		sc.restart++
		attempts := sc.restart
		sc.mu.Unlock()
		if err != nil {
			sc.set("crashed", err.Error())
		} else {
			sc.set("exited", "")
		}
		if attempts > 8 {
			sc.set("crashed", "gave up after 8 restarts")
			return
		}
		if !sleepCtx(ctx, backoff) {
			return
		}
		backoff = nextBackoff(backoff)
	}
}

func nextBackoff(d time.Duration) time.Duration {
	if d *= 2; d > 30*time.Second {
		return 30 * time.Second
	}
	return d
}

func sleepCtx(ctx context.Context, d time.Duration) bool {
	select {
	case <-ctx.Done():
		return false
	case <-time.After(d):
		return true
	}
}

// prefixWriter tags a sidecar's output so both are readable in one console.
type prefixWriter struct{ tag string }

func (p prefixWriter) Write(b []byte) (int, error) {
	for _, line := range strings.Split(strings.TrimRight(string(b), "\n"), "\n") {
		if strings.TrimSpace(line) != "" {
			fmt.Printf("[%s] %s\n", p.tag, line)
		}
	}
	return len(b), nil
}

// Proxy forwards one request to a sidecar under a rewritten path.
func (sc *sidecarState) Proxy(w http.ResponseWriter, r *http.Request, path string) {
	sc.mu.RLock()
	proxy, state := sc.proxy, sc.state
	sc.mu.RUnlock()
	if proxy == nil || state == "off" || state == "import-failed" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"error":   "the " + sc.name + " agent is not running",
			sc.name:   sc.Status(),
			"details": sc.Status(),
		})
		return
	}
	out := r.Clone(r.Context())
	out.URL.Path = path
	out.RequestURI = ""
	out.Host = "127.0.0.1:" + strconv.Itoa(sc.port)
	proxy.ServeHTTP(w, out)
}

// --- jobs --------------------------------------------------------------------
//
// studio/lib/api.js starts slow work as a job and polls it, so a long image or
// deployment call never sits on one HTTP request. The shape it polls for is
// {job_id, status, http_status, result, error, elapsed}.

type job struct {
	ID         string `json:"job_id"`
	Status     string `json:"status"` // running, done, error, unknown
	Path       string `json:"path"`
	HTTPStatus int    `json:"http_status"`
	Result     any    `json:"result"`
	Error      string `json:"error"`
	Elapsed    float64
	started    time.Time
	finished   time.Time
}

const (
	jobKeepFinished = 15 * time.Minute
	jobMax          = 200
)

type jobStore struct {
	mu   sync.Mutex
	jobs map[string]*job
	seq  int
}

func newJobStore() *jobStore { return &jobStore{jobs: map[string]*job{}} }

// Start registers a job and runs work in the background.
func (s *jobStore) Start(path string, work func() (int, any, error)) *job {
	s.mu.Lock()
	s.reap()
	s.seq++
	j := &job{
		ID:     fmt.Sprintf("job_%d_%d", time.Now().UnixNano(), s.seq),
		Status: "running", Path: path, started: time.Now(),
	}
	s.jobs[j.ID] = j
	s.mu.Unlock()

	go func() {
		status, result, err := work()
		s.mu.Lock()
		defer s.mu.Unlock()
		j.finished = time.Now()
		j.HTTPStatus, j.Result = status, result
		if err != nil {
			j.Status, j.Error = "error", err.Error()
		} else {
			j.Status = "done"
		}
	}()
	return &job{ID: j.ID, Status: "running", Path: path}
}

// Poll answers one job's current state.
func (s *jobStore) Poll(id string) map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	j, ok := s.jobs[id]
	if !ok {
		return map[string]any{"job_id": id, "status": "unknown",
			"error": "no such job — it may have expired"}
	}
	end := j.finished
	if end.IsZero() {
		end = time.Now()
	}
	return map[string]any{
		"job_id": j.ID, "status": j.Status, "path": j.Path,
		"http_status": j.HTTPStatus, "result": j.Result, "error": j.Error,
		"elapsed": float64(int(end.Sub(j.started).Seconds()*10)) / 10,
	}
}

// reap drops finished jobs. The caller holds the lock.
func (s *jobStore) reap() {
	now := time.Now()
	for id, j := range s.jobs {
		if !j.finished.IsZero() && now.Sub(j.finished) > jobKeepFinished {
			delete(s.jobs, id)
		}
	}
	if len(s.jobs) <= jobMax {
		return
	}
	oldest, oldestID := time.Time{}, ""
	for len(s.jobs) > jobMax {
		oldest, oldestID = time.Time{}, ""
		for id, j := range s.jobs {
			if j.finished.IsZero() {
				continue
			}
			if oldestID == "" || j.finished.Before(oldest) {
				oldest, oldestID = j.finished, id
			}
		}
		if oldestID == "" {
			return
		}
		delete(s.jobs, oldestID)
	}
}

// forward runs a sidecar request as a job, which is how /deploy/jobs works.
func (sc *sidecarState) forward(method, path string, body []byte) (int, any, error) {
	target := "http://127.0.0.1:" + strconv.Itoa(sc.port) + path
	var reader io.Reader
	if len(body) > 0 {
		reader = strings.NewReader(string(body))
	}
	req, err := http.NewRequest(method, target, reader)
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 30 * time.Minute}
	resp, err := client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return resp.StatusCode, nil, err
	}
	var parsed any
	if err := jsonUnmarshal(raw, &parsed); err != nil {
		parsed = map[string]any{"text": string(raw)}
	}
	return resp.StatusCode, parsed, nil
}

// --- mongodb -----------------------------------------------------------------
//
// The generated apps persist to MongoDB, and the SRS agent keeps its documents
// there. We use whatever is reachable: a URI the user saved, a mongod already
// listening, or one we start ourselves if the binary is on PATH.

const defaultMongoPort = 27017

// Mongo tracks the database the whole system shares.
type Mongo struct {
	Paths core.Paths

	mu       sync.RWMutex
	uri      string
	port     int
	external bool
	override bool
	reason   string
	cmd      *exec.Cmd
}

func NewMongo(paths core.Paths) *Mongo {
	return &Mongo{Paths: paths, port: defaultMongoPort}
}

// URI is the connection string every consumer should use.
func (m *Mongo) URI() string {
	m.mu.RLock()
	defer m.mu.RUnlock()
	if m.uri != "" {
		return m.uri
	}
	return "mongodb://127.0.0.1:" + strconv.Itoa(m.port)
}

// Ensure settles on a database before anything needs it.
func (m *Mongo) Ensure(ctx context.Context) {
	if saved := strings.TrimSpace(fmt.Sprint(core.LoadSettings()["mongodb_uri"])); saved != "" && saved != "<nil>" {
		m.mu.Lock()
		m.uri, m.override, m.reason = saved, true, ""
		m.mu.Unlock()
		return
	}
	if env := strings.TrimSpace(os.Getenv("AGENTFORGE_MONGO_URI")); env != "" {
		m.mu.Lock()
		m.uri, m.override, m.reason = env, true, ""
		m.mu.Unlock()
		return
	}
	if listening(defaultMongoPort) {
		m.mu.Lock()
		m.external, m.reason = true, ""
		m.mu.Unlock()
		return
	}
	m.start(ctx)
}

// start launches a local mongod when one is installed.
func (m *Mongo) start(ctx context.Context) {
	bin, err := exec.LookPath("mongod")
	if err != nil {
		m.mu.Lock()
		m.reason = "mongod is not installed — install MongoDB or save a MongoDB URI in settings"
		m.mu.Unlock()
		return
	}
	dataDir := filepath.Join(m.Paths.Base, "mongo-data")
	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		m.mu.Lock()
		m.reason = "could not create " + dataDir
		m.mu.Unlock()
		return
	}
	cmd := exec.CommandContext(ctx, bin,
		"--dbpath", dataDir,
		"--port", strconv.Itoa(defaultMongoPort),
		"--bind_ip", "127.0.0.1")
	cmd.Stdout = prefixWriter{tag: "mongod"}
	cmd.Stderr = prefixWriter{tag: "mongod"}
	if err := cmd.Start(); err != nil {
		m.mu.Lock()
		m.reason = "mongod would not start: " + err.Error()
		m.mu.Unlock()
		return
	}
	m.mu.Lock()
	m.cmd, m.reason = cmd, ""
	m.mu.Unlock()
	go func() { _ = cmd.Wait() }()

	for i := 0; i < 40; i++ {
		if listening(defaultMongoPort) {
			return
		}
		time.Sleep(250 * time.Millisecond)
	}
	m.mu.Lock()
	m.reason = "mongod started but is not answering on :" + strconv.Itoa(defaultMongoPort)
	m.mu.Unlock()
}

// Status answers /mongo in the shape the settings modal renders.
func (m *Mongo) Status(context.Context) map[string]any {
	m.mu.RLock()
	override, external, reason, port := m.override, m.external, m.reason, m.port
	m.mu.RUnlock()

	_, hasBinary := exec.LookPath("mongod")
	running := override || listening(port)
	return map[string]any{
		"running":    running,
		"port":       port,
		"external":   external,
		"override":   override,
		"downloaded": hasBinary == nil,
		"reason":     reason,
		"uri_set":    override,
	}
}

// Stop shuts down a mongod we started ourselves.
func (m *Mongo) Stop() {
	m.mu.RLock()
	cmd := m.cmd
	m.mu.RUnlock()
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}

// devProxy forwards anything that is not ours to the generated app's dev server.
func devProxy() *httputil.ReverseProxy {
	u, _ := url.Parse("http://127.0.0.1:" + strconv.Itoa(core.DevPort))
	p := httputil.NewSingleHostReverseProxy(u)
	p.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, err error) {
		if errors.Is(err, context.Canceled) {
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = io.WriteString(w, `<!doctype html><meta charset=utf-8>`+
			`<body style="font:14px/1.6 system-ui;padding:2rem;color:#666">`+
			`<p>The preview is not running yet.</p>`)
	}
	return p
}
