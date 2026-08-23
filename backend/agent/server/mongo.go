package server

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"agentforge/agent/core"
)

// The generated apps persist to MongoDB and the SRS agent keeps its documents
// there, so one has to exist. In order: a URI the user saved, a mongod already
// listening, a mongod already installed, and failing all of those we fetch the
// official build for this platform and run it ourselves.

const (
	defaultMongoPort = 27017
	mongoFeedURL     = "https://downloads.mongodb.org/current.json"
	fallbackVersion  = "8.3.7"
	downloadTimeout  = 20 * time.Minute
	startTimeout     = 90 * time.Second
)

// fallbackURLs are used when the version feed cannot be reached.
var fallbackURLs = map[string]string{
	"windows/x86_64":    "https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-" + fallbackVersion + ".zip",
	"macos/arm64":       "https://fastdl.mongodb.org/osx/mongodb-macos-arm64-" + fallbackVersion + ".tgz",
	"macos/x86_64":      "https://fastdl.mongodb.org/osx/mongodb-macos-x86_64-" + fallbackVersion + ".tgz",
	"ubuntu2204/x86_64": "https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-ubuntu2204-" + fallbackVersion + ".tgz",
	"ubuntu2204/arm64":  "https://fastdl.mongodb.org/linux/mongodb-linux-aarch64-ubuntu2204-" + fallbackVersion + ".tgz",
}

// listening reports whether something is answering on a local port. It is
// measured rather than believed: a process that died can leave a state behind.
func listening(port int) bool {
	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+strconv.Itoa(port), 500*time.Millisecond)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

// prefixWriter tags a child process's output so it is obvious in the console
// which process a line came from.
type prefixWriter struct{ tag string }

func (p prefixWriter) Write(b []byte) (int, error) {
	for _, line := range strings.Split(strings.TrimRight(string(b), "\n"), "\n") {
		if strings.TrimSpace(line) != "" {
			log.Printf("%s %s", p.tag, line)
		}
	}
	return len(b), nil
}

// Mongo owns the database the whole system shares.
type Mongo struct {
	Paths core.Paths
	// Log, when set, reports progress to whoever is watching.
	Log func(level, text string)

	mu       sync.RWMutex
	uri      string
	port     int
	binary   string
	version  string
	external bool
	override bool
	ours     bool
	reason   string
	progress int
	cmd      *exec.Cmd
}

func NewMongo(paths core.Paths) *Mongo {
	return &Mongo{Paths: paths, port: defaultMongoPort}
}

func (m *Mongo) log(level, text string) {
	if m.Log != nil {
		m.Log(level, text)
	}
	fmt.Printf("[mongo] %s\n", text)
}

// home is where a mongod we fetched ourselves lives.
func (m *Mongo) home() string {
	if dir, err := os.UserHomeDir(); err == nil {
		return filepath.Join(dir, ".agentforge", "mongo")
	}
	return filepath.Join(m.Paths.Base, ".mongo")
}

func (m *Mongo) binDir() string  { return filepath.Join(m.home(), "bin") }
func (m *Mongo) dataDir() string { return filepath.Join(m.home(), "data") }

