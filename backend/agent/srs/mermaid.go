package srs

import (
	"fmt"
	"regexp"
	"strings"
)

// Whether a diagram is worth drawing, whether the Mermaid it produced is
// structurally sound, and the entry point that builds the whole set.
//
// The validation is structural, not a parser: it catches the failures a
// template can actually produce — an unclosed subgraph, a node nothing links
// to, a participant that speaks without being declared — because those render
// as a blank box in the customer's document and nothing else notices.

// --- is it worth drawing --------------------------------------------------------------

// DiagramApplicable is whether the specification says enough for this diagram
// to mean anything. Saying "not applicable" is an answer; drawing a plausible
// invention is not.
func DiagramApplicable(kind string, doc *Document) (bool, string) {
	switch kind {
	case "state_machine":
		life := firstLifecycle(doc)
		if life == nil || len(stateTransitions(doc, life)) == 0 {
			return false, "No explicit legal state-to-state transitions are specified in the SRS."
		}
	case "erd":
		if len(doc.DatabaseDesign.Tables) == 0 {
			return false, "No persistent entities are specified in the SRS data model."
		}
	case "activity", "bpmn", "sequence":
		if len(doc.BusinessWorkflows) == 0 {
			plan := diagramPlan(doc)
			if len(plan.Workflows) == 0 && len(plan.Features) == 0 {
				return false, "No ordered workflow or interaction is specified in the approved requirements."
			}
		}
	}
	return true, ""
}

// BuildDiagrams writes every diagram's source. A builder that fails is
// reported and replaced with a diagram saying so — one broken diagram must
// never cost the customer the other ten.
func BuildDiagrams(doc *Document, onError func(string)) []Diagram {
	out := make([]Diagram, 0, len(DiagramKinds))
	for _, entry := range DiagramKinds {
		source := sourceFor(entry.Kind, doc)
		if problems := MermaidProblems(entry.Kind, source); len(problems) > 0 && onError != nil {
			onError(fmt.Sprintf("%s: template produced invalid Mermaid — %s",
				entry.Kind, strings.Join(problems, "; ")))
		}
		applicable, reason := DiagramApplicable(entry.Kind, doc)
		out = append(out, Diagram{
			ID: "dia_" + entry.Kind, Kind: entry.Kind, Title: entry.Title,
			Format: "mermaid", Source: source,
			Standard:   DiagramStandard[entry.Kind],
			Applicable: applicable, ApplicabilityNote: reason,
			CanonicalRendering: "native_svg",
		})
	}
	return out
}

// --- structural validation ------------------------------------------------------------

var subgraphLine = regexp.MustCompile(`^subgraph\s+([A-Za-z][\w]*)`)
var nodeDeclaration = regexp.MustCompile(`\b([A-Za-z][\w]*)\s*(?:\[\(§\)\]|\(\[§\]\)|\[§\]|\(§\)|\{§\})`)
var arrowSplit = regexp.MustCompile(`\s*(?:-{2,}>|={2,}>|-\.-+>|-{3,}|={3,})\s*`)
var bareIdentifier = regexp.MustCompile(`^[A-Za-z][\w]*$`)
var quotedRun = regexp.MustCompile(`"[^"]*"`)
var pipeLabel = regexp.MustCompile(`\|[^|]*\|`)
var classSuffix = regexp.MustCompile(`:::\w+`)
var inlineEdgeLabel = regexp.MustCompile(`--\s*[^->]+?\s*-->`)

var skipPrefixes = []string{"classDef", "class ", "%%", "direction", "flowchart", "graph", "style", "linkStyle"}

// flowchartProblems finds the failures a template can actually produce.
func flowchartProblems(source string) []string {
	nodes := map[string]bool{}
	containers := map[string]bool{}
	var endpoints []string
	opens, closes := 0, 0

	for _, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" {
			continue
		}
		if strings.Count(line, `"`)%2 == 1 {
			return []string{"unbalanced quotes: " + truncate(line, 60)}
		}
		if line == "end" {
			closes++
			continue
		}
		if m := subgraphLine.FindStringSubmatch(line); m != nil {
			opens++
			containers[m[1]] = true
			continue
		}
		if hasAnyPrefix(line, skipPrefixes) {
			continue
		}

		s := quotedRun.ReplaceAllString(line, "§")
		s = pipeLabel.ReplaceAllString(s, " ")
		s = classSuffix.ReplaceAllString(s, "")
		s = inlineEdgeLabel.ReplaceAllString(s, " --> ")

		for _, m := range nodeDeclaration.FindAllStringSubmatch(s, -1) {
			nodes[m[1]] = true
		}
		s = nodeDeclaration.ReplaceAllString(s, "$1")

		parts := arrowSplit.Split(s, -1)
		if len(parts) <= 1 {
			continue
		}
		for _, part := range parts {
			part = strings.TrimSpace(part)
			if !bareIdentifier.MatchString(part) {
				continue
			}
			endpoints = append(endpoints, part)
			if !containers[part] {
				nodes[part] = true
			}
		}
	}

	var problems []string
	if opens != closes {
		problems = append(problems, itoa(opens)+" subgraph vs "+itoa(closes)+" end")
	}
	if len(nodes) < 2 {
		problems = append(problems, "only "+itoa(len(nodes))+" nodes")
	}
	if len(endpoints) == 0 {
		problems = append(problems, "no edges")
	}
	linked := map[string]bool{}
	for _, e := range endpoints {
		linked[e] = true
	}
	var orphans []string
	for node := range nodes {
		if !linked[node] {
			orphans = append(orphans, node)
		}
	}
	if len(orphans) > 0 {
		sortStrings(orphans)
		if len(orphans) > 5 {
			orphans = orphans[:5]
		}
		problems = append(problems, "unconnected node(s): "+strings.Join(orphans, ", "))
	}
	return problems
}

