package qa

import (
	"strings"
	"testing"

	"agentforge/agent/core"
)

func TestPageVerdict(t *testing.T) {
	cases := []struct {
		name   string
		status int
		body   string
		broken bool
	}{
		{"a page that renders", 200, "<html><body><h1>Rooms</h1></body></html>", false},
		{"a server error", 500, "<html><body>Internal Server Error</body></html>", true},
		// Next serves the overlay with a 200, so the status alone would miss it.
		{"an error overlay behind a 200", 200,
			"<html><body><div>Unhandled Runtime Error</div><p>rooms.find is not a function</p></body></html>", true},
		// A route that is merely absent is not a fault this stage repairs.
		{"a missing page", 404, "<html><body>404</body></html>", false},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := pageVerdict("/rooms", c.status, c.body)
			if c.broken && got == "" {
				t.Fatal("a broken page was called fine")
			}
			if !c.broken && got != "" {
				t.Fatalf("a working page was called broken: %q", got)
			}
			if c.broken && !strings.Contains(got, "/rooms") {
				t.Errorf("the verdict does not name the route: %q", got)
			}
		})
	}
}

// The repair is only as good as the file it names, so the overlay text — which
// is what carries it — has to survive into the verdict.
func TestPageVerdictKeepsWhatNamesTheFault(t *testing.T) {
	body := "<html><body><div>Unhandled Runtime Error</div><p>rooms.find is not a function</p></body></html>"
	got := pageVerdict("/", 200, body)
	if !strings.Contains(got, "rooms.find is not a function") {
		t.Errorf("the verdict dropped the message that names the fault: %q", got)
	}
}

// The shape Next 16 actually returns, reduced from a real broken page. The
// response is a 200 carrying a normal-looking document; the only sign of the
// fault is inside the streamed payload, twice escaped.
const next16ServerFault = `<!DOCTYPE html><html><body><div id="__next"></div>` +
	`<script>$RX("B:0","2859355405","Switched to client rendering because the server rendering errored:\n\n` +
	`roomsColl.find is not a function","Switched to client rendering because the server rendering errored:\n\n` +
	`TypeError: roomsColl.find is not a function");</script></body></html>`

// This exact page was reported as rendering fine. Nothing else in the pipeline
// would have caught it: the status is 200, the markup is a page, and the words
// the old check looked for are nowhere in it.
func TestAServerRenderFaultIsNotAWorkingPage(t *testing.T) {
	got := errorInPage(next16ServerFault)
	if got == "" {
		t.Fatal("a page whose server render threw was called fine")
	}
	if !strings.Contains(got, "roomsColl.find is not a function") {
		t.Errorf("the verdict does not carry the fault, so nothing can repair it: %q", got)
	}
	// Twice-escaped payload text is unreadable, and unsearchable for a file
	// name, until it is undone.
	if strings.Contains(got, `\n`) || strings.Contains(got, `\"`) {
		t.Errorf("the message was left escaped: %q", got)
	}

	if v := pageVerdict("/", 200, next16ServerFault); v == "" {
		t.Error("a 200 carrying a server render fault must not pass")
	}
}

// The older wording still has to work: a project on an earlier Next reports
// compile failures the other way.
func TestTheOlderOverlayWordingStillCounts(t *testing.T) {
	old := `<html><body><h1>Unhandled Runtime Error</h1><p>rooms is not iterable</p></body></html>`
	if got := errorInPage(old); got == "" {
		t.Error("the older overlay is no longer recognised")
	}
}

// A page with nothing wrong must stay clean, or every route reports a fault.
func TestAGoodPageStaysGood(t *testing.T) {
	good := `<!DOCTYPE html><html><body><main><h1>Rooms</h1><p>Switched to a nicer layout</p></main></body></html>`
	if got := errorInPage(good); got != "" {
		t.Errorf("a working page was called broken: %q", got)
	}
}

// A page renders, then asks for its data. If the endpoint behind it is failing
// the page still passes a render check, so these have to be called separately.
func TestAPIRoutesAreDerivedFromTheFilesThatServeThem(t *testing.T) {
	run := runIn(t, map[string]string{
		"app/api/rooms/route.js":         "rooms",
		"app/api/admin/stats/route.js":   "stats",
		"app/api/bookings/[id]/route.js": "one booking",
		"app/page.jsx":                   "home",
		"app/rooms/page.jsx":             "rooms page",
		"lib/db.js":                      "db",
	})
	core.Refresh(run)

	got := apiRoutes(run)
	joined := strings.Join(got, " ")

	for _, want := range []string{"/api/rooms", "/api/admin/stats"} {
		if !strings.Contains(joined, want) {
			t.Errorf("%s is served but never called: %v", want, got)
		}
	}
	// Calling a [param] route means inventing an id, and the 404 that comes
	// back says nothing about whether the handler works.
	if strings.Contains(joined, "[id]") || strings.Contains(joined, "/api/bookings") {
		t.Errorf("a route needing an id should be left alone: %v", got)
	}
	// Pages are opened by the other half of the check, not called as APIs.
	for _, unwanted := range []string{"/rooms", "page.jsx", "lib"} {
		for _, have := range got {
			if have == unwanted {
				t.Errorf("%q is not an API route: %v", unwanted, got)
			}
		}
	}
	if len(got) != 2 {
		t.Errorf("two endpoints qualify, got %d: %v", len(got), got)
	}
}
