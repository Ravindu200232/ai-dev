package srs

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/dslipak/pdf"
)

// Reading what the customer attached: a PDF, a photo of a whiteboard, a
// receipt, something they said out loud.
//
// Every reader here has the same shape, and it is deliberate: it returns what
// it managed to read and says plainly why it could not read more. None of them
// ever fails the request. A customer who attached a scan of a handwritten menu
// and gets "your file was received, install Tesseract to have it read" is
// better served than one whose upload returns a 500.

// Extraction is what one reader got, and what stopped it getting more.
type Extraction struct {
	Text             string `json:"text"`
	Engine           string `json:"engine"`
	Pages            int    `json:"pages,omitempty"`
	Language         string `json:"language,omitempty"`
	PagesReadByModel int    `json:"pages_read_by_model,omitempty"`
	UnreadPages      []int  `json:"unread_pages,omitempty"`
	Warning          string `json:"warning,omitempty"`
	Error            string `json:"error,omitempty"`

	pageTexts []string
}

// How much of an image has to be readable before OCR is worth quoting.
const (
	minOCRWords   = 4
	minOCRLetters = 12

	maxModelPages = 12
	renderDPI     = 144
)

var letterRune = regexp.MustCompile(`[^\W\d_]`)
var letterWord = regexp.MustCompile(`[^\W\d_]{2,}`)

// ocrIsUsable is whether a reading is text or noise. A scan that yields six
// stray characters has not been read, and quoting it as the customer's own
// words would put nonsense into their specification.
func ocrIsUsable(text string) bool {
	return len(letterRune.FindAllString(text, -1)) >= minOCRLetters &&
		len(letterWord.FindAllString(text, -1)) >= minOCRWords
}

// --- PDFs -----------------------------------------------------------------------------

// ExtractPDFText reads the text layer, first with the library and then with
// poppler if it is installed. A PDF with no text layer at all comes back empty
// and is handled by ReadPDF.
func ExtractPDFText(data []byte) Extraction {
	if pages, err := pdfPageTexts(data); err == nil && len(pages) > 0 {
		joined := strings.TrimSpace(strings.Join(nonEmptyLines(pages), "\n\n"))
		if joined != "" {
			return Extraction{Text: joined, Engine: "pdf", Pages: len(pages), pageTexts: pages}
		}
		// A real page count with no text is a scan; say so rather than
		// pretending the document is empty.
		return Extraction{Engine: "none", Pages: len(pages), pageTexts: pages}
	}

	if text, pages, err := popplerText(data); err == nil && strings.TrimSpace(strings.Join(text, "")) != "" {
		return Extraction{Text: strings.TrimSpace(strings.Join(nonEmptyLines(text), "\n\n")),
			Engine: "pdftotext", Pages: pages, pageTexts: text}
	}
	return Extraction{Engine: "none",
		Error: "The PDF's text could not be read. A scanned document needs " +
			"Tesseract or a vision model to be read at all."}
}

// pdfPageTexts reads each page separately, so a scan can be told apart page by
// page. The library panics on some malformed files, which is a read failure
// rather than a reason to lose the upload.
func pdfPageTexts(data []byte) (pages []string, err error) {
	defer func() {
		if r := recover(); r != nil {
			pages, err = nil, fmt.Errorf("the PDF could not be parsed: %v", r)
		}
	}()
	reader, err := pdf.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, err
	}
	total := reader.NumPage()
	for i := 1; i <= total; i++ {
		page := reader.Page(i)
		if page.V.IsNull() {
			pages = append(pages, "")
			continue
		}
		text, err := page.GetPlainText(nil)
		if err != nil {
			pages = append(pages, "")
			continue
		}
		pages = append(pages, strings.TrimSpace(text))
	}
	return pages, nil
}

// popplerText is the second engine: the same toolkit that rasterises pages.
func popplerText(data []byte) ([]string, int, error) {
	binary, err := exec.LookPath("pdftotext")
	if err != nil {
		return nil, 0, err
	}
	dir, err := os.MkdirTemp("", "srs-pdf-")
	if err != nil {
		return nil, 0, err
	}
	defer os.RemoveAll(dir)

	in := filepath.Join(dir, "in.pdf")
	if err := os.WriteFile(in, data, 0o600); err != nil {
		return nil, 0, err
	}
	out := filepath.Join(dir, "out.txt")
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	if err := exec.CommandContext(ctx, binary, in, out).Run(); err != nil {
		return nil, 0, err
	}
	body, err := os.ReadFile(out)
	if err != nil {
		return nil, 0, err
	}
	// Poppler separates pages with a form feed.
	pages := strings.Split(string(body), "\f")
	for i := range pages {
		pages[i] = strings.TrimSpace(pages[i])
	}
	for len(pages) > 0 && pages[len(pages)-1] == "" {
		pages = pages[:len(pages)-1]
	}
	return pages, len(pages), nil
}

