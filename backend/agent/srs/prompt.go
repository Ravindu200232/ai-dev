package srs

import (
	"regexp"
	"sort"
	"strings"
)

// The build contract as prose. Everything in it is already in the handoff's
// structured fields; this is the same contract written so a model reads it in
// order, with the reason each rule exists next to the rule. A builder that
// only reads JSON still gets the whole product; a builder that reads this gets
// the reasoning too.

// RenderPrompt writes the contract the builder is handed.
func RenderPrompt(h *Handoff, plan *Plan, doc *Document, auth bool) string {
	if plan == nil {
		plan = &Plan{}
	}
	if doc == nil {
		doc = &Document{}
	}
	var lines []string
	add := func(values ...string) { lines = append(lines, values...) }

	name := clean(firstNonEmpty(h.AppName, plan.AppName))
	kind := clean(firstNonEmpty(h.AppType, "Web application"))
	intent := clean(firstNonEmpty(h.ProductIntent, plan.ProductIntent))

	add("AGENTFORGE BUILD HANDOFF v5 — AUTHORITATIVE PRODUCT CONTRACT", "")
	if name != "" {
		add("APP NAME: "+name, "APP TYPE: "+kind)
	} else {
		add("APP TYPE: " + kind)
	}
	if intent != "" {
		add("PRODUCT INTENT: " + intent)
	}
	add("", "SOURCE OF TRUTH:",
		"- This handoff comes from the customer-approved SRS and approved plan. Do not silently shrink, reinterpret or replace it with a sample CRUD app.",
		"- AgentForge owns the implementation plan and stack. Preserve every product capability even when the SRS mentions an integration or technology the runtime cannot use exactly as named; surface the incompatibility instead of deleting the requirement.",
		"- Keep requirement ids. Every FR-* below must survive into the Architect plan as a machine-readable capability and into at least one task `covers` entry.",
		"",
		"NON-NEGOTIABLE DEFINITION OF DONE:",
		"- Build the COMPLETE application. No 'coming soon', TODO page, decorative form, permanently disabled feature, console-only button or dead link counts as implementation.",
		"- Every user-visible action has observable proof. Browser-visible capabilities are `e2e=true` and are covered by a walkable journey. If the SRS has no journey for one, generate a minimal meaningful E2E journey instead of skipping E2E.",
		"- Every route named below is served by a real App Router page/handler and every navigation target exists. Dynamic detail links are tested with real seeded record ids, not only with the `[id]` file existing.",
		"- Every mutation persists to MongoDB and the next visible state proves it happened. A successful HTTP response with unchanged UI/data is not complete.",
		"- Every non-navigation user operation has an exact success toast after confirmed completion and an exact error toast on failure, implemented through one accessible shared toast host. The toast complements durable visible proof; it never replaces it. Pure navigation, tabs, filters and disclosure toggles do not create toast noise.",
		"- Access control is enforced server-side before protected data is read or written. Hiding a nav item is not authorization.",
		"- Mongo relation/ObjectId fields are not plain browser strings: validate URL/form/session ids and convert to ObjectId before Mongo queries; serialize ObjectIds back to strings at client boundaries.",
		"- Do not change an approved data type, route, role boundary or workflow merely to make a generated test pass. Repair the test when its assumption is wrong; repair the app when behavior/contract evidence is wrong.",
		"")

	if len(h.SourceRequirements) > 0 {
		add("AUTHORITATIVE FUNCTIONAL REQUIREMENTS (" + itoa(len(h.SourceRequirements)) + "):")
		for _, r := range h.SourceRequirements {
			var meta []string
			for _, value := range []string{r.Module, r.Priority} {
				if value != "" {
					meta = append(meta, value)
				}
			}
			tail := ""
			if len(meta) > 0 {
				tail = " [" + strings.Join(meta, " · ") + "]"
			}
			if bits := traceBits(r.Proof); len(bits) > 0 {
				tail += "  -> " + strings.Join(bits, "; ")
			}
			add("- " + r.SourceID + ": " + r.Text + "." + tail)
		}
		add("")
	} else {
		add("AUTHORITATIVE FUNCTIONAL REQUIREMENTS:",
			"- No FR ids were present in the SRS. Treat every meaningful user verb in the approved plan as a source requirement; do not merge away distinct actions.",
			"")
	}

	if len(h.Pages) > 0 {
		add("PAGES AND EXACT ROUTES (" + itoa(len(h.Pages)) + "):")
		for _, p := range h.Pages {
			access := "signed-in"
			switch {
			case p.Public:
				access = "public"
			case len(p.AllowedRoles) > 0:
				access = "roles=" + strings.Join(p.AllowedRoles, ",")
			}
			line := "- " + p.Name + "  " + p.Route + "  [" + access + "]  file=" +
				firstNonEmpty(p.ExpectedFile, pageFile(p.Route))
			if job := clean(p.Purpose); job != "" {
				line += " — " + job
			}
			add(line)
		}
		add("", "ROUTE RULE: a route in this list without its page file is a 404; a page with no incoming journey/nav link is unreachable. Both are blockers.", "")
	}

	add(flowSection(plan, h.Pages, doc)...)
	add(authPromptLines(h.AuthContract, auth)...)
	add(featurePromptLines(h.FeatureContracts)...)
	add(testingPromptLines(h.TestingContract)...)

	if len(h.Models) > 0 {
		add("DATA MODEL — TYPES AND REFERENCES ARE AUTHORITATIVE:")
		for _, m := range h.Models {
			add("- " + m.Name + "  collection/table=" + firstNonEmpty(m.Table, m.Name))
			for _, f := range m.FieldSpecs {
				flags := []string{firstNonEmpty(f.Type, "String")}
				if f.References != "" {
					flags = append(flags, "references "+f.References)
				}
				if f.PrimaryKey {
					flags = append(flags, "primary key")
				}
				if f.Unique {
					flags = append(flags, "unique")
				}
				if f.Required != nil {
					if *f.Required {
						flags = append(flags, "required")
					} else {
						flags = append(flags, "optional")
					}
				}
				if len(f.Enum) > 0 {
					flags = append(flags, "one of "+joinAny(f.Enum))
				}
				if text := firstText(f.Default); text != "" {
					flags = append(flags, "default="+text)
				}
				add("    " + f.Name + ": " + strings.Join(flags, "; "))
			}
		}
		for _, rel := range h.Relationships {
			if rel.From == "" || rel.To == "" {
				continue
			}
			line := "- relationship: " + rel.From + " -> " + rel.To
			if rel.Type != "" {
				line += " (" + rel.Type + ")"
			}
			if rel.Via != "" {
				line += " via " + rel.Via
			}
			add(line)
		}
		add("", "DATA BOUNDARY RULE: any field typed ObjectId or referencing another collection is queried as ObjectId on the server. `findOne({_id: id})` with a URL string is not equivalent to `findOne({_id: new ObjectId(id)})`.", "")
	}

	if len(h.APIs) > 0 {
		add("API CONTRACT — DO NOT INVENT DIFFERENT PATHS:")
		for _, ep := range h.APIs {
			access := ""
			switch {
			case len(ep.AllowedRoles) > 0:
				access = " roles=" + strings.Join(ep.AllowedRoles, ",")
			case ep.AuthRequired != nil && *ep.AuthRequired:
				access = " signed-in"
			case ep.AuthRequired != nil:
				access = " public"
			}
			line := "- " + ep.Method + " " + ep.Path + " [" + ep.ExpectedFile + "]" + access
			if ep.Description != "" {
				line += " — " + ep.Description
			}
			add(line)
		}
		add("")
	}

	if len(h.Traceability) > 0 {
		add("TRACEABILITY — USE THIS TO PLACE CAPABILITIES ON FILES/TASKS:")
		ids := make([]string, 0, len(h.Traceability))
		for id := range h.Traceability {
			ids = append(ids, id)
		}
		sort.Strings(ids)
		for _, id := range ids {
			row := h.Traceability[id]
			bits := traceBits(&row)
			if len(bits) == 0 {
				add("- " + id + ": no target recorded — planner must assign one")
				continue
			}
			add("- " + id + ": " + strings.Join(bits, "; "))
		}
		add("")
	}

	if len(h.ValidationRules) > 0 {
		add("VALIDATION RULES:")
		for _, v := range h.ValidationRules {
			add("- " + firstNonEmpty(v.Field, "input") + ": " + v.Rule)
		}
		add("")
	}
	if len(h.AcceptanceCriteria) > 0 {
		add("ACCEPTANCE PROOF:")
		for _, c := range h.AcceptanceCriteria {
			prefix := ""
			if c.ID != "" {
				prefix = c.ID + ": "
			}
			add("- " + prefix + c.Criterion)
		}
		add("")
	}

	var supplementary []string
	for _, r := range h.Requirements {
		if r.SourceID == "" && r.Kind != "page" {
			supplementary = append(supplementary, r.Text)
		}
	}
	if len(supplementary) > 0 {
		add("SUPPLEMENTARY APPROVED PLAN ACTIONS (do not let these disappear when mapping FR ids):")
		for _, text := range supplementary {
			add("- " + text)
		}
		add("")
	}

	if len(h.IntegrationRequirements) > 0 {
		add("INTEGRATIONS — PRODUCT BEHAVIOR MUST NOT BE SILENTLY DROPPED:")
		for _, item := range h.IntegrationRequirements {
			line := "- " + clean(firstNonEmpty(item.Name, "Integration"))
			if item.Required != nil && *item.Required {
				line += " required"
			}
			if desc := clean(item.Description); desc != "" {
				line += " — " + desc
			}
			add(line)
		}
		add("")
	}
	if len(h.NotificationRules) > 0 {
		add("PRODUCT NOTIFICATIONS (email/SMS/inbox rules; distinct from required transient action toasts):")
		for _, item := range h.NotificationRules {
			add("- " + clean(firstNonEmpty(item.Event, "event")) +
				": recipients=" + orText(strings.Join(item.Recipients, ", "), "as specified") +
				"; channels=" + orText(strings.Join(item.Channels, ", "), "as specified"))
		}
		add("")
	}
	if len(h.ReportingRequirements) > 0 {
		add("REPORTS:")
		for _, item := range h.ReportingRequirements {
			var tail []string
			if len(item.Filters) > 0 {
				tail = append(tail, "filters="+strings.Join(item.Filters, ", "))
			}
			if len(item.Exports) > 0 {
				tail = append(tail, "exports="+strings.Join(item.Exports, ", "))
			}
			line := "- " + clean(firstNonEmpty(item.ReportName, "Report"))
			if len(tail) > 0 {
				line += " — " + strings.Join(tail, "; ")
			}
			add(line)
		}
		add("")
	}
	if len(h.NonFunctionalRequirements) > 0 {
		add("NON-FUNCTIONAL REQUIREMENTS:")
		for _, item := range h.NonFunctionalRequirements {
			req := clean(item.Requirement)
			if req == "" {
				continue
			}
			line := "- "
			if item.ID != "" {
				line += item.ID + ": "
			}
			line += req
			if item.Category != "" {
				line += " [" + item.Category + "]"
			}
			add(line)
		}
		add("")
	}
	if security := cleanList(doc.SecurityRequirements); len(security) > 0 {
		add("SECURITY REQUIREMENTS:")
		for _, item := range security {
			add("- " + item)
		}
		add("")
	}

	look := clean(plan.LookAndFeel)
	if look != "" || len(doc.UIUX) > 0 {
		add("DESIGN CONTRACT:")
		if look != "" {
			add("- " + look)
		}
		if comps, ok := doc.UIUX["required_components"].([]string); ok && len(comps) > 0 {
			add("- Required UI pieces: " + strings.Join(comps, ", "))
		} else if raw, ok := doc.UIUX["required_components"].([]any); ok && len(raw) > 0 {
			add("- Required UI pieces: " + joinAny(raw))
		}
		add("- Design requirements never authorize fake controls. A disabled control is valid only for a real business state that can become enabled; it is not a placeholder for unimplemented work.", "")
	}

	add("FINAL BUILDER CHECK:",
		"Before generation starts, consume `testing_contract`: map every FR/source requirement to a capability and exact files, implement every unit target, and prepare every E2E pre-journey fixture before QA. Then run build + unit + real browser E2E against those journeys. If a required capability, fixture or durable proof is missing, the app is incomplete even when `npm run build` is green.")

	return collapse(lines)
}

