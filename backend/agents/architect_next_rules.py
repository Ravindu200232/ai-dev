"""Next auth/root-layout verification, generated linting and component checks."""
from .architect_common import *


class ArchitectNextRulesMixin:
    def fix_next_imports(self) -> int:
        """Rewrite the Pages-Router habits models fall back into."""
        fixed = 0
        for path, content in self._next_files():
            out = content
            out = out.replace("from 'next/router'", "from 'next/navigation'")
            out = out.replace('from "next/router"', 'from "next/navigation"')

            if "<Head" not in out:
                out = re.sub(r"^\s*import\s+\w+\s+from\s+['\"]next/head['\"].*$\n?",
                             "", out, flags=re.M)
            out = re.sub(r"(<Link\b[^>]*?)\sto=", r"\1 href=", out)

            out = re.sub(r"https?://localhost:\d+", "", out)
            if out != content:
                self.write_file(path, out)
                fixed += 1
        if fixed:
            self._log("INFO", f"   🔧 Fixed Next.js imports in {fixed} file(s)")
        return fixed

    TRUSTED_ORIGINS = "['http://localhost:*', 'http://127.0.0.1:*']"
    TRUSTED_RE = re.compile(r"^\s*trustedOrigins:.*?,\s*$\n?", re.M | re.S)

    def verify_auth_config(self) -> bool:
        """Make sure Better Auth trusts the origin the app is actually viewed from."""
        auth = self.files.get("lib/auth.js")
        if not auth or "betterAuth(" not in auth:
            return True
        if self.TRUSTED_ORIGINS in auth:
            return True

        if "trustedOrigins" in auth:

            fixed = self.TRUSTED_RE.sub("", auth, count=1)
            why = "replacing a hardcoded origin list"
        else:
            fixed, why = auth, "it trusted no origin but its own baseURL"

        anchor = "  plugins:"
        if anchor not in fixed:
            anchor = "})"
        if anchor not in fixed:
            self._log("WARN", "   ⚠ lib/auth.js has no place to add "
                              "trustedOrigins — leaving it alone")
            return False
        fixed = fixed.replace(
            anchor,
            f"  trustedOrigins: {self.TRUSTED_ORIGINS},\n{anchor}", 1)

        self._log("WARN", f"   🔧 lib/auth.js — {why}; the preview is served "
                          f"from a different port than the dev server, and "
                          f"Better Auth answers 'Invalid origin' for it")
        self._scaffolding = True
        try:
            return self.write_file("lib/auth.js", fixed)
        finally:
            self._scaffolding = False

    _SOLE_ELEMENT_RE = r"^[ \t]*<%s(?:\s[^>]*?)?/>[ \t]*\r?\n"

    def _drop_layout_duplicates(self, key: str, content: str) -> str:
        """Take out what the ROOT LAYOUT already renders."""
        if not key.startswith("app/") or not key.endswith((".jsx", ".js")):
            return content
        name = key.rsplit("/", 1)[-1]
        if not (name.startswith("page.") or name.startswith("layout.")):
            return content
        if key in ("app/layout.jsx", "app/layout.js"):
            return content

        root = self.files.get("app/layout.jsx") or self.files.get("app/layout.js")
        if not root:
            return content
        rendered = {m for m in re.findall(r"<([A-Z]\w*)\s*(?:\s[^>]*?)?/>", root)}
        if not rendered:
            return content

        for comp in sorted(rendered):
            if f"<{comp}" not in content:
                continue
            fixed, n = re.subn(self._SOLE_ELEMENT_RE % comp, "", content,
                               flags=re.M)
            if not n:
                self._log("WARN", f"   ⚠ {key} renders <{comp}/>, which the "
                                  f"root layout already renders — that is two "
                                  f"of them on the page. Left as it is: it is "
                                  f"not on a line of its own.")
                continue

            if f"<{comp}" not in fixed:
                fixed = re.sub(
                    r"^import\s+%s\s+from\s+['\"][^'\"]+['\"];?[ \t]*\r?\n" % comp,
                    "", fixed, flags=re.M)
            self._log("WARN", f"   🔧 {key} — removed <{comp}/>; the root "
                              f"layout already renders it, so the page was "
                              f"showing two")
            content = fixed
        return content

    def verify_root_layout(self) -> bool:
        """The model may overwrite layout.js and drop what makes it a layout."""
        layout = self.files.get("app/layout.js") or self.files.get("app/layout.jsx")
        if layout and all(t in layout for t in ("<html", "<body", "globals.css")):
            return True
        self._log("WARN", "   🔧 Restoring app/layout.js — it lost <html>/<body>")
        title = self.plan.get("title", "AgentForge App")
        self.write_file("app/layout.jsx", textwrap.dedent(f"""\
            import './globals.css'

            export const metadata = {{ title: {json.dumps(title)} }}

            export default function RootLayout({{ children }}) {{
              // suppressHydrationWarning is on <html> and <body> because
              // extensions — Grammarly (data-gr-ext-installed), QuillBot
              // (data-qb-installed), password managers — inject attributes
              // into exactly these two elements before React hydrates. That
              // produces a red "tree hydrated but some attributes … didn't
              // match" overlay on every page for anyone running one, and it
              // is not a fault in the app. The suppression is one level deep:
              // real mismatches inside the tree are still reported.
              return (
                <html lang="en" suppressHydrationWarning>
                  <body className="min-h-screen antialiased" suppressHydrationWarning>
                    {{children}}
                  </body>
                </html>
              )
            }}
            """))
        return False

    STRAY_DIRECTIVE_RE = re.compile(
        r"^[^\S\n]*['\"]use client['\"][^\S\n]*;?[^\S\n]*$", re.M)

    def _undefined_collection_handles(self, path: str, content: str) -> list:
        """Catch runtime-only `ordersCol is not defined` style defects."""
        # Remove comments and quoted/template text so examples/copy do
        code = re.sub(r"/\*.*?\*/|//[^\n]*", " ", content, flags=re.S)
        code = re.sub(r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`""",
                      " ", code, flags=re.S)
        used = set(re.findall(r"(?<![.\w])([A-Za-z_$][\w$]*Col)\b", code))
        if not used:
            return []
        declared = set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*Col)\b", code))
        for names in re.findall(r"\b(?:const|let|var)\s*\[([^\]]+)\]", code):
            declared.update(re.findall(r"\b([A-Za-z_$][\w$]*Col)\b", names))
        for names in re.findall(r"\bimport\s*\{([^}]+)\}", code):
            declared.update(re.findall(r"\b([A-Za-z_$][\w$]*Col)\b", names))
        for params in re.findall(r"(?:function\s+\w*|\([^)]*\)\s*=>)\s*\(([^)]*)\)", code):
            declared.update(re.findall(r"\b([A-Za-z_$][\w$]*Col)\b", params))
        missing = sorted(used - declared)
        return [f"{path}: uses collection handle `{name}` without declaring it "
                f"in scope — `next build` may pass, but the first request will "
                f"throw ReferenceError. Open it with getCollection(...) before "
                f"first use or use the declared handle name." for name in missing]

    RUNTIME_HELPER_OWNERS = {
        "getSessionUser": "@/lib/auth",
        "getCollection": "@/lib/mongodb",
        "getDb": "@/lib/mongodb",
        "serialize": "@/lib/mongodb",
        "notFound": "next/navigation",
        "redirect": "next/navigation",
        "NextResponse": "next/server",
        "ObjectId": "mongodb",
    }

    def _undefined_runtime_helpers(self, path: str, content: str) -> list:
        """Catch scaffold/framework helpers that JavaScript can leave free."""
        code = re.sub(r"/\*.*?\*/|//[^\n]*", " ", content, flags=re.S)
        # Keep import statements themselves
        scrub = re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`",
                       " ", code, flags=re.S)
        out = []
        for name, owner in self.RUNTIME_HELPER_OWNERS.items():
            # Calls and namespace/class-style member use are the risky
            used = bool(re.search(rf"\b{re.escape(name)}\s*(?:\(|\.)", scrub))
            if not used:
                continue

            imported = bool(re.search(
                rf"\bimport\s+(?:[^;\n]*?\b{re.escape(name)}\b[^;\n]*?\s+from\s+|{re.escape(name)}\s+from\s+)",
                code))
            declared = bool(re.search(
                rf"\b(?:function|class)\s+{re.escape(name)}\b|"
                rf"\b(?:const|let|var)\s+{re.escape(name)}\b", scrub))
            if imported or declared:
                continue

            out.append(
                f"{path}: uses runtime helper `{name}` without importing or "
                f"declaring it — `next build` can still pass and the first "
                f"request can throw ReferenceError. Import `{name}` from "
                f"'{owner}' before use and keep the existing component boundary.")
        return out

    def lint_generated(self) -> list:
        """Problems worth one targeted repair turn rather than shipping."""
        errors = []
        for path, content in self._next_files():
            hits = list(self.STRAY_DIRECTIVE_RE.finditer(content))

            for m in hits:
                head = content[:m.start()]
                head = re.sub(r"//[^\n]*|/\*.*?\*/", "", head, flags=re.S).strip()
                if head:
                    line = content[:m.start()].count("\n") + 1
                    errors.append(
                        f"{path}:{line}: a 'use client' directive appears after "
                        f"other code — it must be the first line of the file, "
                        f"and only once. Move the interactive part into its own "
                        f"file under components/ and import it.")
                    break
        ts = re.compile(r"^\s*interface\s+\w+|:\s*(string|number|boolean|any)\s*[,)=;]"
                        r"|\bas\s+(string|number|const)\b")
        for path, content in self._next_files():
            errors.extend(self._undefined_collection_handles(path, content))
            errors.extend(self._undefined_runtime_helpers(path, content))
            if ts.search(content):
                errors.append(f"{path}: contains TypeScript syntax — this is a "
                              f"JavaScript project")
            if "react-router-dom" in content:
                errors.append(f"{path}: imports react-router-dom — Next.js uses "
                              f"filesystem routing, not a router library")
            if (path not in {"lib/mongodb.js", "lib/auth.js"}
                    and re.search(r"\bnew\s+MongoClient\b", content)):
                # These are framework-owned boundaries.
                errors.append(f"{path}: constructs a MongoClient — import "
                              f"getDb/getCollection from '@/lib/mongodb' instead")
            # Standalone mongod refuses every transaction.
            if re.search(r"\bstartSession\s*\(|\bwithTransaction\s*\(", content):
                errors.append(f"{path}: opens a MongoDB transaction — the mongod "
                              f"is standalone and answers 'Transaction numbers "
                              f"are only allowed on a replica set member or "
                              f"mongos'; use one atomic updateOne with a guard "
                              f"in the filter and check modifiedCount instead")
            for hit in re.finditer(
                    r"\$inc\s*:\s*\{\s*([A-Za-z_][\w.]*)\s*:\s*-\s*\d", content):
                field = hit.group(1)
                near = content[max(0, hit.start() - 600):hit.end() + 400]
                guarded = re.search(re.escape(field) + r"\s*:\s*\{\s*\$gte?\b", near)
                if not guarded and "modifiedCount" not in near:
                    errors.append(
                        f"{path}: decrements {field} with no guard — two "
                        f"requests can both pass the check and both decrement; "
                        f"put {field}: {{ $gt: 0 }} in the updateOne filter and "
                        f"treat modifiedCount === 0 as the failure")
            if "next/head" in content and "<Head" in content:
                errors.append(f"{path}: uses next/head — the App Router has no "
                              f"<Head>; export a `metadata` object instead")
            if path.endswith("route.js") and re.search(r"export\s+default", content):
                errors.append(f"{path}: route handlers must use named exports "
                              f"(GET/POST/...), never export default")
        for path in self.files:
            if path.startswith("pages/"):
                errors.append(f"{path}: the Pages Router is banned — put routes "
                              f"under app/")
            if path.endswith((".ts", ".tsx")):
                errors.append(f"{path}: TypeScript files are banned — use .js/.jsx")

        try:
            from agents.exports import check_syntax, syntax_messages
            broken, _why = check_syntax(self.project_dir, self.files)
            errors.extend(syntax_messages(broken))
        except Exception:                                  # noqa: BLE001

            pass

        for name in self.unresolved_packages():
            errors.append(f"'{name}' is imported but not installed — run "
                          f"<run_command>npm install {name}</run_command>")

        errors.extend(self.client_server_mix())
        errors.extend(self.undefined_jsx_components())
        errors.extend(self.orphaned_components())
        errors.extend(self.event_handlers_in_server())
        errors.extend(self.component_props_to_client())
        errors.extend(self.bson_props_to_client())
        errors.extend(self.broken_named_imports())

        for path in self.missing_planned_files():
            errors.append(f"{path}: the plan lists this file but it was never "
                          f"written — the route it serves will 404")
        return errors

    def lint_plan_contracts(self) -> list:
        """High-confidence accepted-plan obligations checked before handoff."""
        if self.stack != "next":
            return []
        try:
            from .analyzer import AnalyzerAgent
            az = AnalyzerAgent(self, self.project_dir)
            findings = []
            for check in (az.layout_chrome, az.contract_findings,
                          az.planned_data_findings, az.action_id_findings):
                try:
                    findings.extend(check() or [])
                except Exception as e:  # One proof must not disable the rest
                    log.debug(f"builder contract lint {check.__name__}: {e}")
        except Exception as e:
            log.debug(f"builder contract lint unavailable: {e}")
            return []

        out, seen = [], set()
        for f in findings:
            if str(getattr(f, "severity", "")).lower() not in {"major", "blocker"}:
                continue
            path = str(getattr(f, "path", "") or "app")
            code = str(getattr(f, "code", "PLAN_CONTRACT"))
            msg = str(getattr(f, "message", "")).strip()
            fix = str(getattr(f, "fix", "")).strip()
            line = f"{path}: {code}: {msg}"
            if fix:
                line += f". FIX: {fix}"
            if line in seen:
                continue
            seen.add(line)
            out.append(line)
        return out[:18]

    JSX_TAG_RE = re.compile(r"</?([A-Z][\w.]*)")

    def orphaned_components(self) -> list:
        """Components written and then never rendered by anything."""
        out = []
        comps = {p: c for p, c in self._next_files()
                 if p.startswith("components/") and p.endswith((".jsx", ".js"))}
        if not comps:
            return out
        others = [(p, c) for p, c in self._next_files() if p not in comps]
        for path in sorted(comps):
            name = Path(path).stem
            if name.lower() in ("index",):
                continue
            used = re.compile(rf"\b{re.escape(name)}\b")
            if any(used.search(body) for _, body in others):
                continue

            if any(used.search(b) for p, b in comps.items() if p != path):
                continue
            out.append(
                f"{path}: nothing imports or renders {name}. It was written "
                f"and then left out of the tree, so none of it reaches a "
                f"page. Render it where its planned pages need it. If auth "
                f"pages exist, shared navigation must NOT go in the root "
                f"layout; render it from the non-auth pages or a non-auth "
                f"shell instead. Otherwise delete it only when the plan truly "
                f"does not need it.")
        return out

    def undefined_jsx_components(self) -> list:
        """Components rendered in JSX that the file never imports or defines."""
        out = []
        for path, content in self._next_files():
            code = strip_noncode(content)
            used = {m.group(1).split(".")[0]
                    for m in self.JSX_TAG_RE.finditer(code)}
            missing = []
            for name in sorted(used):
                total = len(re.findall(rf"\b{re.escape(name)}\b", code))
                as_tag = len(re.findall(rf"</?{re.escape(name)}\b", code))
                if total and total == as_tag:
                    missing.append(name)
            if missing:
                out.append(
                    f"{path}: renders {', '.join(missing[:5])} but never "
                    f"imports or defines "
                    f"{'them' if len(missing) > 1 else 'it'} — this compiles "
                    f"and then throws \"{missing[0]} is not defined\" the "
                    f"first time the page is requested. Add the missing "
                    f"import at the top (lucide icons come from "
                    f"'lucide-react'), or remove the element.")
        return out


