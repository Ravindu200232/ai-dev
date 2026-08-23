// Package server carries the two surfaces the Studio talks to: the WebSocket
// on 7825 that streams a run, and the HTTP API on 7824.
package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"agentforge/agent/app"
	"agentforge/agent/core"
	"agentforge/agent/deploy"
	"agentforge/agent/srs"
)

// Message is one instruction from the Studio. The typed fields are the ones
// every handler needs; Raw keeps everything else the UI sent so a handler can
// reach for element geometry, strokes or console output without this struct
// having to grow a field per feature.
type Message struct {
	Type    string `json:"type"`
	Project string `json:"project"`
	Prompt  string `json:"prompt"`
	Route   string `json:"route"`
	SRSID   string `json:"srs_id"`
	Model   string `json:"model"`
	Think   bool   `json:"think"`

	Raw map[string]any `json:"-"`
}

// Str reads a string out of the raw payload.
func (m Message) Str(key string) string {
	if v, ok := m.Raw[key]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}

// Map reads a nested object out of the raw payload.
func (m Message) Map(key string) map[string]any {
	if v, ok := m.Raw[key]; ok {
		if mm, ok := v.(map[string]any); ok {
			return mm
		}
	}
	return nil
}

// Agent does the actual work. cmd/agentforge wires the builder, QA and edit
// graphs into it, which keeps this package free of any import cycle.
type Agent interface {
	// Handle runs one instruction. The preview route it returns is what the
	// Studio should show when the run finishes.
	Handle(run *core.Run, msg Message) (preview string, err error)
}

// kindFor maps a message type onto the progress rail the Studio draws.
var kindFor = map[string]string{
	"agent_build":  "build",
	"agent_resume": "build",
	"agent_update": "repair",
	"feature":      "feature",
	"element_edit": "select",
	"pencil_edit":  "pencil",
	"image_edit":   "image",
	"image_swap":   "image",
}

// Server owns the run lifecycle and both network surfaces.
type Server struct {
	Hub      *core.Hub
	Paths    core.Paths
	LLM      *core.LLM
	Agent    Agent
	Mongo    *Mongo
	Pictures *app.Pictures
	SRS      *srs.Service
	Deploy   *deploy.Agent

	mu     sync.Mutex
	active *core.Run
	jobs   *jobStore
	qaPDF  QAPDFFunc
}

func New(hub *core.Hub, paths core.Paths, llm *core.LLM, mongo *Mongo) *Server {
	return &Server{
		Hub: hub, Paths: paths, LLM: llm,
		Mongo: mongo, Pictures: app.NewPictures(paths),
		jobs: newJobStore(),
	}
}

// Active returns the run in flight, if any.
func (s *Server) Active() *core.Run {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.active
}

// CancelActive stops whatever is running. It answers whether there was one.
func (s *Server) CancelActive() bool {
	s.mu.Lock()
	run := s.active
	s.mu.Unlock()
	if run == nil {
		return false
	}
	run.Cancel()
	return true
}

// Dispatch starts one instruction. It returns an error only when the request
// cannot be started at all; everything after that is reported over the socket.
func (s *Server) Dispatch(msg Message) error {
	if s.Agent == nil {
		return errors.New("no agent is wired up")
	}
	kind, known := kindFor[msg.Type]
	if !known {
		return fmt.Errorf("unknown instruction %q", msg.Type)
	}

	s.mu.Lock()
	if s.active != nil && !s.active.Cancelled() {
		s.mu.Unlock()
		return errors.New("a run is already in progress — stop it first")
	}
	run := core.NewRun(context.Background(), s.Hub, s.Paths, s.LLM, msg.Project, kind)
	run.Prompt = msg.Prompt
	run.Shell.Emit = func(line string) { run.Command(line) }
	s.active = run
	s.mu.Unlock()

	go s.run(run, msg)
	return nil
}

