"""Next.js dependency policy and authentication scaffold."""
from agents.builder.orchestration.common import *


class ArchitectScaffoldMixin:
    def _fallback_plan(self, user_prompt: str) -> dict:
        name = re.sub(r"[^a-z0-9]+", "-", user_prompt.lower()).strip("-")[:24]
        base = {
            "project_name": name or "app",
            "title": user_prompt[:60],
            "description": user_prompt[:160],
        }
        return {**base,
                "dependencies": ["lucide-react", "framer-motion", "date-fns"],
                "phases": [
                    {"id": 1, "title": "Shell, theme & seed data", "goal":
                     "Layout, navigation, and demo data in MongoDB",
                     "files": [{"path": "app/layout.jsx", "purpose": "shell + nav"},
                               {"path": "lib/seed.js", "purpose": "idempotent demo data"},
                               {"path": "components/Nav.js", "purpose": "navigation"}]},
                    {"id": 2, "title": "Main dashboard", "goal":
                     "The primary screen, reading from MongoDB",
                     "files": [{"path": "app/page.jsx", "purpose": "dashboard"},
                               {"path": "components/ItemCard.js", "purpose": "record card"}]},
                    {"id": 3, "title": "CRUD API & detail route", "goal":
                     "Route handlers plus a per-record page",
                     "files": [{"path": "app/api/items/route.js", "purpose": "GET/POST"},
                               {"path": "app/items/[id]/page.js", "purpose": "detail view"}]},
                    {"id": 4, "title": "Create & edit flow", "goal":
                     "Forms that write to the database",
                     "files": [{"path": "app/items/new/page.js", "purpose": "create form"},
                               {"path": "components/ItemForm.js", "purpose": "form component"}]},
                    {"id": 5, "title": "Polish", "goal":
                     "Empty states, animation, responsive pass",
                     "files": [{"path": "components/EmptyState.js", "purpose": "empty states"},
                               {"path": "app/not-found.js", "purpose": "404 page"}]},
                ]}

    NEXT_EXTRA_DEPS = {
        "framer-motion": "^11.5.4",
        "lucide-react": "^0.441.0",
        "react-icons": "^5.3.0",
        "recharts": "^2.12.7",
        "date-fns": "^3.6.0",
        "dayjs": "^1.11.13",
        "clsx": "^2.1.1",
        "uuid": "^10.0.0",
        "nanoid": "^5.0.7",
        "slugify": "^1.6.6",
        "zod": "^3.23.8",
        "swr": "^2.2.5",
        "react-hot-toast": "^2.4.1",
        "react-hook-form": "^7.53.0",
        "zustand": "^4.5.5",

        "bcryptjs": "^2.4.3",
    }

    NEXT_BANNED_DEPS = {"react-router-dom", "vite", "@vitejs/plugin-react",
                        "mongoose", "prisma", "@prisma/client", "express",
                        "next-auth"}
    BANNED_DEPS = NEXT_BANNED_DEPS

    # The build toolchain, in one place.
    NEXT_PINNED = {
        "scripts": {"dev": "next dev --webpack",
                    "build": "next build --webpack",
                    "start": "next start",
                    "test:e2e": "playwright test",
                    "test:e2e:agents": "playwright init-agents --loop=vscode"},
        "devDependencies": {"autoprefixer": "^10.4.20",
                            "postcss": "^8.4.47",
                            "tailwindcss": "3.4.19",
                            "vitest": "^3.0.0",
                            "@playwright/test": "^1.62.1",
                            "jsdom": "^25.0.0",
                            "@testing-library/react": "^16.0.0",
                            "@testing-library/jest-dom": "^6.0.0",
                            "@testing-library/user-event": "^14.0.0"},
    }

    NEXT_SCAFFOLD = frozenset({
        "package.json", "next.config.mjs", "jsconfig.json",
        "tailwind.config.js", "postcss.config.js", "app/globals.css",
        "app/layout.jsx", "app/page.jsx", "lib/mongodb.js",
        "app/api/health/route.js", ".env.local", ".gitignore",
        "AGENTS.md", "CLAUDE.md",
        "lib/auth.js", "lib/auth-client.js",
        "app/api/auth/[...all]/route.js",
        "app/api/files/route.js", "app/api/files/[id]/route.js",
    })

    NEXT_PROTECTED = frozenset({
        "package.json", "next.config.mjs", "jsconfig.json",
        "tailwind.config.js", "postcss.config.js",
        "lib/mongodb.js", "app/api/health/route.js", ".env.local", ".gitignore",
        "AGENTS.md", "CLAUDE.md",
        "lib/auth.js", "lib/auth-client.js",
        "app/api/auth/[...all]/route.js",
        "app/api/files/route.js", "app/api/files/[id]/route.js",

        "vitest.config.mjs", "playwright.config.js",
    })

    EXTRA_DEPS = NEXT_EXTRA_DEPS

    NEXT_MARK_BEGIN = "<!-- BEGIN:nextjs-agent-rules -->"
    NEXT_MARK_END = "<!-- END:nextjs-agent-rules -->"

    UI_PORT = 7824

    def write_agent_files(self):
        """Write project guidance that works with Next.js."""
        ours = textwrap.dedent("""\
            # __AGENTFORGE_TITLE__

            Generated and maintained by AgentForge. Next.js App Router + MongoDB,
            JavaScript only.

            ## Rules that decide whether this app runs

            - A file that awaits the database, reads cookies, or calls
              `getSessionUser` / `getCollection` is a **Server Component**: no
              `'use client'`, may be `async`. Its interactive parts belong in a
              separate `components/` file that starts with `'use client'`.
            - A `'use client'` file may never be `async`, never import
              `@/lib/mongodb`, `@/lib/auth`, `@/lib/seed`, `mongodb`, `bcryptjs`
              or `next/headers`. It fetches through `/api/...`.
            - The session user from `getSessionUser()` has **`id`, a string**.
              There is no `user._id` — it is `undefined`, so every comparison
              against it is false and every document written with it stores
              nothing. Compare with `doc.ownerId?.toString() === user.id` and
              store `new ObjectId(user.id)` into an ObjectId column. Use the
              same form everywhere for a given field: a document written with
              an ObjectId is never found by a query on the plain string.
            - URL params/searchParams/request JSON ids are strings too. If the
              Data Model field is ObjectId, validate/convert at the Mongo boundary:
              `findOne({ _id: new ObjectId(id) })`, never `findOne({ _id: id })`.
              The latter silently returns no record and makes a real detail page 404.
            - `lib/auth.js` owns the session cookie name, alone. One exported
              constant; the login route, the session reader and the logout route
              all import it.
            - Every page or route handler that reads the database also exports
              `const dynamic = 'force-dynamic'`.
            - `serialize(doc)` before any Mongo document crosses to a Client
              Component.
            - The seed upserts by identity — `updateOne({ email },
              { $setOnInsert: … }, { upsert: true })` — and **never** guards
              on `countDocuments()`. A count guard stops seeding the moment the
              first real user signs up, and the demo accounts the app still
              advertises are never created.
            - The seed creates fewer than five rows per collection and **never**
              a unique index.
            - Never edit `next.config.mjs`, `package.json`, `jsconfig.json`,
              `tailwind.config.js`, `postcss.config.js` or `lib/mongodb.js` —
              AgentForge generates them and refuses writes to them.

            ## Reading the docs

            The documentation for the exact installed version is in
            `node_modules/next/dist/docs/`. Prefer it over recollection.
            """).replace("__AGENTFORGE_TITLE__",
                         str(self.plan.get("title", "AgentForge app")))

        fp = self.project_dir / "AGENTS.md"
        managed = ""
        if fp.is_file():
            try:
                old = fp.read_text(encoding="utf-8", errors="replace")
                i, j = (old.find(self.NEXT_MARK_BEGIN),
                        old.find(self.NEXT_MARK_END))
                if 0 <= i < j:
                    managed = old[i:j + len(self.NEXT_MARK_END)].strip() + "\n\n"
            except Exception as e:
                self._log("WARN", f"   could not read AGENTS.md: {e}")

        self._scaffolding = True
        try:
            self.write_file("AGENTS.md", managed + ours)
            self.write_file("CLAUDE.md", "@AGENTS.md\n")
        finally:
            self._scaffolding = False

    AUTH_WORDS = (
        "sign in", "signin", "sign-in", "log in", "login", "log-in", "sign up",
        "signup", "sign-up", "register", "account", "session", "password",
        "auth", "role", "roles", "permission", "admin", "staff", "member",
        "manager", "owner", "customer", "user", "who can", "only the",
        "logged in", "signed in", "access",
    )

    # Roles a stranger must never be handed for filling in a form.
    PRIVILEGED_ROLE = re.compile(
        r"\b(admin\w*|owner|manager|management|staff|employee|moderator|mod|"
        r"super\w*|root|cashier|clerk|operator|seller|vendor|merchant|"
        r"teacher|lecturer|doctor|nurse|editor|author|agent|host|landlord|"
        r"librarian|receptionist|supervisor|accountant|hr|warden|dispatcher|"
        r"\w{3,}(?:ist|ian|eer|ator|isor|iser|izer|keeper|master|officer|"
        r"ician|wright|smith|ess))\b", re.I)

    def _signup_role(self) -> str:
        """The role a person gets by filling in the sign-up form."""
        plan = self.plan or {}
        named = str(plan.get("signup_role") or "").strip()
        if named and not self.PRIVILEGED_ROLE.search(named):
            return named
        roles = [str(a.get("role") or "").strip()
                 for a in (plan.get("demo_accounts") or [])]
        roles = [r for r in roles if r]
        public = [r for r in roles if not self.PRIVILEGED_ROLE.search(r)]
        if public:
            return public[0]
        if roles:
            self._log("WARN", "   🔐 every role in the plan is an "
                              "administrative one, so signing up grants "
                              "nothing — the staff accounts are seeded, not "
                              "registered")
        return "user"

    def _needs_auth(self) -> bool:
        """Whether this app has people who sign in."""
        plan = self.plan or {}
        if plan.get("demo_accounts"):
            return True
        planned = {str(f.get("path", "")).lower()
                   for ph in (plan.get("phases") or [])
                   for f in (ph.get("files") or [])}
        if any(p.startswith(("app/login", "app/signup", "app/register",
                             "app/(auth)")) for p in planned):
            return True
        if plan.get("phases"):
            return False
        hay = " ".join([str(plan.get("description") or ""),
                        str(plan.get("title") or ""),
                        self.plan_md or ""]).lower()
        return any(w in hay for w in self.AUTH_WORDS)

    def scaffold(self):
        """Generate the fixed Next.js scaffold."""
        self._scaffolding = True
        try:
            return self._scaffold_next()
        finally:
            self._scaffolding = False

    def _keep_installed_deps(self, pkg: dict) -> None:
        """Merge existing declarations into the new package.json."""
        try:
            on_disk = json.loads(
                (self.project_dir / "package.json").read_text(encoding="utf-8"))
        except Exception:
            return
        for key in ("dependencies", "devDependencies"):
            theirs = on_disk.get(key) or {}
            if not isinstance(theirs, dict) or not theirs:
                continue
            merged = {**theirs, **(pkg.get(key) or {})}
            kept = [n for n in theirs if n not in (pkg.get(key) or {})]
            pkg[key] = dict(sorted(merged.items()))
            if kept:
                self._log("INFO", f"   📦 kept {', '.join(sorted(kept)[:6])}"
                                  f"{'…' if len(kept) > 6 else ''} — already "
                                  f"installed, and an install would prune "
                                  f"anything this file does not name")
