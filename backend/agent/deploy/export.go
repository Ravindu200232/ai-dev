package deploy

import (
	"archive/zip"
	"bytes"
	"encoding/base64"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/go-pdf/fpdf"
)

// What a deployment leaves behind for somebody else to read.
//
// The zip is everything the run generated plus everything it recorded; the PDF
// is the same story told for a person. Both are redacted on the way out, which
// matters more here than anywhere else: an evidence bundle is the artefact
// most likely to be forwarded to somebody who was never meant to see the
// customer's connection string.
//
// The Python agent also tried to screenshot its own dashboard here. It never
// could — it navigated to a page that server does not serve — so this does not.

// evidenceLimit is the largest image the Studio may hand back for the bundle.
const evidenceLimit = 12 << 20

// dataImage is what the Studio sends when a person attaches a screenshot.
var dataImage = regexp.MustCompile(`(?s)^data:image/(png|jpeg|webp);base64,(.+)$`)

// unsafeName is everything an evidence file may not be called.
var unsafeName = regexp.MustCompile(`[^a-zA-Z0-9_-]+`)

// Exporter builds the bundles from one run's record.
type Exporter struct{ Store *Store }

// Zip is every artifact and every event, as a file the customer can keep.
func (e Exporter) Zip(runID string) ([]byte, error) {
	run, err := e.run(runID)
	if err != nil {
		return nil, err
	}
	records, err := e.Store.Artifacts(runID)
	if err != nil {
		return nil, err
	}
	events, err := e.Store.Events(runID, 0, 0)
	if err != nil {
		return nil, err
	}

	var buffer bytes.Buffer
	archive := zip.NewWriter(&buffer)

	for _, record := range records {
		body, err := os.ReadFile(filepath.Join(run.StagedPath, filepath.FromSlash(record.Path)))
		if err != nil {
			continue
		}
		// Text is redacted; anything that is not text is copied as it is,
		// because a partial binary is worse than none.
		if utf8.Valid(body) {
			body = []byte(RedactText(string(body)))
		}
		if err := addToZip(archive, "artifacts/"+record.Path, body); err != nil {
			return nil, err
		}
	}

	if err := addToZip(archive, "evidence/run.json", []byte(indented(Redact(run)))); err != nil {
		return nil, err
	}
	if err := addToZip(archive, "evidence/events.json", []byte(indented(events))); err != nil {
		return nil, err
	}

	for _, item := range e.evidence(runID) {
		body, err := os.ReadFile(item.Path)
		if err != nil {
			continue
		}
		if err := addToZip(archive, "evidence/screenshots/"+filepath.Base(item.Path), body); err != nil {
			return nil, err
		}
	}
	if err := archive.Close(); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}

func addToZip(archive *zip.Writer, name string, body []byte) error {
	entry, err := archive.Create(name)
	if err != nil {
		return err
	}
	_, err = entry.Write(body)
	return err
}

func (e Exporter) evidence(runID string) []Evidence {
	items, err := e.Store.Evidence(runID)
	if err != nil {
		return nil
	}
	return items
}

func (e Exporter) run(runID string) (*Run, error) {
	run, err := e.Store.GetRun(runID)
	if err != nil || run == nil {
		return nil, notFound("Run not found")
	}
	return run, nil
}

// --- the report ------------------------------------------------------------------------------

// The page, in points.
const (
	pageWidth  = 595.28
	pageHeight = 841.89
	margin     = 45.0
	lineHeight = 13.0
)

// report is one PDF being written.
type report struct {
	pdf *fpdf.Fpdf
	tr  func(string) string
	y   float64
}

