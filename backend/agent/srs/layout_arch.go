package srs

import (
	"math"
	"regexp"
)

// The process and architecture layouts: the BPMN pool, the system in its
// context, what it is built from, and where it runs. Each is drawn from the
// specification's own roles, screens, tables and integrations.

// --- BPMN ---------------------------------------------------------------------------------

var systemActor = regexp.MustCompile(`(?i)\b(system|application|platform|service|api)\b`)

// stepLane is whose lane a step belongs in: the role it names, the system when
// it names none, or whoever was doing the work a moment ago.
func stepLane(step string, roles []string, previous string) string {
	for _, r := range roles {
		if regexp.MustCompile(`(?i)\b` + regexp.QuoteMeta(r) + `\b`).MatchString(step) {
			return r
		}
	}
	if systemActor.MatchString(step) {
		return "System"
	}
	if previous != "" {
		return previous
	}
	if len(roles) > 0 {
		return roles[0]
	}
	return "Process"
}

func drawBPMN(doc *Document, W float64) *Drawing {
	var steps []string
	var title string
	if len(doc.BusinessWorkflows) > 0 {
		steps = cleanList(doc.BusinessWorkflows[0].Steps)
		title = doc.BusinessWorkflows[0].WorkflowName
	}
	if len(steps) > 9 {
		steps = steps[:9]
	}
	if len(steps) == 0 {
		return notApplicableDrawing(W, "No business process steps are specified in the SRS.")
	}

	var roles []string
	for _, r := range doc.Roles {
		if name := firstNonEmpty(r.RoleName, r.RoleKey); name != "" {
			roles = append(roles, name)
		}
	}
	laneFor := make([]string, len(steps))
	previous := ""
	for i, s := range steps {
		previous = stepLane(s, roles, previous)
		laneFor[i] = previous
	}
	var lanes []string
	for _, l := range laneFor {
		if !containsString(lanes, l) {
			lanes = append(lanes, l)
		}
	}
	if len(lanes) > 4 {
		lanes = lanes[:4]
	}
	if len(lanes) == 0 {
		lanes = []string{"Process"}
	}
	// A step whose lane was trimmed joins the last visible one.
	for i, l := range laneFor {
		if !containsString(lanes, l) {
			laneFor[i] = lanes[len(lanes)-1]
		}
	}
	laneIndex := map[string]int{}
	for i, l := range lanes {
		laneIndex[l] = i
	}

	const laneH, topPad, y0 = 118.0, 34.0, 20.0
	H := math.Max(210, topPad+y0+laneH*float64(len(lanes)))
	d := newDrawing(W, H)

	const x0, labelW = 18.0, 56.0
	pw := W - 36
	d.rect(x0, y0, pw, laneH*float64(len(lanes)), whiteColor, lineDarkColor, 1.15)
	for i, lane := range lanes {
		ly := y0 + float64(i)*laneH
		if i > 0 {
			d.line(x0, ly, x0+pw, ly, lineDarkColor, 0.8, false)
		}
		d.line(x0+labelW, ly, x0+labelW, ly+laneH, lineDarkColor, 0.8, false)
		// Horizontal lane labels read better than rotated ones in a PDF.
		d.textBlock(x0+labelW/2, ly+laneH/2, lane,
			blockOpts{MaxWidth: labelW - 8, Size: 7.4, Font: fontBold, MaxLines: 3})
	}
	d.text(x0+8, H-17, fitText(firstNonEmpty(title, "Business process"), fontBold, 9, pw-16),
		textOpts{Size: 9, Font: fontBold, Color: mutedColor})

	workLeft := x0 + labelW + 24
	workRight := x0 + pw - 28
	gap := (workRight - workLeft) / float64(len(steps)+1)

	startLane := laneIndex[laneFor[0]]
	sy := y0 + float64(startLane)*laneH + laneH/2
	sx := workLeft - 10
	d.circle(sx, sy, 9, whiteColor, greenColor, 1.5)
	prevX, prevY := sx+9, sy

	for i, step := range steps {
		li := laneIndex[laneFor[i]]
		cy := y0 + float64(li)*laneH + laneH/2
		cx := workLeft + float64(i+1)*gap
		var left, right float64
		if isDecision(step) {
			d.diamond(cx, cy, 38, 34, step, orangeColor, whiteColor, 6.5)
			left, right = cx-19, cx+19
		} else {
			tw := math.Min(126, math.Max(82, gap*0.82))
			d.rounded(cx-tw/2, cy-24, tw, 48, step,
				nodeOpts{Stroke: lineDarkColor, Fill: blueBG, Size: 7.8, Radius: 6, MaxLines: 3})
			left, right = cx-tw/2, cx+tw/2
		}
		via := (prevX + left) / 2
		d.orthArrow(prevX, prevY, left, cy, orthOpts{ViaX: &via, Width: 0.9, Open: true})
		prevX, prevY = right, cy
	}

	endX := math.Min(workRight+10, prevX+gap*0.48)
	d.circle(endX, prevY, 10, whiteColor, blackColor, 2.2)
	d.arrow(prevX, prevY, endX-10, prevY, arrowOpts{Width: 0.9, Open: true})
	return d
}

