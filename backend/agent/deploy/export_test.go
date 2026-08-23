package deploy

import (
	"archive/zip"
	"bytes"
	"encoding/base64"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/go-pdf/fpdf"
)

// exported builds a run with artifacts, events and a snapshot on it, which is
// what an evidence bundle is made of.
func exported(t *testing.T) (Exporter, *Run) {
	t.Helper()
	deployer, run := deployable(t)
	store := deployer.Store

	for _, event := range []Event{
		{Type: EventStep, Stage: "intake", Status: StatusComplete, Message: "Detected 1 Next.js service(s)"},
		{Type: EventStep, Stage: "deploy", Status: StatusComplete,
			Message: "connected to mongodb+srv://user:hunter2@cluster/db"},
	} {
		if err := store.AddEvent(run.ID, event); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := store.Update(run.ID, map[string]any{
		"monitor": map[string]any{"captured_at": NowISO(), "readiness": map[string]any{"score": 100}},
	}); err != nil {
		t.Fatal(err)
	}
	after, _ := store.GetRun(run.ID)
	return Exporter{Store: store}, after
}

func TestZipCarriesEverythingAndNoSecrets(t *testing.T) {
	exporter, run := exported(t)
	body, err := exporter.Zip(run.ID)
	if err != nil {
		t.Fatal(err)
	}

	archive, err := zip.NewReader(bytes.NewReader(body), int64(len(body)))
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]string{}
	for _, file := range archive.File {
		reader, err := file.Open()
		if err != nil {
			t.Fatal(err)
		}
		content, _ := io.ReadAll(reader)
		reader.Close()
		names[file.Name] = string(content)
	}

	for _, want := range []string{
		"artifacts/.github/workflows/ci.yml",
		"artifacts/deployment-manifest.json",
		"evidence/run.json",
		"evidence/events.json",
	} {
		if _, present := names[want]; !present {
			t.Errorf("%s is missing from the bundle", want)
		}
	}
	if !strings.Contains(names["evidence/events.json"], "Detected 1 Next.js service(s)") {
		t.Error("the console log is not in the bundle")
	}
	for name, content := range names {
		if strings.Contains(content, "hunter2") {
			t.Errorf("%s leaked a secret into the bundle", name)
		}
	}
}

func TestPDFIsAReadableDocument(t *testing.T) {
	exporter, run := exported(t)
	body, err := exporter.PDF(run.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(body) < 1000 || !bytes.HasPrefix(body, []byte("%PDF")) {
		t.Fatalf("the report is not a PDF: %d bytes", len(body))
	}
	// A PDF this size holds the whole story; anything much smaller is an
	// empty document with a title on it.
	if len(body) > 4<<20 {
		t.Errorf("the report is %d bytes", len(body))
	}

	if _, err := exporter.PDF(NewRunID()); err == nil {
		t.Error("a run that does not exist has no report")
	}
	if _, err := exporter.Zip(NewRunID()); err == nil {
		t.Error("a run that does not exist has no bundle")
	}
}

func TestSaveEvidence(t *testing.T) {
	exporter, run := exported(t)
	// A one-pixel PNG is enough to prove the path, the record and the limit.
	pixel, err := base64.StdEncoding.DecodeString(
		"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
	if err != nil {
		t.Fatal(err)
	}
	url := "data:image/png;base64," + base64.StdEncoding.EncodeToString(pixel)

	saved, err := exporter.SaveEvidence(run.ID, "Live site!! ", url)
	if err != nil {
		t.Fatal(err)
	}
	if saved["name"] != "Live-site" {
		t.Errorf("name = %v", saved["name"])
	}
	path := text(saved["path"])
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("the image was not written: %v", err)
	}
	if filepath.Ext(path) != ".png" {
		t.Errorf("path = %q", path)
	}

	items, err := exporter.Store.Evidence(run.ID)
	if err != nil || len(items) != 1 {
		t.Fatalf("evidence = %+v %v", items, err)
	}
	if _, body, err := exporter.EvidenceFile(run.ID, items[0].ID); err != nil || len(body) == 0 {
		t.Errorf("the file could not be read back: %v", err)
	}
	if _, _, err := exporter.EvidenceFile(run.ID, 999); err == nil {
		t.Error("an id nobody stored is not evidence")
	}

	// It reaches the bundle and the report.
	body, err := exporter.Zip(run.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(body, []byte("Live-site.png")) {
		t.Error("the attached image is not in the bundle")
	}
	if _, err := exporter.PDF(run.ID); err != nil {
		t.Fatalf("the report could not place the image: %v", err)
	}
}

func TestSaveEvidenceRefusesWhatItShould(t *testing.T) {
	exporter, run := exported(t)
	for _, bad := range []string{
		"", "https://example.com/shot.png", "data:text/html;base64,PGI+",
		"data:image/png;base64,not base64 at all",
	} {
		if _, err := exporter.SaveEvidence(run.ID, "shot", bad); err == nil {
			t.Errorf("%q was accepted", bad)
		}
	}

	oversized := "data:image/png;base64," +
		base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0}, evidenceLimit+1))
	if _, err := exporter.SaveEvidence(run.ID, "huge", oversized); err == nil ||
		!strings.Contains(err.Error(), "12 MB") {
		t.Errorf("err = %v", err)
	}
}

func TestReportWrapsWithoutSplittingWords(t *testing.T) {
	r := &report{pdf: newTestPDF()}
	lines := r.wrap(strings.Repeat("deployment ", 40), "Helvetica", 9, 300)
	if len(lines) < 2 {
		t.Fatalf("lines = %d", len(lines))
	}
	for _, line := range lines {
		for _, word := range strings.Fields(line) {
			if word != "deployment" {
				t.Errorf("a word was broken: %q", line)
			}
		}
	}
	if got := r.wrap("", "Helvetica", 9, 300); len(got) != 1 || got[0] != "" {
		t.Errorf("empty = %q", got)
	}
}

func TestReportFallsBackToWhatTheFontHas(t *testing.T) {
	r := &report{pdf: newTestPDF()}
	if got := r.out("live — ready → yes ✅"); strings.ContainsAny(got, "—→✅") {
		t.Errorf("out = %q", got)
	}
}

func TestTitleOf(t *testing.T) {
	if got := titleOf("auto-overview"); got != "Auto Overview" {
		t.Errorf("titleOf = %q", got)
	}
}

// newTestPDF is a document to measure text against; nothing is written to it.
func newTestPDF() *fpdf.Fpdf {
	document := fpdf.New("P", "pt", "A4", "")
	document.AddPage()
	return document
}
