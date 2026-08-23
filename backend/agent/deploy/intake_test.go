package deploy

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// project writes a small but realistic Next.js app: a route group, an API
// route, a lockfile, an .env nobody may copy, and a build directory.
func project(t *testing.T) string {
	t.Helper()
	// The folder's own name is what the deployment is called, so the fixture
	// has one rather than a temp directory's number.
	root := filepath.Join(t.TempDir(), "Corner Shop")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	write := func(rel, body string) {
		path := filepath.Join(root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	write("package.json", `{
	  "name": "Corner Shop!",
	  "dependencies": {"next": "14.2.3", "mongoose": "8.0.0", "better-auth": "1.0.0"},
	  "devDependencies": {"eslint": "9.0.0"},
	  "scripts": {"dev": "next dev", "build": "next build", "start": "next start --port 4010"}
	}`)
	write("package-lock.json", `{"lockfileVersion": 3}`)
	write("app/page.tsx", `export default function Home() {
	  const url = process.env.MONGODB_URI
	  return <p>{process.env.NEXT_PUBLIC_SITE_NAME}</p>
	}`)
	write("app/(marketing)/pricing/page.tsx", `export default function Pricing() { return null }`)
	write("app/api/orders/route.ts", `export async function GET() {
	  return Response.json({ key: process.env["STRIPE_SECRET_KEY"] })
	}`)
	write(".env.local", "MONGODB_URI=mongodb+srv://user:hunter2@cluster/db\n")
	write("node_modules/left-pad/index.js", `process.env.SHOULD_NOT_BE_SEEN`)
	write(".next/build-manifest.json", `{}`)
	return root
}

func read(t *testing.T, source string) *Spec {
	t.Helper()
	intake := &Intake{}
	spec, err := intake.Read(context.Background(), source, filepath.Join(t.TempDir(), "staged"))
	if err != nil {
		t.Fatal(err)
	}
	return spec
}

func TestIntakeReadsTheProject(t *testing.T) {
	spec := read(t, project(t))

	if spec.Name != "corner-shop" {
		t.Errorf("name = %q", spec.Name)
	}
	if spec.Services[0].Name != "corner-shop" {
		t.Errorf("service name = %q", spec.Services[0].Name)
	}
	if len(spec.Services) != 1 {
		t.Fatalf("services = %d", len(spec.Services))
	}
	service := spec.Services[0]
	if service.Framework != "nextjs" || service.Version != "14.2.3" {
		t.Errorf("framework = %q %q", service.Framework, service.Version)
	}
	if service.Root != "" {
		t.Errorf("a single-app project is rooted at the repository: %q", service.Root)
	}
	if service.PackageManager != "npm" || service.InstallCommand != "npm ci" ||
		service.Lockfile != "package-lock.json" {
		t.Errorf("package manager = %+v", service)
	}
	if service.BuildCommand != "npm run build" || service.StartCommand != "npm run start" {
		t.Errorf("commands = %q %q", service.BuildCommand, service.StartCommand)
	}
	if service.Port != 4010 {
		t.Errorf("the port the project pins is the port it gets: %d", service.Port)
	}
	if !service.HasMongoDB || !service.HasBetterAuth {
		t.Errorf("mongoose and better-auth were missed: %+v", service)
	}
	if len(service.Dependencies) != 4 {
		t.Errorf("dependencies = %v", service.Dependencies)
	}
}

func TestIntakeStagesWithoutSecrets(t *testing.T) {
	source := project(t)
	staged := filepath.Join(t.TempDir(), "staged")
	if _, err := (&Intake{}).Read(context.Background(), source, staged); err != nil {
		t.Fatal(err)
	}

	if _, err := os.Stat(filepath.Join(staged, "package.json")); err != nil {
		t.Fatal("the project was not staged")
	}
	for _, forbidden := range []string{".env.local", "node_modules", ".next"} {
		if _, err := os.Stat(filepath.Join(staged, forbidden)); err == nil {
			t.Errorf("%s reached the staging workspace", forbidden)
		}
	}
	// The customer's own folder is never touched.
	if _, err := os.Stat(filepath.Join(source, ".env.local")); err != nil {
		t.Error("staging removed something from the source project")
	}
}

func TestIntakeFindsEveryVariable(t *testing.T) {
	spec := read(t, project(t))
	byName := map[string]EnvVar{}
	for _, item := range spec.Services[0].Environment {
		byName[item.Name] = item
	}

	if len(byName) != 3 {
		t.Fatalf("environment = %+v", spec.Services[0].Environment)
	}
	if !byName["MONGODB_URI"].Secret || byName["MONGODB_URI"].Scope != "runtime" {
		t.Errorf("MONGODB_URI = %+v", byName["MONGODB_URI"])
	}
	if !byName["STRIPE_SECRET_KEY"].Secret {
		t.Error("a bracketed process.env read was missed, or not treated as a secret")
	}
	if byName["NEXT_PUBLIC_SITE_NAME"].Scope != "build" {
		t.Error("a public variable is needed at build time, not at runtime")
	}
	if byName["NEXT_PUBLIC_SITE_NAME"].Secret {
		t.Error("a public variable is not a secret")
	}
	if _, seen := byName["SHOULD_NOT_BE_SEEN"]; seen {
		t.Error("node_modules was scanned")
	}
	if got := byName["MONGODB_URI"].Sources; len(got) != 1 || got[0] != "app/page.tsx" {
		t.Errorf("sources = %v", got)
	}
}

func TestIntakeReadsRoutes(t *testing.T) {
	spec := read(t, project(t))
	got := map[string]string{}
	for _, route := range spec.Services[0].Routes {
		got[route["path"]] = route["type"]
	}
	want := map[string]string{"/": "page", "/pricing": "page", "/api/orders": "api"}
	for path, kind := range want {
		if got[path] != kind {
			t.Errorf("route %s = %q, want %q (all: %v)", path, got[path], kind, got)
		}
	}
	if len(got) != len(want) {
		t.Errorf("routes = %v", got)
	}
}

func TestIntakePrefersTheShallowestService(t *testing.T) {
	root := project(t)
	inner := filepath.Join(root, "apps", "admin")
	if err := os.MkdirAll(inner, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inner, "package.json"),
		[]byte(`{"name":"admin","dependencies":{"next":"14.0.0"}}`), 0o644); err != nil {
		t.Fatal(err)
	}

	spec := read(t, root)
	if len(spec.Services) != 2 {
		t.Fatalf("services = %d", len(spec.Services))
	}
	if spec.Services[0].Root != "" || spec.Services[1].Root != "apps/admin" {
		t.Errorf("order = %q %q", spec.Services[0].Root, spec.Services[1].Root)
	}
	if !warned(spec.Warnings, "Multiple Next.js services") {
		t.Errorf("warnings = %v", spec.Warnings)
	}
	// The second service has no lockfile of its own and npm may not be here.
	if spec.Services[1].Lockfile == "" && !warned(spec.Warnings, "lock") {
		t.Errorf("a service with no lockfile is a warning: %v", spec.Warnings)
	}
}

func TestIntakeRejectsAProjectWithNoApp(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "package.json"),
		[]byte(`{"name":"api","dependencies":{"express":"4.0.0"}}`), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := (&Intake{}).Read(context.Background(), root, filepath.Join(t.TempDir(), "staged"))
	if err == nil || !strings.Contains(err.Error(), "Next.js") {
		t.Fatalf("err = %v", err)
	}
	if status, ok := err.(statusError); !ok || status.Status != 400 {
		t.Errorf("a project the agent cannot deploy is the caller's problem: %v", err)
	}

	if _, err := (&Intake{}).Read(context.Background(),
		filepath.Join(root, "nowhere"), filepath.Join(t.TempDir(), "staged")); err == nil {
		t.Error("a folder that does not exist is an error")
	}
}

