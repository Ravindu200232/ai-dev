package server

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strconv"
	"sync"
	"time"

	"agentforge/agent/core"
)

// A job is how the Studio asks for something that takes longer than a request
// should be held open for: it starts the work, gets an id, and polls. The SRS
// and the deployment agent both go through here, and so does anything local
// that talks to a model.

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
