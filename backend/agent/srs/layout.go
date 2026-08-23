package srs

import (
	"math"
	"regexp"
	"strings"
)

// The eleven layouts. Each one is drawn from the specification alone: a
// diagram whose input is missing says so (notApplicableDrawing) instead of
// filling the space with something plausible.

// Render lays one diagram out at the given width, or returns nil when the kind
// is unknown. A layout that cannot be drawn never costs the document its other
// ten diagrams.
func Render(kind string, doc *Document, width float64) *Drawing {
	switch kind {
	case "use_case":
		return drawUseCase(doc, width)
	case "sequence":
		return drawSequence(doc, width)
	case "erd":
		return drawERD(doc, width)
	case "activity":
		return drawActivity(doc, width)
	case "class_object":
		return drawClass(doc, width)
	case "state_machine":
		return drawStateMachine(doc, width)
	case "dfd":
		return drawDFD(doc, width)
	case "bpmn":
		return drawBPMN(doc, width)
	case "system_context":
		return drawContext(doc, width)
	case "component":
		return drawComponent(doc, width)
	case "deployment":
		return drawDeployment(doc, width)
	}
	return nil
}

// --- use case ---------------------------------------------------------------------------

func drawUseCase(doc *Document, W float64) *Drawing {
	actors, cases := actorsAndUseCases(doc)
	if len(actors) > 6 {
		actors = actors[:6]
	}
	if len(cases) > 14 {
		cases = cases[:14]
	}
	if len(cases) == 0 {
		return notApplicableDrawing(W,
			"No actor goals or system use cases are specified in the approved SRS.")
	}

	const actorX, bx, ucH, rowGap, topPad, bottomPad = 62.0, 142.0, 38.0, 11.0, 42.0, 26.0
	bw := W - bx - 24
	ucW := math.Min(280, bw-52)
	H := math.Max(300, topPad+float64(len(cases))*(ucH+rowGap)+bottomPad)
	d := newDrawing(W, H)

	d.rect(bx, 18, bw, H-36, whiteColor, lineColor, 1.15)
	d.text(bx+14, H-34, fitText(firstNonEmpty(doc.ProjectName, "System"), fontBold, 9.2, bw-28),
		textOpts{Size: 9.2, Font: fontBold, Color: mutedColor})

	type box struct{ X, Y, W, H float64 }
	position := map[string]box{}
	cx := bx + bw*0.57
	for i, uc := range cases {
		cy := H - topPad - float64(i)*(ucH+rowGap) - ucH/2
		d.ellipse(cx, cy, ucW/2, ucH/2, greenBG, greenColor, 1.25)
		d.textBlock(cx, cy, firstNonEmpty(uc.Label, "Use case"),
			blockOpts{MaxWidth: ucW - 26, Size: 8.8, MaxLines: 2})
		position[uc.ID] = box{cx - ucW/2, cy, ucW, ucH}
	}

	// Each actor sits beside the use cases it reaches, so the lines stay short.
	fallbackGap := (H - 110) / math.Max(float64(len(actors)), 1)
	for i, a := range actors {
		var sum float64
		count := 0
		for _, uid := range a.Does {
			if b, ok := position[uid]; ok {
				sum += b.Y
				count++
			}
		}
		ay := H - 70 - float64(i)*fallbackGap
		if count > 0 {
			ay = sum / float64(count)
		}
		ay = math.Max(65, math.Min(H-70, ay))
		d.stickFigure(actorX, ay, firstNonEmpty(a.Label, "Actor"), 0.95)

		// One routing lane per actor keeps the boundary edge orderly.
		routeX := bx - 12 - float64(i)*3
		for _, uid := range a.Does {
			b, ok := position[uid]
			if !ok {
				continue
			}
			d.orthArrow(actorX+17, ay+4, b.X, b.Y,
				orthOpts{ViaX: &routeX, Color: lineDarkColor, Width: 0.9, NoHead: true})
		}
	}
	return d
}

// --- sequence ---------------------------------------------------------------------------

// The verb groups accept the inflections people actually write: "adds" and
// "deleting" name the same intent as "add" and "delete", and matching only the
// base form sent every step to whichever endpoint happened to be listed first.
var createVerbs = regexp.MustCompile(`(?i)\b(create|add|submit|book|place|register|pay|write|post)(s|es|ed|ing)?\b`)
var updateVerbs = regexp.MustCompile(`(?i)\b(update|edit|change|mark|set)(s|es|ed|ing)?\b`)
var deleteVerbs = regexp.MustCompile(`(?i)\b(delete|remove|cancel)(s|es|ed|ing|led|ling)?\b`)
var readVerbs = regexp.MustCompile(`(?i)\b(view|browse|list|filter|track|read|get|search)(s|es|ed|ing)?\b`)

