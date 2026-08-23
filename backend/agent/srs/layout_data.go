package srs

import (
	"math"
	"regexp"
	"sort"
	"strings"
)

// The data-shaped layouts: the entity-relationship diagram and the data-flow
// view. Both work out their own structure from the schema rather than being
// told it, because a relationship the specification states and the drawing
// omits is worse than no drawing.

// entityRelation is one edge between two tables.
type entityRelation struct {
	From        string
	To          string
	Type        string
	Description string
}

var idSuffixField = regexp.MustCompile(`(?i)(_id|Id|ID)$`)

// tableNameIndex maps every spelling of a table name onto the real one.
func tableNameIndex(tables []Table) map[string]string {
	out := map[string]string{}
	for _, t := range tables {
		name := t.TableName
		if name == "" {
			continue
		}
		low := strings.ToLower(name)
		for _, variant := range []string{low, strings.TrimSuffix(low, "s"), strings.ReplaceAll(low, "_", "")} {
			out[variant] = name
		}
	}
	return out
}

// resolveReference finds which table a foreign key points at, from the
// reference if there is one and from the column name if there is not.
func resolveReference(reference, fieldName string, tables []Table) string {
	index := tableNameIndex(tables)
	if text := strings.TrimSpace(reference); text != "" {
		first := strings.ToLower(strings.TrimSpace(regexp.MustCompile(`[./:]`).Split(text, 2)[0]))
		for _, candidate := range []string{first, strings.TrimSuffix(first, "s"), strings.ReplaceAll(first, "_", "")} {
			if name, ok := index[candidate]; ok {
				return name
			}
		}
	}
	stem := strings.Trim(strings.ToLower(idSuffixField.ReplaceAllString(fieldName, "")), "_")
	for _, candidate := range []string{stem, strings.TrimSuffix(stem, "s"), strings.ReplaceAll(stem, "_", "")} {
		if name, ok := index[candidate]; ok {
			return name
		}
	}
	return ""
}

// entityRelations is every edge worth drawing: the ones the specification
// states, then the ones its own foreign keys imply.
func entityRelations(doc *Document, tables []Table) []entityRelation {
	names := map[string]bool{}
	for _, t := range tables {
		names[t.TableName] = true
	}
	var out []entityRelation
	seen := map[string]bool{}

	for _, r := range doc.DatabaseDesign.Relationships {
		a := strings.SplitN(r.From, ".", 2)[0]
		b := strings.SplitN(r.To, ".", 2)[0]
		if !names[a] || !names[b] || a == b {
			continue
		}
		kind := firstNonEmpty(r.Type, "one_to_many")
		key := a + "|" + b + "|" + kind
		if seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, entityRelation{a, b, kind, r.Description})
	}

	for _, child := range tables {
		for _, f := range child.Fields {
			if f.References == "" && !strings.EqualFold(f.Type, "foreign_key") {
				continue
			}
			parent := resolveReference(f.References, f.Name, tables)
			if parent == "" || parent == child.TableName {
				continue
			}
			kind := "many_to_one"
			if f.Unique {
				kind = "one_to_one"
			}
			key := child.TableName + "|" + parent + "|" + kind
			if seen[key] || seen[parent+"|"+child.TableName+"|one_to_many"] {
				continue
			}
			seen[key] = true
			out = append(out, entityRelation{child.TableName, parent, kind, f.Name})
		}
	}
	if len(out) > 24 {
		out = out[:24]
	}
	return out
}

// --- the entity-relationship diagram --------------------------------------------------

func entitySize(t Table, width float64) (float64, float64) {
	fields := len(t.Fields)
	if fields > 10 {
		fields = 10
	}
	return width, 28 + math.Max(2, float64(fields))*14 + 12
}

