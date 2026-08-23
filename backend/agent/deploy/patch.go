package deploy

import (
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// A Next.js project written for a laptop is usually not deployable as it
// stands, and the reasons are always the same handful: the build has no
// database to talk to, there is no health route for a load balancer to ask,
// the standalone output nobody needed locally is not switched on, and the auth
// client points at a hard-coded address.
//
// These are the changes that fix exactly that, and nothing else. Each one is
// recorded with the reason it was made, they happen only in the staging copy,
// and each is skipped when the project has already done it.

var (
	standaloneSet = regexp.MustCompile(`\boutput\s*:\s*['"]standalone['"]`)
	// A config is declared at the start of a line, and in TypeScript it
	// carries a type: `const nextConfig: NextConfig = {`.
	namedConfig    = regexp.MustCompile(`(?m)^[ \t]*(?:const|let|var)\s+(?:nextConfig|config)\s*(?::[^=\n]+)?=\s*\{`)
	exportedConfig = regexp.MustCompile(`(?m)^[ \t]*(?:module\.exports\s*=|export\s+default)\s*\{`)
	// Which identifier the file actually exports, so the right object is the
	// one that gets the setting.
	exportedName    = regexp.MustCompile(`(?m)^[ \t]*(?:module\.exports\s*=|export\s+default)\s+([A-Za-z_$][\w$]*)`)
	eagerConnect    = regexp.MustCompile(`(?s)let\s+clientPromise\b.*?export\s+default\s+clientPromise`)
	eagerAuth       = regexp.MustCompile(`toNextJsHandler\s*\(\s*auth\.handler\s*\)`)
	pinnedAuthURL   = regexp.MustCompile(`createAuthClient\(\s*\{[\s\S]*?baseURL\s*:\s*process\.env\.BETTER_AUTH_URL[\s\S]*?\}\s*\)`)
	readsDatabase   = regexp.MustCompile(`@/lib/mongodb|getCollection|getSessionUser|ensureSeeded|\bgetDb\b`)
	declaresDynamic = regexp.MustCompile(`export\s+const\s+dynamic\b`)
	alertInfo       = regexp.MustCompile(`(<Alert\b[^>]*\bvariant\s*=\s*["'])info(["'])`)
)

// configNames are the names Next.js accepts for its config, newest first.
var configNames = []string{"next.config.mjs", "next.config.js", "next.config.cjs", "next.config.ts"}

// patch is one change, with the reason it was needed. The review screen shows
// these, so they are written for a person to read.
func patch(path, reason, change string) map[string]string {
	return map[string]string{"path": path, "reason": reason, "change": change}
}

// applyPatches makes the project deployable. It returns what it wrote, what
// each change was for, and anything it could not do that a person now has to.
func applyPatches(w writer, service Service, target string) ([]Artifact, []map[string]string, []string) {
	records := []Artifact{}
	changes := []map[string]string{}
	problems := []string{}
	prefix := ""
	if service.Root != "" {
		prefix = service.Root + "/"
	}
	root := filepath.Join(w.staged, filepath.FromSlash(service.Root))

	add := func(relative, content, reason, change string) {
		record, err := w.write(relative, content, "source-patch")
		if err != nil {
			return
		}
		records = append(records, record)
		changes = append(changes, patch(relative, reason, change))
	}

	// Standalone output. Only the AWS targets need it — Vercel builds the app
	// its own way and would be confused by it.
	if target == TargetEC2 || target == TargetECS {
		if name, body, found := findConfig(w, prefix); found {
			if updated, ok := addStandalone(body); ok {
				add(prefix+name, updated,
					"Next.js standalone build output", "Add output: standalone")
			} else if !standaloneSet.MatchString(codeOnly(body)) {
				// The build will fail the standalone check and nothing would
				// say why, so the review screen says it instead.
				problems = append(problems, "Could not switch on standalone output in "+
					prefix+name+"; add  output: \"standalone\"  to the config by hand "+
					"before deploying.")
			}
		} else {
			add(prefix+"next.config.mjs", assetText("next.config.mjs"),
				"Next.js standalone build output", "Create a minimal standalone Next.js config")
		}
	}

	// A health route, so nginx and the release script have something to ask.
	appRoot := prefix + "app"
	if !isDir(filepath.Join(root, "app")) && isDir(filepath.Join(root, "src", "app")) {
		appRoot = prefix + "src/app"
	}
	typescript := w.has(prefix + "tsconfig.json")
	switch {
	case isDir(filepath.Join(w.staged, filepath.FromSlash(appRoot))):
		if !hasHealthRoute(w, appRoot) {
			name := "route.js"
			if typescript {
				name = "route.ts"
			}
			add(appRoot+"/api/health/"+name, assetText("health-route.js"),
				"nginx and release health checks", "Add safe GET /api/health endpoint")
		}
	case isDir(filepath.Join(root, "pages")):
		if !hasHealthPage(w, prefix) {
			add(prefix+"pages/api/health.js", assetText("health-page.js"),
				"nginx and release health checks", "Add safe GET /api/health endpoint")
		}
	}

	// Connect to the database on first use. `next build` imports every module,
	// and at build time there is no database to connect to.
	for _, name := range []string{"mongodb", "db", "mongo"} {
		for _, suffix := range []string{".ts", ".js"} {
			relative := prefix + "lib/" + name + suffix
			body, ok := w.read(relative)
			if !ok || strings.Contains(body, "function connection()") {
				continue
			}
			if !eagerConnect.MatchString(body) {
				continue
			}
			updated := eagerConnect.ReplaceAllLiteralString(body, assetSnippet("mongo-lazy.js"))
			updated = strings.ReplaceAll(updated, "await clientPromise", "await connection()")
			add(relative, updated,
				"next build imports every module and has no database",
				"Connect to MongoDB on first use instead of at import")
			// One file per helper name: a project can have both lib/mongodb.ts
			// and lib/db.ts, and a build fails on whichever is left eager.
			break
		}
	}

	// The same for Better Auth's catch-all route, which builds a handler at
	// import time and opens the database doing it.
	for _, suffix := range []string{".ts", ".js", ".tsx", ".jsx"} {
		relative := appRoot + "/api/auth/[...all]/route" + suffix
		body, ok := w.read(relative)
		if !ok {
			continue
		}
		if !eagerAuth.MatchString(body) || strings.Contains(body, "await import(") {
			break
		}
		add(relative, assetText("auth-route.js"),
			"next build imports route modules and has no database",
			"Build the Better Auth handler on first request, not at import")
		break
	}

	// Behind nginx the app is reached on one origin, so the client does not
	// need — and must not have — an address compiled into it.
	for _, suffix := range []string{".js", ".ts", ".jsx", ".tsx"} {
		relative := prefix + "lib/auth-client" + suffix
		body, ok := w.read(relative)
		if !ok || !strings.Contains(body, "process.env.BETTER_AUTH_URL") {
			continue
		}
		updated := replaceFirst(pinnedAuthURL, body, "createAuthClient()")
		if updated != body {
			add(relative, updated,
				"Same-origin authentication behind nginx",
				"Use Better Auth same-origin client defaults")
		}
		break
	}

	// A page that reads the database at request time must not be prerendered
	// during the build, where the database does not exist.
	for _, relative := range pagesReadingDatabase(w, appRoot) {
		body, ok := w.read(relative)
		if !ok {
			continue
		}
		add(relative, forceDynamic(body),
			"Reads the database at request time",
			"Add export const dynamic = 'force-dynamic' so it is not prerendered")
	}

	return records, changes, problems
}

// findConfig is the project's Next.js config, whichever name it uses.
func findConfig(w writer, prefix string) (name, body string, found bool) {
	for _, candidate := range configNames {
		if body, ok := w.read(prefix + candidate); ok {
			return candidate, body, true
		}
	}
	return "", "", false
}

// addStandalone switches standalone output on in a config that does not have
// it. A config it cannot recognise is left alone and reported instead: a wrong
// edit to a config file breaks the build for everyone, and the review screen
// can ask a person for it.
//
// Matching happens against the file with its comments blanked out, so a
// commented-out config above the real one cannot be edited into life.
func addStandalone(body string) (string, bool) {
	code := codeOnly(body)
	if standaloneSet.MatchString(code) {
		return body, false
	}

	// The object the file exports is the one Next.js reads. A file may well
	// declare others — a middleware matcher, a shared constant — and putting
	// the setting in one of those would change nothing and look like success.
	if name := exportedName.FindStringSubmatch(code); name != nil {
		declared := regexp.MustCompile(
			`(?m)^[ \t]*(?:const|let|var)\s+` + regexp.QuoteMeta(name[1]) + `\s*(?::[^=\n]+)?=\s*\{`)
		if at := declared.FindStringIndex(code); at != nil {
			return withStandalone(body, at[1]), true
		}
	}
	for _, pattern := range []*regexp.Regexp{namedConfig, exportedConfig} {
		if at := pattern.FindStringIndex(code); at != nil {
			return withStandalone(body, at[1]), true
		}
	}
	return body, false
}

// withStandalone writes the setting immediately after the opening brace the
// match ended on, keeping an empty object tidy.
func withStandalone(body string, after int) string {
	const setting = "\n  output: \"standalone\","
	rest := body[after:]
	if trimmed := strings.TrimLeft(rest, " \t"); strings.HasPrefix(trimmed, "}") {
		return body[:after] + setting + "\n" + rest[len(rest)-len(trimmed):]
	}
	return body[:after] + setting + rest
}

// codeOnly is the file with every comment replaced by spaces. It is the same
// length as the original, so an index into it is an index into the original.
//
// This is not a JavaScript parser and does not pretend to be. It exists for one
// question — is this match real code, or is it something a developer commented
// out? — which is the question that decides whether an edit here is safe.
func codeOnly(body string) string {
	out := []byte(body)
	line, block := false, false

	for i := 0; i < len(out); i++ {
		switch {
		case line:
			if out[i] == '\n' {
				line = false
				continue
			}
			out[i] = ' '
		case block:
			if out[i] == '*' && i+1 < len(out) && out[i+1] == '/' {
				out[i], out[i+1] = ' ', ' '
				i++
				block = false
				continue
			}
			if out[i] != '\n' {
				out[i] = ' '
			}
		case out[i] == '/' && i+1 < len(out) && out[i+1] == '/':
			line = true
			out[i], out[i+1] = ' ', ' '
			i++
		case out[i] == '/' && i+1 < len(out) && out[i+1] == '*':
			block = true
			out[i], out[i+1] = ' ', ' '
			i++
		}
	}
	return string(out)
}

func hasHealthRoute(w writer, appRoot string) bool {
	for _, suffix := range []string{".ts", ".tsx", ".js", ".jsx"} {
		if w.has(appRoot + "/api/health/route" + suffix) {
			return true
		}
	}
	return false
}

func hasHealthPage(w writer, prefix string) bool {
	for _, suffix := range []string{".js", ".ts", ".jsx", ".tsx"} {
		if w.has(prefix + "pages/api/health" + suffix) {
			return true
		}
	}
	return false
}

// pagesReadingDatabase are the pages and layouts that talk to the database and
// have not already said they are dynamic.
func pagesReadingDatabase(w writer, appRoot string) []string {
	dir := filepath.Join(w.staged, filepath.FromSlash(appRoot))
	if !isDir(dir) {
		return nil
	}
	out := []string{}
	_ = filepath.WalkDir(dir, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			if skipDirs[entry.Name()] {
				return fs.SkipDir
			}
			return nil
		}
		name := entry.Name()
		stem, _, _ := strings.Cut(name, ".")
		if stem != "page" && stem != "layout" {
			return nil
		}
		if !map[string]bool{".js": true, ".jsx": true, ".ts": true, ".tsx": true}[filepath.Ext(name)] {
			return nil
		}
		body, err := os.ReadFile(path)
		if err != nil {
			return nil
		}
		if !readsDatabase.Match(body) || declaresDynamic.Match(body) {
			return nil
		}
		out = append(out, appRoot+"/"+relPosix(dir, path))
		return nil
	})
	sort.Strings(out)
	return out
}