func TestIntakeReportsProgress(t *testing.T) {
	seen := []Event{}
	intake := &Intake{Emit: func(event Event) { seen = append(seen, event) }}
	if _, err := intake.Read(context.Background(), project(t),
		filepath.Join(t.TempDir(), "staged")); err != nil {
		t.Fatal(err)
	}
	if len(seen) < 2 {
		t.Fatalf("events = %+v", seen)
	}
	first, last := seen[0], seen[len(seen)-1]
	if first.Type != EventStep || first.Status != StatusRunning || first.Stage != "intake" {
		t.Errorf("first = %+v", first)
	}
	if last.Status != StatusComplete || last.Percent != 14 {
		t.Errorf("last = %+v", last)
	}
	if last.Data["primary_root"] != "." || last.Data["services"] != 1 {
		t.Errorf("data = %+v", last.Data)
	}
}

func TestSlug(t *testing.T) {
	cases := map[string]string{
		"Corner Shop!":          "corner-shop",
		"  ":                    "nextjs-app",
		"@acme/storefront":      "acme-storefront",
		strings.Repeat("a", 60): strings.Repeat("a", 40),
	}
	for value, want := range cases {
		if got := slug(value, "nextjs-app"); got != want {
			t.Errorf("slug(%q) = %q, want %q", value, got, want)
		}
	}
}

func TestDetectPort(t *testing.T) {
	cases := []struct {
		scripts map[string]string
		want    int
	}{
		{map[string]string{"start": "next start"}, 3000},
		{map[string]string{"start": "next start -p 8080"}, 8080},
		{map[string]string{"dev": "next dev --port 4321"}, 4321},
		{nil, 3000},
	}
	for _, c := range cases {
		if got := detectPort(c.scripts); got != c.want {
			t.Errorf("detectPort(%v) = %d, want %d", c.scripts, got, c.want)
		}
	}
}

func warned(warnings []string, needle string) bool {
	for _, warning := range warnings {
		if strings.Contains(warning, needle) {
			return true
		}
	}
	return false
}