func (d *Drawing) entityBox(x, y, w, h float64, t Table) {
	fields := t.Fields
	if len(fields) > 10 {
		fields = fields[:10]
	}
	const head = 27.0
	d.rect(x, y, w, h, whiteColor, lineDarkColor, 1.15)
	d.rect(x, y+h-head, w, head, blueBG, lineDarkColor, 1.15)
	d.text(x+w/2, y+h-18,
		fitText(titleCase(strings.ReplaceAll(firstNonEmpty(t.TableName, "Entity"), "_", " ")), fontBold, 9.2, w-14),
		textOpts{Size: 9.2, Font: fontBold, Anchor: "middle"})

	fy := y + h - head - 15
	for _, f := range fields {
		key := ""
		colour := greenColor
		switch {
		case f.PrimaryKey:
			key = "PK"
		case f.References != "" || strings.EqualFold(f.Type, "foreign_key"):
			key, colour = "FK", orangeColor
		}
		if key != "" {
			d.text(x+7, fy, key, textOpts{Size: 6.8, Font: fontBold, Color: colour})
		}
		d.text(x+31, fy, fitText(firstNonEmpty(f.Name, "field"), fontMono, 7.2, w*0.48),
			textOpts{Size: 7.2, Font: fontMono})
		kind := f.Type
		if kind == "foreign_key" {
			kind = "ObjectId / FK"
		}
		if kind != "" {
			d.text(x+w-7, fy, fitText(kind, fontRegular, 6.8, w*0.33),
				textOpts{Size: 6.8, Color: mutedColor, Anchor: "end"})
		}
		fy -= 14
	}
}

// crowEnd draws the Crow's Foot terminator: a bar for one, three splayed lines
// for many, a circle for optional.
func (d *Drawing) crowEnd(x, y, angle float64, one, many, optional bool) {
	ux, uy := math.Cos(angle), math.Sin(angle)
	px, py := -uy, ux
	if optional {
		d.circle(x-7*ux, y-7*uy, 3.2, whiteColor, inkColor, 0.9)
	}
	if one {
		bx, by := x-11*ux, y-11*uy
		d.line(bx-5*px, by-5*py, bx+5*px, by+5*py, inkColor, 0.9, false)
	}
	if many {
		bx, by := x-8*ux, y-8*uy
		d.line(x, y, bx+7*px, by+7*py, inkColor, 0.9, false)
		d.line(x, y, bx-7*px, by-7*py, inkColor, 0.9, false)
		d.line(x, y, bx-8*ux, by-8*uy, inkColor, 0.9, false)
	}
}

// erdColumns groups the tables into layers, so a table sits to the right of
// what it depends on and the connectors mostly run one way.
func erdColumns(tables []Table, relations []entityRelation, W float64) (int, map[int][]string) {
	var names []string
	for _, t := range tables {
		names = append(names, t.TableName)
	}
	parents := map[string]map[string]bool{}
	children := map[string]map[string]bool{}
	link := func(m map[string]map[string]bool, a, b string) {
		if m[a] == nil {
			m[a] = map[string]bool{}
		}
		m[a][b] = true
	}
	for _, r := range relations {
		var parent, child string
		switch r.Type {
		case "many_to_one":
			child, parent = r.From, r.To
		case "one_to_many":
			parent, child = r.From, r.To
		case "one_to_one":
			parent, child = r.To, r.From
		default:
			parent, child = r.From, r.To
		}
		link(parents, child, parent)
		link(children, parent, child)
	}

	depth := map[string]int{}
	for range names {
		changed := false
		for child, ps := range parents {
			best := 0
			for p := range ps {
				if depth[p] > best {
					best = depth[p]
				}
			}
			if next := min(best+1, 3); next > depth[child] {
				depth[child] = next
				changed = true
			}
		}
		if !changed {
			break
		}
	}

	deepest := 0
	for _, v := range depth {
		if v > deepest {
			deepest = v
		}
	}
	limit := 2
	if W >= 640 && len(tables) >= 6 {
		limit = 3
	}
	cols := min(limit, max(1, deepest+1))

	groups := map[int][]string{}
	for _, n := range names {
		groups[min(depth[n], cols-1)] = append(groups[min(depth[n], cols-1)], n)
	}
	degree := map[string]int{}
	for _, n := range names {
		degree[n] = len(parents[n]) + len(children[n])
	}
	for c := range groups {
		items := groups[c]
		sort.SliceStable(items, func(i, j int) bool {
			if degree[items[i]] != degree[items[j]] {
				return degree[items[i]] > degree[items[j]]
			}
			return strings.ToLower(items[i]) < strings.ToLower(items[j])
		})
		groups[c] = items
	}
	return cols, groups
}