func exeName(name string) string {
	if runtime.GOOS == "windows" {
		return name + ".exe"
	}
	return name
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

// FindBinary returns a mongod we can run, or "".
func (m *Mongo) FindBinary() string {
	if local := filepath.Join(m.binDir(), exeName("mongod")); fileExists(local) {
		return local
	}
	if found, err := exec.LookPath("mongod"); err == nil {
		return found
	}
	if runtime.GOOS == "windows" {
		matches, _ := filepath.Glob(`C:\Program Files\MongoDB\Server\*\bin\mongod.exe`)
		for i := len(matches) - 1; i >= 0; i-- { // newest version first
			return matches[i]
		}
	}
	return ""
}

// Resolve settles the connection string without doing anything slow, so the
// sidecars can be told where the database is before it necessarily exists. It
// reports whether a database is already there.
func (m *Mongo) Resolve() bool {
	if saved := savedURI(); saved != "" {
		m.mu.Lock()
		m.uri, m.override, m.reason = saved, true, ""
		m.mu.Unlock()
		m.log("INFO", "using the MongoDB URI from settings")
		return true
	}
	if listening(defaultMongoPort) {
		m.mu.Lock()
		m.external, m.reason = true, ""
		m.mu.Unlock()
		m.log("INFO", "adopted the MongoDB already on :"+strconv.Itoa(defaultMongoPort))
		return true
	}
	return false
}

// Ensure makes a database exist, fetching mongod if this machine has none.
// The first fetch is around 90 MB, so this is meant to run in the background
// while the rest of the backend comes up.
func (m *Mongo) Ensure(ctx context.Context) {
	if m.Resolve() {
		return
	}
	if err := m.Start(ctx); err != nil {
		m.mu.Lock()
		m.reason = err.Error()
		m.mu.Unlock()
		m.log("WARN", err.Error())
	}
}

// savedURI is a URI the user typed into settings, or the environment.
func savedURI() string {
	if env := strings.TrimSpace(os.Getenv("MONGODB_URI")); env != "" {
		return env
	}
	if env := strings.TrimSpace(os.Getenv("AGENTFORGE_MONGO_URI")); env != "" {
		return env
	}
	if v, ok := core.LoadSettings()["mongodb_uri"].(string); ok {
		return strings.TrimSpace(v)
	}
	return ""
}

// Start finds or fetches mongod and runs it.
func (m *Mongo) Start(ctx context.Context) error {
	binary := m.FindBinary()
	if binary == "" {
		fetched, err := m.Download(ctx)
		if err != nil {
			return err
		}
		binary = fetched
	}
	m.mu.Lock()
	m.binary = binary
	m.mu.Unlock()

	if err := os.MkdirAll(m.dataDir(), 0o755); err != nil {
		return fmt.Errorf("could not create %s: %w", m.dataDir(), err)
	}
	// A hard stop leaves a lock behind that stops the next start.
	_ = os.Remove(filepath.Join(m.dataDir(), "mongod.lock"))

	cmd := exec.CommandContext(ctx, binary,
		"--dbpath", m.dataDir(),
		"--port", strconv.Itoa(m.port),
		"--bind_ip", "127.0.0.1",
		"--wiredTigerCacheSizeGB", "0.25")
	cmd.Dir = m.home()
	cmd.Stdout = prefixWriter{tag: "mongod"}
	cmd.Stderr = prefixWriter{tag: "mongod"}

	m.log("INFO", "starting MongoDB on 127.0.0.1:"+strconv.Itoa(m.port))
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("mongod would not start: %w", err)
	}
	m.mu.Lock()
	m.cmd, m.ours, m.reason = cmd, true, ""
	m.mu.Unlock()
	go func() { _ = cmd.Wait() }()

	deadline := time.Now().Add(startTimeout)
	for time.Now().Before(deadline) {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if listening(m.port) {
			m.log("INFO", "MongoDB is ready")
			return nil
		}
		time.Sleep(400 * time.Millisecond)
	}
	return fmt.Errorf("mongod started but never answered on :%d", m.port)
}

// Download fetches the official build for this platform and keeps just mongod.
func (m *Mongo) Download(ctx context.Context) (string, error) {
	info, err := m.resolveDownload(ctx)
	if err != nil {
		return "", err
	}
	m.mu.Lock()
	m.version, m.progress = info.Version, 0
	m.mu.Unlock()

	if err := os.MkdirAll(m.binDir(), 0o755); err != nil {
		return "", err
	}
	m.log("INFO", fmt.Sprintf("MongoDB not found — fetching mongod %s", info.Version))

	archive, err := m.fetch(ctx, info.URL)
	if err != nil {
		return "", err
	}
	defer os.Remove(archive)

	target := filepath.Join(m.binDir(), exeName("mongod"))
	if info.Zip {
		err = extractFromZip(archive, target)
	} else {
		err = extractFromTgz(archive, target)
	}
	if err != nil {
		return "", err
	}
	if runtime.GOOS != "windows" {
		_ = os.Chmod(target, 0o755)
	}
	_ = core.WriteJSON(filepath.Join(m.home(), "version.json"),
		map[string]any{"version": info.Version, "url": info.URL})

	m.mu.Lock()
	m.progress = 100
	m.mu.Unlock()
	m.log("INFO", "mongod "+info.Version+" installed")
	return target, nil
}

type downloadInfo struct {
	Version string
	URL     string
	Zip     bool
}

