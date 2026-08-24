package qa

import (
	"context"
	"fmt"
	"time"

	"agentforge/agent/core"
)

const (
	// buildTimeout is generous on purpose: a first production build compiles
	// every route in the app and everything they pull in.
	buildTimeout = 8 * time.Minute
	maxBuildFix  = 4
)

// buildCheck compiles the whole application before the dev server is started.
//
// `next dev` compiles a route only when something first asks for it, so a page
// that cannot compile stays quiet until a later stage happens to open it — and
// what that stage reports is whatever the browser was handed, not the compiler
// error underneath it. `next build` compiles everything at once and names the
// file and the line, which is the difference between a repair that can be made
// and one that cannot.
func (s *Suite) buildCheck(ctx context.Context, run *core.Run) {
	s.report.Runtime.Total++

	for attempt := 1; attempt <= maxBuildFix; attempt++ {
		if run.Check() != nil {
			return
		}
		run.Info("🏗️ npm run build")
		res, err := run.Shell.Exec(ctx, buildTimeout, core.NPM(), "run", "build")
		if err != nil && res.Stdout == "" && res.Stderr == "" {
			run.Warn("the production build could not run: " + err.Error())
			s.report.Runtime.Total--
			return
		}
		if res.OK() {
			run.Info("✅ the whole app compiles")
			run.TestResult("pass", "The app compiles", "")
			s.report.Runtime.Passed++
			return
		}

		problem := res.Tail(60)
		run.Warn(fmt.Sprintf("🐞 the build failed (%d/%d)", attempt, maxBuildFix))
		run.TestResult("fail", "The app does not compile", firstLines(problem, 6))
		if attempt == maxBuildFix {
			break
		}

		// A failure that names no file of ours is not one to hand to a repair.
		// Without a file the repair falls back to a general set — package.json
		// among them — and is asked to fix a fault in code it was never shown,
		// which is how a build script loses the flag the harness depends on.
		if named := filesIn(problem, run); len(named) == 0 {
			run.Warn("the build failure names no file this build owns; leaving it to the dev server")
			break
		}

		fixed, err := s.repairRuntime(ctx, run, problem)
		if err != nil {
			run.Warn("the build error could not be repaired: " + err.Error())
			break
		}
		if !fixed {
			break
		}
		run.TestFixing(attempt, []string{firstLines(problem, 2)})
	}

	// Not fatal. A page that will not compile here will not compile under the
	// dev server either, and there it gets another attempt with the browser's
	// account of it as well as the compiler's.
	s.report.Runtime.Failed++
	s.report.Runtime.Unresolved = append(s.report.Runtime.Unresolved, "the app does not compile")
	run.Warn("⚠ carrying on to the dev server despite the build")
}