// renderPDFPages rasterises the named pages so a model can look at them. It
// needs poppler; without it a scanned PDF simply stays unread, and ReadPDF
// says so.
func renderPDFPages(ctx context.Context, data []byte, indexes []int) map[int][]byte {
	binary, err := exec.LookPath("pdftoppm")
	if err != nil || len(indexes) == 0 {
		return nil
	}
	dir, err := os.MkdirTemp("", "srs-raster-")
	if err != nil {
		return nil
	}
	defer os.RemoveAll(dir)

	in := filepath.Join(dir, "in.pdf")
	if err := os.WriteFile(in, data, 0o600); err != nil {
		return nil
	}
	out := map[int][]byte{}
	for _, index := range indexes {
		page := itoa(index + 1)
		prefix := filepath.Join(dir, "p"+page)
		run, cancel := context.WithTimeout(ctx, time.Minute)
		err := exec.CommandContext(run, binary, "-png", "-r", itoa(renderDPI),
			"-f", page, "-l", page, in, prefix).Run()
		cancel()
		if err != nil {
			continue
		}
		matches, _ := filepath.Glob(prefix + "*.png")
		if len(matches) == 0 {
			continue
		}
		if body, err := os.ReadFile(matches[0]); err == nil {
			out[index] = body
		}
	}
	return out
}

// ReadPDF extracts what the document says, showing the model any page the text
// layer could not reach.
func (s *Service) ReadPDF(ctx context.Context, data []byte, filename string) Extraction {
	found := ExtractPDFText(data)
	texts := found.pageTexts
	if len(texts) == 0 && found.Text != "" {
		texts = []string{found.Text}
	}
	total := found.Pages
	if total == 0 {
		total = len(texts)
	}

	var blank []int
	for i := 0; i < total; i++ {
		own := ""
		if i < len(texts) {
			own = texts[i]
		}
		if !ocrIsUsable(own) {
			blank = append(blank, i)
		}
	}
	if len(blank) == 0 {
		found.pageTexts = nil
		return found
	}

	lookAt := blank
	if len(lookAt) > maxModelPages {
		lookAt = lookAt[:maxModelPages]
	}
	images := renderPDFPages(ctx, data, lookAt)
	if len(images) == 0 {
		found.pageTexts = nil
		found.Warning = firstNonEmpty(found.Error,
			"Some pages are scans and could not be read — install Poppler "+
				"(pdftoppm) to have the model read them.")
		found.Error = ""
		return found
	}

	// Pages are read one at a time on purpose: a local vision model given
	// three pages at once takes longer than the three sequentially and can
	// run the machine out of memory.
	byPage := map[int]string{}
	var readErr string
	for _, index := range sortedKeys(images) {
		seen := s.DescribeImage(ctx, images[index], filename+" page "+itoa(index+1))
		if text := strings.TrimSpace(seen.Text); text != "" {
			byPage[index] = text
			continue
		}
		if readErr == "" {
			readErr = seen.Error
		}
	}

	wanted := map[int]bool{}
	for _, i := range blank {
		wanted[i] = true
	}
	var merged []string
	var unread []int
	for i := 0; i < total; i++ {
		own := ""
		if i < len(texts) {
			own = strings.TrimSpace(texts[i])
		}
		read := byPage[i]
		switch {
		case read != "":
			line := "[page " + itoa(i+1) + "]\n" + read
			if own != "" {
				line += "\n" + own
			}
			merged = append(merged, line)
		case own != "":
			merged = append(merged, "[page "+itoa(i+1)+"]\n"+own)
		case wanted[i]:
			unread = append(unread, i+1)
		}
	}

	text := strings.TrimSpace(strings.Join(merged, "\n\n"))
	if text == "" {
		return Extraction{Engine: "none", Pages: total,
			Error: firstNonEmpty(readErr, "The PDF could not be read.")}
	}

	engine := "vision"
	if found.Text != "" {
		engine = found.Engine + "+vision"
	}
	out := Extraction{Text: text, Pages: total, Engine: engine,
		PagesReadByModel: len(byPage)}
	if len(unread) == 0 {
		return out
	}

	shown := unread
	more := ""
	if len(shown) > 10 {
		more = " and " + itoa(len(shown)-10) + " more"
		shown = shown[:10]
	}
	why := firstNonEmpty(readErr, "the model returned nothing for them")
	if len(blank) > len(lookAt) {
		why = "only the first " + itoa(maxModelPages) + " unreadable pages are sent to the model"
	}
	var numbers []string
	for _, p := range shown {
		numbers = append(numbers, itoa(p))
	}
	out.UnreadPages = unread
	out.Warning = itoa(len(unread)) + " of " + itoa(total) + " pages could not be read (" +
		strings.Join(numbers, ", ") + more + ") — " + why + "."
	return out
}

