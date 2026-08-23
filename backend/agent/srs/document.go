package srs

import (
	"strings"

	"github.com/go-pdf/fpdf"
)

// The SRS as a PDF. The library underneath draws where it is told and nothing
// else, so this file is the flowing-document layer above it: a cursor that
// knows when a page is full, headings that record themselves for the contents
// page, and tables whose cells wrap.
//
// The contents page is why the document is written twice. The first pass finds
// which page each heading landed on; the second inserts a contents page of a
// known length and shifts every number by it. Guessing instead would give a
// specification whose own index is wrong.

// Page geometry, in points.
const (
	pageWidth   = 595.28
	pageHeight  = 841.89
	marginLeft  = 53.86 // 1.9cm
	marginRight = 53.86
	marginTop   = 62.36 // 2.2cm
	marginBot   = 51.02 // 1.8cm
	cm          = 28.3465
)

// The document palette. It is brighter than the diagrams' on purpose: a table
// header has to separate from the rows under it.
var (
	pdfPrimary = rgb(0x25, 0x63, 0xEB)
	pdfInk     = rgb(0x0F, 0x17, 0x2A)
	pdfMuted   = rgb(0x64, 0x74, 0x8B)
	pdfLine    = rgb(0xE2, 0xE8, 0xF0)
	pdfSoft    = rgb(0xF8, 0xFA, 0xFC)
	pdfGreen   = rgb(0x16, 0xA3, 0x4A)
	pdfOrange  = rgb(0xEA, 0x58, 0x0C)
	pdfRed     = rgb(0xDC, 0x26, 0x26)
	pdfWhite   = rgb(0xFF, 0xFF, 0xFF)
)

type colour struct{ R, G, B int }

func rgb(r, g, b int) colour { return colour{r, g, b} }

// hexColour reads a "#RRGGBB" string, falling back to black.
func hexColour(text string) colour {
	text = strings.TrimPrefix(strings.TrimSpace(text), "#")
	if len(text) != 6 {
		return colour{0, 0, 0}
	}
	value := 0
	for _, r := range text {
		digit := 0
		switch {
		case r >= '0' && r <= '9':
			digit = int(r - '0')
		case r >= 'a' && r <= 'f':
			digit = int(r-'a') + 10
		case r >= 'A' && r <= 'F':
			digit = int(r-'A') + 10
		default:
			return colour{0, 0, 0}
		}
		value = value*16 + digit
	}
	return colour{value >> 16 & 0xFF, value >> 8 & 0xFF, value & 0xFF}
}

// tocEntry is one heading, and the page it ended up on.
type tocEntry struct {
	Level int
	Text  string
	Page  int
}

// writer is the flowing document: a page cursor with the chrome around it.
type writer struct {
	pdf    *fpdf.Fpdf
	width  float64
	y      float64
	name   string
	verson string

	entries []tocEntry
	chrome  bool // the cover has no header or footer
	section int
	tr      func(string) string
}

// asciiFallback covers the few characters the built-in fonts have no glyph
// for. They are replaced rather than dropped, because a requirement that
// silently loses its tick mark reads as a requirement nobody ticked.
var asciiFallback = strings.NewReplacer(
	"☑", "[x]", "☐", "[ ]", "✓", "v", "✗", "x", "→", "->", "⟶", "->",
	"≥", ">=", "≤", "<=", "×", "x",
)

// out prepares one string for the page: the fallbacks first, then the
// encoding the built-in fonts actually use.
func (w *writer) out(text string) string {
	if w.tr == nil {
		return text
	}
	return w.tr(asciiFallback.Replace(text))
}