// collapse drops repeated blank lines, which the section builders leave behind.
func collapse(lines []string) string {
	var out []string
	for _, line := range lines {
		if line == "" && len(out) > 0 && out[len(out)-1] == "" {
			continue
		}
		out = append(out, line)
	}
	return strings.TrimSpace(strings.Join(out, "\n")) + "\n"
}

func traceBits(row *TraceRow) []string {
	if row == nil {
		return nil
	}
	var bits []string
	if len(row.Pages) > 0 {
		bits = append(bits, "pages="+strings.Join(row.Pages, ","))
	}
	if len(row.Tables) > 0 {
		bits = append(bits, "data="+strings.Join(row.Tables, ","))
	}
	if row.TestCase != "" {
		bits = append(bits, "test="+row.TestCase)
	}
	return bits
}

func joinAny(values []any) string {
	parts := make([]string, 0, len(values))
	for _, v := range values {
		parts = append(parts, firstText(v))
	}
	return strings.Join(parts, ", ")
}

func orText(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

// --- the road map -----------------------------------------------------------------------

// flowSection is where somebody starts and how they get anywhere. It is the
// one section that says what the finished app has to *do*, rather than what it
// has to contain.
func flowSection(plan *Plan, pages []HandoffPage, doc *Document) []string {
	if len(pages) == 0 {
		return nil
	}
	rolesOf := map[string][]string{}
	for _, bucket := range [][]Page{doc.PublicPages, doc.ProtectedPages} {
		for _, page := range bucket {
			name := strings.ToLower(clean(page.PageName))
			allowed := cleanList(page.AllowedRoles)
			for _, p := range pages {
				if strings.ToLower(clean(p.Name)) == name && p.Route != "" {
					rolesOf[p.Route] = allowed
				}
			}
		}
	}

	entry := pages[0]
	lines := []string{
		"USER JOURNEYS — HOW THE PAGES CONNECT:",
		"- Somebody arrives at " + firstNonEmpty(entry.Route, "/") + " (" + entry.Name +
			"). Every other page is reached from there, in at most two clicks.",
	}

	for _, flow := range plan.Workflows {
		title := clean(flow.Name)
		who := clean(flow.Who)
		steps := cleanList(flow.Steps)
		if title == "" || len(steps) == 0 {
			continue
		}
		if who != "" {
			title += " [" + who + "]"
		}
		var chain []string
		at := map[string][]string{}
		for _, step := range steps {
			routes := routesFor(step, pages, who, rolesOf)
			if len(routes) == 0 {
				if len(chain) > 0 {
					last := chain[len(chain)-1]
					at[last] = append(at[last], step)
				}
				continue
			}
			for _, route := range routes {
				if len(chain) == 0 || chain[len(chain)-1] != route {
					chain = append(chain, route)
				}
			}
			// What happens is written under the last page the step reaches.
			last := routes[len(routes)-1]
			at[last] = append(at[last], step)
		}
		if len(chain) >= 2 {
			lines = append(lines, "- "+title+": "+strings.Join(chain, " → "))
			for _, route := range chain {
				what := at[route]
				if len(what) == 0 {
					continue
				}
				var lowered []string
				for _, w := range what {
					lowered = append(lowered, lowerFirst(w))
				}
				lines = append(lines, "    "+route+": "+strings.Join(lowered, "; "))
			}
			continue
		}
		start := firstNonEmpty(entry.Route, "/")
		if len(chain) > 0 {
			start = chain[0]
		}
		lines = append(lines, "- "+title+": starts at "+start+" — "+lowerFirst(steps[0]))
	}

	return append(lines, "",
		"Every journey above must be walkable end to end when you are done — start at "+
			"the first route, do what each step says, arrive at the last. That is the "+
			"test of a finished app, and a journey that stops at a page that does not "+
			"exist yet, or at a button that does nothing, is a half-built app however "+
			"good the pages before it look.",
		"",
		"Build the navigation from that map and nothing else. Every route in it exists "+
			"as a real page; every link points at a route in it. A page that no other "+
			"page links to is unreachable and does not count as built, and a link to a "+
			"route you have not written is a dead link — if a page is not ready yet, do "+
			"not link to it at all. After an action finishes — a form saved, an item "+
			"added — send the person to the next page in the journey above, not back to "+
			"a blank form.",
		"")
}

// labelStop are the words in a page name that carry no signal about which page
// a sentence is talking about.
var labelStop = map[string]bool{
	"page": true, "pages": true, "screen": true, "the": true, "a": true,
	"an": true, "and": true, "of": true, "for": true, "to": true, "in": true,
	"on": true, "my": true, "your": true, "view": true, "all": true,
}

var letters = regexp.MustCompile(`[a-z]+`)

func labelWords(label string) []string {
	var out []string
	for _, w := range letters.FindAllString(strings.ToLower(label), -1) {
		if len(w) >= 3 && !labelStop[w] {
			out = append(out, w)
		}
	}
	return out
}

func stemWord(w string) string {
	if len(w) > 4 && strings.HasSuffix(w, "s") {
		return w[:len(w)-1]
	}
	return w
}

// mentions is `word` appearing in `text`, tolerant of plurals both ways.
func mentions(word, text string) bool {
	stem := stemWord(word)
	for _, token := range letters.FindAllString(text, -1) {
		if token == stem || token == stem+"s" || token == stem+"es" {
			return true
		}
	}
	return false
}

// routesFor is which pages a workflow step is talking about. An exact page
// name in the sentence wins; otherwise a page matches when every word of its
// name is mentioned, or when its distinctive last word is and no other page
// shares it.
func routesFor(step string, pages []HandoffPage, who string, rolesOf map[string][]string) []string {
	text := strings.ToLower(step)
	who = strings.ToLower(strings.TrimSpace(who))

	sees := func(route string) bool {
		allowed := rolesOf[route]
		if who == "" || len(allowed) == 0 {
			return true
		}
		for _, r := range allowed {
			if strings.ToLower(r) == who {
				return true
			}
		}
		return false
	}

	var named []nameHit
	for _, page := range pages {
		label := strings.ToLower(strings.TrimSpace(page.Name))
		if label == "" || page.Route == "" {
			continue
		}
		if i := strings.Index(text, label); i >= 0 {
			named = append(named, nameHit{i, len(label), page.Route})
		}
	}
	if len(named) > 0 {
		sort.SliceStable(named, func(i, j int) bool {
			if named[i].pos != named[j].pos {
				return named[i].pos < named[j].pos
			}
			return named[i].length > named[j].length
		})
		return routesOf(named)
	}

	// How many reachable pages share each word, so a word that identifies one
	// page can stand in for the whole name.
	freq := map[string]int{}
	for _, page := range pages {
		if who != "" && len(rolesOf[page.Route]) > 0 && !sees(page.Route) {
			continue
		}
		seen := map[string]bool{}
		for _, w := range labelWords(page.Name) {
			if stem := stemWord(w); !seen[stem] {
				seen[stem] = true
				freq[stem]++
			}
		}
	}

	type scored struct {
		pos   int
		score float64
		route string
	}
	var found []scored
	for _, page := range pages {
		words := labelWords(page.Name)
		if len(words) == 0 || page.Route == "" {
			continue
		}
		if who != "" && len(rolesOf[page.Route]) > 0 && !sees(page.Route) {
			continue
		}
		var got []string
		for _, w := range words {
			if mentions(w, text) {
				got = append(got, w)
			}
		}
		if len(got) == 0 {
			continue
		}
		full := len(got) == len(words)
		head := stemWord(words[len(words)-1])
		headOnly := false
		for _, g := range got {
			if stemWord(g) == head && freq[head] == 1 {
				headOnly = true
			}
		}
		if !full && !headOnly {
			continue
		}
		score := 0.6
		if full {
			score = 1.0
		}
		if sees(page.Route) {
			score += 0.3
		}
		score += float64(len(page.Name)) * 0.0001

		pos := len(text)
		for _, g := range got {
			if i := strings.Index(text, stemWord(g)); i >= 0 && i < pos {
				pos = i
			}
		}
		found = append(found, scored{pos, score, page.Route})
	}
	if len(found) == 0 {
		return nil
	}
	sort.SliceStable(found, func(i, j int) bool {
		if found[i].pos != found[j].pos {
			return found[i].pos < found[j].pos
		}
		return found[i].score > found[j].score
	})
	out := []string{}
	for _, f := range found {
		if !containsString(out, f.route) {
			out = append(out, f.route)
		}
	}
	return out
}

// nameHit is one page whose exact name appears in the step.
type nameHit struct {
	pos, length int
	route       string
}

func routesOf(hits []nameHit) []string {
	out := []string{}
	for _, h := range hits {
		if !containsString(out, h.route) {
			out = append(out, h.route)
		}
	}
	return out
}
