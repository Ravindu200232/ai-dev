package qa

import (
	"fmt"
	"strings"
	"testing"

	"agentforge/agent/core"
)

const realTest = `import { describe, it, expect } from 'vitest'
describe('rooms', () => { it('lists them', () => { expect(1).toBe(1) }) })
`

func TestMissingUnitTestsFindsWhatWasNeverWritten(t *testing.T) {
	targets := []string{
		"app/page.jsx",
		"components/RoomCard.jsx",
		"lib/db.js",
		"app/api/rooms/route.js",
	}
	run := runIn(t, map[string]string{
		"app/page.jsx":                          "home",
		"components/RoomCard.jsx":               "card",
		"lib/db.js":                             "db",
		"app/api/rooms/route.js":                "rooms",
		unitTestPath("app/page.jsx"):            realTest,
		unitTestPath("components/RoomCard.jsx"): "",
		unitTestPath("lib/db.js"):               "   \n\n",
		// app/api/rooms/route.js has no test file at all
	})

	missing := strings.Join(missingUnitTests(run, targets, map[string]bool{}), "\n")

	if strings.Contains(missing, "app/page.jsx") {
		t.Error("a file with a real test was called untested")
	}
	for _, want := range []string{"components/RoomCard.jsx", "lib/db.js", "app/api/rooms/route.js"} {
		if !strings.Contains(missing, want) {
			t.Errorf("%s has no usable test but was not reported:\n%s", want, missing)
		}
	}
}

// A file the author deliberately passed over is not a hole in the coverage, and
// retrying it forever would cost a model call every round.
func TestMissingUnitTestsRespectsASkip(t *testing.T) {
	targets := []string{"lib/db.js"}
	run := runIn(t, map[string]string{"lib/db.js": "db"})

	if got := missingUnitTests(run, targets, map[string]bool{}); len(got) != 1 {
		t.Fatalf("without a skip it is missing, got %v", got)
	}
	if got := missingUnitTests(run, targets, map[string]bool{"lib/db.js": true}); len(got) != 0 {
		t.Errorf("a skipped file is not missing, got %v", got)
	}
}

// The check for a missing test and the author must agree on where a test lives.
// If they ever drifted apart every file would look untested and be written
// twice, which is a model call per file per round.
func TestUnitTestPathSurvivesAwkwardNames(t *testing.T) {
	cases := map[string]string{
		"app/page.jsx":                   "tests/unit/app-page.test.js",
		"app/api/bookings/[id]/route.js": "tests/unit/app-api-bookings-id-route.test.js",
		"components/RoomEditForm.jsx":    "tests/unit/components-RoomEditForm.test.js",
	}
	for target, want := range cases {
		if got := unitTestPath(target); got != want {
			t.Errorf("unitTestPath(%q) = %q, want %q", target, got, want)
		}
	}
}

// unitTargets no longer decides how many tests are affordable — it says what
// qualifies, and the caller reports what it had to leave out. If the cap were
// still applied here the count in the log would be the cap, with nothing to
// compare it against, which reads as full coverage when it is not.
func TestUnitTargetsReturnsEverythingThatQualifies(t *testing.T) {
	files := map[string]string{
		"lib/db.js":              "db",
		"components/Card.jsx":    "card",
		"app/api/rooms/route.js": "rooms",
		"app/layout.jsx":         "layout", // covered by every page test
		"tests/unit/x.test.js":   "a test", // not a target
		"next.config.mjs":        "config", // not a target
	}
	for i := 0; i < 20; i++ {
		files[fmt.Sprintf("app/p%d/page.jsx", i)] = "page"
	}
	run := runIn(t, files)
	core.Refresh(run)

	got := unitTargets(run)
	if len(got) != 23 {
		t.Fatalf("23 files qualify (20 pages, lib, component, api route), got %d: %v", len(got), got)
	}
	if len(got) <= maxTargets {
		t.Fatal("this fixture is meant to exceed the cap, or it proves nothing")
	}

	// Shared code first, pages last: the cap falls on whatever is at the end,
	// so the order decides what goes untested.
	if got[0] != "lib/db.js" {
		t.Errorf("shared code should be tested first, got %q", got[0])
	}
	if !strings.HasPrefix(got[len(got)-1], "app/") || strings.Contains(got[len(got)-1], "/api/") {
		t.Errorf("a page should be last in line, got %q", got[len(got)-1])
	}
	for _, unwanted := range []string{"app/layout.jsx", "tests/unit/x.test.js", "next.config.mjs"} {
		for _, have := range got {
			if have == unwanted {
				t.Errorf("%s is not a unit target", unwanted)
			}
		}
	}
}