// --- system context -------------------------------------------------------------------

func drawContext(doc *Document, W float64) *Drawing {
	actors, _ := actorsAndUseCases(doc)
	if len(actors) > 4 {
		actors = actors[:4]
	}
	integrations := doc.IntegrationRequirements
	if len(integrations) > 4 {
		integrations = integrations[:4]
	}
	hasDB := len(doc.DatabaseDesign.Tables) > 0
	right := len(integrations)
	if hasDB {
		right++
	}
	H := math.Max(300, 100+float64(max(len(actors), right))*62)
	d := newDrawing(W, H)
	cx, cy := W/2, H/2

	sw := math.Min(220, W*0.36)
	const sh = 86.0
	d.rect(cx-sw/2, cy-sh/2, sw, sh, purpleBG, purpleColor, 1.5)
	d.textBlock(cx, cy+9, firstNonEmpty(doc.ProjectName, "System"),
		blockOpts{MaxWidth: sw - 20, Size: 10.2, Font: fontBold})
	d.text(cx, cy-23,
		fitText(firstNonEmpty(doc.AppType.PrimaryType, "Application"), fontRegular, 7.6, sw-20),
		textOpts{Size: 7.6, Color: mutedColor, Anchor: "middle"})

	for i, a := range actors {
		y := H - 58 - float64(i)*62
		const w = 112.0
		d.rect(20, y-18, w, 36, greenBG, greenColor, 1.0)
		d.textBlock(20+w/2, y, a.Label, blockOpts{MaxWidth: w - 12, Size: 8, Font: fontBold})
		d.arrow(20+w, y, cx-sw/2, cy+(float64(len(actors))/2-float64(i)-0.5)*10,
			arrowOpts{Color: lineDarkColor, Width: 0.9, Label: "uses", Open: true, LabelSize: 6.5})
	}
	for i, integ := range integrations {
		y := H - 58 - float64(i)*62
		const w = 128.0
		x := W - 20 - w
		d.rect(x, y-18, w, 36, orangeBG, orangeColor, 1.0)
		d.textBlock(x+w/2, y, firstNonEmpty(integ.Name, "External system"),
			blockOpts{MaxWidth: w - 12, Size: 7.9, Font: fontBold})
		d.arrow(cx+sw/2, cy+(float64(len(integrations))/2-float64(i)-0.5)*10, x, y,
			arrowOpts{Color: lineDarkColor, Width: 0.9, Label: "integrates with", Open: true, LabelSize: 6.3})
	}
	if hasDB {
		const dw, dy = 150.0, 28.0
		dx := cx - dw/2
		d.rect(dx, dy, dw, 34, blueBG, blueColor, 1.0)
		d.ellipse(cx, dy+34, dw/2, 8, blueBG, blueColor, 1.0)
		d.ellipse(cx, dy, dw/2, 8, blueBG, blueColor, 1.0)
		d.text(cx, dy+14, "Application data",
			textOpts{Size: 8.1, Font: fontBold, Anchor: "middle"})
		d.arrow(cx, cy-sh/2, cx, dy+42,
			arrowOpts{Color: lineDarkColor, Width: 0.9, Label: "reads / writes", Open: true, LabelSize: 6.4})
	}
	return d
}