// forceDynamic adds the declaration below the file's imports and directives,
// which is the only place it is allowed to be.
//
// Finding that place means knowing where the last import statement ends, and an
// import in a Next.js page is usually several lines long:
//
//	import {
//	  Card,
//	} from '@/components/ui/card'
//
// Counting lines that begin with "import " would put the declaration inside
// that statement and leave the customer with a file that does not parse. So
// this walks the top of the file, follows each import to its end, and stops at
// the first line that is neither an import, a directive, nor a comment.
func forceDynamic(body string) string {
	lines := strings.Split(strings.ReplaceAll(body, "\r\n", "\n"), "\n")
	at, depth, inImport, inComment := 0, 0, false, false

	for index, line := range lines {
		trimmed := strings.TrimSpace(line)

		if inComment {
			if strings.Contains(trimmed, "*/") {
				inComment = false
			}
			continue
		}
		if inImport {
			depth += brackets(line)
			if depth <= 0 && importEnds(trimmed) {
				at, inImport, depth = index+1, false, 0
			}
			continue
		}

		switch {
		case trimmed == "" || strings.HasPrefix(trimmed, "//"):
			// Blank lines and comments neither move the insertion point nor
			// end the run of imports.
		case strings.HasPrefix(trimmed, "/*"):
			inComment = !strings.Contains(trimmed, "*/")
		case strings.HasPrefix(trimmed, "'use ") || strings.HasPrefix(trimmed, `"use `):
			at = index + 1
		case strings.HasPrefix(trimmed, "import"):
			if brackets(line) == 0 && importEnds(trimmed) {
				at = index + 1
				continue
			}
			// An import that is still open: follow it to its closing line.
			inImport, depth = true, brackets(line)
		default:
			// The first real statement. Anything below this is code, and the
			// declaration has to be above all of it.
			return insertAt(lines, at)
		}
	}
	return insertAt(lines, at)
}

