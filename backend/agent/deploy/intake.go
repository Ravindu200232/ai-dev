package deploy

import (
	"context"
	"encoding/json"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Intake reads a project as it actually is.
//
// Nothing here asks a model anything. The framework, the package manager, the
// port, the routes and the variables the code reads are all facts on disk, and
// a deployment that guessed any of them would be wrong in a way nobody would
// notice until it was live.
//
// The project is copied into a staging workspace first. Everything after this
// point — generated workflows, patched files, the build — happens to the copy,
// so a deployment can never alter the customer's own folder.

// skipDirs are never staged, scanned or walked into.
var skipDirs = map[string]bool{
	".git": true, ".next": true, ".cache": true, ".turbo": true, ".vercel": true,
	"node_modules": true, "dist": true, "build": true, "out": true, "coverage": true,
}

// sourceExtensions are the files worth reading for what the project needs.
var sourceExtensions = map[string]bool{
	".js": true, ".jsx": true, ".ts": true, ".tsx": true, ".mjs": true, ".cjs": true,
	".json": true, ".css": true, ".md": true, ".yml": true, ".yaml": true,
}

// routeFiles are what the Next.js App Router treats as a reachable address.
var routeFiles = map[string]bool{
	"page.js": true, "page.jsx": true, "page.ts": true, "page.tsx": true,
	"route.js": true, "route.ts": true,
}

// envPattern finds every variable the code reads, in both the forms Next.js
// supports: process.env.NAME and process.env["NAME"].
var envPattern = regexp.MustCompile(`process\.env(?:\.([A-Z][A-Z0-9_]+)|\[['"]([A-Z][A-Z0-9_]+)['"]\])`)

// portPattern finds a port a script pins, so a deployment listens where the
// project already expects to be listening.
var portPattern = regexp.MustCompile(`(?:--port|-p)\s+(\d{2,5})`)

// slugPattern is everything a name may not contain. nonSlug is the same for a
// name that has already been slugged once and may keep its hyphens.
var (
	slugPattern = regexp.MustCompile(`[^a-z0-9]+`)
	nonSlug     = regexp.MustCompile(`[^a-z0-9-]+`)
)

const (
	gitTimeout      = 10 * time.Minute
	lockfileTimeout = 10 * time.Minute
	defaultPort     = 3000
)

// Emit is how a stage reports what it is doing. The store's AddEvent satisfies
// it; a test can pass a function that keeps the events in memory.
type Emit func(event Event)

func (e Emit) step(stage, status string, percent int, message string, data map[string]any) {
	if e == nil {
		return
	}
	e(Event{Type: EventStep, Stage: stage, Status: status, Percent: percent, Message: message, Data: data})
}

// Intake stages a project and works out what it is.
type Intake struct{ Emit Emit }

// Read copies the project into the staging workspace and returns what it
// found there. The error it returns is the one thing that stops a run before
// it starts: a folder with no Next.js application in it.
func (in *Intake) Read(ctx context.Context, source, staged string) (*Spec, error) {
	source, err := filepath.Abs(source)
	if err != nil {
		return nil, err
	}
	info, err := os.Stat(source)
	if err != nil || !info.IsDir() {
		return nil, badRequest("Project folder does not exist: " + source)
	}

	in.Emit.step("intake", StatusRunning, 4, "Copying project into an isolated review workspace", nil)
	if err := os.RemoveAll(staged); err != nil {
		return nil, err
	}
	if err := stage(source, staged); err != nil {
		return nil, err
	}

	repository := readRepository(ctx, source)
	services := discoverServices(staged)
	if len(services) == 0 {
		return nil, badRequest("No Next.js application was found. " +
			"v1 requires a package.json with a Next.js dependency.")
	}

	warnings := in.ensureLockfiles(ctx, staged, services)
	// Generating a lockfile changes what the service is, so anything that had
	// none is read again rather than patched up in place.
	if missingLockfile(services) {
		services = discoverServices(staged)
	}
	if len(services) > 1 {
		warnings = append(warnings,
			"Multiple Next.js services were detected; the first service is the primary deployment target.")
	}
	if len(repository.DirtyFiles) > 0 {
		warnings = append(warnings,
			"The source repository has uncommitted files. Only agent-owned files will be staged on deploy.")
	}

	primary := services[0]
	root := primary.Root
	if root == "" {
		root = "."
	}
	in.Emit.step("intake", StatusComplete, 14,
		"Detected "+strconv.Itoa(len(services))+" Next.js service(s); primary root: "+root,
		map[string]any{"services": len(services), "primary_root": root})

	return &Spec{
		Name:       slug(filepath.Base(source), primary.Name),
		SourcePath: source,
		StagedPath: staged,
		Services:   services,
		Repository: repository,
		Warnings:   warnings,
	}, nil
}

// --- staging ----------------------------------------------------------------------------

// stage copies the project, leaving out what a build produces and anything
// beginning .env — a staged workspace must never hold the customer's secrets,
// because it is the thing that gets committed, archived and exported.
func stage(source, target string) error {
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			// A file that cannot be read is skipped, not fatal: a locked file
			// somewhere in the tree must not stop a deployment.
			if entry != nil && entry.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		name := entry.Name()
		if path != source && (skipDirs[name] || strings.HasPrefix(name, ".env")) {
			if entry.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		rel, err := filepath.Rel(source, path)
		if err != nil {
			return nil
		}
		destination := filepath.Join(target, rel)
		if entry.IsDir() {
			return os.MkdirAll(destination, 0o755)
		}
		if !entry.Type().IsRegular() {
			return nil
		}
		return copyFile(path, destination)
	})
}