// PDF is the run as a document: what was deployed, how ready it is, and what
// happened while it was being deployed.
func (e Exporter) PDF(runID string) ([]byte, error) {
	run, err := e.run(runID)
	if err != nil {
		return nil, err
	}
	events, err := e.Store.Events(runID, 0, 0)
	if err != nil {
		return nil, err
	}

	r := &report{pdf: fpdf.New("P", "pt", "A4", "")}
	// The base-14 fonts are cp1252, so everything written goes through a
	// translator and an ASCII fallback for what cp1252 has no room for.
	r.tr = r.pdf.UnicodeTranslatorFromDescriptor("")
	r.pdf.SetMargins(margin, margin, margin)
	r.pdf.SetAutoPageBreak(false, margin)
	r.pdf.SetTitle("Deployment Report - "+run.ProjectName, true)
	r.page()

	r.heading("Deployment Agent Evidence Report", 20)
	r.heading("Project: "+run.ProjectName, 14)
	r.line("Run: " + run.ID)
	r.line("State: " + string(run.State))
	r.line("Target: " + ProfileFor(TargetOf(run)).Label)
	r.line("Captured: " + time.Now().UTC().Format(time.RFC1123))
	r.space(8)

	readiness := object(run.Readiness)
	r.heading("Readiness: "+strconv.Itoa(number(readiness["score"]))+"/100", 14)
	if categories := object(readiness["categories"]); len(categories) > 0 {
		rows := [][]string{}
		for _, name := range sortedKeys(categories) {
			rows = append(rows, []string{strings.ToUpper(name), strconv.Itoa(number(categories[name]))})
		}
		r.table([]string{"Category", "Score"}, rows)
	}

	r.heading("Detected Services", 14)
	services, _ := run.Spec["services"].([]any)
	for _, item := range services {
		service := object(item)
		root := text(service["root"])
		if root == "" {
			root = "."
		}
		r.line(text(service["name"]) + " - Next.js " + text(service["version"]) +
			" - root " + root + " - port " + strconv.Itoa(number(service["port"])))
	}
	if len(services) == 0 {
		r.line("None recorded.")
	}

	r.heading("Pipeline Evidence", 14)
	from := 0
	if len(events) > 80 {
		from = len(events) - 80
	}
	for _, event := range events[from:] {
		r.line("[" + event.Stage + "] " + event.Message)
	}

	if len(run.Monitor) > 0 {
		r.heading("Remote Snapshot", 14)
		r.code(clip(indented(run.Monitor), 5000))
	}

	if items := e.evidence(runID); len(items) > 0 {
		r.heading("Supporting Images", 14)
		for index, item := range items {
			if index == 10 {
				break
			}
			r.image(item)
		}
	}

	var buffer bytes.Buffer
	if err := r.pdf.Output(&buffer); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}

func (r *report) page() {
	r.pdf.AddPage()
	r.y = margin
}

// room makes sure there is space for what is about to be written, and starts a
// page if there is not.
func (r *report) room(height float64) {
	if r.y+height > pageHeight-margin {
		r.page()
	}
}

func (r *report) space(height float64) { r.y += height }

func (r *report) heading(text string, size float64) {
	r.room(size + 14)
	r.space(10)
	r.pdf.SetFont("Helvetica", "B", size)
	r.pdf.SetTextColor(20, 24, 33)
	r.pdf.Text(margin, r.y+size, r.out(text))
	r.y += size + 6
}

func (r *report) line(text string) {
	r.pdf.SetTextColor(40, 46, 58)
	for _, part := range r.wrap(text, "Helvetica", 9, pageWidth-2*margin) {
		r.room(lineHeight)
		r.pdf.Text(margin, r.y+9, r.out(part))
		r.y += lineHeight
	}
}

func (r *report) code(body string) {
	r.pdf.SetTextColor(70, 76, 90)
	for _, raw := range strings.Split(body, "\n") {
		for _, part := range r.wrap(raw, "Courier", 7.5, pageWidth-2*margin) {
			r.room(10)
			r.pdf.Text(margin, r.y+7.5, r.out(part))
			r.y += 10
		}
	}
}

func (r *report) table(header []string, rows [][]string) {
	widths := []float64{pageWidth - 2*margin - 90, 90}
	r.room(float64(len(rows)+1) * 18)

	r.pdf.SetFont("Helvetica", "B", 9)
	r.pdf.SetFillColor(238, 242, 255)
	r.pdf.SetTextColor(20, 24, 33)
	r.pdf.Rect(margin, r.y, widths[0]+widths[1], 18, "F")
	for index, cell := range header {
		r.pdf.Text(margin+offset(widths, index)+6, r.y+12.5, r.out(cell))
	}
	r.y += 18

	r.pdf.SetFont("Helvetica", "", 9)
	r.pdf.SetDrawColor(203, 213, 225)
	r.pdf.SetLineWidth(0.4)
	for _, row := range rows {
		r.room(18)
		r.pdf.Rect(margin, r.y, widths[0]+widths[1], 18, "D")
		for index, cell := range row {
			if index >= len(widths) {
				break
			}
			r.pdf.Text(margin+offset(widths, index)+6, r.y+12.5, r.out(cell))
		}
		r.y += 18
	}
	r.space(10)
}

func offset(widths []float64, index int) float64 {
	total := 0.0
	for i := 0; i < index && i < len(widths); i++ {
		total += widths[i]
	}
	return total
}