// --- component ---------------------------------------------------------------------------

// componentBox is the UML component: a box with the two small tabs on its edge.
func (d *Drawing) componentBox(x, y, w, h float64, label string) {
	d.rect(x, y, w, h, blueBG, blueColor, 1.05)
	d.rect(x+8, y+h-16, 12, 7, whiteColor, lineDarkColor, 0.8)
	d.rect(x+8, y+h-27, 12, 7, whiteColor, lineDarkColor, 0.8)
	d.textBlock(x+w/2+6, y+h/2, label,
		blockOpts{MaxWidth: w - 34, Size: 7.7, Font: fontBold, MaxLines: 2})
}

func drawComponent(doc *Document, W float64) *Drawing {
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
	if len(screens) > 6 {
		screens = screens[:6]
	}
	modules := cleanList(doc.MainModules)
	if len(modules) > 7 {
		modules = modules[:7]
	}
	if len(screens) == 0 && len(modules) == 0 {
		return notApplicableDrawing(W,
			"No presentation or application modules are specified in the SRS.")
	}
	if len(screens) == 0 {
		screens = []string{"User interface"}
	}
	if len(modules) == 0 {
		modules = []string{"Application core"}
	}

	var stores, integrations []string
	for _, t := range doc.DatabaseDesign.Tables {
		if t.TableName != "" && len(stores) < 6 {
			stores = append(stores, t.TableName)
		}
	}
	for _, i := range doc.IntegrationRequirements {
		if i.Name != "" && len(integrations) < 3 {
			integrations = append(integrations, i.Name)
		}
	}

	const rowH, H = 110.0, 360.0
	d := newDrawing(W, H)
	bands := []struct {
		Label  string
		Y      float64
		Fill   string
		Stroke string
	}{
		{"Presentation", 250, blueBG, blueColor},
		{"Application / Domain", 140, greenBG, greenColor},
		{"Data & External Interfaces", 30, orangeBG, orangeColor},
	}
	for _, band := range bands {
		d.rect(14, band.Y-4, W-28, rowH-4, band.Fill, lineColor, 0.8)
		d.text(24, band.Y+rowH-25, band.Label,
			textOpts{Size: 8.2, Font: fontBold, Color: mutedColor})
	}

	type placed struct {
		X, Y, W, H float64
		Label      string
	}
	place := func(items []string, y float64, kind string) []placed {
		count := math.Max(1, float64(len(items)))
		const gap = 12.0
		avail := W - 56
		bw := math.Min(142, (avail-gap*(count-1))/count)
		total := bw*count + gap*(count-1)
		start := (W - total) / 2
		out := make([]placed, 0, len(items))
		for i, item := range items {
			x := start + float64(i)*(bw+gap)
			const h = 44.0
			if kind == "component" {
				d.componentBox(x, y, bw, h, item)
			} else {
				stroke := lineDarkColor
				if kind == "external" {
					stroke = orangeColor
				}
				d.rect(x, y, bw, h, whiteColor, stroke, 1.0)
				d.textBlock(x+bw/2, y+h/2, item,
					blockOpts{MaxWidth: bw - 12, Size: 7.5, Font: fontBold, MaxLines: 2})
			}
			out = append(out, placed{x, y, bw, h, item})
		}
		return out
	}

	screenPos := place(limit(screens, 5), 276, "component")
	modulePos := place(limit(modules, 5), 166, "component")
	bottom := append(append([]string{}, limit(stores, 4)...), limit(integrations, 2)...)
	var bottomPos []placed
	if len(bottom) > 0 {
		bottomPos = place(limit(bottom, 6), 56, "external")
	}

	for i, s := range screenPos {
		best, bestScore := i%len(modulePos), 0
		for j, m := range modulePos {
			if score := overlapScore(s.Label, m.Label); score > bestScore {
				best, bestScore = j, score
			}
		}
		m := modulePos[best]
		d.arrow(s.X+s.W/2, s.Y, m.X+m.W/2, m.Y+m.H,
			arrowOpts{Color: lineDarkColor, Width: 0.8, Open: true})
	}
	for _, b := range bottomPos {
		best, bestScore := 0, 0
		for j, m := range modulePos {
			if score := overlapScore(b.Label, m.Label); score > bestScore {
				best, bestScore = j, score
			}
		}
		if bestScore == 0 {
			continue
		}
		m := modulePos[best]
		d.arrow(m.X+m.W/2, m.Y, b.X+b.W/2, b.Y+b.H,
			arrowOpts{Color: lineDarkColor, Width: 0.8, Open: true})
	}
	return d
}