func drawERD(doc *Document, W float64) *Drawing {
	var tables []Table
	for _, t := range doc.DatabaseDesign.Tables {
		if t.TableName != "" {
			tables = append(tables, t)
		}
	}
	if len(tables) > 10 {
		tables = tables[:10]
	}
	if len(tables) == 0 {
		return notApplicableDrawing(W, "No persistent entities are specified in the SRS data model.")
	}
	relations := entityRelations(doc, tables)
	cols, groups := erdColumns(tables, relations, W)

	const marginX, gapX, gapY = 18.0, 28.0, 28.0
	cw := math.Max(150, (W-2*marginX-gapX*float64(cols-1))/float64(cols))

	byName := map[string]Table{}
	sizes := map[string][2]float64{}
	for _, t := range tables {
		byName[t.TableName] = t
		w, h := entitySize(t, cw)
		sizes[t.TableName] = [2]float64{w, h}
	}
	colHeight := map[int]float64{}
	tallest := 0.0
	for c := 0; c < cols; c++ {
		total := 0.0
		for _, n := range groups[c] {
			total += sizes[n][1]
		}
		if len(groups[c]) > 1 {
			total += gapY * float64(len(groups[c])-1)
		}
		colHeight[c] = total
		tallest = math.Max(tallest, total)
	}
	H := math.Max(300, tallest+52)
	d := newDrawing(W, H)

	type box struct{ X, Y, W, H float64 }
	pos := map[string]box{}
	for c := 0; c < cols; c++ {
		y := H - 28 - math.Max(0, (H-52-colHeight[c])/2)
		x := marginX + float64(c)*(cw+gapX)
		for _, n := range groups[c] {
			w, h := sizes[n][0], sizes[n][1]
			y -= h
			pos[n] = box{x, y, w, h}
			y -= gapY
		}
	}

	sameColumn := map[int]int{}
	for _, r := range relations {
		a, okA := pos[r.From]
		b, okB := pos[r.To]
		if !okA || !okB || r.From == r.To {
			continue
		}
		acx, acy := a.X+a.W/2, a.Y+a.H/2
		bcx, bcy := b.X+b.W/2, b.Y+b.H/2
		var x1, y1, x2, y2, angle1, angle2 float64

		if math.Abs(acx-bcx) > 8 {
			if acx < bcx {
				x1, y1, x2, y2 = a.X+a.W, acy, b.X, bcy
			} else {
				x1, y1, x2, y2 = a.X, acy, b.X+b.W, bcy
			}
			mid := (x1 + x2) / 2
			d.line(x1, y1, mid, y1, lineDarkColor, 0.85, false)
			d.line(mid, y1, mid, y2, lineDarkColor, 0.85, false)
			d.line(mid, y2, x2, y2, lineDarkColor, 0.85, false)
			if x2 > x1 {
				angle1, angle2 = 0, math.Pi
			} else {
				angle1, angle2 = math.Pi, 0
			}
		} else {
			// Two tables in the same column route around the outside.
			key := int(math.Round(acx))
			idx := sameColumn[key]
			sameColumn[key]++
			outside := math.Max(8, a.X-12-float64(idx)*8)
			if acx < W/2 {
				outside = math.Min(W-8, a.X+a.W+12+float64(idx)*8)
			}
			x1, y1 = a.X, acy
			if outside > acx {
				x1 = a.X + a.W
			}
			x2, y2 = b.X, bcy
			if outside > bcx {
				x2 = b.X + b.W
			}
			d.line(x1, y1, outside, y1, lineDarkColor, 0.85, false)
			d.line(outside, y1, outside, y2, lineDarkColor, 0.85, false)
			d.line(outside, y2, x2, y2, lineDarkColor, 0.85, false)
			angle1, angle2 = math.Pi, 0
			if outside > x1 {
				angle1 = 0
			}
			if outside <= x2 {
				angle2 = math.Pi
			}
		}

		switch r.Type {
		case "one_to_many":
			d.crowEnd(x1, y1, angle1, true, false, false)
			d.crowEnd(x2, y2, angle2, false, true, false)
		case "many_to_one":
			d.crowEnd(x1, y1, angle1, false, true, false)
			d.crowEnd(x2, y2, angle2, true, false, false)
		case "many_to_many":
			d.crowEnd(x1, y1, angle1, false, true, false)
			d.crowEnd(x2, y2, angle2, false, true, false)
		default:
			d.crowEnd(x1, y1, angle1, true, false, false)
			d.crowEnd(x2, y2, angle2, true, false, false)
		}
	}

	for _, t := range tables {
		b := pos[t.TableName]
		d.entityBox(b.X, b.Y, b.W, b.H, t)
	}
	if len(relations) == 0 {
		d.text(W/2, 14,
			"No explicit foreign-key relationships are specified; entities are shown independently.",
			textOpts{Size: 7.2, Color: mutedColor, Anchor: "middle"})
	}
	return d
}

