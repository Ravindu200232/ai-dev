package qa

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"agentforge/agent/core"
)

// Three checks that all work the same way: look at what is really there, probe
// the running application, and record what was found. None of them can fail the
// build — a slow page is worth reporting, not worth throwing the app away for.

// Report is what .agentforge/qa/report.json holds and the Testing tab renders.
type Report struct {
	Project     string         `json:"project"`
	Runtime     StageReport    `json:"runtime"`
	Suite       StageReport    `json:"suite"`
	API         StageReport    `json:"api"`
	E2E         StageReport    `json:"e2e"`
	Security    SecurityReport `json:"security"`
	Performance *Performance   `json:"performance,omitempty"`
	FinishedAt  string         `json:"finished_at"`
}

// StageReport is one stage's tally.
type StageReport struct {
	Name       string   `json:"name"`
	Passed     int      `json:"passed"`
	Failed     int      `json:"failed"`
	Total      int      `json:"total"`
	Unresolved []string `json:"unresolved"`
	Note       string   `json:"note,omitempty"`
}

// SecurityReport lists what the scan found.
type SecurityReport struct {
	Findings []Finding `json:"findings"`
	Checked  int       `json:"checked"`
}

// Finding is one security problem, with the file that proves it.
type Finding struct {
	Severity string `json:"severity"` // high, medium, low
	Title    string `json:"title"`
	File     string `json:"file,omitempty"`
	Line     int    `json:"line,omitempty"`
	Detail   string `json:"detail"`
}

// Performance is the shape studio/components/testing/Overview.jsx reads.
type Performance struct {
	Scores    map[string]int    `json:"scores"`
	Metrics   map[string]string `json:"metrics"`
	Routes    []RouteTiming     `json:"routes"`
	FetchTime string            `json:"fetchTime"`
}

// RouteTiming is one page's measured response.
type RouteTiming struct {
	Route  string `json:"route"`
	MS     int    `json:"ms"`
	Bytes  int    `json:"bytes"`
	Status int    `json:"status"`
}

// Save writes the report the Studio polls for.
func (s *Suite) Save(run *core.Run) {
	s.report.Project = run.Project
	s.report.FinishedAt = time.Now().UTC().Format(time.RFC3339)
	path := filepath.Join(run.Paths.Meta(run.Project), "qa", "report.json")
	if err := core.WriteJSON(path, s.report); err != nil {
		run.Warn("could not write the QA report: " + err.Error())
	}
}

// --- API ----------------------------------------------------------------------

const apiPlanSystem = `You design HTTP checks for the API routes of a Next.js
App Router application.

You are given each route's source. For each one, write the requests that prove
it works: the method, a valid body where one is needed, and the status you
expect. Only describe requests the source actually handles.

Rules:
- A GET that lists things expects 200 and a JSON array or object.
- A POST that creates something expects 200 or 201, and names the field in the
  response that proves it was created.
- Include one request with a missing or invalid body that should be rejected
  with a 4xx, when the source validates its input.
- Never expect a 500. A 500 is a bug, not a contract.

Answer with JSON only:
{"checks":[{"route":"/api/orders","method":"GET","body":null,
  "expect_status":[200],"expect_contains":"","why":"lists the orders"}]}`

// APICheck is one planned request.
type APICheck struct {
	Route          string `json:"route"`
	Method         string `json:"method"`
	Body           any    `json:"body"`
	ExpectStatus   []int  `json:"expect_status"`
	ExpectContains string `json:"expect_contains"`
	Why            string `json:"why"`
}

// API probes every route the survey found against the running dev server.
func (s *Suite) API(ctx context.Context, run *core.Run) error {
	core.Refresh(run)
	routes := run.Structure.APIs
	if len(routes) == 0 {
		s.report.API.Note = "this application has no API routes"
		run.Info("🔌 no API routes to check")
		return nil
	}
	run.Info(fmt.Sprintf("🔌 Checking %d API route(s)", len(routes)))

	checks, err := s.planAPIChecks(ctx, run, routes)
	if err != nil {
		run.Warn("the API plan could not be written: " + err.Error())
		checks = defaultAPIChecks(routes)
	}

	var failures []string
	for i, check := range checks {
		if err := run.Check(); err != nil {
			return err
		}
		run.Progress("api", 74+3*float64(i)/float64(len(checks)))
		ok, detail := s.probe(ctx, check)
		s.report.API.Total++
		if ok {
			s.report.API.Passed++
			run.TestResult("pass", check.Method+" "+check.Route, check.Why)
			continue
		}
		s.report.API.Failed++
		line := check.Method + " " + check.Route + " — " + detail
		failures = append(failures, line)
		s.report.API.Unresolved = append(s.report.API.Unresolved, line)
		run.TestResult("fail", check.Method+" "+check.Route, detail)
	}

	if len(failures) > 0 {
		run.TestFixing(1, failures)
		if err := s.repairAPI(ctx, run, failures); err != nil {
			run.Warn("the API repair failed: " + err.Error())
			return nil
		}
		// One verification pass: the repair either fixed it or it did not.
		s.reprobe(ctx, run, checks)
	}
	return nil
}