// matchEndpoint is the API call a step is actually making. Matching the verb
// as well as the words stops every step calling the same endpoint.
func matchEndpoint(step string, endpoints []Endpoint) *Endpoint {
	if len(endpoints) == 0 {
		return nil
	}
	stepWords := tokenise(step)
	preferred := ""
	switch {
	case createVerbs.MatchString(step):
		preferred = "POST"
	case updateVerbs.MatchString(step):
		preferred = "PATCH"
	case deleteVerbs.MatchString(step):
		preferred = "DELETE"
	case readVerbs.MatchString(step):
		preferred = "GET"
	}

	best, bestScore := -1, 0
	for i, ep := range endpoints {
		overlap := 0
		for word := range tokenise(ep.Path) {
			if stepWords[word] {
				overlap++
			}
		}
		if overlap <= 0 {
			continue
		}
		if preferred != "" && strings.ToUpper(ep.Method) != preferred {
			continue
		}
		score := overlap * 4
		if preferred != "" {
			score += 2
		}
		if score > bestScore {
			best, bestScore = i, score
		}
	}
	if best < 0 {
		return nil
	}
	return &endpoints[best]
}

func matchingStore(step string, doc *Document) string {
	best, bestScore := "", 0
	for _, t := range doc.DatabaseDesign.Tables {
		if score := overlapScore(step, t.TableName); score > bestScore {
			best, bestScore = t.TableName, score
		}
	}
	return best
}

func drawSequence(doc *Document, W float64) *Drawing {
	var steps []string
	if len(doc.BusinessWorkflows) > 0 {
		steps = cleanList(doc.BusinessWorkflows[0].Steps)
	}
	if len(steps) > 7 {
		steps = steps[:7]
	}
	if len(steps) == 0 {
		steps = cleanList(diagramPlan(doc).Features)
		if len(steps) > 5 {
			steps = steps[:5]
		}
	}
	if len(steps) == 0 {
		return notApplicableDrawing(W,
			"No ordered user/system interaction is specified in the approved SRS.")
	}

	// The role the steps actually name, not merely the first one listed.
	role, bestHits := "User", 0
	for _, r := range doc.Roles {
		name := firstNonEmpty(r.RoleName, r.RoleKey)
		if name == "" {
			continue
		}
		pattern := regexp.MustCompile(`(?i)\b` + regexp.QuoteMeta(name) + `\b`)
		hits := 0
		for _, s := range steps {
			if pattern.MatchString(s) {
				hits++
			}
		}
		if hits > bestHits {
			role, bestHits = name, hits
		}
	}

	endpoints := doc.APIDesign
	hasDB := len(doc.DatabaseDesign.Tables) > 0
	labels := []string{role, "Web Application"}
	apiIndex, dbIndex := -1, -1
	if len(endpoints) > 0 {
		apiIndex = len(labels)
		labels = append(labels, "Application API")
	}
	if hasDB {
		dbIndex = len(labels)
		labels = append(labels, "Database")
	}

	n := len(labels)
	const margin = 36.0
	xs := make([]float64, n)
	span := 1.0
	if n > 1 {
		span = float64(n - 1)
	}
	for i := range xs {
		xs[i] = margin + float64(i)*((W-2*margin)/span)
	}
	band := 66.0
	if apiIndex >= 0 && hasDB {
		band = 88
	}
	H := 94 + float64(len(steps))*band + 42
	d := newDrawing(W, H)
	top := H - 34

	for i, label := range labels {
		bw := math.Min(132, (W-36)/float64(n)-8)
		d.rect(xs[i]-bw/2, top-13, bw, 26, blueBG, lineDarkColor, 1.05)
		d.textBlock(xs[i], top, label, blockOpts{MaxWidth: bw - 12, Size: 8.4, Font: fontBold})
		d.line(xs[i], top-13, xs[i], 24, lineColor, 0.8, true)
	}

	// One narrow activation bar for the application, not one per message.
	webX := xs[1]
	d.rect(webX-4, 34, 8, top-61, greenBG, greenColor, 0.7)

	rolePrefix := regexp.MustCompile(`(?i)^\s*` + regexp.QuoteMeta(role) + `\s+`)
	y := top - 44
	for i, step := range steps {
		message := cleanText(step)
		if role != "User" {
			message = rolePrefix.ReplaceAllString(message, "")
		}
		d.arrow(xs[0], y, webX-5, y, arrowOpts{
			Width: 0.95, Label: itoa(i+1) + ". " + message, LabelSize: 7.2})
		y -= 21

		ep := matchEndpoint(step, endpoints)
		store := matchingStore(step, doc)
		switch {
		case ep != nil && apiIndex >= 0:
			ax := xs[apiIndex]
			d.arrow(webX+5, y, ax-4, y, arrowOpts{
				Width: 0.95, Label: strings.ToUpper(ep.Method) + " " + ep.Path, LabelSize: 6.8})
			y -= 18
			if dbIndex >= 0 && store != "" {
				dx := xs[dbIndex]
				operation := "read/write"
				if strings.EqualFold(ep.Method, "GET") {
					operation = "read"
				}
				d.arrow(ax+4, y, dx, y, arrowOpts{Width: 0.9, Label: operation + " " + store, LabelSize: 6.6})
				y -= 16
				d.arrow(dx, y, ax+4, y, arrowOpts{
					Width: 0.85, Dashed: true, Label: "record/result", Open: true, LabelSize: 6.5})
				y -= 16
			}
			d.arrow(ax-4, y, webX+5, y, arrowOpts{
				Width: 0.85, Dashed: true, Label: "result", Open: true, LabelSize: 6.5})
			y -= 18
		case dbIndex >= 0 && store != "":
			dx := xs[dbIndex]
			d.arrow(webX+5, y, dx, y, arrowOpts{Width: 0.9, Label: "access " + store, LabelSize: 6.7})
			y -= 17
			d.arrow(dx, y, webX+5, y, arrowOpts{
				Width: 0.85, Dashed: true, Label: "record/result", Open: true, LabelSize: 6.5})
			y -= 18
		}
		d.arrow(webX-5, y, xs[0], y, arrowOpts{
			Width: 0.85, Dashed: true, Label: "present outcome", Open: true, LabelSize: 6.6})
		y -= 28
	}
	return d
}