func hasAnyPrefix(line string, prefixes []string) bool {
	for _, prefix := range prefixes {
		if strings.HasPrefix(line, prefix) {
			return true
		}
	}
	return false
}

var entityOpen = regexp.MustCompile(`(?m)^\s*\w+\s*\{\s*$`)
var entityClose = regexp.MustCompile(`(?m)^\s*\}\s*$`)
var classHeader = regexp.MustCompile(`(?m)^\s*class\s+\w+`)
var activateLine = regexp.MustCompile(`(?m)^\s*activate\s`)
var deactivateLine = regexp.MustCompile(`(?m)^\s*deactivate\s`)
var participantLine = regexp.MustCompile(`(?m)^\s*(?:actor|participant)\s`)
var participantName = regexp.MustCompile(`(?m)^\s*(?:actor|participant)\s+(\w+)`)
var senderName = regexp.MustCompile(`(?m)^\s*(\w+)\s*-[->]+>?`)
var receiverName = regexp.MustCompile(`-[->]+>?\s*(\w+)\s*:`)

// sequenceKeywords are the block words that look like participants but are not.
var sequenceKeywords = map[string]bool{
	"alt": true, "else": true, "end": true, "loop": true, "opt": true,
}

// MermaidProblems is everything structurally wrong with one diagram.
func MermaidProblems(kind, source string) []string {
	src := strings.TrimSpace(source)
	if len(src) < 30 {
		return []string{"source is empty or a stub"}
	}
	head := strings.ToLower(strings.TrimSpace(strings.SplitN(src, "\n", 2)[0]))

	switch kind {
	case "erd":
		if !strings.HasPrefix(head, "erdiagram") {
			return []string{"must start with erDiagram"}
		}
		opens := len(entityOpen.FindAllString(src, -1))
		closes := len(entityClose.FindAllString(src, -1))
		if opens != closes {
			return []string{itoa(opens) + " entity blocks opened, " + itoa(closes) + " closed"}
		}
		if opens == 0 {
			return []string{"no entities"}
		}
		return nil

	case "class_object":
		if !strings.HasPrefix(head, "classdiagram") {
			return []string{"must start with classDiagram"}
		}
		if !classHeader.MatchString(src) {
			return []string{"no classes"}
		}
		return nil

	case "state_machine":
		if !strings.HasPrefix(head, "statediagram") {
			return []string{"must start with stateDiagram-v2"}
		}
		if !strings.Contains(src, "[*]") {
			return []string{"missing initial/final pseudo-state"}
		}
		return nil

	case "sequence":
		if !strings.HasPrefix(head, "sequencediagram") {
			return []string{"must start with sequenceDiagram"}
		}
		var problems []string
		acts := len(activateLine.FindAllString(src, -1))
		deacts := len(deactivateLine.FindAllString(src, -1))
		if acts != deacts {
			problems = append(problems, itoa(acts)+" activate vs "+itoa(deacts)+" deactivate")
		}
		if len(participantLine.FindAllString(src, -1)) < 2 {
			problems = append(problems, "fewer than two participants")
		}
		declared := map[string]bool{}
		for _, m := range participantName.FindAllStringSubmatch(src, -1) {
			declared[m[1]] = true
		}
		used := map[string]bool{}
		for _, pattern := range []*regexp.Regexp{senderName, receiverName} {
			for _, m := range pattern.FindAllStringSubmatch(src, -1) {
				used[m[1]] = true
			}
		}
		var unknown []string
		for name := range used {
			if !declared[name] && !sequenceKeywords[name] {
				unknown = append(unknown, name)
			}
		}
		if len(unknown) > 0 {
			sortStrings(unknown)
			if len(unknown) > 5 {
				unknown = unknown[:5]
			}
			problems = append(problems, "undeclared participant(s): "+strings.Join(unknown, ", "))
		}
		return problems
	}

	if !strings.HasPrefix(head, "flowchart") && !strings.HasPrefix(head, "graph") {
		return []string{"must start with flowchart or graph"}
	}
	return flowchartProblems(src)
}

// ValidMermaid is the yes/no form.
func ValidMermaid(kind, source string) bool { return len(MermaidProblems(kind, source)) == 0 }

// DiagramStandard names the notation each diagram follows. It is stated on the
// diagram so a reader knows which rules it is being held to.
var DiagramStandard = map[string]string{
	"use_case": "OMG UML 2.5.1", "sequence": "OMG UML 2.5.1",
	"activity": "OMG UML 2.5.1", "class_object": "OMG UML 2.5.1",
	"state_machine": "OMG UML 2.5.1", "bpmn": "OMG BPMN 2.0.2",
	"erd": "Crow's Foot ERD", "dfd": "Yourdon/DeMarco-style DFD",
	"system_context": "Architecture context view",
	"component":      "UML-inspired component view",
	"deployment":     "UML-inspired deployment view",
}
