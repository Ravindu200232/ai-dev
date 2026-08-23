package srs

import (
	"math"
	"strings"
	"sync"

	"github.com/go-pdf/fpdf"
)

// The native diagram renderer. Every diagram is laid out as a list of plain
// shapes, which render.go writes as SVG for the Studio and document.go draws
// into the PDF. One layout, two outputs, so the two can never disagree.
//
// Coordinates are bottom-left origin with y increasing upward — the
// convention the layout arithmetic was written in, and the one the PDF uses.
// The SVG writer is the only place that flips.

// The visual system: quiet and document-like. A specification is read, not
// admired, so nothing here is brighter than the text it sits beside.
const (
	inkColor      = "#1F2933"
	mutedColor    = "#52606D"
	lineColor     = "#B8C2CC"
	lineDarkColor = "#405261"
	panelColor    = "#F7F9F8"
	panel2Color   = "#F2F5F7"
	greenColor    = "#2F7D5B"
	greenBG       = "#F4F9F6"
	orangeColor   = "#B96821"
	orangeBG      = "#FFF8F1"
	blueColor     = "#496A84"
	blueBG        = "#F4F7F9"
	purpleColor   = "#6857A5"
	purpleBG      = "#F7F5FB"
	whiteColor    = "#FFFFFF"
	blackColor    = "#202A33"
	shadowColor   = "#E8EDF1"
)

// The three base-14 fonts the diagrams use.
const (
	fontRegular = "Helvetica"
	fontBold    = "Helvetica-Bold"
	fontMono    = "Courier"
)

// Shape is one drawable thing. Its zero value draws nothing.
type Shape struct {
	Kind   string // rect, ellipse, circle, line, polygon, text
	X, Y   float64
	W, H   float64
	Radius float64 // rounded rectangle corner, or circle radius
	RX, RY float64 // ellipse radii
	X2, Y2 float64 // line end
	Points []float64

	Text   string
	Font   string
	Size   float64
	Anchor string // start, middle, end

	Fill   string
	Stroke string
	Width  float64
	Dash   bool
}

// Drawing is one diagram: a size and the shapes inside it.
type Drawing struct {
	Width, Height float64
	Shapes        []Shape
}

func (d *Drawing) add(s Shape) { d.Shapes = append(d.Shapes, s) }

// newDrawing starts a diagram on a white page.
func newDrawing(width, height float64) *Drawing {
	d := &Drawing{Width: width, Height: height}
	d.add(Shape{Kind: "rect", W: width, H: height, Fill: whiteColor})
	return d
}

// --- text metrics ---------------------------------------------------------------------
//
// The base-14 font widths come from the PDF library, so a label that fits in
// the SVG fits identically in the PDF. Measuring with anything else would let
// the two drift apart.

var (
	metricsOnce sync.Once
	metricsMu   sync.Mutex
	metricsPDF  *fpdf.Fpdf
)

func stringWidth(text, font string, size float64) float64 {
	metricsMu.Lock()
	defer metricsMu.Unlock()
	metricsOnce.Do(func() {
		metricsPDF = fpdf.New("P", "pt", "A4", "")
		metricsPDF.AddPage()
	})
	family, style := fontRegular, ""
	switch font {
	case fontBold:
		style = "B"
	case fontMono:
		family = "Courier"
	}
	metricsPDF.SetFont(family, style, size)
	return metricsPDF.GetStringWidth(text)
}

func cleanText(text string) string {
	return strings.Join(strings.Fields(strings.ReplaceAll(text, "\n", " ")), " ")
}

// fitText shortens a label until it fits, ending in an ellipsis so a reader
// can tell something was cut rather than mis-typed.
func fitText(text, font string, size, maxWidth float64) string {
	text = cleanText(text)
	if stringWidth(text, font, size) <= maxWidth {
		return text
	}
	runes := []rune(text)
	for len(runes) > 0 && stringWidth(string(runes)+"…", font, size) > maxWidth {
		runes = runes[:len(runes)-1]
	}
	return strings.TrimRight(string(runes), " ") + "…"
}

