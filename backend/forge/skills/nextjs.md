---
name: nextjs
description: Next.js App Router conventions for pages, layouts, route handlers and data.
triggers: next, next.js, nextjs, app router, page, layout, route, api, server component, tsx, react, component, ui, form, dashboard, build an app
---

# Next.js App Router

## Where files go
- `app/page.tsx` — the route `/`. `app/items/page.tsx` — `/items`.
- `app/items/[id]/page.tsx` — a dynamic route; `params` is a Promise, so
  `const { id } = await params`.
- `app/layout.tsx` — must render `<html>` and `<body>`, and must exist.
- `app/api/items/route.ts` — exports named `GET`, `POST`, `PATCH`, `DELETE`.
  A route handler returns `Response.json(data)` or `NextResponse.json(data)`.
- `lib/` — shared, non-React code. `components/` — React components.

## Server and client
- Every component is a server component. Add `"use client"` as the first line
  only when the file uses `useState`, `useEffect`, `onClick` or a browser API.
- A server component may be `async` and await data directly. A client
  component may not — it fetches in `useEffect` or takes props.
- Never import a server-only module (a database client, `fs`) into a file
  marked `"use client"`.

## Data
- Do the query in the server component or the route handler, never in the
  browser through a secret.
- Serialise before it crosses to the client: `_id.toString()`, dates to ISO
  strings. An ObjectId that reaches a client component throws.
- `export const dynamic = "force-dynamic"` on pages that must not be cached.

## What makes a page testable
- Put `data-testid` on the elements a test must find: the form, its inputs,
  the submit button, each row of a list, the empty state, the error.
- Give inputs real labels — `<label htmlFor="title">` with a matching `id`.
  Tests find fields by label, and so do screen readers.
- Render an explicit empty state ("No items yet") rather than nothing.

## Rules that keep a build green
- Keep one component per file and files under ~150 lines.
- Import with the `@/` alias, which `tsconfig.json` maps to the project root.
- Anything imported must be exported under exactly that name — read the file
  first if you are not certain.
- `metadata` is exported from a server component only, never a client one.