// brackets is how much deeper into an unfinished statement one line takes us.
func brackets(line string) int {
	return strings.Count(line, "{") - strings.Count(line, "}") +
		strings.Count(line, "(") - strings.Count(line, ")")
}

// importEnds reports whether an import statement finishes on this line: it has
// reached its module specifier, or it is a bare side-effect import.
func importEnds(trimmed string) bool {
	after := trimmed
	if at := strings.LastIndex(trimmed, " from "); at >= 0 {
		after = trimmed[at+len(" from "):]
	} else if !strings.HasPrefix(trimmed, "import '") && !strings.HasPrefix(trimmed, `import "`) {
		return false
	} else {
		after = strings.TrimSpace(strings.TrimPrefix(trimmed, "import"))
	}
	after = strings.TrimSuffix(strings.TrimSpace(after), ";")
	return len(after) >= 2 && (after[0] == '\'' || after[0] == '"') &&
		after[len(after)-1] == after[0]
}

// insertAt puts the declaration at the line the walk settled on.
func insertAt(lines []string, at int) string {
	out := append([]string{}, lines[:at]...)
	out = append(out, "", "export const dynamic = 'force-dynamic'")
	out = append(out, lines[at:]...)
	return strings.Join(out, "\n") + "\n"
}

// replaceFirst applies a replacement to the first match only. Go's regexp has
// no count, and every rule here is about one place in one file.
func replaceFirst(pattern *regexp.Regexp, body, replacement string) string {
	at := pattern.FindStringSubmatchIndex(body)
	if at == nil {
		return body
	}
	return body[:at[0]] + string(pattern.ExpandString(nil, replacement, body, at)) + body[at[1]:]
}

