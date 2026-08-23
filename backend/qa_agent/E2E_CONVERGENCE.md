# E2E convergence

The E2E gate ends green only when the required journeys pass. Otherwise it reports the unresolved blocker.

1. Build a workflow contract.
2. Normalize locator syntax before browser execution.
3. Capture URL, DOM, session, console and network evidence.
4. Resolve deterministic failures before deeper analysis.
5. Repair only evidence-owned source.
6. Roll back unrelated or unsafe changes.
7. Restore auth and checkpoint state before resuming.
8. Verify persisted actions with real mutation evidence.
9. Rebuild after production repairs.
10. Replay accepted journeys in a clean browser context.
11. Recheck role separation before reporting green.

A test locator may change only when the live DOM proves an equivalent target. Production code changes require workflow or runtime evidence.