// resolveDownload asks MongoDB's own version feed what to fetch here.
func (m *Mongo) resolveDownload(ctx context.Context) (downloadInfo, error) {
	arch := mongoArch()
	targets := mongoTargets()

	if info, err := m.fromFeed(ctx, targets, arch); err == nil {
		return info, nil
	} else {
		m.log("WARN", fmt.Sprintf("version feed unreachable (%v) — using %s", err, fallbackVersion))
	}
	for _, target := range targets {
		if url, ok := fallbackURLs[target+"/"+arch]; ok {
			return downloadInfo{Version: fallbackVersion, URL: url, Zip: strings.HasSuffix(url, ".zip")}, nil
		}
	}
	return downloadInfo{}, fmt.Errorf("no MongoDB build is published for %s/%s — "+
		"install MongoDB yourself or save a MongoDB URI in settings", runtime.GOOS, arch)
}

func (m *Mongo) fromFeed(ctx context.Context, targets []string, arch string) (downloadInfo, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, mongoFeedURL, nil)
	if err != nil {
		return downloadInfo{}, err
	}
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return downloadInfo{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return downloadInfo{}, fmt.Errorf("the feed answered %d", resp.StatusCode)
	}

	var feed struct {
		Versions []struct {
			Version           string `json:"version"`
			ProductionRelease bool   `json:"production_release"`
			ReleaseCandidate  bool   `json:"release_candidate"`
			Downloads         []struct {
				Target  string `json:"target"`
				Arch    string `json:"arch"`
				Edition string `json:"edition"`
				Archive struct {
					URL string `json:"url"`
				} `json:"archive"`
			} `json:"downloads"`
		} `json:"versions"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 32<<20)).Decode(&feed); err != nil {
		return downloadInfo{}, err
	}
	for _, want := range targets {
		for _, v := range feed.Versions {
			if !v.ProductionRelease || v.ReleaseCandidate {
				continue
			}
			for _, d := range v.Downloads {
				if d.Target == want && d.Arch == arch && d.Edition == "base" && d.Archive.URL != "" {
					return downloadInfo{
						Version: v.Version, URL: d.Archive.URL,
						Zip: strings.HasSuffix(d.Archive.URL, ".zip"),
					}, nil
				}
			}
		}
	}
	return downloadInfo{}, fmt.Errorf("the feed lists no build for %s/%s", strings.Join(targets, ","), arch)
}

// fetch streams the archive to a temporary file, reporting progress.
func (m *Mongo) fetch(ctx context.Context, url string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	resp, err := (&http.Client{Timeout: downloadTimeout}).Do(req)
	if err != nil {
		return "", fmt.Errorf("the MongoDB download failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("the MongoDB download answered %d", resp.StatusCode)
	}

	tmp, err := os.CreateTemp("", "mongodb-*.archive")
	if err != nil {
		return "", err
	}
	defer tmp.Close()

	total := resp.ContentLength
	written := int64(0)
	buf := make([]byte, 1<<20)
	lastReported := -5
	for {
		n, readErr := resp.Body.Read(buf)
		if n > 0 {
			if _, err := tmp.Write(buf[:n]); err != nil {
				os.Remove(tmp.Name())
				return "", err
			}
			written += int64(n)
			if total > 0 {
				pct := int(written * 100 / total)
				if pct >= lastReported+5 {
					lastReported = pct
					m.mu.Lock()
					m.progress = pct
					m.mu.Unlock()
					m.log("INFO", fmt.Sprintf("MongoDB %d%% (%.1f / %.1f MB)",
						pct, float64(written)/1e6, float64(total)/1e6))
				}
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			os.Remove(tmp.Name())
			return "", readErr
		}
		if ctx.Err() != nil {
			os.Remove(tmp.Name())
			return "", ctx.Err()
		}
	}
	return tmp.Name(), nil
}

// extractFromZip pulls just bin/mongod out of the archive.
func extractFromZip(archive, target string) error {
	r, err := zip.OpenReader(archive)
	if err != nil {
		return err
	}
	defer r.Close()
	for _, f := range r.File {
		if !isMongodEntry(f.Name) {
			continue
		}
		src, err := f.Open()
		if err != nil {
			return err
		}
		defer src.Close()
		return writeBinary(target, src)
	}
	return fmt.Errorf("mongod was not inside the downloaded archive")
}

// extractFromTgz does the same for a .tgz, which is what macOS and Linux get.
func extractFromTgz(archive, target string) error {
	fh, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer fh.Close()
	gz, err := gzip.NewReader(fh)
	if err != nil {
		return err
	}
	defer gz.Close()

	tr := tar.NewReader(gz)
	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		if header.Typeflag != tar.TypeReg || !isMongodEntry(header.Name) {
			continue
		}
		return writeBinary(target, tr)
	}
	return fmt.Errorf("mongod was not inside the downloaded archive")
}

// isMongodEntry matches bin/mongod inside the archive, not mongodump or mongos.
func isMongodEntry(name string) bool {
	base := path.Base(strings.ReplaceAll(name, `\`, "/"))
	return base == "mongod" || base == "mongod.exe"
}

func writeBinary(target string, src io.Reader) error {
	tmp := target + ".part"
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, src); err != nil {
		out.Close()
		os.Remove(tmp)
		return err
	}
	if err := out.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, target)
}

// mongoArch is MongoDB's name for this processor.
func mongoArch() string {
	if runtime.GOARCH == "arm64" {
		return "arm64"
	}
	return "x86_64"
}

// mongoTargets is MongoDB's name for this operating system. Linux builds are
// published per distribution, so the closest matches are tried in order.
func mongoTargets() []string {
	switch runtime.GOOS {
	case "windows":
		return []string{"windows"}
	case "darwin":
		return []string{"macos"}
	}
	return linuxTargets()
}

func linuxTargets() []string {
	info := map[string]string{}
	if data, err := os.ReadFile("/etc/os-release"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if key, value, ok := strings.Cut(line, "="); ok {
				info[key] = strings.Trim(strings.TrimSpace(value), `"`)
			}
		}
	}
	id := strings.ToLower(info["ID"])
	version := strings.ReplaceAll(info["VERSION_ID"], ".", "")

	var targets []string
	switch id {
	case "ubuntu":
		targets = append(targets, "ubuntu"+version, "ubuntu2204", "ubuntu2004")
	case "debian":
		targets = append(targets, "debian"+version, "debian12", "debian11")
	case "rhel", "centos", "rocky", "almalinux", "fedora":
		major := strings.SplitN(info["VERSION_ID"], ".", 2)[0]
		targets = append(targets, "rhel"+major, "rhel93", "rhel90")
	case "amzn":
		targets = append(targets, "amazon"+version, "amazon2023", "amazon2")
	}
	// Ubuntu builds are the most widely compatible, so they end the list.
	targets = append(targets, "ubuntu2204", "ubuntu2004")

	seen := map[string]bool{}
	out := targets[:0]
	for _, t := range targets {
		if t != "" && !seen[t] {
			seen[t] = true
			out = append(out, t)
		}
	}
	return out
}