// assetSnippet is an asset used as a replacement inside another file, so it
// carries no trailing newline of its own.
func assetSnippet(name string) string {
	return strings.TrimRight(assetText(name), "\n")
}

func isDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// --- repairing a build that failed -----------------------------------------------------------

// maxScanned and maxFileSize bound the alert-variant sweep. A repair pass must
// not turn into a walk of an entire monorepo.
const (
	maxScanned  = 2000
	maxFileSize = 512_000
)

// RepairCompatibility applies the bounded repairs the planner chose to the
// staging copy, and returns the records as they stand plus whether anything
// actually changed.
//
// Nothing here is open-ended. Each action is one known edit; a build that
// fails for any other reason fails, and says so, rather than being guessed at.
func RepairCompatibility(w writer, spec *Spec, plan *Plan, records []Artifact,
	actions []string, buildError string) ([]Artifact, bool) {
	if len(actions) == 0 {
		return records, false
	}
	service := planned(spec.Services[0], plan)
	byPath := map[string]Artifact{}
	before := map[string]string{}
	for _, record := range records {
		byPath[record.Path] = record
		before[record.Path] = record.SHA256
	}
	prefix := ""
	if service.Root != "" {
		prefix = service.Root + "/"
	}
	appRoot := prefix + "app"
	if !isDir(filepath.Join(w.staged, filepath.FromSlash(appRoot))) {
		appRoot = prefix + "src/app"
	}

	// A JavaScript health route in a TypeScript project fails the build with
	// allowJs off. It is only ever removed when the agent wrote it and there
	// was nothing at that path before.
	if contains(actions, "ensure-type-safe-health-route") && w.has(prefix+"tsconfig.json") {
		legacy := appRoot + "/api/health/route.js"
		if record, ours := byPath[legacy]; ours && record.Kind == "source-patch" && !record.OriginalExists {
			w.remove(legacy)
			delete(byPath, legacy)
		}
	}

	// Everything the patches do is idempotent, so re-running them fills in
	// whatever the removal above left missing.
	patched, changes, problems := applyPatches(w, service, plan.Target)
	for _, problem := range problems {
		if !warnedAbout(plan.Risks, problem) {
			plan.Risks = append(plan.Risks, problem)
		}
	}
	for _, record := range patched {
		byPath[record.Path] = record
	}

	if contains(actions, "normalize-alert-variant") {
		for _, change := range normalizeAlertVariant(w, service) {
			record, err := w.write(change["path"], change["body"], "source-patch")
			if err != nil {
				continue
			}
			byPath[record.Path] = record
			changes = append(changes, patch(change["path"],
				"Type-safe Alert variant compatibility",
				"Normalize unsupported Alert variant info to default"))
		}
	}

	for _, change := range changes {
		if !hasChange(plan.SourcePatches, change) {
			plan.SourcePatches = append(plan.SourcePatches, change)
		}
	}

	changed := len(byPath) != len(before)
	for path, record := range byPath {
		if before[path] != record.SHA256 {
			changed = true
		}
	}

	if changed {
		if record, ok := refreshManifest(w, plan, byPath); ok {
			byPath[record.Path] = record
		}
	} else if strings.Contains(buildError, "route.js") && strings.Contains(buildError, "allowJs") {
		plan.Risks = append(plan.Risks,
			"The bounded type-safe health repair made no change; manual application code review is required.")
	}

	out := make([]Artifact, 0, len(byPath))
	for _, path := range sortedKeys(byPath) {
		out = append(out, byPath[path])
	}
	return out, changed
}