// --- activity ---------------------------------------------------------------------------

func drawActivity(doc *Document, W float64) *Drawing {
	if len(doc.BusinessWorkflows) == 0 {
		return notApplicableDrawing(W, "No ordered business workflow is specified in the approved SRS.")
	}
	wf := doc.BusinessWorkflows[0]
	steps := cleanList(wf.Steps)
	if len(steps) > 9 {
		steps = steps[:9]
	}
	if len(steps) == 0 {
		return notApplicableDrawing(W, "No ordered business workflow is specified in the approved SRS.")
	}

	H := 92 + float64(len(steps))*62 + 44
	d := newDrawing(W, H)
	cx := W / 2
	y := H - 34
	d.text(24, H-24, fitText(firstNonEmpty(wf.WorkflowName, "Primary workflow"), fontBold, 9.4, W-48),
		textOpts{Size: 9.4, Font: fontBold, Color: mutedColor})

	d.startNode(cx, y)
	prevX, prevY := cx, y-7
	for _, step := range steps {
		y -= 62
		if isDecision(step) {
			d.diamond(cx, y, math.Min(170, W*0.36), 42, step, "", "", 7.7)
			d.arrow(prevX, prevY, cx, y+21, arrowOpts{Width: 0.95})
			prevX, prevY = cx, y-21
			continue
		}
		w, h := math.Min(W*0.58, 300), 38.0
		d.rounded(cx-w/2, y-h/2, w, h, step,
			nodeOpts{Stroke: greenColor, Fill: greenBG, Size: 8.4, Radius: 14})
		d.arrow(prevX, prevY, cx, y+h/2, arrowOpts{Width: 0.95})
		prevX, prevY = cx, y-h/2
	}
	y -= 52
	d.endNode(cx, y)
	d.arrow(prevX, prevY, cx, y+8, arrowOpts{Width: 0.95})
	return d
}

// --- class and object -------------------------------------------------------------------

func classBoxHeight(t Table) float64 {
	fields := len(t.Fields)
	if fields > 9 {
		fields = 9
	}
	return 27 + math.Max(34, float64(14*fields)+8)
}