func limit(values []string, n int) []string {
	if len(values) > n {
		return values[:n]
	}
	return values
}

// --- deployment ----------------------------------------------------------------------------

// deploymentNode is the UML node: a box with a shallow offset behind it.
func (d *Drawing) deploymentNode(x, y, w, h float64, title, stereotype string, items []string) {
	const offset = 7.0
	d.rect(x+offset, y+offset, w, h, shadowColor, lineColor, 0.7)
	d.rect(x, y, w, h, panelColor, lineDarkColor, 1.05)
	d.text(x+10, y+h-17, "«"+stereotype+"»", textOpts{Size: 6.8, Color: mutedColor})
	d.text(x+10, y+h-32, fitText(title, fontBold, 8.4, w-20),
		textOpts{Size: 8.4, Font: fontBold})
	fy := y + h - 50
	for _, item := range limit(items, 3) {
		d.text(x+16, fy, fitText("• "+item, fontRegular, 7, w-24), textOpts{Size: 7})
		fy -= 13
	}
}

func drawDeployment(doc *Document, W float64) *Drawing {
	stack := doc.AppType.ExampleStack
	frontend := firstText(stack["frontend"], "Web client")
	backend := firstText(stack["backend"], "Application/API")
	database := firstText(stack["database"], "Database")
	var integrations []string
	for _, i := range doc.IntegrationRequirements {
		if i.Name != "" && len(integrations) < 3 {
			integrations = append(integrations, i.Name)
		}
	}

	const H = 300.0
	d := newDrawing(W, H)
	const margin, gap, y, h = 24.0, 28.0, 108.0, 112.0
	w := (W - 2*margin - 2*gap) / 3
	d.deploymentNode(margin, y, w, h, "Client Device", "device", []string{"Browser / mobile web"})
	d.deploymentNode(margin+w+gap, y, w, h, "Application Host", "executionEnvironment",
		[]string{frontend, backend})
	d.deploymentNode(margin+2*(w+gap), y, w, h, "Database Host", "executionEnvironment",
		[]string{database})

	d.arrow(margin+w, y+h/2, margin+w+gap, y+h/2, arrowOpts{
		Color: lineDarkColor, Width: 0.9, Label: "HTTPS", Open: true, LabelSize: 6.7})
	d.arrow(margin+2*w+gap, y+h/2, margin+2*(w+gap), y+h/2, arrowOpts{
		Color: lineDarkColor, Width: 0.9, Label: "database connection", Open: true, LabelSize: 6.4})

	if len(integrations) == 0 {
		return d
	}
	count := float64(len(integrations))
	bw := math.Min(150, (W-48-(count-1)*16)/count)
	total := count*bw + (count-1)*16
	start := (W - total) / 2
	hostCX := margin + w + gap + w/2
	for i, name := range integrations {
		x := start + float64(i)*(bw+16)
		const by = 28.0
		d.rect(x, by, bw, 44, orangeBG, orangeColor, 1.0)
		d.textBlock(x+bw/2, by+22, name,
			blockOpts{MaxWidth: bw - 12, Size: 7.6, Font: fontBold, MaxLines: 2})
		d.arrow(hostCX, y, x+bw/2, by+44, arrowOpts{
			Color: lineDarkColor, Width: 0.8, Label: "API / integration", Open: true, LabelSize: 6.2})
	}
	return d
}