// wrapText breaks a label over at most maxLines, shortening the last one.
func wrapText(text, font string, size, maxWidth float64, maxLines int) []string {
	words := strings.Fields(cleanText(text))
	if len(words) == 0 {
		return []string{""}
	}
	var lines []string
	current := ""
	used := 0
	for i, word := range words {
		trial := strings.TrimSpace(current + " " + word)
		if current == "" || stringWidth(trial, font, size) <= maxWidth {
			current = trial
			continue
		}
		lines = append(lines, current)
		current, used = word, i
		if len(lines) == maxLines-1 {
			break
		}
	}
	if len(lines) < maxLines && current != "" {
		tail := current
		if len(lines) == maxLines-1 && used < len(words) {
			tail = strings.Join(words[used:], " ")
		}
		lines = append(lines, fitText(tail, font, size, maxWidth))
	}
	if len(lines) > maxLines {
		lines = lines[:maxLines]
	}
	return lines
}

// --- primitives -----------------------------------------------------------------------

type textOpts struct {
	Size   float64
	Font   string
	Color  string
	Anchor string
}

func (d *Drawing) text(x, y float64, body string, o textOpts) {
	if o.Size == 0 {
		o.Size = 8.5
	}
	if o.Font == "" {
		o.Font = fontRegular
	}
	if o.Color == "" {
		o.Color = inkColor
	}
	if o.Anchor == "" {
		o.Anchor = "start"
	}
	d.add(Shape{Kind: "text", X: x, Y: y, Text: body,
		Font: o.Font, Size: o.Size, Anchor: o.Anchor, Fill: o.Color})
}

type blockOpts struct {
	MaxWidth float64
	Size     float64
	Font     string
	Color    string
	MaxLines int
	Leading  float64
}

// textBlock centres a wrapped label on a point.
func (d *Drawing) textBlock(cx, cy float64, body string, o blockOpts) {
	if o.Size == 0 {
		o.Size = 8.5
	}
	if o.Font == "" {
		o.Font = fontRegular
	}
	if o.MaxLines == 0 {
		o.MaxLines = 2
	}
	lines := wrapText(body, o.Font, o.Size, o.MaxWidth, o.MaxLines)
	leading := o.Leading
	if leading == 0 {
		leading = o.Size + 2
	}
	start := cy + float64(len(lines)-1)*leading/2 - o.Size*0.36
	for i, line := range lines {
		d.text(cx, start-float64(i)*leading, line,
			textOpts{Size: o.Size, Font: o.Font, Color: o.Color, Anchor: "middle"})
	}
}

func (d *Drawing) line(x1, y1, x2, y2 float64, color string, width float64, dashed bool) {
	d.add(Shape{Kind: "line", X: x1, Y: y1, X2: x2, Y2: y2,
		Stroke: color, Width: width, Dash: dashed})
}

func (d *Drawing) rect(x, y, w, h float64, fill, stroke string, width float64) {
	d.add(Shape{Kind: "rect", X: x, Y: y, W: w, H: h, Fill: fill, Stroke: stroke, Width: width})
}

func (d *Drawing) roundRect(x, y, w, h, radius float64, fill, stroke string, width float64) {
	d.add(Shape{Kind: "rect", X: x, Y: y, W: w, H: h, Radius: radius,
		Fill: fill, Stroke: stroke, Width: width})
}

func (d *Drawing) ellipse(cx, cy, rx, ry float64, fill, stroke string, width float64) {
	d.add(Shape{Kind: "ellipse", X: cx, Y: cy, RX: rx, RY: ry,
		Fill: fill, Stroke: stroke, Width: width})
}

func (d *Drawing) circle(cx, cy, r float64, fill, stroke string, width float64) {
	d.add(Shape{Kind: "circle", X: cx, Y: cy, Radius: r, Fill: fill, Stroke: stroke, Width: width})
}

func (d *Drawing) polygon(points []float64, fill, stroke string, width float64) {
	d.add(Shape{Kind: "polygon", Points: points, Fill: fill, Stroke: stroke, Width: width})
}

// arrowHead draws the head only, pointing from (x1,y1) toward (x2,y2).
func (d *Drawing) arrowHead(x1, y1, x2, y2 float64, color string, width float64, open bool, size float64) {
	if size == 0 {
		size = 6.5
	}
	angle := math.Atan2(y2-y1, x2-x1)
	p1x, p1y := x2-size*math.Cos(angle-0.48), y2-size*math.Sin(angle-0.48)
	p2x, p2y := x2-size*math.Cos(angle+0.48), y2-size*math.Sin(angle+0.48)
	if open {
		d.line(x2, y2, p1x, p1y, color, width, false)
		d.line(x2, y2, p2x, p2y, color, width, false)
		return
	}
	d.polygon([]float64{x2, y2, p1x, p1y, p2x, p2y}, color, color, width)
}