func (d *Drawing) classBox(x, y, w float64, t Table) {
	fields := t.Fields
	if len(fields) > 9 {
		fields = fields[:9]
	}
	h := classBoxHeight(t)
	const head = 27.0
	d.rect(x, y, w, h, whiteColor, lineDarkColor, 1.15)
	d.line(x, y+h-head, x+w, y+h-head, lineDarkColor, 0.95, false)
	d.text(x+w/2, y+h-18,
		fitText(titleCase(strings.ReplaceAll(firstNonEmpty(t.TableName, "Class"), "_", " ")), fontBold, 9.1, w-12),
		textOpts{Size: 9.1, Font: fontBold, Anchor: "middle"})

	fy := y + h - head - 16
	for _, f := range fields {
		visibility := "-"
		if f.PrimaryKey {
			visibility = "+"
		}
		kind := firstNonEmpty(f.Type, "String")
		if kind == "foreign_key" {
			kind = "ObjectId"
		}
		d.text(x+7, fy, fitText(visibility+" "+firstNonEmpty(f.Name, "field")+": "+kind, fontMono, 7, w-14),
			textOpts{Size: 7, Font: fontMono})
		fy -= 14
	}
}

func drawClass(doc *Document, W float64) *Drawing {
	tables := doc.DatabaseDesign.Tables
	if len(tables) > 5 {
		tables = tables[:5]
	}
	if len(tables) == 0 {
		return notApplicableDrawing(W, "No domain/data classes are specified in the SRS.")
	}
	relations := entityRelations(doc, tables)

	cols := 2
	if len(tables) == 1 {
		cols = 1
	}
	const margin, gap = 20.0, 30.0
	cw := (W - 2*margin - gap*float64(cols-1)) / float64(cols)

	heights := map[string]float64{}
	tallest := 0.0
	byName := map[string]Table{}
	for _, t := range tables {
		heights[t.TableName] = classBoxHeight(t)
		byName[t.TableName] = t
		tallest = math.Max(tallest, heights[t.TableName])
	}
	rows := math.Ceil(float64(len(tables)) / float64(cols))
	rowH := tallest + 38
	const objectH = 86.0
	H := 44 + rows*rowH + objectH + 42
	d := newDrawing(W, H)

	type box struct{ X, Y, W, H float64 }
	pos := map[string]box{}
	for i, t := range tables {
		c, r := i%cols, i/cols
		h := heights[t.TableName]
		pos[t.TableName] = box{
			X: margin + float64(c)*(cw+gap),
			Y: H - 38 - float64(r+1)*rowH + (rowH-h)/2, W: cw, H: h,
		}
	}

	// Associations first, so the boxes sit over their own connectors.
	if len(relations) > 8 {
		relations = relations[:8]
	}
	multiplicity := map[string][2]string{
		"one_to_many":  {"1", "0..*"},
		"many_to_one":  {"0..*", "1"},
		"one_to_one":   {"1", "1"},
		"many_to_many": {"0..*", "0..*"},
	}
	for _, rel := range relations {
		a, okA := pos[rel.From]
		b, okB := pos[rel.To]
		if !okA || !okB {
			continue
		}
		var x1, y1, x2, y2 float64
		switch {
		case a.X < b.X:
			x1, y1, x2, y2 = a.X+a.W, a.Y+a.H/2, b.X, b.Y+b.H/2
		case a.X > b.X:
			x1, y1, x2, y2 = a.X, a.Y+a.H/2, b.X+b.W, b.Y+b.H/2
		default:
			x1, y1, x2, y2 = a.X+a.W/2, a.Y, b.X+b.W/2, b.Y+b.H
		}
		d.orthArrow(x1, y1, x2, y2, orthOpts{Color: lineDarkColor, Width: 0.85, NoHead: true})
		if mult, ok := multiplicity[rel.Type]; ok {
			d.text(x1+4, y1+4, mult[0], textOpts{Size: 6.8, Color: mutedColor})
			d.text(x2+4, y2+4, mult[1], textOpts{Size: 6.8, Color: mutedColor})
		}
	}
	for _, t := range tables {
		b := pos[t.TableName]
		d.classBox(b.X, b.Y, b.W, t)
	}

	// One instance, so the reader can see what a row actually looks like.
	d.text(W/2, objectH+24, "Object snapshot (illustrative instance)",
		textOpts{Size: 7.6, Color: mutedColor, Anchor: "middle"})
	t := tables[0]
	attrs := t.Fields
	if len(attrs) > 3 {
		attrs = attrs[:3]
	}
	ow := math.Min(250, W*0.46)
	ox, oy := (W-ow)/2, 20.0
	oh := 36 + 15*math.Max(1, float64(len(attrs)))
	d.rect(ox, oy, ow, oh, greenBG, greenColor, 1.15)
	label := fitText("example : "+titleCase(strings.ReplaceAll(firstNonEmpty(t.TableName, "Class"), "_", " ")),
		fontBold, 8.1, ow-14)
	d.text(ox+ow/2, oy+oh-18, label, textOpts{Size: 8.1, Font: fontBold, Anchor: "middle"})
	tw := stringWidth(label, fontBold, 8.1)
	d.line(ox+ow/2-tw/2, oy+oh-20, ox+ow/2+tw/2, oy+oh-20, inkColor, 0.65, false)
	d.line(ox, oy+oh-27, ox+ow, oy+oh-27, greenColor, 0.8, false)

	fy := oy + oh - 42
	for _, f := range attrs {
		value := firstText(f.Default)
		if value == "" && len(f.Values) > 0 {
			value = firstText(f.Values[0])
		}
		if value == "" {
			value = "<" + firstNonEmpty(f.Type, "value") + ">"
		}
		d.text(ox+8, fy, fitText(firstNonEmpty(f.Name, "field")+" = "+value, fontMono, 7.1, ow-16),
			textOpts{Size: 7.1, Font: fontMono})
		fy -= 15
	}
	return d
}

