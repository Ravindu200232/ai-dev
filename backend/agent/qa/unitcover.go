package qa

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"agentforge/agent/core"
)

// errTestSkipped means the author judged a file not worth a unit test, which is
// a different outcome from failing to write one and must not be retried.
var errTestSkipped = errors.New("the file was judged not worth a unit test")

// stubTestBytes is the size below which a test file cannot be asserting
// anything. An import line and a closing brace clear a plain existence check.
const stubTestBytes = 60

// unitTestPath is where a target's test lives. One definition, so the check for
// a missing test cannot drift from where the author puts it — if those two ever
// disagreed, every file would look untested and be written twice.
func unitTestPath(target string) string {
	return "tests/unit/" + testNameFor(target) + ".test.js"
}

// missingUnitTests names the targets with no usable test file, ignoring the
// ones deliberately skipped.
func missingUnitTests(run *core.Run, targets []string, skipped map[string]bool) []string {
	var out []string
	for _, target := range targets {
		if skipped[target] {
			continue
		}
		dest := unitTestPath(target)
		if !run.Shell.Exists(dest) {
			out = append(out, target)
			continue
		}
		// A file that exists but holds nothing passes for a test until vitest
		// reports zero cases and nobody reads the number.
		body, _, err := run.Shell.Read(dest)
		if err != nil || len(strings.TrimSpace(body)) < stubTestBytes {
			out = append(out, target)
		}
	}
	return out
}

// writeMissingUnitTests gives every target that still has no test one more
// attempt, and returns whatever remains untested after that.
func (s *Suite) writeMissingUnitTests(ctx context.Context, run *core.Run,
	targets []string, skipped map[string]bool) []string {

	missing := missingUnitTests(run, targets, skipped)
	if len(missing) == 0 {
		return nil
	}

	run.Warn(fmt.Sprintf("🧪 %d of %d file(s) got no test — writing those again",
		len(missing), len(targets)))
	for _, target := range missing {
		if err := run.Check(); err != nil {
			return missing
		}
		switch err := s.authorUnitTest(ctx, run, target); {
		case errors.Is(err, errTestSkipped):
			skipped[target] = true
		case err != nil:
			run.Warn("still no test for " + target + ": " + err.Error())
		}
	}
	return missingUnitTests(run, targets, skipped)
}