// run executes one instruction and guarantees a terminal event, so the Studio
// never stays busy after the work has stopped.
func (s *Server) run(run *core.Run, msg Message) {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("panic in %s: %v", msg.Type, r)
			run.Failed(fmt.Sprintf("the run stopped unexpectedly: %v", r))
		}
		s.mu.Lock()
		if s.active == run {
			s.active = nil
		}
		s.mu.Unlock()
	}()

	preview, err := s.Agent.Handle(run, msg)
	switch {
	case run.Cancelled():
		run.Cancelled_(nil)
	case err != nil:
		run.Fault(err.Error())
		run.Failed(err.Error())
	default:
		run.Done(preview)
	}
}

// --- socket ------------------------------------------------------------------

const (
	writeWait  = 20 * time.Second
	pongWait   = 90 * time.Second
	pingPeriod = 30 * time.Second
	sendQueue  = 512
)

// upgrader accepts the Studio from any local origin: the page is served by
// next dev on 3000 while this listens on 7825.
var upgrader = websocket.Upgrader{
	ReadBufferSize:  4096,
	WriteBufferSize: 4096,
	CheckOrigin:     func(*http.Request) bool { return true },
}

// wsClient is one Studio socket. Writes go through a queue so a slow reader
// cannot block a build.
type wsClient struct {
	conn *websocket.Conn
	out  chan []byte
	once sync.Once
}

func (c *wsClient) Send(data []byte) {
	select {
	case c.out <- data:
	default:
		// The queue is full: this client is not keeping up. Drop it rather
		// than stalling the run for everyone else.
		c.close()
	}
}

func (c *wsClient) close() {
	c.once.Do(func() { close(c.out) })
}

// ServeWS runs the WebSocket listener until the context is cancelled.
func (s *Server) ServeWS(ctx context.Context, addr string) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleWS)
	srv := &http.Server{Addr: addr, Handler: mux}
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

func (s *Server) handleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	client := &wsClient{conn: conn, out: make(chan []byte, sendQueue)}
	s.Hub.Add(client)

	go s.writePump(client)
	s.readPump(client)

	s.Hub.Remove(client)
	client.close()
}

func (s *Server) writePump(c *wsClient) {
	ticker := time.NewTicker(pingPeriod)
	defer func() {
		ticker.Stop()
		_ = c.conn.Close()
	}()
	for {
		select {
		case data, ok := <-c.out:
			_ = c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if !ok {
				_ = c.conn.WriteMessage(websocket.CloseMessage, nil)
				return
			}
			if err := c.conn.WriteMessage(websocket.TextMessage, data); err != nil {
				return
			}
		case <-ticker.C:
			_ = c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if err := c.conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}

func (s *Server) readPump(c *wsClient) {
	_ = c.conn.SetReadDeadline(time.Now().Add(pongWait))
	c.conn.SetPongHandler(func(string) error {
		return c.conn.SetReadDeadline(time.Now().Add(pongWait))
	})
	for {
		_, data, err := c.conn.ReadMessage()
		if err != nil {
			return
		}
		_ = c.conn.SetReadDeadline(time.Now().Add(pongWait))
		msg, err := ParseMessage(data)
		if err != nil {
			s.Hub.Emit(map[string]any{"type": "log", "level": "WARN",
				"text": "unreadable instruction: " + err.Error()})
			continue
		}
		if err := s.Dispatch(msg); err != nil {
			s.Hub.Emit(map[string]any{"type": "error", "text": err.Error()})
		}
	}
}

// ParseMessage decodes one Studio instruction, keeping the untyped payload.
func ParseMessage(data []byte) (Message, error) {
	var msg Message
	if err := json.Unmarshal(data, &msg); err != nil {
		return msg, err
	}
	raw := map[string]any{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return msg, err
	}
	msg.Raw = raw
	msg.Type = strings.TrimSpace(msg.Type)
	if msg.Type == "" {
		return msg, errors.New("the instruction has no type")
	}
	return msg, nil
}
