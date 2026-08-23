---
name: playwright
description: Playwright end-to-end tests against a running Next.js dev server.
triggers: playwright, e2e, end-to-end, browser, journey, click, navigate, login flow, screenshot, selector, locator
---

# Playwright end-to-end tests

## Setup that must exist
- `playwright.config.ts` with `testDir: "tests/e2e"`, `reporter: [["json",
  { outputFile: "test-results/e2e.json" }]]`, `use: { baseURL:
  "http://127.0.0.1:3000" }` and a `webServer` block that runs the app and
  sets `reuseExistingServer: true`.
- One spec per journey in `tests/e2e/*.spec.ts`.

## Writing one
```ts
import { expect, test } from "@playwright/test";

test("a visitor can add an item", async ({ page }) => {
  await page.goto("/items");
  await page.getByTestId("new-item").click();
  await page.getByLabel("Title").fill("Kettle");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId("item-row")).toContainText("Kettle");
});
```

- Locate by role, label or testid. Never by a Tailwind class — those change
  every time the design does.
- `await expect(locator).toBeVisible()` waits on its own. Never
  `page.waitForTimeout` to paper over a race.
- Assert the outcome a user would check: the row appears, the URL changed,
  the error message is on screen.

## A journey worth testing
One per real user goal, end to end, in the order a person would do it:
land on the page → act → see the result persist after a reload. Cover the
unhappy path too: submit the form empty and assert the validation message.

## Reading a failure
- `strict mode violation: resolved to N elements` — the locator matches more
  than one. Add `.first()` only if any of them is genuinely fine; otherwise
  narrow it.
- `Timeout … waiting for locator` — the element never appeared. Read the page
  component and check the testid exists before changing the test.
- `net::ERR_CONNECTION_REFUSED` — the dev server is not up. That is an
  environment problem, not a test failure.

## Rules
- Tests must pass against a fresh database, so a spec seeds what it needs and
  does not depend on another spec having run.
- Run with `npx playwright test --reporter=json`. Install once with
  `npx playwright install --with-deps chromium`.
- Never weaken an assertion to make a run green.
