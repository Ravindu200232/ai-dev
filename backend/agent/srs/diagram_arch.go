package srs

import (
	"regexp"
	"strings"
)

// The four architecture views: what the system talks to, what it is made of,
// where it runs, and how data moves through it. They are drawn from the
// specification's own integrations, pages and tables, so a system with no
// external dependency gets a context diagram that says so by having none.

// --- architecture --------------------------------------------------------------------

func systemContextSource(doc *Document) string {
	name := san(firstNonEmpty(doc.ProjectName, "System"), 32)
	kind := san(firstNonEmpty(doc.AppType.PrimaryType, "Web application"), 28)
	integrations := doc.IntegrationRequirements
	if len(integrations) > 6 {
		integrations = integrations[:6]
	}

	lines := []string{"flowchart TB",
		"  classDef sys fill:#EEF2FF,stroke:#6366F1,color:#3730A3,stroke-width:2px;",
		"  classDef actor fill:#F0FDF4,stroke:#16A34A,color:#166534;",
		"  classDef data fill:#F1F5F9,stroke:#64748B,color:#0F172A;"}
	if len(integrations) > 0 {
		lines = append(lines, "  classDef ext fill:#FFF7ED,stroke:#EA580C,color:#9A3412;")
	}
	lines = append(lines, `  SYS["`+name+`<br/>`+kind+`"]:::sys`)

	actors, _ := actorsAndUseCases(doc)
	for _, a := range actors {
		lines = append(lines, `  `+a.ID+`["`+a.Label+`"]:::actor`, "  "+a.ID+" -- uses --> SYS")
	}
	for i, integ := range integrations {
		lines = append(lines, `  X`+itoa(i)+`["`+san(firstNonEmpty(integ.Name, "External service"), 28)+`"]:::ext`,
			"  SYS -- integrates with --> X"+itoa(i))
	}
	if len(doc.DatabaseDesign.Tables) > 0 {
		lines = append(lines, `  DB[("Application data")]:::data`, "  SYS -- reads and writes --> DB")
	}
	return strings.Join(lines, "\n")
}

func componentSource(doc *Document) string {
	plan := diagramPlan(doc)
	var screens []string
	for _, s := range plan.Screens {
		if s.Name != "" {
			screens = append(screens, s.Name)
		}
	}
	if len(screens) == 0 {
		for _, p := range append(append([]Page{}, doc.PublicPages...), doc.ProtectedPages...) {
			if p.PageName != "" {
				screens = append(screens, p.PageName)
			}
		}
	}
	if len(screens) > 8 {
		screens = screens[:8]
	}
	if len(screens) == 0 {
		screens = []string{"Main screen"}
	}

	var tables []string
	for _, t := range doc.DatabaseDesign.Tables {
		tables = append(tables, t.TableName)
	}
	if len(tables) > 8 {
		tables = tables[:8]
	}
	auth := doc.Auth.LoginRequired

	lines := []string{"flowchart TB",
		"  classDef ui fill:#EEF2FF,stroke:#6366F1,color:#3730A3;",
		"  classDef svc fill:#F8FAFC,stroke:#94A3B8,color:#0F172A;",
		"  classDef data fill:#F1F5F9,stroke:#64748B,color:#0F172A;",
		`  subgraph CLIENT["Screens"]`}
	for i, s := range screens {
		lines = append(lines, `    P`+itoa(i)+`["`+san(s, 32)+`"]:::ui`)
	}
	lines = append(lines, "  end", `  subgraph APP["Application"]`, `    GW["Request handling"]:::svc`)
	if auth {
		lines = append(lines, `    AUTH["Sign-in and permissions"]:::svc`)
	}
	lines = append(lines, "  end")

	if len(tables) > 0 {
		lines = append(lines, `  subgraph DATA["Stored data"]`)
		for i, t := range tables {
			lines = append(lines, `    D`+itoa(i)+`[("`+san(strings.ReplaceAll(t, "_", " "), 28)+`")]:::data`)
		}
		lines = append(lines, "  end")
	}
	for i := range screens {
		lines = append(lines, "  P"+itoa(i)+" --> GW")
	}
	if auth {
		lines = append(lines, "  GW --> AUTH")
	}
	for i := range tables {
		lines = append(lines, "  GW --> D"+itoa(i))
	}
	if len(doc.NotificationRules) > 0 {
		lines = append(lines, `  NOTIF["Notifications"]:::svc`, "  GW --> NOTIF")
	}
	integrations := doc.IntegrationRequirements
	if len(integrations) > 4 {
		integrations = integrations[:4]
	}
	for i, integ := range integrations {
		lines = append(lines, `  X`+itoa(i)+`["`+san(firstNonEmpty(integ.Name, "External service"), 28)+`"]:::svc`,
			"  GW --> X"+itoa(i))
	}
	return strings.Join(lines, "\n")
}