// normalizeAlertVariant finds components using an Alert variant the project's
// own union does not have, and puts them back to the default.
func normalizeAlertVariant(w writer, service Service) []map[string]string {
	root := filepath.Join(w.staged, filepath.FromSlash(service.Root))
	out := []map[string]string{}
	scanned := 0

	sourceFile := map[string]bool{".ts": true, ".tsx": true, ".js": true, ".jsx": true}
	_ = filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || scanned >= maxScanned {
			return nil
		}
		if entry.IsDir() {
			if skipDirs[entry.Name()] {
				return fs.SkipDir
			}
			return nil
		}
		if !sourceFile[strings.ToLower(filepath.Ext(entry.Name()))] {
			return nil
		}
		info, err := entry.Info()
		if err != nil || info.Size() > maxFileSize {
			return nil
		}
		body, err := os.ReadFile(path)
		if err != nil {
			return nil
		}
		scanned++
		updated := alertInfo.ReplaceAllString(string(body), "${1}default${2}")
		if updated == string(body) {
			return nil
		}
		out = append(out, map[string]string{"path": relPosix(w.staged, path), "body": updated})
		return nil
	})
	sort.Slice(out, func(i, j int) bool { return out[i]["path"] < out[j]["path"] })
	return out
}

// refreshManifest brings the record of what the agent owns back in line with
// what it owns after a repair.
func refreshManifest(w writer, plan *Plan, byPath map[string]Artifact) (Artifact, bool) {
	body, ok := w.read("deployment-manifest.json")
	if !ok {
		return Artifact{}, false
	}
	var record Manifest
	if json.Unmarshal([]byte(body), &record) != nil {
		return Artifact{}, false
	}

	owned := sortedKeys(byPath)
	if !contains(owned, "deployment-manifest.json") {
		owned = append(owned, "deployment-manifest.json")
		sort.Strings(owned)
	}
	record.Generation.RepairActions = append([]string{}, plan.RepairActions...)
	record.Artifacts = owned
	record.AgentOwnedFiles = owned

	written, err := w.write("deployment-manifest.json", indented(record), "manifest")
	if err != nil {
		return Artifact{}, false
	}
	return written, true
}

func hasChange(changes []map[string]string, change map[string]string) bool {
	for _, existing := range changes {
		if existing["path"] == change["path"] && existing["change"] == change["change"] {
			return true
		}
	}
	return false
}