// --- the data-flow diagram ---------------------------------------------------------------

var managementSuffix = regexp.MustCompile(`(?i)management$`)

// processName reads a module as something the system does.
func processName(module string) string {
	m := strings.ReplaceAll(cleanText(module), "_", " ")
	if !managementSuffix.MatchString(m) {
		return m
	}
	base := strings.TrimSpace(managementSuffix.ReplaceAllString(m, ""))
	if base == "" {
		return "Management"
	}
	return "Manage " + base
}

// dfdStore is the Yourdon/DeMarco open-ended store symbol.
func (d *Drawing) dfdStore(x, y, w, h float64, label string) {
	d.line(x, y+h, x+w, y+h, orangeColor, 1.05, false)
	d.line(x, y, x+w, y, orangeColor, 1.05, false)
	d.line(x+30, y, x+30, y+h, orangeColor, 0.85, false)

	id, rest := label, label
	if i := strings.Index(label, ":"); i >= 0 {
		id, rest = label[:i], strings.TrimSpace(label[i+1:])
	}
	d.text(x+15, y+h/2-3, id, textOpts{Size: 7, Font: fontBold, Color: orangeColor, Anchor: "middle"})
	d.textBlock(x+30+(w-30)/2, y+h/2, rest, blockOpts{MaxWidth: w - 40, Size: 7.4, MaxLines: 2})
}

