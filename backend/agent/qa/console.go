package qa

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"agentforge/agent/core"
)

const (
	// consoleSpecPath is written by this agent rather than by the model, so it
	// keeps the same shape every run and its failures always read the same way.
	consoleSpecPath = "tests/e2e/zz-console.spec.js"
	maxConsoleFix   = 3
)

// consoleSpec opens every route and fails on anything the browser logged as an
// error. A page can serve perfectly good HTML and still be broken the moment it
// hydrates, and nothing else in the pipeline ever looks at the browser: the
// runtime check reads status codes, the units never mount a page at all, and a
// journey only notices what its own assertions happen to touch.
const consoleSpec = `import { test, expect } from '@playwright/test'

// Written by AgentForge. Do not edit: it is rewritten on every run.
const ROUTES = %s

// Noise no application can do anything about.
const IGNORE = [
  'favicon',
  'Download the React DevTools',
  'react-devtools',
]

for (const route of ROUTES) {
  test('no console errors on ' + route, async ({ page }) => {
    const problems = []
    page.on('console', msg => {
      if (msg.type() !== 'error') return
      const text = msg.text()
      if (IGNORE.some(skip => text.includes(skip))) return
      problems.push(text)
    })
    page.on('pageerror', err => {
      problems.push(err && err.message ? err.message : String(err))
    })

    const response = await page.goto(route, { waitUntil: 'load' })
    if (response && response.status() >= 500) {
      problems.push('the page answered ' + response.status())
    }
    // Hydration errors arrive just after load, not during it.
    await page.waitForTimeout(1500)

    expect(problems, 'the browser logged errors on ' + route).toEqual([])
  })
}
`

// ConsoleCheck opens every route in a real browser and repairs what the browser
// complains about. It runs inside the e2e stage because that is where a browser
// already exists; installing one earlier would slow every build that never gets
// this far.
func (s *Suite) ConsoleCheck(ctx context.Context, run *core.Run) error {
	core.Refresh(run)
	routes := walkableRoutes(run)
	if len(routes) == 0 {
		return nil
	}
	list, err := json.Marshal(routes)
	if err != nil {
		return err
	}
	if err := run.Shell.Write(consoleSpecPath, fmt.Sprintf(consoleSpec, list)); err != nil {
		return fmt.Errorf("the console spec could not be written: %w", err)
	}

	run.Info(fmt.Sprintf("🖥️ Reading the browser console on %d route(s)", len(routes)))
	s.report.Runtime.Total++

	for attempt := 1; attempt <= maxConsoleFix; attempt++ {
		if err := run.Check(); err != nil {
			return err
		}
		res, err := run.Shell.Exec(ctx, e2eTimeout, core.NPX(),
			"playwright", "test", consoleSpecPath, "--reporter=line")
		if err != nil && res.Stdout == "" && res.Stderr == "" {
			run.Warn("the console check could not run: " + err.Error())
			s.report.Runtime.Total--
			return nil
		}
		if res.OK() {
			run.Info("✅ the browser logged nothing on any route")
			run.TestResult("pass", "No console errors", "")
			s.report.Runtime.Passed++
			return nil
		}

		failure := res.Tail(40)
		run.Warn(fmt.Sprintf("🐞 the browser logged errors (%d/%d)", attempt, maxConsoleFix))
		run.TestResult("fail", "Console errors", firstLines(failure, 6))
		// What the browser actually said, where it can be read. Without this
		// the log says errors were found and never what they were.
		for _, line := range consoleProblems(failure) {
			run.Info("   • " + line)
		}
		if attempt == maxConsoleFix {
			break
		}

		// The same rule the build and lint checks follow: a report naming no
		// file of ours is not one to hand to a repair, which would be given a
		// general set of files instead and asked to fix what it cannot see.
		if named := filesIn(failure, run); len(named) == 0 {
			run.Warn("the console errors name no file this build owns; leaving them")
			break
		}

		fixed, err := s.repairRuntime(ctx, run, failure)
		if err != nil {
			run.Warn("the console errors could not be repaired: " + err.Error())
			break
		}
		if !fixed {
			run.Warn("the repair could not see what to change from what the browser said")
			break
		}
		run.TestFixing(attempt, []string{firstLines(failure, 1)})
		if !sleepCtx(ctx, settleAfterFix) {
			return run.Check()
		}
	}

	s.report.Runtime.Failed++
	s.report.Runtime.Unresolved = append(s.report.Runtime.Unresolved,
		"the browser still logs errors on at least one route")
	return nil
}

// consoleProblems pulls the browser's own words out of a playwright report.
// The interesting lines are the ones the spec pushed into the expected array,
// not the runner's framing around them.
func consoleProblems(report string) []string {
	var out []string
	for _, line := range strings.Split(report, "\n") {
		line = strings.TrimSpace(line)
		line = strings.TrimPrefix(line, "- ")
		line = strings.Trim(line, `"`)
		if line == "" || len(out) >= 8 {
			continue
		}
		switch {
		case strings.HasPrefix(line, "the browser logged errors on"),
			strings.Contains(line, "Error"),
			strings.Contains(line, "error"),
			strings.Contains(line, "the page answered"):
			out = append(out, truncateLine(line, 200))
		}
	}
	return out
}

func truncateLine(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