FIELD_TAG_RE = re.compile(r"<(input|select|textarea)\b([^>]*?)/?>", re.I | re.S)
NAMED_ATTR_RE = re.compile(
    r"\b(aria-label|aria-labelledby|name|id|placeholder|data-testid|title)\s*=",
    re.I)
HIDDEN_TYPE_RE = re.compile(r"""type\s*=\s*[\"']?\{?\s*[\"']?(hidden)""", re.I)


def unnamed_fields(files: dict) -> list:
    """Every form control a person could not find by name.

    A box with no `aria-label`, label, `name`, `id`, `placeholder` or
    `data-testid` can only be reached by its position on the page. The E2E
    then cannot write a stable step for it, and the run ends up reporting a
    working app as broken.
    """
    out = []
    for rel, body in (files or {}).items():
        if not str(rel).endswith((".jsx", ".tsx", ".js")):
            continue
        for m in FIELD_TAG_RE.finditer(str(body or "")):
            tag, attrs = m.group(1).lower(), m.group(2) or ""
            if HIDDEN_TYPE_RE.search(attrs) or NAMED_ATTR_RE.search(attrs):
                continue
            line = str(body or "")[:m.start()].count("\n") + 1
            out.append({
                "file": rel, "line": line, "tag": tag,
                "snippet": re.sub(r"\s+", " ", m.group(0))[:160],
                "why": (f"this <{tag}> has no aria-label, label, name, id, "
                        f"placeholder or data-testid, so no test can name it"),
            })
    return out