func (s *Suite) planAPIChecks(ctx context.Context, run *core.Run, routes []string) ([]APICheck, error) {
	var b strings.Builder
	b.WriteString("API ROUTES AND THEIR SOURCE\n")
	for _, route := range routes {
		file := "app" + route + "/route.js"
		if !run.Shell.Exists(file) {
			file = "app" + route + "/route.jsx"
		}
		if body, _, err := run.Shell.Read(file); err == nil {
			fmt.Fprintf(&b, "\n--- %s (%s) ---\n%s\n", route, file, body)
		}
	}
	var reply struct {
		Checks []APICheck `json:"checks"`
	}
	if err := run.LLM.JSON(ctx, core.RoleQA, apiPlanSystem, b.String(), &reply); err != nil {
		return nil, err
	}
	if len(reply.Checks) == 0 {
		return nil, fmt.Errorf("no checks were planned")
	}
	return reply.Checks, nil
}

// defaultAPIChecks is the fallback when the model cannot plan: every route
// should at least answer a GET without erroring.
func defaultAPIChecks(routes []string) []APICheck {
	out := make([]APICheck, 0, len(routes))
	for _, route := range routes {
		out = append(out, APICheck{
			Route: route, Method: http.MethodGet,
			ExpectStatus: []int{200, 201, 204, 400, 401, 403, 404, 405},
			Why:          "answers without a server error",
		})
	}
	return out
}

// probe performs one check against the live app.
func (s *Suite) probe(ctx context.Context, check APICheck) (bool, string) {
	method := strings.ToUpper(strings.TrimSpace(check.Method))
	if method == "" {
		method = http.MethodGet
	}
	var body io.Reader
	if check.Body != nil {
		if data, err := json.Marshal(check.Body); err == nil {
			body = strings.NewReader(string(data))
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, s.BaseURL()+check.Route, body)
	if err != nil {
		return false, err.Error()
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return false, "the route did not answer: " + err.Error()
	}
	defer resp.Body.Close()
	payload, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))

	if resp.StatusCode >= 500 {
		return false, fmt.Sprintf("answered %d — %s", resp.StatusCode, firstLines(stripTags(string(payload)), 3))
	}
	if len(check.ExpectStatus) > 0 && !containsInt(check.ExpectStatus, resp.StatusCode) {
		return false, fmt.Sprintf("answered %d, expected %v", resp.StatusCode, check.ExpectStatus)
	}
	if want := strings.TrimSpace(check.ExpectContains); want != "" && !strings.Contains(string(payload), want) {
		return false, "the response did not contain " + want
	}
	return true, ""
}

// reprobe re-runs the failing checks after a repair and updates the tally.
func (s *Suite) reprobe(ctx context.Context, run *core.Run, checks []APICheck) {
	s.report.API = StageReport{Name: "api"}
	for _, check := range checks {
		if run.Check() != nil {
			return
		}
		ok, detail := s.probe(ctx, check)
		s.report.API.Total++
		if ok {
			s.report.API.Passed++
			continue
		}
		s.report.API.Failed++
		s.report.API.Unresolved = append(s.report.API.Unresolved,
			check.Method+" "+check.Route+" — "+detail)
	}
	if s.report.API.Failed == 0 {
		run.Info("✅ every API route answers correctly")
		run.TestResult("pass", "API routes answer correctly",
			fmt.Sprintf("%d check(s)", s.report.API.Total))
	}
}

const apiRepairSystem = `You repair failing API routes in a Next.js App Router
application.

Each failure names a route, what was sent, and how it answered. A 500 means the
handler throws — find the throw. Fix the handler, not the check, unless the
check expects something the route was never meant to do.

Output format — nothing else:

<<<FILE app/api/<name>/route.js
...the complete corrected file...
>>>END`