type arrowOpts struct {
	Color     string
	Width     float64
	Dashed    bool
	Label     string
	Open      bool
	LabelSize float64
}

func (d *Drawing) arrow(x1, y1, x2, y2 float64, o arrowOpts) {
	if o.Color == "" {
		o.Color = inkColor
	}
	if o.Width == 0 {
		o.Width = 1.0
	}
	if o.LabelSize == 0 {
		o.LabelSize = 7.2
	}
	d.line(x1, y1, x2, y2, o.Color, o.Width, o.Dashed)
	d.arrowHead(x1, y1, x2, y2, o.Color, o.Width, o.Open, 6.5)
	if o.Label == "" {
		return
	}
	cap := math.Max(62, math.Hypot(x2-x1, y2-y1)-18)
	d.text((x1+x2)/2, (y1+y2)/2+5, fitText(o.Label, fontRegular, o.LabelSize, cap),
		textOpts{Size: o.LabelSize, Anchor: "middle"})
}

type orthOpts struct {
	ViaX, ViaY *float64
	Color      string
	Width      float64
	Dashed     bool
	Label      string
	Open       bool
	NoHead     bool
	LabelSize  float64
}

// orthArrow is a right-angled connector with one bend pair. Diagonal lines
// across a dense diagram are much harder to follow than two straight ones.
func (d *Drawing) orthArrow(x1, y1, x2, y2 float64, o orthOpts) {
	if o.Color == "" {
		o.Color = inkColor
	}
	if o.Width == 0 {
		o.Width = 0.95
	}
	if o.LabelSize == 0 {
		o.LabelSize = 7.0
	}
	type point struct{ X, Y float64 }
	var pts []point
	switch {
	case o.ViaX != nil:
		pts = []point{{x1, y1}, {*o.ViaX, y1}, {*o.ViaX, y2}, {x2, y2}}
	case o.ViaY != nil:
		pts = []point{{x1, y1}, {x1, *o.ViaY}, {x2, *o.ViaY}, {x2, y2}}
	default:
		mid := (x1 + x2) / 2
		pts = []point{{x1, y1}, {mid, y1}, {mid, y2}, {x2, y2}}
	}
	for i := 0; i < len(pts)-1; i++ {
		d.line(pts[i].X, pts[i].Y, pts[i+1].X, pts[i+1].Y, o.Color, o.Width, o.Dashed)
	}
	if !o.NoHead {
		a, b := pts[len(pts)-2], pts[len(pts)-1]
		open := o.Open
		if !open {
			open = true
		}
		d.arrowHead(a.X, a.Y, b.X, b.Y, o.Color, o.Width, open, 6.2)
	}
	if o.Label == "" {
		return
	}
	// Label the longest segment, never the bend.
	best, bestLen := 0, -1.0
	for i := 0; i < len(pts)-1; i++ {
		if l := math.Hypot(pts[i+1].X-pts[i].X, pts[i+1].Y-pts[i].Y); l > bestLen {
			best, bestLen = i, l
		}
	}
	a, b := pts[best], pts[best+1]
	mx, my := (a.X+b.X)/2, (a.Y+b.Y)/2
	if math.Abs(a.X-b.X) >= math.Abs(a.Y-b.Y) {
		d.text(mx, my+5, fitText(o.Label, fontRegular, o.LabelSize, math.Max(70, math.Abs(a.X-b.X)-12)),
			textOpts{Size: o.LabelSize, Color: mutedColor, Anchor: "middle"})
		return
	}
	d.text(mx+5, my, fitText(o.Label, fontRegular, o.LabelSize, 100),
		textOpts{Size: o.LabelSize, Color: mutedColor})
}

type nodeOpts struct {
	Stroke   string
	Fill     string
	Size     float64
	Radius   float64
	Font     string
	MaxLines int
}