func copyFile(from, to string) error {
	in, err := os.Open(from)
	if err != nil {
		return nil
	}
	defer in.Close()
	if err := os.MkdirAll(filepath.Dir(to), 0o755); err != nil {
		return err
	}
	mode := fs.FileMode(0o644)
	if info, err := in.Stat(); err == nil {
		mode = info.Mode().Perm()
	}
	out, err := os.OpenFile(to, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, mode)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

// --- discovery --------------------------------------------------------------------------

type packageJSON struct {
	Name            string            `json:"name"`
	Dependencies    map[string]string `json:"dependencies"`
	DevDependencies map[string]string `json:"devDependencies"`
	Scripts         map[string]string `json:"scripts"`
	PackageManager  string            `json:"packageManager"`
}

func (p packageJSON) deps() map[string]string {
	all := make(map[string]string, len(p.Dependencies)+len(p.DevDependencies))
	for name, version := range p.Dependencies {
		all[name] = version
	}
	for name, version := range p.DevDependencies {
		all[name] = version
	}
	return all
}

// discoverServices finds every Next.js application in the tree, shallowest
// first: in a monorepo the one nearest the root is the one being deployed.
func discoverServices(root string) []Service {
	type candidate struct {
		dir  string
		pkg  packageJSON
		deep int
	}
	found := []candidate{}

	_ = filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			if path != root && skipDirs[entry.Name()] {
				return fs.SkipDir
			}
			return nil
		}
		if entry.Name() != "package.json" {
			return nil
		}
		body, err := os.ReadFile(path)
		if err != nil {
			return nil
		}
		var pkg packageJSON
		if json.Unmarshal(body, &pkg) != nil {
			return nil
		}
		if _, ok := pkg.deps()["next"]; !ok {
			return nil
		}
		dir := filepath.Dir(path)
		found = append(found, candidate{dir: dir, pkg: pkg, deep: depth(root, dir)})
		return nil
	})

	sort.SliceStable(found, func(i, j int) bool {
		if found[i].deep != found[j].deep {
			return found[i].deep < found[j].deep
		}
		return found[i].dir < found[j].dir
	})

	services := make([]Service, 0, len(found))
	for _, item := range found {
		services = append(services, serviceSpec(root, item.dir, item.pkg))
	}
	return services
}

func depth(root, dir string) int {
	rel, err := filepath.Rel(root, dir)
	if err != nil || rel == "." {
		return 0
	}
	return len(strings.Split(filepath.ToSlash(rel), "/"))
}

func serviceSpec(root, dir string, pkg packageJSON) Service {
	relative := relPosix(root, dir)
	deps := pkg.deps()
	manager, install, lockfile := packageManager(dir, pkg)

	name := pkg.Name
	if strings.TrimSpace(name) == "" {
		name = filepath.Base(dir)
	}
	version := deps["next"]
	if version == "" {
		version = "unknown"
	}

	build := ""
	if pkg.Scripts["build"] != "" {
		build = manager + " run build"
	}
	// A project with no start script is one the build turned into a server of
	// its own; Next.js standalone output puts it in server.js.
	start := "node server.js"
	if pkg.Scripts["start"] != "" {
		start = manager + " run start"
	}

	_, hasMongo := deps["mongodb"]
	_, hasMongoose := deps["mongoose"]
	_, hasBetterAuth := deps["better-auth"]

	return Service{
		Name:           slug(name, "nextjs-app"),
		Root:           relative,
		Framework:      "nextjs",
		Version:        version,
		PackageManager: manager,
		InstallCommand: install,
		BuildCommand:   build,
		StartCommand:   start,
		Port:           detectPort(pkg.Scripts),
		HealthPath:     DefaultHealthPath,
		Routes:         scanRoutes(dir),
		Environment:    scanEnvironment(dir),
		Dependencies:   sortedKeys(deps),
		Scripts:        copyScripts(pkg.Scripts),
		Lockfile:       lockfile,
		HasMongoDB:     hasMongo || hasMongoose,
		HasBetterAuth:  hasBetterAuth,
	}
}