func sortedKeys(m map[int][]byte) []int {
	out := make([]int, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Ints(out)
	return out
}

func nonEmptyLines(values []string) []string {
	out := make([]string, 0, len(values))
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			out = append(out, v)
		}
	}
	return out
}

// --- images ---------------------------------------------------------------------------

// tesseractCandidates are where the binary lives when it is not on PATH.
var tesseractCandidates = []string{
	`C:\Program Files\Tesseract-OCR\tesseract.exe`,
	`C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`,
	"/usr/bin/tesseract", "/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract",
}

// tesseractCommand is where Tesseract actually is, or "".
func tesseractCommand() string {
	if override := strings.TrimSpace(os.Getenv("TESSERACT_CMD")); override != "" {
		if _, err := os.Stat(override); err == nil {
			return override
		}
		if path, err := exec.LookPath(override); err == nil {
			return path
		}
	}
	if path, err := exec.LookPath("tesseract"); err == nil {
		return path
	}
	for _, candidate := range tesseractCandidates {
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	return ""
}

// ExtractImageText runs OCR. The Python wrapper only ever shelled to this same
// binary, so nothing is lost by calling it directly.
func ExtractImageText(ctx context.Context, data []byte, filename string) Extraction {
	binary := tesseractCommand()
	if binary == "" {
		return Extraction{Engine: "none",
			Error: "OCR is not available. Install Tesseract to have images read."}
	}
	dir, err := os.MkdirTemp("", "srs-ocr-")
	if err != nil {
		return Extraction{Engine: "none", Error: "OCR could not start: " + err.Error()}
	}
	defer os.RemoveAll(dir)

	in := filepath.Join(dir, "image"+filepath.Ext(filename))
	if err := os.WriteFile(in, data, 0o600); err != nil {
		return Extraction{Engine: "none", Error: "OCR could not start: " + err.Error()}
	}
	run, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	// The trailing "-" tells Tesseract to write to standard output.
	out, err := exec.CommandContext(run, binary, in, "-").Output()
	if err != nil {
		return Extraction{Engine: "none", Error: "OCR failed: " + err.Error()}
	}
	return Extraction{Text: strings.TrimSpace(string(out)), Engine: "tesseract"}
}

const visionSystem = `You are reading an image a non-technical customer attached while describing an app they want built. Reply in plain text, no markdown.
1. Transcribe every piece of text in the image, exactly, keeping the line order and any prices, codes or units.
2. Then, in one or two sentences, say what the image shows and what it tells you about their business.
If the image has no text at all, skip straight to the description.`

// DescribeImage asks the model to read the image.
func (s *Service) DescribeImage(ctx context.Context, data []byte, filename string) Extraction {
	text, err := s.LLM.Vision(ctx, roleDesign, visionSystem,
		"The attached image is called "+filename+". Read it now.",
		mimeOf(filename), data)
	if err != nil {
		return Extraction{Engine: "none", Error: "Vision read failed. (" + err.Error() + ")"}
	}
	return Extraction{Text: strings.TrimSpace(text), Engine: "vision"}
}

// ReadImage shows the image to the model and keeps OCR's exact reading beside
// it. The model says what the picture means; OCR gets the prices right.
func (s *Service) ReadImage(ctx context.Context, data []byte, filename string) Extraction {
	seen := s.DescribeImage(ctx, data, filename)
	ocr := ExtractImageText(ctx, data, filename)
	described, read := strings.TrimSpace(seen.Text), strings.TrimSpace(ocr.Text)

	if described != "" {
		if ocrIsUsable(read) {
			return Extraction{Engine: "vision+tesseract",
				Text: described + "\n\nText read from the image:\n" + read}
		}
		return Extraction{Text: described, Engine: "vision"}
	}
	if read != "" {
		ocr.Warning = "Read by OCR only — the vision model did not answer."
		return ocr
	}
	return Extraction{Engine: "none",
		Error: firstNonEmpty(seen.Error, ocr.Error, "Image could not be read.")}
}

func mimeOf(filename string) string {
	switch strings.ToLower(filepath.Ext(filename)) {
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".webp":
		return "image/webp"
	case ".gif":
		return "image/gif"
	}
	return "image/png"
}

// --- speech ---------------------------------------------------------------------------

// TranscribeAudio turns a recording into text with whatever Whisper build is
// installed. With none, it says so and keeps the recording — it never fails
// the upload, because the customer has still told us something.
func TranscribeAudio(ctx context.Context, data []byte, filename string) Extraction {
	binary := ""
	for _, name := range []string{
		strings.TrimSpace(os.Getenv("AGENTFORGE_WHISPER")), "whisper-cli", "whisper",
	} {
		if name == "" {
			continue
		}
		if path, err := exec.LookPath(name); err == nil {
			binary = path
			break
		}
	}
	if binary == "" {
		return Extraction{Engine: "none",
			Warning: "Voice transcription is not configured. Install a Whisper " +
				"command-line build to enable it. Your audio was received (" +
				itoa(len(data)) + " bytes) and stored."}
	}

	dir, err := os.MkdirTemp("", "srs-audio-")
	if err != nil {
		return Extraction{Engine: "none", Error: "Transcription could not start: " + err.Error()}
	}
	defer os.RemoveAll(dir)

	name := filepath.Base(filename)
	if name == "" || name == "." {
		name = "audio.webm"
	}
	in := filepath.Join(dir, name)
	if err := os.WriteFile(in, data, 0o600); err != nil {
		return Extraction{Engine: "none", Error: "Transcription could not start: " + err.Error()}
	}

	run, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()
	err = exec.CommandContext(run, binary, in,
		"--output_format", "json", "--output_dir", dir, "--model", "base").Run()
	if err != nil {
		return Extraction{Engine: "whisper",
			Error: "Transcription failed. Your audio was received (" + itoa(len(data)) +
				" bytes) and stored. (" + err.Error() + ")"}
	}
	return readWhisperOutput(dir, len(data))
}

// readWhisperOutput reads whichever transcript file the build wrote.
func readWhisperOutput(dir string, size int) Extraction {
	if matches, _ := filepath.Glob(filepath.Join(dir, "*.json")); len(matches) > 0 {
		if body, err := os.ReadFile(matches[0]); err == nil {
			var out struct {
				Text     string `json:"text"`
				Language string `json:"language"`
			}
			if err := json.Unmarshal(body, &out); err == nil && strings.TrimSpace(out.Text) != "" {
				return Extraction{Text: strings.TrimSpace(out.Text),
					Engine: "whisper", Language: out.Language}
			}
		}
	}
	if matches, _ := filepath.Glob(filepath.Join(dir, "*.txt")); len(matches) > 0 {
		if body, err := os.ReadFile(matches[0]); err == nil && strings.TrimSpace(string(body)) != "" {
			return Extraction{Text: strings.TrimSpace(string(body)), Engine: "whisper"}
		}
	}
	return Extraction{Engine: "whisper",
		Warning: "The transcript came back empty. Your audio was received (" +
			itoa(size) + " bytes) and stored."}
}

// --- the brief ---------------------------------------------------------------------------

var spacesRun = regexp.MustCompile(`[ \t]+`)
var blankRun = regexp.MustCompile(`\n{3,}`)

func normalise(text string) string {
	text = strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")
	return strings.TrimSpace(blankRun.ReplaceAllString(spacesRun.ReplaceAllString(text, " "), "\n\n"))
}

// BuildBrief combines the typed idea with everything read out of what they
// attached, in the order they gave it.
func BuildBrief(rawIdea string, sources []Source) string {
	var parts []string
	if idea := normalise(rawIdea); idea != "" {
		parts = append(parts, "USER IDEA:\n"+idea)
	}
	for _, src := range sources {
		text := normalise(src.Text)
		purpose := normalise(firstText(src.Meta["purpose"]))
		url := strings.TrimSpace(firstText(src.Meta["url"]))
		if text == "" && purpose == "" && url == "" {
			continue
		}
		header := strings.ToUpper(firstNonEmpty(src.Mode, "source")) + " SOURCE"
		if src.Filename != "" {
			header += " (" + src.Filename + ")"
		}
		lines := []string{header + ":"}
		// What it is for comes before what it looks like.
		if purpose != "" {
			lines = append(lines, "The person says this is for: "+purpose)
		}
		if url != "" {
			lines = append(lines, "It is already in the project and served at `"+url+
				"` — use that exact path, do not invent another and do not ask "+
				"for it to be generated.")
		}
		if text != "" {
			lines = append(lines, text)
		}
		parts = append(parts, strings.Join(lines, "\n"))
	}
	return strings.TrimSpace(strings.Join(parts, "\n\n---\n\n"))
}