func (d *Drawing) rounded(x, y, w, h float64, label string, o nodeOpts) {
	if o.Stroke == "" {
		o.Stroke = greenColor
	}
	if o.Fill == "" {
		o.Fill = panelColor
	}
	if o.Size == 0 {
		o.Size = 8.5
	}
	if o.Radius == 0 {
		o.Radius = 10
	}
	if o.MaxLines == 0 {
		o.MaxLines = 2
	}
	d.roundRect(x, y, w, h, o.Radius, o.Fill, o.Stroke, 1.25)
	d.textBlock(x+w/2, y+h/2, label, blockOpts{
		MaxWidth: w - 14, Size: o.Size, Font: o.Font, MaxLines: o.MaxLines})
}

func (d *Drawing) diamond(cx, cy, w, h float64, label string, stroke, fill string, size float64) {
	if stroke == "" {
		stroke = orangeColor
	}
	if fill == "" {
		fill = whiteColor
	}
	if size == 0 {
		size = 7.5
	}
	d.polygon([]float64{cx, cy + h/2, cx + w/2, cy, cx, cy - h/2, cx - w/2, cy}, fill, stroke, 1.25)
	d.textBlock(cx, cy, label, blockOpts{MaxWidth: w * 0.64, Size: size, MaxLines: 2})
}

// startNode and endNode are the UML initial and final pseudo-states.
func (d *Drawing) startNode(cx, cy float64) { d.circle(cx, cy, 7, blackColor, blackColor, 1) }

func (d *Drawing) endNode(cx, cy float64) {
	d.circle(cx, cy, 8, whiteColor, blackColor, 1.5)
	d.circle(cx, cy, 5, blackColor, blackColor, 1)
}

// stickFigure is the UML actor. It is drawn rather than boxed because a box
// beside an oval reads as another use case.
func (d *Drawing) stickFigure(cx, cy float64, label string, scale float64) {
	d.circle(cx, cy+25*scale, 7*scale, whiteColor, blackColor, 1.25)
	d.line(cx, cy+18*scale, cx, cy-4*scale, blackColor, 1.25, false)
	d.line(cx-16*scale, cy+8*scale, cx+16*scale, cy+8*scale, blackColor, 1.25, false)
	d.line(cx, cy-4*scale, cx-14*scale, cy-24*scale, blackColor, 1.25, false)
	d.line(cx, cy-4*scale, cx+14*scale, cy-24*scale, blackColor, 1.25, false)
	d.text(cx, cy-39*scale, fitText(label, fontRegular, 8.5*scale, 110*scale),
		textOpts{Size: 8.5 * scale, Anchor: "middle"})
}

// notApplicable is what a diagram the specification cannot support looks like.
// Saying so plainly is the whole point: the alternative is a plausible
// invention nobody can tell apart from a real one.
func notApplicableDrawing(width float64, reason string) *Drawing {
	const height = 160
	d := newDrawing(width, height)
	x, y, w, h := 34.0, 32.0, width-68, 92.0
	d.roundRect(x, y, w, h, 8, panel2Color, lineColor, 1.1)
	d.text(x+18, y+h-28, "Not applicable to the current specification",
		textOpts{Size: 10, Font: fontBold})
	d.textBlock(x+w/2, y+h/2-10, reason,
		blockOpts{MaxWidth: w - 36, Size: 8.5, Color: mutedColor, MaxLines: 3})
	return d
}

// --- matching helpers ------------------------------------------------------------------

// drawStop are the words that say nothing about which thing a label names.
var drawStop = map[string]bool{
	"the": true, "a": true, "an": true, "and": true, "or": true, "to": true,
	"of": true, "in": true, "on": true, "for": true, "with": true, "using": true,
	"manage": true, "management": true, "system": true, "application": true,
	"app": true, "page": true, "screen": true, "module": true, "data": true,
	"api": true, "service": true, "customer": true, "user": true, "admin": true,
}

// tokenise reduces a label to the words that identify it, so two labels can be
// compared for what they are about rather than how they are worded.
func tokenise(text string) map[string]bool {
	out := map[string]bool{}
	for _, word := range wordPattern.FindAllString(strings.ToLower(text), -1) {
		if len(word) < 3 || drawStop[word] {
			continue
		}
		switch {
		case len(word) > 4 && strings.HasSuffix(word, "ies"):
			word = word[:len(word)-3] + "y"
		case len(word) > 4 && strings.HasSuffix(word, "s"):
			word = word[:len(word)-1]
		}
		out[word] = true
	}
	return out
}

func overlapScore(a, b string) int {
	first, second := tokenise(a), tokenise(b)
	count := 0
	for word := range first {
		if second[word] {
			count++
		}
	}
	return count
}