func relPosix(root, dir string) string {
	rel, err := filepath.Rel(root, dir)
	if err != nil || rel == "." {
		return ""
	}
	return filepath.ToSlash(rel)
}

// packageManager is decided by the lockfile that is actually there, because
// that is the file the install has to honour. Only when there is none does the
// project's own declaration get a say.
func packageManager(dir string, pkg packageJSON) (manager, install, lockfile string) {
	switch {
	case exists(dir, "pnpm-lock.yaml"):
		return "pnpm", "pnpm install --frozen-lockfile", "pnpm-lock.yaml"
	case exists(dir, "yarn.lock"):
		return "yarn", "yarn install --frozen-lockfile", "yarn.lock"
	case exists(dir, "bun.lock"):
		return "bun", "bun install --frozen-lockfile", "bun.lock"
	case exists(dir, "bun.lockb"):
		return "bun", "bun install --frozen-lockfile", "bun.lockb"
	case exists(dir, "package-lock.json"):
		return "npm", "npm ci", "package-lock.json"
	}
	declared, _, _ := strings.Cut(strings.ToLower(strings.TrimSpace(pkg.PackageManager)), "@")
	switch declared {
	case "pnpm", "yarn", "bun":
		return declared, declared + " install --frozen-lockfile", ""
	}
	return "npm", "npm ci", ""
}

func exists(dir, name string) bool {
	info, err := os.Stat(filepath.Join(dir, name))
	return err == nil && !info.IsDir()
}

func missingLockfile(services []Service) bool {
	for _, service := range services {
		if service.Lockfile == "" {
			return true
		}
	}
	return false
}

// ensureLockfiles makes the install reproducible where it can. npm can write a
// lockfile without installing anything; the others cannot, so those become a
// warning the review screen shows rather than something done behind the
// customer's back.
func (in *Intake) ensureLockfiles(ctx context.Context, staged string, services []Service) []string {
	warnings := []string{}
	for _, service := range services {
		if service.Lockfile != "" {
			continue
		}
		root := staged
		if service.Root != "" {
			root = filepath.Join(staged, filepath.FromSlash(service.Root))
		}
		where := service.Root
		if where == "" {
			where = "."
		}
		if service.PackageManager != "npm" || !Have("npm") {
			warnings = append(warnings,
				"A reproducible "+service.PackageManager+" lockfile is missing for "+where+".")
			continue
		}
		in.Emit.step("lockfile", StatusRunning, 12,
			"Generating package-lock.json in the isolated workspace", nil)
		out := Exec(ctx, Command{
			Name:    "npm",
			Args:    []string{"install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"},
			Dir:     root,
			Timeout: lockfileTimeout,
		})
		if !out.OK() || !exists(root, "package-lock.json") {
			warnings = append(warnings,
				"package-lock.json generation failed; the package lock gate will block deployment.")
		}
	}
	return warnings
}

// --- what the code needs ----------------------------------------------------------------

// scanEnvironment collects every variable the source reads, and where it reads
// it. NEXT_PUBLIC_ names are baked into the bundle at build time, so they have
// to be present before the build; everything else is only needed at runtime.
func scanEnvironment(dir string) []EnvVar {
	sources := map[string]map[string]bool{}

	_ = filepath.WalkDir(dir, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			if path != dir && skipDirs[entry.Name()] {
				return fs.SkipDir
			}
			return nil
		}
		if !sourceExtensions[strings.ToLower(filepath.Ext(entry.Name()))] {
			return nil
		}
		body, err := os.ReadFile(path)
		if err != nil {
			return nil
		}
		rel := relPosix(dir, path)
		if rel == "" {
			rel = entry.Name()
		}
		for _, match := range envPattern.FindAllStringSubmatch(string(body), -1) {
			name := match[1]
			if name == "" {
				name = match[2]
			}
			if name == "" {
				continue
			}
			if sources[name] == nil {
				sources[name] = map[string]bool{}
			}
			sources[name][rel] = true
		}
		return nil
	})

	out := make([]EnvVar, 0, len(sources))
	for _, name := range sortedKeys(sources) {
		scope := "runtime"
		if strings.HasPrefix(name, "NEXT_PUBLIC_") {
			scope = "build"
		}
		out = append(out, EnvVar{
			Name:     name,
			Required: true,
			Secret:   IsSecretName(name),
			Scope:    scope,
			Sources:  sortedKeys(sources[name]),
		})
	}
	return out
}