# What the box is bound to, which is what a person would call it.
_BOUND_RE = re.compile(
    r"\b(?:value|checked|defaultValue|defaultChecked)\s*=\s*\{\s*"
    r"([A-Za-z_$][\w$]*)(?:\s*\.\s*([A-Za-z_$][\w$]*))?", re.S)
_TYPE_RE = re.compile(r"""\btype\s*=\s*[\"']([a-z-]+)[\"']""", re.I)
_ONCHANGE_SETTER_RE = re.compile(r"\bset([A-Z][\w$]*)\s*\(")

_LABEL_STOP = {"e", "ev", "event", "val", "v", "x", "row", "item", "data",
               "state", "form", "input", "field", "target", "props", "this"}


def _readable(name: str) -> str:
    """`cleaningStatus` -> `Cleaning status`, `room_price` -> `Room price`."""
    spaced = re.sub(r"[_-]+", " ", str(name or "").strip())
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", spaced)
    spaced = re.sub(r"\s+", " ", spaced).strip().lower()
    return spaced[:1].upper() + spaced[1:] if spaced else ""


def field_label(attrs: str) -> str:
    """A name for this control, taken from whatever it is already bound to."""
    for m in _BOUND_RE.finditer(attrs):
        for part in (m.group(2), m.group(1)):
            if part and part.lower() not in _LABEL_STOP:
                got = _readable(part)
                if len(got) > 1:
                    return got
    setter = _ONCHANGE_SETTER_RE.search(attrs)
    if setter and setter.group(1).lower() not in _LABEL_STOP:
        got = _readable(setter.group(1))
        if len(got) > 1:
            return got
    kind = _TYPE_RE.search(attrs)
    if kind and kind.group(1).lower() not in ("text", "hidden"):
        return _readable(kind.group(1))
    return ""


def name_unnamed_fields(body: str) -> tuple:
    """Give every anonymous form control an `aria-label`. Returns (body, notes).

    A box with nothing but a className cannot be named by a test, so the E2E
    reports a working app as broken. The name it gets here is the one the
    code already uses for the value it holds, which is what a person reading
    the page would call it too.
    """
    text = str(body or "")
    notes, out, last = [], [], 0
    for m in FIELD_TAG_RE.finditer(text):
        tag, attrs = m.group(1).lower(), m.group(2) or ""
        if HIDDEN_TYPE_RE.search(attrs) or NAMED_ATTR_RE.search(attrs):
            continue
        label = field_label(attrs)
        if not label:
            continue
        insert = m.start() + 1 + len(tag)
        out.append(text[last:insert])
        out.append(f' aria-label="{label}"')
        last = insert
        notes.append(f"<{tag}> -> aria-label=\"{label}\"")
    if not notes:
        return text, []
    out.append(text[last:])
    return "".join(out), notes
