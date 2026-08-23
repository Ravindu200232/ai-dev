package srs

import (
	"strings"
	"testing"
)

func TestRenderEveryDiagram(t *testing.T) {
	doc := diagramDoc(t)
	// A described lifecycle, so the state machine has something to draw.
	doc.DatabaseDesign.Tables[3].Fields = append(doc.DatabaseDesign.Tables[3].Fields,
		Field{Name: "status", Type: "enum", Values: []any{"draft", "paid"}})
	doc.FunctionalRequirements = append(doc.FunctionalRequirements, Requirement{
		ID: "FR-900", Requirement: "The system shall move a sale from draft to paid on payment.",
	})

	for _, entry := range DiagramKinds {
		t.Run(entry.Kind, func(t *testing.T) {
			d := Render(entry.Kind, doc, 720)
			if d == nil {
				t.Fatalf("%s has no renderer", entry.Kind)
			}
			if d.Width != 720 || d.Height < 100 {
				t.Errorf("size = %v x %v", d.Width, d.Height)
			}
			if len(d.Shapes) < 3 {
				t.Errorf("%s drew %d shapes", entry.Kind, len(d.Shapes))
			}

			svg := d.SVG()
			if !strings.HasPrefix(svg, `<svg xmlns="http://www.w3.org/2000/svg"`) {
				t.Fatalf("not an SVG document:\n%s", truncate(svg, 200))
			}
			if !strings.HasSuffix(svg, "</svg>\n") {
				t.Error("the document is not closed")
			}
			if strings.Contains(svg, "NaN") || strings.Contains(svg, "+Inf") {
				t.Errorf("%s produced a broken coordinate:\n%s", entry.Kind, truncate(svg, 400))
			}
			if strings.Contains(svg, "Not applicable") {
				t.Errorf("%s should be drawable from this document", entry.Kind)
			}
		})
	}
}

func TestRenderSaysWhenItCannot(t *testing.T) {
	empty := &Document{}
	for _, kind := range []string{"use_case", "sequence", "erd", "activity", "class_object",
		"state_machine", "dfd", "bpmn", "component"} {
		d := Render(kind, empty, 720)
		if d == nil {
			t.Fatalf("%s has no renderer", kind)
		}
		svg := d.SVG()
		if !strings.Contains(svg, "Not applicable to the current specification") {
			t.Errorf("%s must say it cannot be drawn rather than inventing one:\n%s",
				kind, truncate(svg, 300))
		}
	}
	if Render("nonexistent", empty, 720) != nil {
		t.Error("an unknown kind has no drawing")
	}
	// The context and deployment views are always drawable: a system with no
	// integrations still runs somewhere.
	for _, kind := range []string{"system_context", "deployment"} {
		if svg := Render(kind, empty, 720).SVG(); strings.Contains(svg, "Not applicable") {
			t.Errorf("%s should always draw", kind)
		}
	}
}

func TestSVGFlipsTheOrigin(t *testing.T) {
	d := newDrawing(100, 200)
	d.rect(10, 20, 30, 40, "#FFFFFF", "#000000", 1)
	d.text(5, 60, "hello", textOpts{Size: 8})
	d.line(0, 0, 10, 10, "#000000", 1, true)
	svg := d.SVG()

	// A rect at y=20 with height 40 has its top edge at 200-60 = 140.
	if !strings.Contains(svg, `y="140"`) {
		t.Errorf("the rectangle was not flipped:\n%s", svg)
	}
	if !strings.Contains(svg, `<text x="5" y="140"`) {
		t.Errorf("the baseline was not flipped:\n%s", svg)
	}
	if !strings.Contains(svg, `y1="200"`) || !strings.Contains(svg, `y2="190"`) {
		t.Errorf("the line was not flipped:\n%s", svg)
	}
	if !strings.Contains(svg, `stroke-dasharray="5 3"`) {
		t.Error("the dash is lost")
	}
}

func TestSVGEscapesText(t *testing.T) {
	d := newDrawing(100, 100)
	d.text(0, 0, `a & b <c> "d"`, textOpts{Size: 8})
	svg := d.SVG()
	if strings.Contains(svg, "<c>") || !strings.Contains(svg, "&amp;") {
		t.Errorf("text was not escaped:\n%s", svg)
	}
}