func deploymentSource(doc *Document) string {
	stack := doc.AppType.ExampleStack
	frontend := san(firstText(stack["frontend"], "Web application"), 26)
	backend := san(firstText(stack["backend"], "API"), 26)
	database := san(firstText(stack["database"], "Database"), 26)
	auth := doc.Auth.LoginRequired

	uploads := false
	for _, f := range diagramPlan(doc).Features {
		low := strings.ToLower(f)
		if strings.Contains(low, "upload") || strings.Contains(low, "image") {
			uploads = true
		}
	}
	integrations := doc.IntegrationRequirements
	if len(integrations) > 3 {
		integrations = integrations[:3]
	}

	lines := []string{"flowchart LR",
		"  classDef node fill:#F8FAFC,stroke:#94A3B8,color:#0F172A;",
		`  subgraph EDGE["User device"]`, `    Browser["Browser"]:::node`, "  end",
		`  subgraph HOST["Hosting"]`, `    Web["` + frontend + `"]:::node`,
		`    Api["` + backend + `"]:::node`, "  end",
		`  subgraph STORE["Data"]`, `    DB[("` + database + `")]:::node`}
	if uploads {
		lines = append(lines, `    OBJ[("Uploaded files")]:::node`)
	}
	lines = append(lines, "  end", "  Browser --> Web", "  Web --> Api", "  Api --> DB")
	if uploads {
		lines = append(lines, "  Api --> OBJ")
	}
	if auth {
		lines = append(lines, `  Api --> SESS["Session store"]:::node`)
	}
	for i, integ := range integrations {
		lines = append(lines, `  Api --> X`+itoa(i)+`["`+san(firstNonEmpty(integ.Name, "External service"), 26)+`"]:::node`)
	}
	return strings.Join(lines, "\n")
}

func dfdSource(doc *Document) string {
	actors, _ := actorsAndUseCases(doc)
	if len(actors) > 4 {
		actors = actors[:4]
	}
	modules := cleanList(doc.MainModules)
	if len(modules) > 5 {
		modules = modules[:5]
	}
	if len(modules) == 0 {
		modules = []string{"Core processing"}
	}
	var stores []string
	for _, t := range doc.DatabaseDesign.Tables {
		if t.TableName != "" {
			stores = append(stores, t.TableName)
		}
	}
	if len(stores) > 5 {
		stores = stores[:5]
	}

	lines := []string{"flowchart LR",
		"  classDef ext fill:#fff,stroke:#334155,color:#0f172a;",
		"  classDef proc fill:#f0fdf4,stroke:#2f855a,color:#0f172a;",
		"  classDef store fill:#fff7ed,stroke:#c2410c,color:#0f172a;"}
	for i, a := range actors {
		lines = append(lines, `  E`+itoa(i)+`["`+san(a.Label, 28)+`"]:::ext`)
	}
	for i, m := range modules {
		lines = append(lines, `  P`+itoa(i)+`(["`+itoa(i+1)+`.0 `+san(m, 30)+`"]):::proc`)
	}
	for i, t := range stores {
		lines = append(lines, `  D`+itoa(i)+`["D`+itoa(i+1)+`: `+
			san(titleCase(strings.ReplaceAll(t, "_", " ")), 28)+`"]:::store`)
	}
	if len(actors) == 0 {
		return strings.Join(lines, "\n")
	}
	for i := range actors {
		lines = append(lines, "  E"+itoa(i)+" -->|request data| P0")
	}
	for i := 0; i < len(modules)-1; i++ {
		lines = append(lines, "  P"+itoa(i)+" -->|processed data| P"+itoa(i+1))
	}
	for i := range stores {
		lines = append(lines, "  P"+itoa(i%len(modules))+" -->|records| D"+itoa(i),
			"  D"+itoa(i)+" -->|stored data| P"+itoa(i%len(modules)))
	}
	for i := range actors {
		lines = append(lines, "  P"+itoa(len(modules)-1)+" -->|result| E"+itoa(i))
	}
	return strings.Join(lines, "\n")
}

var gatewayWord = regexp.MustCompile(`(?i)\b(if|whether|approve|reject|valid|complete)\b`)

func bpmnSource(doc *Document) string {
	var steps []string
	if len(doc.BusinessWorkflows) > 0 {
		steps = cleanList(doc.BusinessWorkflows[0].Steps)
	}
	if len(steps) > 8 {
		steps = steps[:8]
	}
	if len(steps) == 0 {
		steps = []string{"Receive request", "Process request", "Return result"}
	}
	lines := []string{"flowchart TD", "  START((Start))"}
	prev := "START"
	for i, step := range steps {
		id := "T" + itoa(i)
		if gatewayWord.MatchString(step) {
			lines = append(lines, `  `+id+`{"`+san(step, 40)+`"}`)
		} else {
			lines = append(lines, `  `+id+`(["`+san(step, 40)+`"])`)
		}
		lines = append(lines, "  "+prev+" --> "+id)
		prev = id
	}
	return strings.Join(append(lines, "  END(((End)))", "  "+prev+" --> END"), "\n")
}