// Prefetch is what the Studio's "Download mongod" button triggers. It installs
// the binary and starts it, and answers with the status either way.
func (m *Mongo) Prefetch(ctx context.Context) map[string]any {
	if savedURI() != "" {
		return m.Status(ctx)
	}
	if m.FindBinary() == "" {
		if _, err := m.Download(ctx); err != nil {
			m.mu.Lock()
			m.reason = err.Error()
			m.mu.Unlock()
			return m.Status(ctx)
		}
	}
	if !listening(m.port) {
		if err := m.Start(ctx); err != nil {
			m.mu.Lock()
			m.reason = err.Error()
			m.mu.Unlock()
		}
	}
	return m.Status(ctx)
}

// Status answers /mongo in the shape the settings modal renders.
func (m *Mongo) Status(context.Context) map[string]any {
	m.mu.RLock()
	external, reason, port := m.external, m.reason, m.port
	version, progress := m.version, m.progress
	ours := m.ours && m.cmd != nil
	m.mu.RUnlock()

	binary := m.FindBinary()
	override := savedURI() != ""
	running := override || listening(port)
	return map[string]any{
		"available":  running,
		"running":    running,
		"external":   external,
		"override":   override,
		"ours":       ours,
		"downloaded": binary != "",
		"binary":     binary,
		"version":    version,
		"port":       port,
		"reason":     reason,
		"progress":   progress,
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