func newWriter(projectName, version string) *writer {
	pdf := fpdf.New("P", "pt", "A4", "")
	pdf.SetMargins(marginLeft, marginTop, marginRight)
	pdf.SetAutoPageBreak(false, marginBot)
	w := &writer{pdf: pdf, width: pageWidth - marginLeft - marginRight,
		name: projectName, verson: version,
		// The base-14 fonts are Windows-1252, so every string is converted on
		// the way to the page rather than emitted as raw UTF-8 bytes.
		tr: pdf.UnicodeTranslatorFromDescriptor("")}
	pdf.SetHeaderFunc(func() {
		if !w.chrome {
			return
		}
		pdf.SetDrawColor(pdfLine.R, pdfLine.G, pdfLine.B)
		pdf.SetLineWidth(0.5)
		pdf.Line(marginLeft, pageHeight-1.5*cm, pageWidth-marginRight, pageHeight-1.5*cm)
		pdf.SetFont("Helvetica", "", 7.5)
		pdf.SetTextColor(pdfMuted.R, pdfMuted.G, pdfMuted.B)
		pdf.Text(marginLeft, pageHeight-1.35*cm, w.out(truncate(w.name, 70)))
		right := w.out("SRS v" + w.verson)
		pdf.Text(pageWidth-marginRight-pdf.GetStringWidth(right), pageHeight-1.35*cm, right)
	})
	pdf.SetFooterFunc(func() {
		if !w.chrome {
			return
		}
		pdf.SetDrawColor(pdfLine.R, pdfLine.G, pdfLine.B)
		pdf.SetLineWidth(0.5)
		pdf.Line(marginLeft, 1.4*cm, pageWidth-marginRight, 1.4*cm)
		pdf.SetFont("Helvetica", "", 7.5)
		pdf.SetTextColor(pdfMuted.R, pdfMuted.G, pdfMuted.B)
		pdf.Text(marginLeft, 1.1*cm, w.out("AgentForge Studio · "+Knowledge().Standards.SRSStandard+"-aligned"))
		page := w.out("Page " + itoa(pdf.PageNo()))
		pdf.Text(pageWidth-marginRight-pdf.GetStringWidth(page), 1.1*cm, page)
	})
	return w
}

// bottom is where the text frame ends.
func (w *writer) bottom() float64 { return pageHeight - marginBot }

func (w *writer) newPage() {
	w.pdf.AddPage()
	w.y = marginTop
}

// ensure starts a new page when the next block will not fit on this one.
func (w *writer) ensure(height float64) {
	if w.y+height > w.bottom() {
		w.newPage()
	}
}

func (w *writer) spacer(height float64) { w.y += height }

// --- text -------------------------------------------------------------------------------

type style struct {
	Size    float64
	Leading float64
	Bold    bool
	Mono    bool
	Colour  colour
	Indent  float64
	Centre  bool
}

var (
	styleBody  = style{Size: 9.5, Leading: 13.5, Colour: pdfInk}
	styleSmall = style{Size: 8.5, Leading: 11, Colour: pdfMuted}
	styleCell  = style{Size: 8.5, Leading: 11, Colour: pdfInk}
	styleCellB = style{Size: 8.5, Leading: 11, Colour: pdfInk, Bold: true}
	styleCode  = style{Size: 7.5, Leading: 9.5, Colour: rgb(0x33, 0x41, 0x55), Mono: true}
)

func (w *writer) setFont(s style) {
	family, weight := "Helvetica", ""
	if s.Bold {
		weight = "B"
	}
	if s.Mono {
		family = "Courier"
	}
	w.pdf.SetFont(family, weight, s.Size)
	w.pdf.SetTextColor(s.Colour.R, s.Colour.G, s.Colour.B)
}

// wrap splits text into lines that fit a width, in the current font. Words are
// kept whole: the library's own splitter breaks mid-word, which turns a narrow
// column of requirement ids into "FR-00" above a lone "1".
func (w *writer) wrap(text string, s style, width float64) []string {
	w.setFont(s)
	if strings.TrimSpace(text) == "" {
		return nil
	}
	var out []string
	for _, paragraph := range strings.Split(w.out(text), "\n") {
		words := strings.Fields(paragraph)
		if len(words) == 0 {
			continue
		}
		line := ""
		for _, word := range words {
			// A single word wider than the column has to be broken; there is
			// nowhere else for it to go.
			for w.pdf.GetStringWidth(word) > width && len(word) > 1 {
				cut := len(word)
				for cut > 1 && w.pdf.GetStringWidth(word[:cut]) > width {
					cut--
				}
				if line != "" {
					out = append(out, line)
					line = ""
				}
				out = append(out, word[:cut])
				word = word[cut:]
			}
			trial := strings.TrimSpace(line + " " + word)
			if line == "" || w.pdf.GetStringWidth(trial) <= width {
				line = trial
				continue
			}
			out = append(out, line)
			line = word
		}
		if line != "" {
			out = append(out, line)
		}
	}
	return out
}

// para writes a wrapped paragraph, breaking the page between lines when it has
// to rather than pushing the whole block over.
func (w *writer) para(text string, s style) {
	lines := w.wrap(text, s, w.width-s.Indent)
	if len(lines) == 0 {
		return
	}
	leading := s.Leading
	if leading == 0 {
		leading = s.Size + 3
	}
	for _, line := range lines {
		w.ensure(leading)
		w.setFont(s)
		x := marginLeft + s.Indent
		if s.Centre {
			x = marginLeft + (w.width-w.pdf.GetStringWidth(line))/2
		}
		w.pdf.Text(x, w.y+s.Size, line)
		w.y += leading
	}
	w.y += 2
}