func (s *Suite) repairAPI(ctx context.Context, run *core.Run, failures []string) error {
	var paths []string
	for _, route := range run.Structure.APIs {
		for _, f := range failures {
			if strings.Contains(f, route) {
				for _, ext := range []string{".js", ".jsx"} {
					if candidate := "app" + route + "/route" + ext; run.Shell.Exists(candidate) {
						paths = append(paths, candidate)
					}
				}
			}
		}
	}
	paths = append(paths, "lib/db.js")

	var b strings.Builder
	b.WriteString("FAILING API CHECKS\n")
	for _, f := range failures {
		b.WriteString("- " + f + "\n")
	}
	b.WriteString("\nTHE ROUTES INVOLVED\n")
	b.WriteString(core.ReadFiles(run, dedupePaths(paths), 40000))

	writer := core.NewFileWriter(run)
	if _, err := run.LLM.Stream(ctx, core.RoleQA, apiRepairSystem, b.String(), writer.Feed); err != nil {
		return err
	}
	writer.Finish()
	if len(writer.Written()) == 0 {
		return fmt.Errorf("the repair produced no changes")
	}
	if !sleepCtx(ctx, settleAfterFix) {
		return run.Check()
	}
	return nil
}

// --- performance --------------------------------------------------------------

// Performance measures what the running app actually serves. It is a real
// measurement of this machine, not a Lighthouse score, and the report says so.
func (s *Suite) Performance(ctx context.Context, run *core.Run) error {
	core.Refresh(run)
	routes := walkableRoutes(run)
	if len(routes) == 0 {
		return nil
	}
	run.Info(fmt.Sprintf("⚡ Timing %d route(s)", len(routes)))

	perf := &Performance{
		Scores:    map[string]int{},
		Metrics:   map[string]string{},
		FetchTime: time.Now().UTC().Format(time.RFC3339),
	}
	slowest, total := 0, 0
	for _, route := range routes {
		if err := run.Check(); err != nil {
			return err
		}
		timing := s.time(ctx, route)
		perf.Routes = append(perf.Routes, timing)
		total += timing.MS
		if timing.MS > slowest {
			slowest = timing.MS
		}
	}

	average := total / len(perf.Routes)
	perf.Metrics["average-response"] = fmt.Sprintf("%d ms", average)
	perf.Metrics["slowest-response"] = fmt.Sprintf("%d ms", slowest)
	perf.Metrics["routes-measured"] = fmt.Sprintf("%d", len(perf.Routes))
	perf.Scores["performance"] = responseScore(average)

	s.report.Performance = perf
	_ = core.WriteJSON(filepath.Join(run.Paths.Meta(run.Project), "performance.json"), perf)

	status, detail := "pass", fmt.Sprintf("average %d ms, slowest %d ms", average, slowest)
	if average > 1500 {
		status = "warn"
	}
	run.TestResult(status, "Response times measured", detail)
	run.Info("⚡ " + detail)
	return nil
}

