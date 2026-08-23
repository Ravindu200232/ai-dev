package srs

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Where an SRS lands on disk, and how its diagrams get there.
//
// The native renderer is canonical: it produces the SVG the Studio shows and
// the vectors the PDF draws, from the same shape list. mermaid-cli is only a
// fallback for a kind the native renderer cannot draw, and it is never
// required — a machine with no Node still produces a complete specification.

// StorageRoot is where SRS artifacts are staged, alongside the generated apps.
func (s *Service) StorageRoot() string {
	if s.Storage != "" {
		return s.Storage
	}
	return filepath.Join(s.Paths.Projects, ".srs")
}

func (s *Service) projectDir(projectID string) string {
	return filepath.Join(s.StorageRoot(), projectID)
}

func (s *Service) diagramsDir(projectID string) string {
	return filepath.Join(s.projectDir(projectID), "diagrams")
}

// SRSPath is where one version of the document is kept. The latest is also
// written under a stable name, because the builder reads that one.
func (s *Service) SRSPath(projectID, version string) string {
	return filepath.Join(s.projectDir(projectID), "srs_v"+version+".json")
}

func (s *Service) LatestSRSPath(projectID string) string {
	return filepath.Join(s.projectDir(projectID), "srs_latest.json")
}

// PDFPath is where the printed specification is written.
func (s *Service) PDFPath(projectID, version string) string {
	if version == "" {
		return filepath.Join(s.projectDir(projectID), "SRS_latest.pdf")
	}
	return filepath.Join(s.projectDir(projectID), "SRS_v"+version+".pdf")
}

// SaveSRS writes the document under its version and as the latest.
func (s *Service) SaveSRS(projectID, version string, doc *Document) error {
	envelope := Envelope{Document: *doc}
	if err := writeJSON(s.SRSPath(projectID, version), envelope); err != nil {
		return err
	}
	return writeJSON(s.LatestSRSPath(projectID), envelope)
}

// SnapshotDiagrams keeps this version's diagrams before the next revision
// overwrites them, and points the stored rows at the copies.
func (s *Service) SnapshotDiagrams(projectID, version string, diagrams []Diagram) []Diagram {
	source := s.diagramsDir(projectID)
	target := filepath.Join(source, "v"+version)
	if err := os.MkdirAll(target, 0o755); err != nil {
		return diagrams
	}
	moved := map[string]string{}
	entries, err := os.ReadDir(source)
	if err != nil {
		return diagrams
	}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !(strings.HasSuffix(name, ".mmd") || strings.HasSuffix(name, ".svg")) {
			continue
		}
		from, to := filepath.Join(source, name), filepath.Join(target, name)
		body, err := os.ReadFile(from)
		if err != nil {
			continue
		}
		if err := os.WriteFile(to, body, 0o644); err == nil {
			moved[from] = to
		}
	}
	out := make([]Diagram, len(diagrams))
	copy(out, diagrams)
	for i := range out {
		if to, ok := moved[out[i].MmdPath]; ok {
			out[i].MmdPath = to
		}
		if to, ok := moved[out[i].SvgPath]; ok {
			out[i].SvgPath = to
		}
	}
	return out
}

// RenderDiagrams writes every diagram's source and drawing to disk.
func (s *Service) RenderDiagrams(ctx context.Context, projectID string, doc *Document,
	diagrams []Diagram, onError func(string)) []Diagram {

	dir := s.diagramsDir(projectID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		if onError != nil {
			onError("could not create the diagram directory: " + err.Error())
		}
		return diagrams
	}

	out := make([]Diagram, len(diagrams))
	copy(out, diagrams)
	for i := range out {
		d := &out[i]
		mmd := filepath.Join(dir, d.Kind+".mmd")
		if err := os.WriteFile(mmd, []byte(d.Source), 0o644); err == nil {
			d.MmdPath = mmd
		}

		if drawing := Render(d.Kind, doc, 720); drawing != nil {
			svg := filepath.Join(dir, d.Kind+".svg")
			if err := os.WriteFile(svg, []byte(drawing.SVG()), 0o644); err == nil {
				d.SvgPath, d.Format, d.RenderedBy = svg, "svg+mermaid-source", "native_vector"
				continue
			} else if onError != nil {
				onError(d.Kind + ".svg could not be written: " + err.Error())
			}
		}

		// Nothing native for this kind, so mermaid-cli renders it if it is
		// installed. When it is not, the source alone is what the Studio gets.
		renderer := mermaidRenderer()
		if len(renderer) == 0 {
			continue
		}
		for _, ext := range []string{"svg", "png"} {
			path := filepath.Join(dir, d.Kind+"."+ext)
			scale := "1"
			if ext == "png" {
				scale = "2"
			}
			args := append(append([]string{}, renderer[1:]...),
				"-i", mmd, "-o", path, "-b", "white", "-s", scale)
			run, cancel := context.WithTimeout(ctx, 3*time.Minute)
			err := exec.CommandContext(run, renderer[0], args...).Run()
			cancel()
			if err != nil {
				if onError != nil {
					onError(d.Kind + "." + ext + " render failed: " + err.Error())
				}
				break
			}
			if ext == "svg" {
				d.SvgPath = path
			} else {
				d.PngPath = path
			}
			if d.RenderedBy == "" {
				d.RenderedBy = "mermaid_cli"
			}
		}
	}
	return out
}

var (
	rendererOnce sync.Once
	rendererCmd  []string
)

// mermaidRenderer is the first mermaid-cli invocation on this machine that
// actually runs, or nothing. It is looked for once: the answer cannot change
// while the process is up, and probing it per diagram cost seconds.
func mermaidRenderer() []string {
	rendererOnce.Do(func() {
		name := strings.TrimSpace(os.Getenv("AGENTFORGE_MERMAID_CLI"))
		switch strings.ToLower(name) {
		case "off", "none", "disabled":
			return
		}
		var candidates [][]string
		for _, binary := range []string{name, "mmdc"} {
			if binary == "" {
				continue
			}
			if path, err := exec.LookPath(binary); err == nil {
				candidates = append(candidates, []string{path})
			}
		}
		if npx, err := exec.LookPath("npx"); err == nil {
			candidates = append(candidates, []string{npx, "-y", "@mermaid-js/mermaid-cli"})
		}
		for _, candidate := range candidates {
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			err := exec.CommandContext(ctx, candidate[0],
				append(append([]string{}, candidate[1:]...), "--version")...).Run()
			cancel()
			if err == nil {
				rendererCmd = candidate
				return
			}
		}
	})
	return rendererCmd
}

func writeJSON(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, raw, 0o644)
}
