package deploy

import (
	"embed"
	"io"
	"os"
	"path/filepath"
	"strings"
	"text/template"
)

// Every file the agent writes is written through here, and every one is
// recorded: what it wrote, and what was there before it.
//
// That record is the whole basis of the promise the agent makes — that it
// touched nothing of the customer's it cannot put back. The staging copy makes
// that true; the record makes it checkable.

//go:embed assets
var assets embed.FS

// templates are the files the agent generates, kept as the files they are
// rather than as strings inside Go. The delimiters are <% %> because the
// output is GitHub Actions YAML, which uses ${{ }} for its own expressions,
// and shell, which uses {} for its own.
var templates = template.Must(
	template.New("assets").Delims("<%", "%>").ParseFS(assets, "assets/*"))

// asset is one generated file's text, with the values filled in.
func asset(name string, data any) (string, error) {
	var out strings.Builder
	if err := templates.ExecuteTemplate(&out, name, data); err != nil {
		return "", err
	}
	return out.String(), nil
}

// assetText is a file with nothing to fill in.
func assetText(name string) string {
	body, err := assets.ReadFile("assets/" + name)
	if err != nil {
		return ""
	}
	return string(body)
}

// writer puts generated files into the staging copy and records each one
// against what the customer's own folder has at the same path.
type writer struct {
	source string // the customer's project, only ever read
	staged string // the copy everything is written to
}

// write puts one file into the staging copy and returns its record.
func (w writer) write(relative, content, kind string) (Artifact, error) {
	relative = strings.TrimLeft(strings.ReplaceAll(relative, `\`, "/"), "/")
	target := filepath.Join(w.staged, filepath.FromSlash(relative))
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return Artifact{}, err
	}
	// Generated files are LF everywhere. A workflow or a shell script with
	// CRLF in it fails on Linux in ways that are hard to read.
	body := strings.ReplaceAll(content, "\r\n", "\n")
	if err := os.WriteFile(target, []byte(body), 0o644); err != nil {
		return Artifact{}, err
	}

	original := filepath.Join(w.source, filepath.FromSlash(relative))
	record := Artifact{
		Path:   relative,
		Kind:   kind,
		SHA256: SHA256([]byte(body)),
		Size:   int64(len(body)),
	}
	if info, err := os.Stat(original); err == nil {
		record.OriginalExists = true
		if info.Mode().IsRegular() {
			record.OriginalSHA256 = SHA256File(original)
		}
	}
	return record, nil
}

// read is a file in the staging copy.
func (w writer) read(relative string) (string, bool) {
	body, err := os.ReadFile(filepath.Join(w.staged, filepath.FromSlash(relative)))
	if err != nil {
		return "", false
	}
	return string(body), true
}

// has reports whether the staging copy holds a file.
func (w writer) has(relative string) bool {
	info, err := os.Stat(filepath.Join(w.staged, filepath.FromSlash(relative)))
	return err == nil && !info.IsDir()
}

// remove deletes a file from the staging copy. Only ever used on a file the
// agent itself wrote, which the record proves.
func (w writer) remove(relative string) {
	_ = os.Remove(filepath.Join(w.staged, filepath.FromSlash(relative)))
}

// Diff is what the run changed, as a patch a reviewer can read: the staged
// file against whatever the customer's own folder had at that path.
func Diff(source, staged string, records []Artifact) string {
	var out strings.Builder
	for _, record := range records {
		after, err := os.ReadFile(filepath.Join(staged, filepath.FromSlash(record.Path)))
		if err != nil {
			continue
		}
		before, err := os.ReadFile(filepath.Join(source, filepath.FromSlash(record.Path)))
		if err != nil {
			before = nil
		}
		out.WriteString(unified("a/"+record.Path, "b/"+record.Path, string(before), string(after)))
	}
	return out.String()
}

// unified is one file's diff. It is deliberately whole-file rather than
// minimal: the reviewer is reading a generated file they have never seen, so
// context costs nothing and a hunk header they cannot place costs a lot.
func unified(from, to, before, after string) string {
	if before == after {
		return ""
	}
	beforeLines := splitKeep(before)
	afterLines := splitKeep(after)

	var out strings.Builder
	out.WriteString("--- " + from + "\n")
	out.WriteString("+++ " + to + "\n")
	out.WriteString("@@ -1," + itoa(len(beforeLines)) + " +1," + itoa(len(afterLines)) + " @@\n")
	for _, line := range beforeLines {
		out.WriteString("-" + line)
	}
	for _, line := range afterLines {
		out.WriteString("+" + line)
	}
	return out.String()
}

// splitKeep splits into lines that keep their newline, so a file with no
// trailing newline still round-trips.
func splitKeep(body string) []string {
	if body == "" {
		return nil
	}
	out := []string{}
	for {
		at := strings.IndexByte(body, '\n')
		if at < 0 {
			return append(out, body)
		}
		out = append(out, body[:at+1])
		body = body[at+1:]
		if body == "" {
			return out
		}
	}
}

// copyInto is used by the export path, where a file is taken out of the
// staging copy as it is.
func copyInto(from string, to io.Writer) error {
	body, err := os.ReadFile(from)
	if err != nil {
		return err
	}
	_, err = to.Write(body)
	return err
}
