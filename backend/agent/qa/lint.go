package qa

import (
	"context"
	"fmt"
	"time"

	"agentforge/agent/core"
)

const (
	lintTimeout = 4 * time.Minute
	maxLintFix  = 3
)

// lintCheck reads the code without running it, which finds a different class of
// fault from everything else here. The build only cares that a file compiles,
// and a page can compile perfectly while calling a hook conditionally, leaving
// a variable undefined on one branch, or linking somewhere that does not exist
// — none of which the compiler objects to and none of which a unit test that
// never renders that branch will reach.
//
// Only errors fail this. The Next config reports things like a bare <img> as
// warnings, and chasing those would spend repair budget on appearance while
// real faults wait.
func (s *Suite) lintCheck(ctx context.Context, run *core.Run) {
	if !run.Shell.Exists("eslint.config.mjs") {
		return // scaffolded before lint was part of a project
	}
	s.report.Runtime.Total++

	for attempt := 1; attempt <= maxLintFix; attempt++ {
		if run.Check() != nil {
			return
		}
		run.Info("🔎 eslint")
		res, err := run.Shell.Exec(ctx, lintTimeout, core.NPX(), "eslint", ".")
		if err != nil && res.Stdout == "" && res.Stderr == "" {
			run.Warn("eslint could not run: " + err.Error())
			s.report.Runtime.Total--
			return
		}
		if res.OK() {
			run.Info("✅ eslint found nothing wrong")
			run.TestResult("pass", "Lint is clean", "")
			s.report.Runtime.Passed++
			return
		}

		problem := res.Tail(50)
		run.Warn(fmt.Sprintf("🐞 eslint found errors (%d/%d)", attempt, maxLintFix))
		run.TestResult("fail", "Lint errors", firstLines(problem, 6))
		if attempt == maxLintFix {
			break
		}

		// The same rule the build check follows: a report naming no file of
		// ours must not be handed to a repair, which would otherwise be given
		// a general set of files and asked to fix a fault in code it was
		// never shown.
		if named := filesIn(problem, run); len(named) == 0 {
			run.Warn("the lint report names no file this build owns; leaving it")
			break
		}

		fixed, err := s.repairRuntime(ctx, run, problem)
		if err != nil {
			run.Warn("the lint errors could not be repaired: " + err.Error())
			break
		}
		if !fixed {
			break
		}
		run.TestFixing(attempt, []string{firstLines(problem, 2)})
	}

	s.report.Runtime.Failed++
	s.report.Runtime.Unresolved = append(s.report.Runtime.Unresolved, "eslint reports errors")
	run.Warn("⚠ carrying on with lint errors outstanding")
}
