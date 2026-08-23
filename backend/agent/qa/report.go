package qa

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"agentforge/agent/core"
)

// The Testing tab offers the report as a PDF to hand to someone who is not
// going to open the Studio. It is a text document, so it is written directly
// rather than through a layout library: one built-in font, one column, and no
// dependency to keep current.

const (
	pageWidth    = 595.28 // A4 at 72dpi
	pageHeight   = 841.89
	marginLeft   = 56
	marginTop    = 56
	lineHeight   = 14
	bodySize     = 10
	headingSize  = 15
	titleSize    = 20
	linesPerPage = 52 // (pageHeight - 2*marginTop) / lineHeight, rounded down
)

// line is one rendered row.
type line struct {
	text string
	size float64
	gap  bool // an empty row before this one
}

// PDF renders a project's QA report. It is what /qa-pdf serves.
func PDF(paths core.Paths, project string) ([]byte, error) {
	project = core.SafeName(project)
	if project == "" {
		return nil, fmt.Errorf("name a project")
	}
	if _, err := os.Stat(paths.Project(project)); err != nil {
		return nil, fmt.Errorf("no such project: %s", project)
	}

	var report Report
	path := filepath.Join(paths.Meta(project), "qa", "report.json")
	if err := core.ReadJSON(path, &report); err != nil {
		return nil, fmt.Errorf("this project has no test report yet")
	}
	if report.FinishedAt == "" && report.Suite.Total == 0 && report.E2E.Total == 0 {
		return nil, fmt.Errorf("this project has no test report yet")
	}
	return render(reportLines(project, report)), nil
}

// reportLines turns the report into the rows of the document.
func reportLines(project string, r Report) []line {
	out := []line{
		{text: "Test report", size: titleSize},
		{text: project, size: bodySize},
	}
	if r.FinishedAt != "" {
		out = append(out, line{text: "Finished " + r.FinishedAt, size: bodySize})
	}

	out = append(out, stageSection("Runtime", r.Runtime)...)
	out = append(out, stageSection("Unit tests", r.Suite)...)
	out = append(out, stageSection("API checks", r.API)...)
	out = append(out, stageSection("Browser journeys", r.E2E)...)

	out = append(out, line{text: "Performance", size: headingSize, gap: true})
	if r.Performance != nil {
		for key, value := range r.Performance.Scores {
			out = append(out, line{text: fmt.Sprintf("  %s: %d / 100", key, value), size: bodySize})
		}
		for key, value := range r.Performance.Metrics {
			out = append(out, line{text: fmt.Sprintf("  %s: %s", key, value), size: bodySize})
		}
		for _, route := range r.Performance.Routes {
			out = append(out, line{
				text: fmt.Sprintf("  %-28s %5d ms  %6d bytes  HTTP %d",
					truncate(route.Route, 28), route.MS, route.Bytes, route.Status),
				size: bodySize,
			})
		}
	} else {
		out = append(out, line{text: "  not measured", size: bodySize})
	}

	out = append(out, line{text: "Security", size: headingSize, gap: true})
	out = append(out, line{
		text: fmt.Sprintf("  %d file(s) scanned, %d finding(s)",
			r.Security.Checked, len(r.Security.Findings)),
		size: bodySize,
	})
	for _, f := range r.Security.Findings {
		where := f.File
		if f.Line > 0 {
			where = fmt.Sprintf("%s:%d", f.File, f.Line)
		}
		out = append(out,
			line{text: fmt.Sprintf("  [%s] %s", strings.ToUpper(f.Severity), f.Title), size: bodySize},
			line{text: "      " + where, size: bodySize},
		)
		for _, wrapped := range wrap(f.Detail, 86) {
			out = append(out, line{text: "      " + wrapped, size: bodySize})
		}
	}
	return out
}

// stageSection renders one stage's tally and whatever it could not close.
func stageSection(title string, s StageReport) []line {
	out := []line{{text: title, size: headingSize, gap: true}}
	if s.Note != "" {
		return append(out, line{text: "  " + s.Note, size: bodySize})
	}
	if s.Total == 0 && s.Passed == 0 && s.Failed == 0 {
		return append(out, line{text: "  did not run", size: bodySize})
	}
	out = append(out, line{
		text: fmt.Sprintf("  %d passed, %d failed, %d total", s.Passed, s.Failed, s.Total),
		size: bodySize,
	})
	for _, item := range s.Unresolved {
		for i, wrapped := range wrap(item, 88) {
			prefix := "  - "
			if i > 0 {
				prefix = "    "
			}
			out = append(out, line{text: prefix + wrapped, size: bodySize})
		}
	}
	return out
}