func (w *writer) body(text string)  { w.para(text, styleBody) }
func (w *writer) small(text string) { w.para(text, styleSmall) }

func (w *writer) bullet(text string) {
	w.para("• "+text, style{Size: 9.5, Leading: 13.5, Colour: pdfInk, Indent: 8})
}

// heading1 opens a numbered section and records it for the contents page.
func (w *writer) heading1(text string) int {
	w.section++
	label := itoa(w.section) + ". " + text
	w.ensure(34)
	w.y += 14
	w.entries = append(w.entries, tocEntry{0, label, w.pdf.PageNo()})
	w.setFont(style{Size: 16, Bold: true, Colour: pdfPrimary})
	w.pdf.Text(marginLeft, w.y+16, w.out(label))
	w.y += 28
	return w.section
}

// frontHeading is a heading with no section number — the front matter, which
// is indexed but not numbered.
func (w *writer) frontHeading(text string) {
	w.ensure(34)
	w.entries = append(w.entries, tocEntry{0, text, w.pdf.PageNo()})
	w.setFont(style{Size: 16, Bold: true, Colour: pdfPrimary})
	w.pdf.Text(marginLeft, w.y+16, w.out(text))
	w.y += 30
}

func (w *writer) heading2(text string) {
	w.ensure(26)
	w.y += 10
	w.entries = append(w.entries, tocEntry{1, text, w.pdf.PageNo()})
	w.setFont(style{Size: 12.5, Bold: true, Colour: pdfInk})
	w.pdf.Text(marginLeft, w.y+12.5, w.out(text))
	w.y += 20
}

// --- tables ------------------------------------------------------------------------------

type cell struct {
	Text  string
	Style style
}

func plainCell(text string) cell  { return cell{Text: text, Style: styleCell} }
func boldCell(text string) cell   { return cell{Text: text, Style: styleCellB} }
func smallCell(text string) cell  { return cell{Text: text, Style: styleSmall} }
func headerCell(text string) cell { return cell{Text: text, Style: styleCellB} }

// table draws rows with wrapping cells, a repeated header and zebra striping.
// A row that will not fit takes the next page with the header above it, which
// is the only way a long requirements table stays readable.
func (w *writer) table(rows [][]cell, widths []float64, header bool) {
	if len(rows) == 0 {
		return
	}
	const padX, padY = 5.0, 4.0

	measure := func(row []cell) ([][]string, float64) {
		lines := make([][]string, len(row))
		tallest := 0.0
		for i, c := range row {
			if i >= len(widths) {
				break
			}
			lines[i] = w.wrap(c.Text, c.Style, widths[i]-2*padX)
			leading := c.Style.Leading
			if leading == 0 {
				leading = c.Style.Size + 3
			}
			if h := float64(len(lines[i]))*leading + 2*padY; h > tallest {
				tallest = h
			}
		}
		if tallest < 16 {
			tallest = 16
		}
		return lines, tallest
	}

	drawRow := func(row []cell, index int) {
		lines, height := measure(row)
		w.ensure(height)

		background := pdfWhite
		switch {
		case header && index == 0:
			background = pdfPrimary
		case index%2 == 0:
			background = pdfSoft
		}
		w.pdf.SetFillColor(background.R, background.G, background.B)
		w.pdf.SetDrawColor(pdfLine.R, pdfLine.G, pdfLine.B)
		w.pdf.SetLineWidth(0.4)

		x := marginLeft
		for i := range row {
			if i >= len(widths) {
				break
			}
			w.pdf.Rect(x, w.y, widths[i], height, "FD")
			x += widths[i]
		}

		x = marginLeft
		for i, c := range row {
			if i >= len(widths) {
				break
			}
			s := c.Style
			if header && index == 0 {
				s = style{Size: 8.5, Leading: 11, Bold: true, Colour: pdfWhite}
			}
			leading := s.Leading
			if leading == 0 {
				leading = s.Size + 3
			}
			ty := w.y + padY
			for _, line := range lines[i] {
				w.setFont(s)
				w.pdf.Text(x+padX, ty+s.Size, line)
				ty += leading
			}
			x += widths[i]
		}
		w.y += height
	}

	for i, row := range rows {
		if header && i > 0 {
			// A header repeats whenever the body of the table starts a page.
			_, height := measure(row)
			if w.y+height > w.bottom() {
				w.newPage()
				drawRow(rows[0], 0)
			}
		}
		drawRow(row, i)
	}
	w.y += 6
}