// time fetches one route and measures it. The dev server compiles on first
// request, so the first hit is discarded.
func (s *Suite) time(ctx context.Context, route string) RouteTiming {
	client := &http.Client{Timeout: 45 * time.Second}
	url := s.BaseURL() + route

	warm, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if resp, err := client.Do(warm); err == nil {
		_, _ = io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
	}

	started := time.Now()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	resp, err := client.Do(req)
	if err != nil {
		return RouteTiming{Route: route, Status: 0, MS: int(time.Since(started).Milliseconds())}
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	return RouteTiming{
		Route: route, Status: resp.StatusCode,
		MS: int(time.Since(started).Milliseconds()), Bytes: len(body),
	}
}

// responseScore turns an average response time into the 0-100 the Studio shows.
func responseScore(averageMS int) int {
	switch {
	case averageMS <= 200:
		return 100
	case averageMS >= 3000:
		return 10
	default:
		return 100 - (averageMS-200)*90/2800
	}
}

// walkableRoutes are the pages a browser can actually open — no [id] segments,
// because there is no way to know a real id from the file name alone.
func walkableRoutes(run *core.Run) []string {
	if run.Structure == nil {
		return nil
	}
	var out []string
	for _, route := range run.Structure.Routes {
		if !strings.Contains(route, "[") {
			out = append(out, route)
		}
	}
	return out
}

// --- security -----------------------------------------------------------------

// secretPattern matches credentials committed into source.
var securityRules = []struct {
	name     string
	severity string
	pattern  *regexp.Regexp
	detail   string
}{
	{"hard-coded secret", "high",
		regexp.MustCompile(`(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*["'][^"'\s]{12,}["']`),
		"a credential is written into the source instead of read from the environment"},
	{"dangerouslySetInnerHTML", "medium",
		regexp.MustCompile(`dangerouslySetInnerHTML`),
		"raw HTML is injected into the page, which is an XSS route if any of it came from a user"},
	{"eval on request data", "high",
		regexp.MustCompile(`\beval\s*\(|new\s+Function\s*\(`),
		"code is built from a string at runtime"},
	{"mongo query built from a raw string", "medium",
		regexp.MustCompile(`\$where\s*:`),
		"$where runs JavaScript inside the query engine"},
	{"connection string in source", "high",
		regexp.MustCompile(`mongodb(\+srv)?://[^'"\s]*:[^'"\s]*@`),
		"a database URI with a password is written into the source"},
}

// Security scans the source for the mistakes that matter, then checks that the
// API actually refuses what it should refuse.
func (s *Suite) Security(ctx context.Context, run *core.Run) error {
	core.Refresh(run)
	run.Info("🔒 Scanning for security problems")

	findings := []Finding{}
	checked := 0
	for _, rel := range run.Structure.Files {
		if !isSource(rel) {
			continue
		}
		body, _, err := run.Shell.Read(rel)
		if err != nil {
			continue
		}
		checked++
		for _, rule := range securityRules {
			// The scaffold reads its URI from the environment; a match there
			// would be the fallback default, not a leaked credential.
			if rel == "lib/db.js" && rule.name == "connection string in source" {
				continue
			}
			if loc := rule.pattern.FindStringIndex(body); loc != nil {
				findings = append(findings, Finding{
					Severity: rule.severity, Title: rule.name, File: rel,
					Line: 1 + strings.Count(body[:loc[0]], "\n"), Detail: rule.detail,
				})
			}
		}
	}
	findings = append(findings, s.probeExposure(ctx, run)...)

	s.report.Security = SecurityReport{Findings: findings, Checked: checked}
	if len(findings) == 0 {
		run.Info("🔒 nothing found in " + fmt.Sprint(checked) + " file(s)")
		run.TestResult("pass", "Security scan is clean", fmt.Sprintf("%d file(s) scanned", checked))
		return nil
	}
	high := 0
	for _, f := range findings {
		if f.Severity == "high" {
			high++
		}
		run.Warn(fmt.Sprintf("   %s: %s (%s)", f.Severity, f.Title, f.File))
	}
	status := "warn"
	if high > 0 {
		status = "fail"
	}
	run.TestResult(status, fmt.Sprintf("%d security finding(s)", len(findings)),
		fmt.Sprintf("%d high severity", high))
	return nil
}

// probeExposure asks the running app for things it should not hand over.
func (s *Suite) probeExposure(ctx context.Context, run *core.Run) []Finding {
	var findings []Finding
	client := &http.Client{Timeout: 20 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}

	for _, path := range []string{"/.env", "/.git/config", "/package.json", "/lib/db.js"} {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.BaseURL()+path, nil)
		if err != nil {
			continue
		}
		resp, err := client.Do(req)
		if err != nil {
			continue
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		resp.Body.Close()
		if resp.StatusCode == http.StatusOK && len(body) > 0 && !isHTML(body) {
			findings = append(findings, Finding{
				Severity: "high", Title: "a private file is served to the browser",
				File:   strings.TrimPrefix(path, "/"),
				Detail: fmt.Sprintf("GET %s answered %d with %d bytes", path, resp.StatusCode, len(body)),
			})
		}
	}
	return findings
}

func isHTML(body []byte) bool {
	head := strings.ToLower(strings.TrimSpace(string(body[:min(len(body), 200)])))
	return strings.HasPrefix(head, "<!doctype") || strings.HasPrefix(head, "<html")
}

func isSource(rel string) bool {
	for _, ext := range []string{".js", ".jsx", ".mjs", ".ts", ".tsx", ".json", ".env"} {
		if strings.HasSuffix(rel, ext) {
			return !strings.HasPrefix(rel, "tests/")
		}
	}
	return false
}

func containsInt(list []int, want int) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

// appendLine adds one line to a file, creating it and its directory.
func appendLine(path, line string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	fh, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer fh.Close()
	_, err = fh.WriteString(line + "\n")
	return err
}

// fetchText GETs a URL and returns the body as text.
func fetchText(ctx context.Context, url string, timeout time.Duration) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	client := &http.Client{Timeout: timeout}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	return string(body), err
}
