---
name: vitest
description: Vitest and Testing Library unit tests for a Next.js project, and how to read their failures.
triggers: vitest, unit test, unit, jest, test, testing library, mock, spec, assertion, coverage, render, screen
---

# Vitest unit tests

## Setup that must exist
- `vitest.config.ts` with `environment: "jsdom"`, `globals: true`,
  `setupFiles: ["./vitest.setup.ts"]` and the `@/` alias pointing at the root.
- `vitest.setup.ts` importing `@testing-library/jest-dom/vitest`.
- Tests live in `tests/unit/*.test.tsx` next to nothing else.

## Writing one
```ts
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import ItemList from "@/components/ItemList";

describe("ItemList", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows the empty state when there are no items", () => {
    render(<ItemList items={[]} />);
    expect(screen.getByTestId("empty")).toBeInTheDocument();
  });
});
```

- Query by role or label first, `getByTestId` second. Never by class name.
- `findBy*` when the value arrives after an await; `getBy*` when it is already
  rendered. `queryBy*` only to assert absence.
- Assert behaviour a user can see, not the shape of internal state.

## Mocking
- `vi.mock("@/lib/db", () => ({ db: { find: vi.fn() } }))` — the factory is
  hoisted above the imports, so it may not close over a `const` defined later.
  That is what `Cannot access 'x' before initialization` means.
- Mock `next/navigation` when a component calls `useRouter`:
  `vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }))`.
- Mock `global.fetch` so it resolves: `{ ok: true, json: async () => ({}) }`.
  A fetch mock that resolves to nothing makes the test hang, not fail.

## Reading a failure
- `Unable to find an element by: [data-testid=…]` — the testid is not in the
  component. Read the component and use what is there; do not invent one.
- `Test timed out` — nothing asserted wrongly, something never finished.
  Look for fake timers with `waitFor`, a `fireEvent.click` on a submit button
  where `fireEvent.submit(form)` is needed, or a fetch mock that never resolves.
- `expected 500 to be 200` — the handler threw. Read the handler.
- `Found multiple elements` — the query is too loose, or the fixture has
  duplicates. Narrow with `within(...)`.

## Rules
- Fix the code when the code is wrong; fix the test when the test assumed
  something the code never promised. Never delete or skip a case to go green.
- Run with `npx vitest run --reporter=json --outputFile=.vitest-report.json`.
