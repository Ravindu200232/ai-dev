package app

import (
	"context"
	"fmt"
	"math/rand"
	"strings"

	"agentforge/agent/core"
)

// Before a build starts the Studio offers a few whole-app visual directions to
// choose from. Each one is a single self-contained HTML page mocking the app's
// main screen — enough to judge a look without building anything.

const themeCount = 4

// direction is one visual stance the model is asked to commit to.
type direction struct {
	Name  string
	Brief string
}

var directions = []direction{
	{"Quiet Utility", "Near-monochrome, generous whitespace, one restrained accent. " +
		"Type does the work; borders are hairlines. Nothing decorative."},
	{"Warm Editorial", "Cream ground, deep ink text, a serif for headings against a " +
		"clean sans for the rest. Generous line height, wide measure."},
	{"Confident Product", "Saturated brand colour used sparingly and deliberately, " +
		"solid filled buttons, soft rounded cards on a light grey ground."},
	{"Dense Console", "Compact rows, tabular numerals, mono for identifiers, a dark " +
		"surface with a bright single accent. Built for people who look at it all day."},
	{"Soft Modern", "Rounded corners, layered soft shadows, pastel tints for status, " +
		"a light airy ground and plenty of padding."},
	{"Structured Grid", "Visible column structure, strong horizontal rules, flat " +
		"surfaces, a single primary colour and a lot of alignment discipline."},
}

const themeSystem = `You design one screen of a web application as a single
self-contained HTML page, for someone choosing a visual direction.

Rules:
- One file. All CSS in a <style> block. No external stylesheets, fonts, scripts
  or images. No JavaScript beyond what a static mock needs, which is none.
- Show the application's real main screen with realistic sample content — real
  looking names, amounts and dates, not "Lorem ipsum" and not "Item 1".
- Include the app's actual navigation and the main table, list or board the
  brief describes.
- Commit fully to the visual direction you were given. Two directions must not
  look like the same design with different colours.
- It must look finished: aligned, consistent spacing, real states.

Answer with the HTML document only, starting at <!doctype html>. No prose, no
markdown fences.`

// Theme is one direction, rendered.
type Theme struct {
	ID    string `json:"id"`
	Name  string `json:"name"`
	Blurb string `json:"blurb"`
	HTML  string `json:"html"`
}

// ThemeRequest is what the Studio asks for.
type ThemeRequest struct {
	Prompt string `json:"prompt"`
	SRSID  string `json:"srs_id"`
	Model  string `json:"model"`
}

// Themes draws several directions of the same app, one after another. They are
// deliberately sequential: they share one local model, and running them at once
// makes every one of them slower.
func Themes(ctx context.Context, llm *core.LLM, brief string, req ThemeRequest) (map[string]any, error) {
	brief = strings.TrimSpace(brief)
	if brief == "" {
		brief = strings.TrimSpace(req.Prompt)
	}
	if brief == "" {
		return nil, fmt.Errorf("describe the app, or approve a specification first")
	}

	picked := pickDirections(themeCount)
	themes := make([]Theme, 0, len(picked))
	var lastErr error

	for i, d := range picked {
		if err := ctx.Err(); err != nil {
			break
		}
		html, err := llm.Text(ctx, core.RoleDesign, themeSystem, themePrompt(brief, d, i+1, len(picked)))
		if err != nil {
			lastErr = err
			continue
		}
		html = cleanHTML(html)
		if html == "" {
			lastErr = fmt.Errorf("%s came back without a page", d.Name)
			continue
		}
		themes = append(themes, Theme{
			ID: fmt.Sprintf("d%d", i+1), Name: d.Name, Blurb: d.Brief, HTML: html,
		})
	}

	if len(themes) == 0 {
		if lastErr != nil {
			return nil, fmt.Errorf("no design could be drawn: %w", lastErr)
		}
		return nil, fmt.Errorf("no design could be drawn")
	}
	return map[string]any{"themes": themes, "page": "Main screen"}, nil
}

// pickDirections takes n distinct directions at random, so two runs of the same
// idea do not offer the same four looks.
func pickDirections(n int) []direction {
	if n > len(directions) {
		n = len(directions)
	}
	order := rand.Perm(len(directions))[:n]
	out := make([]direction, 0, n)
	for _, i := range order {
		out = append(out, directions[i])
	}
	return out
}

func themePrompt(brief string, d direction, index, total int) string {
	var b strings.Builder
	fmt.Fprintf(&b, "THE APPLICATION\n%s\n\n", truncate(brief, 6000))
	fmt.Fprintf(&b, "VISUAL DIRECTION %d OF %d: %s\n%s\n\n", index, total, d.Name, d.Brief)
	b.WriteString("Draw this application's main screen in that direction, as one " +
		"complete HTML document.\n")
	return b.String()
}

// cleanHTML strips whatever preamble the model put in front of the document.
func cleanHTML(text string) string {
	text = strings.TrimSpace(text)
	if i := strings.Index(text, "```"); i >= 0 {
		rest := text[i+3:]
		if nl := strings.IndexByte(rest, '\n'); nl >= 0 {
			rest = rest[nl+1:]
		}
		if end := strings.Index(rest, "```"); end >= 0 {
			rest = rest[:end]
		}
		text = strings.TrimSpace(rest)
	}
	lower := strings.ToLower(text)
	for _, opening := range []string{"<!doctype", "<html"} {
		if i := strings.Index(lower, opening); i >= 0 {
			return strings.TrimSpace(text[i:])
		}
	}
	return ""
}

const logoSystem = `You write one image-generation prompt for an application's
logo.

Describe a simple, flat, memorable mark: the subject, the shape language, the
palette, and a plain background. No text or lettering in the image — logos with
generated words always come out misspelt. No camera or photography words.

Answer with the prompt only, one or two sentences, no preamble and no quotes.`

// LogoPrompt turns an app idea into something the picture model can draw.
func LogoPrompt(ctx context.Context, llm *core.LLM, idea string) (string, error) {
	idea = strings.TrimSpace(idea)
	if idea == "" {
		return "", fmt.Errorf("describe the app first")
	}
	out, err := llm.Text(ctx, core.RoleDesign, logoSystem, "THE APPLICATION\n"+truncate(idea, 3000))
	if err != nil {
		return "", err
	}
	out = strings.TrimSpace(strings.Trim(strings.TrimSpace(out), `"`))
	if out == "" {
		return "", fmt.Errorf("no logo prompt came back")
	}
	return out, nil
}