// badge is the small filled label a risk severity is printed in.
func (w *writer) badge(text string, fill colour) {
	w.setFont(style{Size: 8, Bold: true, Colour: pdfWhite})
	text = w.out(text)
	width := w.pdf.GetStringWidth(text) + 14
	w.ensure(18)
	w.pdf.SetFillColor(fill.R, fill.G, fill.B)
	w.pdf.RoundedRect(marginLeft, w.y, width, 14, 4, "1234", "F")
	w.setFont(style{Size: 8, Bold: true, Colour: pdfWhite})
	w.pdf.Text(marginLeft+7, w.y+10, text) // already translated above
	w.y += 18
}

// --- drawings ----------------------------------------------------------------------------

// drawing places one diagram, scaled to fit and boxed. It is the same shape
// list the SVG is written from, so the two cannot drift apart.
func (w *writer) drawing(d *Drawing, maxHeight float64) {
	if d == nil || d.Width <= 0 || d.Height <= 0 {
		return
	}
	scale := 1.0
	if d.Width > w.width {
		scale = w.width / d.Width
	}
	if d.Height*scale > maxHeight {
		scale = maxHeight / d.Height
	}
	width, height := d.Width*scale, d.Height*scale
	w.ensure(height + 16)

	x0 := marginLeft + (w.width-width)/2
	y0 := w.y + 8
	w.pdf.SetDrawColor(pdfLine.R, pdfLine.G, pdfLine.B)
	w.pdf.SetLineWidth(0.6)
	w.pdf.Rect(x0-4, y0-4, width+8, height+8, "D")

	// The layout has a bottom-left origin; the page has a top-left one.
	at := func(x, y float64) (float64, float64) {
		return x0 + x*scale, y0 + (d.Height-y)*scale
	}
	for _, s := range d.Shapes {
		w.shape(s, at, scale)
	}
	w.y += height + 16
}

func (w *writer) shape(s Shape, at func(float64, float64) (float64, float64), scale float64) {
	mode := ""
	if s.Fill != "" && s.Kind != "line" {
		c := hexColour(s.Fill)
		w.pdf.SetFillColor(c.R, c.G, c.B)
		mode = "F"
	}
	if s.Stroke != "" {
		c := hexColour(s.Stroke)
		w.pdf.SetDrawColor(c.R, c.G, c.B)
		width := s.Width
		if width == 0 {
			width = 1
		}
		w.pdf.SetLineWidth(width * scale)
		mode += "D"
	}
	if s.Dash {
		w.pdf.SetDashPattern([]float64{5 * scale, 3 * scale}, 0)
		defer w.pdf.SetDashPattern(nil, 0)
	}

	switch s.Kind {
	case "rect":
		x, y := at(s.X, s.Y+s.H)
		if mode == "" {
			return
		}
		if s.Radius > 0 {
			w.pdf.RoundedRect(x, y, s.W*scale, s.H*scale, s.Radius*scale, "1234", mode)
			return
		}
		w.pdf.Rect(x, y, s.W*scale, s.H*scale, mode)

	case "ellipse":
		x, y := at(s.X, s.Y)
		if mode != "" {
			w.pdf.Ellipse(x, y, s.RX*scale, s.RY*scale, 0, mode)
		}

	case "circle":
		x, y := at(s.X, s.Y)
		if mode != "" {
			w.pdf.Circle(x, y, s.Radius*scale, mode)
		}

	case "line":
		x1, y1 := at(s.X, s.Y)
		x2, y2 := at(s.X2, s.Y2)
		w.pdf.Line(x1, y1, x2, y2)

	case "polygon":
		if mode == "" || len(s.Points) < 6 {
			return
		}
		points := make([]fpdf.PointType, 0, len(s.Points)/2)
		for i := 0; i+1 < len(s.Points); i += 2 {
			x, y := at(s.Points[i], s.Points[i+1])
			points = append(points, fpdf.PointType{X: x, Y: y})
		}
		w.pdf.Polygon(points, mode)

	case "text":
		if strings.TrimSpace(s.Text) == "" {
			return
		}
		family, weight := "Helvetica", ""
		switch s.Font {
		case fontBold:
			weight = "B"
		case fontMono:
			family = "Courier"
		}
		size := s.Size * scale
		w.pdf.SetFont(family, weight, size)
		c := hexColour(orText(s.Fill, inkColor))
		w.pdf.SetTextColor(c.R, c.G, c.B)
		body := w.out(s.Text)
		x, y := at(s.X, s.Y)
		switch s.Anchor {
		case "middle":
			x -= w.pdf.GetStringWidth(body) / 2
		case "end":
			x -= w.pdf.GetStringWidth(body)
		}
		w.pdf.Text(x, y, body)
	}
}
