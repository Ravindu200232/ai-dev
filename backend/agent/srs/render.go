package srs

import (
	"fmt"
	"strconv"
	"strings"
)

// A drawing written out as SVG. The layout uses a bottom-left origin with y
// increasing upward; SVG's grows downward, so this is the one place the two
// conventions meet and every y is flipped exactly once.

// SVG writes the drawing as a standalone document.
func (d *Drawing) SVG() string {
	var b strings.Builder
	fmt.Fprintf(&b, `<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" `+
		`viewBox="0 0 %s %s" font-family="Helvetica, Arial, sans-serif">`+"\n",
		num(d.Width), num(d.Height), num(d.Width), num(d.Height))
	for _, s := range d.Shapes {
		if line := d.shapeSVG(s); line != "" {
			b.WriteString("  " + line + "\n")
		}
	}
	b.WriteString("</svg>\n")
	return b.String()
}

// flip turns a bottom-left y into a top-left one.
func (d *Drawing) flip(y float64) float64 { return d.Height - y }

func (d *Drawing) shapeSVG(s Shape) string {
	switch s.Kind {
	case "rect":
		attrs := fmt.Sprintf(`x="%s" y="%s" width="%s" height="%s"`,
			num(s.X), num(d.flip(s.Y+s.H)), num(s.W), num(s.H))
		if s.Radius > 0 {
			attrs += fmt.Sprintf(` rx="%s" ry="%s"`, num(s.Radius), num(s.Radius))
		}
		return "<rect " + attrs + paint(s) + "/>"

	case "ellipse":
		return fmt.Sprintf(`<ellipse cx="%s" cy="%s" rx="%s" ry="%s"%s/>`,
			num(s.X), num(d.flip(s.Y)), num(s.RX), num(s.RY), paint(s))

	case "circle":
		return fmt.Sprintf(`<circle cx="%s" cy="%s" r="%s"%s/>`,
			num(s.X), num(d.flip(s.Y)), num(s.Radius), paint(s))

	case "line":
		return fmt.Sprintf(`<line x1="%s" y1="%s" x2="%s" y2="%s"%s/>`,
			num(s.X), num(d.flip(s.Y)), num(s.X2), num(d.flip(s.Y2)), paint(s))

	case "polygon":
		var points []string
		for i := 0; i+1 < len(s.Points); i += 2 {
			points = append(points, num(s.Points[i])+","+num(d.flip(s.Points[i+1])))
		}
		return fmt.Sprintf(`<polygon points="%s"%s/>`, strings.Join(points, " "), paint(s))

	case "text":
		if strings.TrimSpace(s.Text) == "" {
			return ""
		}
		anchor := ""
		switch s.Anchor {
		case "middle":
			anchor = ` text-anchor="middle"`
		case "end":
			anchor = ` text-anchor="end"`
		}
		return fmt.Sprintf(`<text x="%s" y="%s" font-size="%s"%s%s fill="%s">%s</text>`,
			num(s.X), num(d.flip(s.Y)), num(s.Size), fontAttrs(s.Font), anchor,
			orText(s.Fill, inkColor), escapeXML(s.Text))
	}
	return ""
}

// paint is the fill, stroke, width and dash of one shape. A shape with no
// stroke width still gets one when it has a stroke, because a hairline that
// vanishes at print size is the same as no border at all.
func paint(s Shape) string {
	fill := s.Fill
	if fill == "" {
		fill = "none"
	}
	out := ` fill="` + fill + `"`
	if s.Kind == "line" {
		out = ` fill="none"`
	}
	if s.Stroke != "" {
		width := s.Width
		if width == 0 {
			width = 1
		}
		out += ` stroke="` + s.Stroke + `" stroke-width="` + num(width) + `"`
	}
	if s.Dash {
		out += ` stroke-dasharray="5 3"`
	}
	return out
}

func fontAttrs(font string) string {
	switch font {
	case fontBold:
		return ` font-weight="bold"`
	case fontMono:
		return ` font-family="Courier, monospace"`
	}
	return ""
}

// num prints a coordinate without a trailing run of zeroes.
func num(v float64) string {
	return strconv.FormatFloat(round2(v), 'f', -1, 64)
}

func round2(v float64) float64 {
	return float64(int64(v*100+sign(v)*0.5)) / 100
}

func sign(v float64) float64 {
	if v < 0 {
		return -1
	}
	return 1
}

var xmlEscape = strings.NewReplacer(
	"&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&apos;")

func escapeXML(text string) string { return xmlEscape.Replace(text) }