// image places one attached screenshot, scaled to the page.
func (r *report) image(item Evidence) {
	kind := strings.ToLower(strings.TrimPrefix(filepath.Ext(item.Path), "."))
	switch kind {
	case "jpg":
		kind = "jpeg"
	case "png", "jpeg":
	default:
		return
	}
	if _, err := os.Stat(item.Path); err != nil {
		return
	}

	width := pageWidth - 2*margin
	height := width * 0.62
	r.room(height + 20)
	r.pdf.SetFont("Helvetica", "B", 10)
	r.pdf.SetTextColor(20, 24, 33)
	r.pdf.Text(margin, r.y+10, r.out(titleOf(item.Name)))
	r.y += 16

	r.pdf.ImageOptions(item.Path, margin, r.y, width, 0,
		false, fpdf.ImageOptions{ImageType: kind, ReadDpi: true}, 0, "")
	if info := r.pdf.GetImageInfo(item.Path); info != nil && info.Width() > 0 {
		height = width * info.Height() / info.Width()
	}
	r.y += height + 10
}

func titleOf(name string) string {
	words := strings.Split(strings.ReplaceAll(name, "-", " "), " ")
	for index, word := range words {
		if word != "" {
			words[index] = strings.ToUpper(word[:1]) + word[1:]
		}
	}
	return strings.Join(words, " ")
}

// wrap breaks text at word boundaries so a long message does not run off the
// page — and never mid-word, which is what fpdf's own splitter does.
func (r *report) wrap(text, family string, size, width float64) []string {
	// The measurement has to be made in the font the text will be drawn in,
	// so wrap sets it rather than trusting the caller to have done it.
	r.pdf.SetFont(family, "", size)
	text = strings.TrimRight(text, " \t")
	if text == "" {
		return []string{""}
	}
	out := []string{}
	current := ""
	for _, word := range strings.Fields(text) {
		candidate := word
		if current != "" {
			candidate = current + " " + word
		}
		if r.pdf.GetStringWidth(r.out(candidate)) <= width || current == "" {
			current = candidate
			continue
		}
		out = append(out, current)
		current = word
	}
	return append(out, current)
}

// out is text the base-14 fonts can actually render.
func (r *report) out(text string) string {
	replaced := strings.NewReplacer(
		"—", "-", "–", "-", "·", "-", "→", "->", "’", "'", "‘", "'",
		"“", `"`, "”", `"`, "…", "...", "✅", "[x]", "⚠", "!", "🛑", "!",
	).Replace(text)
	if r.tr == nil {
		return replaced
	}
	return r.tr(replaced)
}

// --- images the Studio attaches ---------------------------------------------------------

// SaveEvidence stores an image a person attached to the run.
func (e Exporter) SaveEvidence(runID, name, dataURL string) (map[string]any, error) {
	run, err := e.run(runID)
	if err != nil {
		return nil, err
	}
	match := dataImage.FindStringSubmatch(dataURL)
	if match == nil {
		return nil, badRequest("Evidence must be a PNG, JPEG, or WebP data URL")
	}
	body, err := base64.StdEncoding.DecodeString(match[2])
	if err != nil {
		return nil, badRequest("Evidence image is not valid base64")
	}
	if len(body) > evidenceLimit {
		return nil, badRequest("Evidence image exceeds 12 MB")
	}

	extension := match[1]
	if extension == "jpeg" {
		extension = "jpg"
	}
	safe := strings.Trim(unsafeName.ReplaceAllString(name, "-"), "-")
	if len(safe) > 80 {
		safe = safe[:80]
	}
	if safe == "" {
		safe = "evidence"
	}

	folder := filepath.Join(filepath.Dir(run.StagedPath), "evidence")
	if err := os.MkdirAll(folder, 0o755); err != nil {
		return nil, err
	}
	path := filepath.Join(folder, safe+"."+extension)
	if err := os.WriteFile(path, body, 0o644); err != nil {
		return nil, err
	}
	if err := e.Store.AddEvidence(runID, safe, path); err != nil {
		return nil, err
	}
	return map[string]any{"name": safe, "path": path, "size": len(body)}, nil
}

// EvidenceFile is one stored image, for the Studio to display.
func (e Exporter) EvidenceFile(runID string, evidenceID int64) (string, []byte, error) {
	items, err := e.Store.Evidence(runID)
	if err != nil {
		return "", nil, err
	}
	for _, item := range items {
		if item.ID != evidenceID {
			continue
		}
		body, err := os.ReadFile(item.Path)
		if err != nil {
			return "", nil, notFound("Evidence file is no longer on disk")
		}
		return item.Path, body, nil
	}
	return "", nil, notFound("Evidence not found")
}