// --- state machine ------------------------------------------------------------------------

func drawStateMachine(doc *Document, W float64) *Drawing {
	life := firstLifecycle(doc)
	transitions := stateTransitions(doc, life)
	if life == nil || len(transitions) == 0 {
		return notApplicableDrawing(W,
			"The SRS does not define legal state-to-state transitions. A professional "+
				"state machine is omitted rather than inventing a lifecycle.")
	}

	states := life.States
	n := math.Max(float64(len(states)), 1)
	nodeW := math.Min(124, math.Max(88, (W-110)/n*0.75))
	gap := (W - 84) / n
	const H, y = 250.0, 122.0
	d := newDrawing(W, H)
	d.text(22, H-26,
		fitText(life.Entity+" · "+life.Field+" lifecycle", fontBold, 9, W-44),
		textOpts{Size: 9, Font: fontBold, Color: mutedColor})

	centre := map[string]float64{}
	for i, state := range states {
		cx := 42 + gap*(float64(i)+0.5)
		d.rounded(cx-nodeW/2, y-19, nodeW, 38, state,
			nodeOpts{Stroke: greenColor, Fill: greenBG, Size: 8.1, Radius: 16})
		centre[state] = cx
	}

	first := transitions[0].From
	d.startNode(22, y)
	d.arrow(29, y, centre[first]-nodeW/2, y, arrowOpts{Width: 0.95})

	reverseLane := 0.0
	for _, t := range transitions {
		ax, okA := centre[t.From]
		bx, okB := centre[t.To]
		if !okA || !okB {
			continue
		}
		if ax < bx {
			d.arrow(ax+nodeW/2, y, bx-nodeW/2, y, arrowOpts{
				Width: 0.9, Open: true, LabelSize: 6.6,
				Label: fitText(t.Label, fontRegular, 6.8, math.Max(72, bx-ax-nodeW))})
			continue
		}
		// A backward transition is routed above the row so it stays readable.
		reverseLane++
		top := y + 48 + reverseLane*16
		d.line(ax, y+19, ax, top, inkColor, 0.85, false)
		d.line(ax, top, bx, top, inkColor, 0.85, false)
		d.arrow(bx, top, bx, y+19, arrowOpts{
			Width: 0.85, Open: true, LabelSize: 6.4,
			Label: fitText(t.Label, fontRegular, 6.5, 100)})
	}

	outgoing := map[string]bool{}
	for _, t := range transitions {
		outgoing[t.From] = true
	}
	final := ""
	for _, state := range states {
		if !outgoing[state] {
			final = state
		}
	}
	if final != "" {
		ex := math.Min(W-22, centre[final]+nodeW/2+34)
		d.endNode(ex, y)
		d.arrow(centre[final]+nodeW/2, y, ex-8, y, arrowOpts{Width: 0.9})
	}
	return d
}