func TestTextFitsWithRealMetrics(t *testing.T) {
	wide := stringWidth("Hello world", fontRegular, 10)
	if wide < 40 || wide > 70 {
		t.Errorf("Helvetica metrics look wrong: %v", wide)
	}
	if stringWidth("Hello world", fontBold, 10) <= wide {
		t.Error("bold is wider than regular")
	}

	fitted := fitText("a very long label that will certainly not fit", fontRegular, 8, 60)
	if !strings.HasSuffix(fitted, "…") {
		t.Errorf("fitText = %q", fitted)
	}
	if stringWidth(fitted, fontRegular, 8) > 60 {
		t.Errorf("fitText did not fit: %q is %v wide", fitted, stringWidth(fitted, fontRegular, 8))
	}
	if got := fitText("short", fontRegular, 8, 200); got != "short" {
		t.Errorf("a label that fits is left alone: %q", got)
	}

	lines := wrapText("one two three four five six seven eight", fontRegular, 8, 40, 2)
	if len(lines) != 2 {
		t.Fatalf("wrapText = %v", lines)
	}
	for _, line := range lines {
		if stringWidth(line, fontRegular, 8) > 41 {
			t.Errorf("line %q is too wide", line)
		}
	}
	if got := wrapText("", fontRegular, 8, 40, 2); len(got) != 1 || got[0] != "" {
		t.Errorf("wrapText on nothing = %v", got)
	}
}

func TestERDLayoutFollowsTheKeys(t *testing.T) {
	doc := composed(t)
	relations := entityRelations(doc, doc.DatabaseDesign.Tables)
	var found bool
	for _, r := range relations {
		if r.From == "sales" && r.To == "products" {
			found = true
		}
	}
	if !found {
		t.Errorf("the foreign key from sales was not read: %+v", relations)
	}

	// A relation nothing states and no key implies is not drawn.
	bare := &Document{DatabaseDesign: DatabaseDesign{Tables: []Table{
		{TableName: "products", Fields: []Field{{Name: "id", PrimaryKey: true}}},
		{TableName: "notes", Fields: []Field{{Name: "id", PrimaryKey: true}}},
	}}}
	if got := entityRelations(bare, bare.DatabaseDesign.Tables); len(got) != 0 {
		t.Errorf("relations invented: %+v", got)
	}
	if svg := Render("erd", bare, 720).SVG(); !strings.Contains(svg, "shown independently") {
		t.Error("an ERD with no relationships should say so")
	}
}

func TestResolveReference(t *testing.T) {
	tables := []Table{{TableName: "products"}, {TableName: "sale_items"}}
	cases := map[[2]string]string{
		{"products.id", ""}:   "products",
		{"", "product_id"}:    "products",
		{"", "sale_item_id"}:  "sale_items",
		{"nothing.id", "xyz"}: "",
		{"Products", ""}:      "products",
	}
	for in, want := range cases {
		if got := resolveReference(in[0], in[1], tables); got != want {
			t.Errorf("resolveReference(%q, %q) = %q, want %q", in[0], in[1], got, want)
		}
	}
}

func TestSequenceMatchesEndpointsToVerbs(t *testing.T) {
	endpoints := []Endpoint{
		{Method: "GET", Path: "/api/products"},
		{Method: "POST", Path: "/api/products"},
		{Method: "DELETE", Path: "/api/products/[id]"},
	}
	if ep := matchEndpoint("Cashier adds a product", endpoints); ep == nil || ep.Method != "POST" {
		t.Errorf("adding should be a POST: %+v", ep)
	}
	if ep := matchEndpoint("Cashier views the products", endpoints); ep == nil || ep.Method != "GET" {
		t.Errorf("viewing should be a GET: %+v", ep)
	}
	if ep := matchEndpoint("Cashier deletes a product", endpoints); ep == nil || ep.Method != "DELETE" {
		t.Errorf("deleting should be a DELETE: %+v", ep)
	}
	if ep := matchEndpoint("Nothing to do with anything", endpoints); ep != nil {
		t.Errorf("an unrelated step matches no endpoint: %+v", ep)
	}
}

func TestBPMNLanesFollowTheRoles(t *testing.T) {
	roles := []string{"Cashier", "Admin"}
	if got := stepLane("Cashier opens the till", roles, ""); got != "Cashier" {
		t.Errorf("lane = %q", got)
	}
	if got := stepLane("The system prints a receipt", roles, "Cashier"); got != "System" {
		t.Errorf("lane = %q", got)
	}
	// A step naming nobody stays with whoever was working.
	if got := stepLane("Count the drawer", roles, "Cashier"); got != "Cashier" {
		t.Errorf("lane = %q", got)
	}
	if got := stepLane("Count the drawer", roles, ""); got != "Cashier" {
		t.Errorf("lane = %q", got)
	}
	if got := stepLane("Count the drawer", nil, ""); got != "Process" {
		t.Errorf("lane = %q", got)
	}
}

func TestProcessName(t *testing.T) {
	if got := processName("Product Management"); got != "Manage Product" {
		t.Errorf("processName = %q", got)
	}
	if got := processName("sale_terminal"); got != "sale terminal" {
		t.Errorf("processName = %q", got)
	}
	if got := processName("Management"); got != "Management" {
		t.Errorf("processName = %q", got)
	}
}

func TestOverlapIgnoresFillerWords(t *testing.T) {
	if overlapScore("Product management", "the user application") != 0 {
		t.Error("filler words must not count as agreement")
	}
	if overlapScore("Products", "product catalogue") == 0 {
		t.Error("a plural should still match its singular")
	}
}
