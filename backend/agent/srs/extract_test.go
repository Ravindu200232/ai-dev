package srs

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"agentforge/agent/core"
)

func TestExtractPDFTextReadsARealDocument(t *testing.T) {
	// The one PDF we can be sure exists on any machine is the one we write.
	doc := composed(t)
	ApplyInternationalProfile(doc)
	path := filepath.Join(t.TempDir(), "srs.pdf")
	if err := PDF(doc, path, "Draft"); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}

	got := ExtractPDFText(data)
	if got.Engine == "none" {
		t.Fatalf("a PDF we wrote ourselves must be readable: %+v", got)
	}
	if got.Pages < 10 {
		t.Errorf("pages = %d", got.Pages)
	}
	if !strings.Contains(got.Text, "Corner Shop") {
		t.Errorf("the project name is missing from the extracted text:\n%s", truncate(got.Text, 300))
	}
	if len(got.pageTexts) != got.Pages {
		t.Errorf("per-page text is what tells a scan from a document: %d vs %d",
			len(got.pageTexts), got.Pages)
	}
}

func TestExtractPDFTextSurvivesRubbish(t *testing.T) {
	got := ExtractPDFText([]byte("this is not a PDF at all"))
	if got.Engine != "none" {
		t.Errorf("engine = %q", got.Engine)
	}
	if got.Error == "" {
		t.Error("a reader that cannot read must say why")
	}
	if got.Text != "" {
		t.Errorf("text = %q", got.Text)
	}
}

func TestReadPDFReportsUnreadableScans(t *testing.T) {
	svc := NewService(NewRepo(NewMemoryStore()), core.NewLLM(), core.Paths{})
	// Rubbish stands in for a scan: no text layer, nothing to rasterise.
	got := svc.ReadPDF(context.Background(), []byte("%PDF-1.4 not really"), "scan.pdf")
	if got.Text != "" {
		t.Errorf("text = %q", got.Text)
	}
	if got.Error == "" && got.Warning == "" {
		t.Error("an unread document must explain itself")
	}
}

func TestOCRUsability(t *testing.T) {
	if ocrIsUsable("a1 %% 3") {
		t.Error("noise is not a reading")
	}
	if !ocrIsUsable("Espresso 450 Cappuccino 520 Latte 540 Mocha 600") {
		t.Error("a menu is a reading")
	}
	if ocrIsUsable("") {
		t.Error("nothing is not a reading")
	}
}

func TestExtractImageTextWithoutTesseract(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	t.Setenv("TESSERACT_CMD", "")
	got := ExtractImageText(context.Background(), []byte("not an image"), "photo.png")
	if got.Engine != "none" || got.Error == "" {
		t.Errorf("without the binary it must say so: %+v", got)
	}
}

func TestTranscribeAudioWithoutWhisper(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	t.Setenv("AGENTFORGE_WHISPER", "")
	got := TranscribeAudio(context.Background(), []byte("some audio bytes"), "note.webm")
	if got.Text != "" {
		t.Errorf("text = %q", got.Text)
	}
	if !strings.Contains(got.Warning, "received") {
		t.Errorf("the customer must be told their recording was kept: %+v", got)
	}
	if got.Error != "" {
		t.Error("a missing engine is not an error the customer caused")
	}
}

func TestReadImageFallsBackToOCR(t *testing.T) {
	svc := NewService(NewRepo(NewMemoryStore()), core.NewLLM(), core.Paths{})
	t.Setenv("PATH", t.TempDir())
	t.Setenv("TESSERACT_CMD", "")
	// Neither the model nor OCR can answer here, which is the case that has
	// to produce a usable message rather than an empty success.
	got := svc.ReadImage(context.Background(), []byte("not an image"), "photo.png")
	if got.Text != "" || got.Error == "" {
		t.Errorf("read = %+v", got)
	}
}

func TestBuildBrief(t *testing.T) {
	brief := BuildBrief("  a hotel   booking site\r\n\r\n\r\n\r\nfor guests  ", []Source{
		{Mode: "pdf", Filename: "menu.pdf", Text: "Espresso 450\nLatte 540"},
		{Mode: "image", Filename: "logo.png", Meta: map[string]any{
			"purpose": "our logo", "url": "/uploads/logo.png"}},
		{Mode: "voice", Text: ""},
	})

	if !strings.HasPrefix(brief, "USER IDEA:\na hotel booking site\n\nfor guests") {
		t.Errorf("the idea is normalised and comes first:\n%s", brief)
	}
	if !strings.Contains(brief, "PDF SOURCE (menu.pdf):\nEspresso 450") {
		t.Errorf("the pdf source is missing:\n%s", brief)
	}
	if !strings.Contains(brief, "The person says this is for: our logo") {
		t.Errorf("the purpose is missing:\n%s", brief)
	}
	if !strings.Contains(brief, "`/uploads/logo.png`") {
		t.Errorf("a file already in the project must be named by its real path:\n%s", brief)
	}
	if strings.Contains(brief, "VOICE SOURCE") {
		t.Error("a source with nothing in it is not a source")
	}
	if strings.Count(brief, "---") != 2 {
		t.Errorf("sources are separated:\n%s", brief)
	}
}

func TestMimeOf(t *testing.T) {
	for name, want := range map[string]string{
		"a.png": "image/png", "b.JPG": "image/jpeg", "c.jpeg": "image/jpeg",
		"d.webp": "image/webp", "e.gif": "image/gif", "f": "image/png",
	} {
		if got := mimeOf(name); got != want {
			t.Errorf("mimeOf(%q) = %q, want %q", name, got, want)
		}
	}
}