// scanRoutes lists the addresses the App Router serves. Route groups — the
// (marketing) folders — are naming, not addressing, so they come out.
func scanRoutes(dir string) []map[string]string {
	app := filepath.Join(dir, "app")
	if info, err := os.Stat(app); err != nil || !info.IsDir() {
		app = filepath.Join(dir, "src", "app")
	}
	routes := []map[string]string{}
	if info, err := os.Stat(app); err != nil || !info.IsDir() {
		return routes
	}

	_ = filepath.WalkDir(app, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			if path != app && skipDirs[entry.Name()] {
				return fs.SkipDir
			}
			return nil
		}
		if !routeFiles[entry.Name()] {
			return nil
		}
		parts := []string{}
		if rel := relPosix(app, filepath.Dir(path)); rel != "" {
			for _, part := range strings.Split(rel, "/") {
				if !strings.HasPrefix(part, "(") {
					parts = append(parts, part)
				}
			}
		}
		route := "/" + strings.Join(parts, "/")
		if route == "" {
			route = "/"
		}
		kind := "page"
		if strings.HasPrefix(entry.Name(), "route") {
			kind = "api"
		}
		routes = append(routes, map[string]string{"path": route, "type": kind})
		return nil
	})

	sort.SliceStable(routes, func(i, j int) bool { return routes[i]["path"] < routes[j]["path"] })
	return routes
}

// detectPort reads the port out of the project's own scripts. A project that
// pins one is a project that expects it everywhere else too.
func detectPort(scripts map[string]string) int {
	text := strings.Join(sortedValues(scripts), " ")
	if match := portPattern.FindStringSubmatch(text); match != nil {
		if port, err := strconv.Atoi(match[1]); err == nil {
			return port
		}
	}
	return defaultPort
}

// --- the repository ---------------------------------------------------------------------

// readRepository asks git what it knows. A folder that is not a repository, or
// a machine with no git, is not a problem: it changes how the deployment is
// wired, not whether it can happen.
func readRepository(ctx context.Context, source string) Repository {
	if info, err := os.Stat(filepath.Join(source, ".git")); err != nil || !info.IsDir() {
		return Repository{DirtyFiles: []string{}}
	}
	if !Have("git") {
		return Repository{DirtyFiles: []string{}}
	}

	branch := strings.TrimSpace(Exec(ctx, Command{
		Name: "git", Args: []string{"branch", "--show-current"}, Dir: source,
	}).Stdout)
	if branch == "" {
		branch = "main"
	}

	remote := ""
	if out := Exec(ctx, Command{
		Name: "git", Args: []string{"remote", "get-url", "origin"}, Dir: source,
	}); out.OK() {
		remote = strings.TrimSpace(out.Stdout)
	}

	status := Exec(ctx, Command{
		Name: "git", Args: []string{"status", "--porcelain"}, Dir: source, Timeout: gitTimeout,
	})
	dirty := []string{}
	for _, line := range status.Lines() {
		// Porcelain lines are two status characters, a space, then the path.
		if len(line) > 3 {
			line = line[3:]
		}
		dirty = append(dirty, line)
	}

	return Repository{IsGit: true, Branch: branch, Remote: remote, DirtyFiles: dirty}
}

// --- small shared helpers ---------------------------------------------------------------

// slug is a name safe to use for a stack, a bucket and a repository at once.
func slug(value, fallback string) string {
	out := strings.Trim(slugPattern.ReplaceAllString(strings.ToLower(value), "-"), "-")
	if len(out) > 40 {
		out = strings.Trim(out[:40], "-")
	}
	if out == "" {
		if fallback == "" {
			return "nextjs-app"
		}
		return slug(fallback, "")
	}
	return out
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for key := range m {
		out = append(out, key)
	}
	sort.Strings(out)
	return out
}

func sortedValues(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for _, key := range sortedKeys(m) {
		out = append(out, m[key])
	}
	return out
}

func copyScripts(scripts map[string]string) map[string]string {
	out := make(map[string]string, len(scripts))
	for key, value := range scripts {
		out[key] = value
	}
	return out
}