// render lays the rows onto pages and writes the file.
func render(lines []line) []byte {
	pages := paginate(lines)
	var objects [][]byte

	// 1 catalog, 2 pages, 3 font, then a content stream and a page per page.
	contentFirst := 4
	pageFirst := contentFirst + len(pages)

	kids := make([]string, 0, len(pages))
	for i := range pages {
		kids = append(kids, fmt.Sprintf("%d 0 R", pageFirst+i))
	}

	objects = append(objects, []byte("<< /Type /Catalog /Pages 2 0 R >>"))
	objects = append(objects, []byte(fmt.Sprintf(
		"<< /Type /Pages /Count %d /Kids [%s] >>", len(pages), strings.Join(kids, " "))))
	objects = append(objects, []byte(
		"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"))

	for _, page := range pages {
		stream := pageStream(page)
		objects = append(objects, []byte(fmt.Sprintf(
			"<< /Length %d >>\nstream\n%s\nendstream", len(stream), stream)))
	}
	for i := range pages {
		objects = append(objects, []byte(fmt.Sprintf(
			"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "+
				"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>",
			pageWidth, pageHeight, contentFirst+i)))
	}
	return assemble(objects)
}

// paginate splits the rows into pages.
func paginate(lines []line) [][]line {
	var pages [][]line
	var current []line
	used := 0
	for _, l := range lines {
		cost := 1
		if l.gap {
			cost = 2
		}
		if used+cost > linesPerPage && len(current) > 0 {
			pages = append(pages, current)
			current, used = nil, 0
			l.gap = false // a heading at the top of a page needs no gap
			cost = 1
		}
		current = append(current, l)
		used += cost
	}
	if len(current) > 0 {
		pages = append(pages, current)
	}
	if len(pages) == 0 {
		pages = [][]line{{{text: "Nothing has been recorded yet.", size: bodySize}}}
	}
	return pages
}

// pageStream writes one page's text operators.
func pageStream(lines []line) string {
	var b strings.Builder
	b.WriteString("BT\n")
	y := pageHeight - marginTop
	for _, l := range lines {
		if l.gap {
			y -= lineHeight
		}
		fmt.Fprintf(&b, "/F1 %.1f Tf\n1 0 0 1 %.2f %.2f Tm\n(%s) Tj\n",
			l.size, float64(marginLeft), y, escapePDF(l.text))
		y -= lineHeight
	}
	b.WriteString("ET")
	return b.String()
}

// assemble writes the objects, the cross-reference table and the trailer.
func assemble(objects [][]byte) []byte {
	var out bytes.Buffer
	out.WriteString("%PDF-1.4\n")

	offsets := make([]int, len(objects))
	for i, body := range objects {
		offsets[i] = out.Len()
		fmt.Fprintf(&out, "%d 0 obj\n", i+1)
		out.Write(body)
		out.WriteString("\nendobj\n")
	}

	xref := out.Len()
	fmt.Fprintf(&out, "xref\n0 %d\n", len(objects)+1)
	out.WriteString("0000000000 65535 f \n")
	for _, offset := range offsets {
		fmt.Fprintf(&out, "%010d 00000 n \n", offset)
	}
	fmt.Fprintf(&out, "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n",
		len(objects)+1, xref)
	return out.Bytes()
}

// escapePDF makes one line safe inside a PDF string literal. Anything outside
// WinAnsi becomes a question mark rather than corrupting the stream.
func escapePDF(text string) string {
	var b strings.Builder
	for _, r := range text {
		switch {
		case r == '\\' || r == '(' || r == ')':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r < 32:
			b.WriteByte(' ')
		case r < 127:
			b.WriteRune(r)
		default:
			b.WriteByte('?')
		}
	}
	return b.String()
}

// wrap breaks a long line at word boundaries.
func wrap(text string, width int) []string {
	words := strings.Fields(text)
	if len(words) == 0 {
		return nil
	}
	var out []string
	current := words[0]
	for _, word := range words[1:] {
		if len(current)+1+len(word) > width {
			out = append(out, current)
			current = word
			continue
		}
		current += " " + word
	}
	return append(out, current)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n-1] + "…"
}