func drawDFD(doc *Document, W float64) *Drawing {
	actors, cases := actorsAndUseCases(doc)
	if len(actors) > 4 {
		actors = actors[:4]
	}
	var modules []string
	for _, m := range cleanList(doc.MainModules) {
		modules = append(modules, processName(m))
	}
	if len(modules) > 5 {
		modules = modules[:5]
	}
	if len(modules) == 0 {
		for _, s := range diagramPlan(doc).Screens {
			if name := cleanText(s.Name); name != "" {
				modules = append(modules, name)
			}
		}
		if len(modules) > 4 {
			modules = modules[:4]
		}
	}
	if len(modules) == 0 {
		return notApplicableDrawing(W,
			"No processes/modules are specified from which to derive a data-flow view.")
	}

	var allStores []string
	for _, t := range doc.DatabaseDesign.Tables {
		if t.TableName != "" {
			allStores = append(allStores, t.TableName)
		}
	}
	if len(allStores) > 10 {
		allStores = allStores[:10]
	}
	// Only the stores this view's processes actually touch.
	var stores []string
	for _, t := range allStores {
		for _, m := range modules {
			if overlapScore(t, m) > 0 {
				stores = append(stores, t)
				break
			}
		}
	}
	if len(stores) == 0 {
		stores = allStores
		if len(stores) > 3 {
			stores = stores[:3]
		}
	}
	if len(stores) > 6 {
		stores = stores[:6]
	}

	procX, storeX, extX := W*0.48, W-158, 18.0
	const rowGap = 82.0
	widest := max(max(len(modules), len(actors)), len(stores))
	H := math.Max(300, 70+float64(widest)*rowGap)
	d := newDrawing(W, H)

	caseLabel := map[string]string{}
	for _, c := range cases {
		caseLabel[c.ID] = c.Label
	}

	type point struct{ X, Y float64 }
	actorPos := map[string]point{}
	procPos := make([]point, len(modules))
	storePos := make([]point, len(stores))

	for i, a := range actors {
		y := H - 66 - float64(i)*rowGap
		actorPos[a.ID] = point{extX, y}
		d.rect(extX, y-18, 105, 36, whiteColor, lineDarkColor, 1.1)
		d.textBlock(extX+52.5, y, a.Label, blockOpts{MaxWidth: 92, Size: 8.1, Font: fontBold})
	}
	for i, m := range modules {
		y := H - 62 - float64(i)*rowGap
		procPos[i] = point{procX, y}
		d.ellipse(procX, y, 72, 31, greenBG, greenColor, 1.2)
		d.textBlock(procX, y, itoa(i+1)+".0 "+processName(m),
			blockOpts{MaxWidth: 122, Size: 7.8, MaxLines: 2})
	}
	for i, t := range stores {
		y := H - 62 - float64(i)*rowGap
		storePos[i] = point{storeX, y}
		d.dfdStore(storeX, y-17, 140, 34,
			"D"+itoa(i+1)+": "+titleCase(strings.ReplaceAll(t, "_", " ")))
	}

	for _, a := range actors {
		var goals []string
		for _, uid := range a.Does {
			goals = append(goals, caseLabel[uid])
		}
		blob := strings.Join(goals, " ")
		best, bestScore := 0, 0
		for i, m := range modules {
			if score := overlapScore(blob, m); score > bestScore {
				best, bestScore = i, score
			}
		}
		from := actorPos[a.ID]
		to := procPos[best]
		flow := "request"
		if len(a.Does) > 0 {
			flow = fitText(caseLabel[a.Does[0]], fontRegular, 6.8, 100)
		}
		routeX := from.X + 105 + 18
		routeBack := routeX + 8
		d.orthArrow(from.X+105, from.Y, to.X-72, to.Y,
			orthOpts{ViaX: &routeX, Width: 0.9, Label: flow, Open: true, LabelSize: 6.4})
		d.orthArrow(to.X-72, to.Y-8, from.X+105, from.Y-10,
			orthOpts{ViaX: &routeBack, Width: 0.8, Label: "information", Open: true, LabelSize: 6.3})
	}

	for i := 0; i < len(modules)-1; i++ {
		d.arrow(procPos[i].X, procPos[i].Y-31, procPos[i+1].X, procPos[i+1].Y+31,
			arrowOpts{Color: lineDarkColor, Width: 0.85, Label: "processed data", Open: true, LabelSize: 6.3})
	}

	for i, t := range stores {
		best, bestScore := min(i, len(modules)-1), 0
		for pi, m := range modules {
			if score := overlapScore(t, m); score > bestScore {
				best, bestScore = pi, score
			}
		}
		p, s := procPos[best], storePos[i]
		d.arrow(p.X+72, p.Y+5, s.X, s.Y+7,
			arrowOpts{Width: 0.85, Label: "write/update", Open: true, LabelSize: 6.3})
		d.arrow(s.X, s.Y-7, p.X+72, p.Y-7,
			arrowOpts{Width: 0.8, Label: "stored records", Open: true, LabelSize: 6.2})
	}
	return d
}
