"""Initialization, model streaming, design skill and conversation startup."""
from .architect_common import *


class ArchitectRuntimeMixin:
    def __init__(self, client: OllamaClient, model: str, project_dir: Path,
                 callbacks: dict = None, stack: str = "next",
                 mongo_uri: str = "", db_name: str = "", dev_port: int = 5173,
                 think: bool = None, theme: str = "", theme_html: str = "",
                 theme_page: str = ""):
        self.client = client
        self.model = model
        self.project_dir = Path(project_dir)
        self.cb = callbacks or {}
        self.stack = stack if stack in PROMPTS else "next"
        self.mongo_uri = mongo_uri
        self.db_name = db_name

        self.dev_port = dev_port
        self.files = {}

        self.write_seq = 0

        self._scaffolding = False
        self.plan = {}
        self.user_prompt = ""
        self._vocab_cache = None
        self.plan_md = ""
        self.design_md = ""

        # The design the customer chose out of five, before the build.
        self.theme_id = theme or ""
        self.theme_html = theme_html or ""
        self.theme_page = theme_page or ""
        self.tokens_in = 0
        self.tokens_out = 0

        self.convo = []

        self._last_truncated = None

        self._refused = {}

        # Track files written during this build.
        self._owned = {}

        self._scaffold_baseline = {}
        self._invalid_generated = set()
        self._write_history = []

        self.num_ctx = max_context(model)
        self.is_cloud = is_cloud_model(model)
        # Context windows for models borrowed for a single call
        self._ctx_cache = {model: self.num_ctx} if model else {}

        self.think = think

        self.cmd = CommandRunner(
            self.project_dir,
            npm_bin=(self.cb or {}).get("npm_bin", "npm"),
            node_bin=(self.cb or {}).get("node_bin", "node"),
            on_log=lambda lvl, txt: self._fire("on_log", lvl, txt),
            on_event=lambda ev: self._fire("on_command", ev))

    def _fire(self, name, *a):
        fn = self.cb.get(name)
        if fn:
            try:
                fn(*a)
            except Exception as e:
                log.warning(f"callback {name} failed: {e}")

    def _log(self, lvl, txt):

        if self.cb and self.cb.get("on_log"):
            self._fire("on_log", lvl, txt)
            return
        log.info(txt)

    @property
    def _P(self) -> dict:
        return PROMPTS[self.stack]

    @property
    def vocab(self):
        """The nouns this app is about, for the examples in every prompt."""
        from .app_vocab import derive
        cached = getattr(self, "_vocab_cache", None)
        key = (str(getattr(self, "user_prompt", "") or "")[:400],
               len((self.plan or {}).get("phases") or []))
        if cached and cached[0] == key:
            return cached[1]
        got = derive(getattr(self, "user_prompt", "") or "", self.plan or {})
        self._vocab_cache = (key, got)
        return got

    def _fit_vocab(self, text: str) -> str:
        from .app_vocab import render
        try:
            return render(text, self.vocab)
        except Exception as e:                                  # noqa: BLE001
            log.debug(f"vocabulary not applied: {e}")
            return text

    def _builder_sys(self) -> str:
        base = self._fit_vocab(self._P["builder"]).replace(
            "{stack}", self._P["rules"])
        try:
            from .source_guidance import HUMAN_COMMENT_POLICY
            base += "\n\n    " + HUMAN_COMMENT_POLICY.replace("\n", "\n    ")
        except Exception as e:  # noqa: BLE001
            log.debug(f"source guidance unavailable: {e}")

        # What earlier builds got wrong, from the checks that found it.
        try:
            from . import lessons
            learned = lessons.prompt_block()
            if learned:
                base += "\n\n    " + learned.replace("\n", "\n    ")
        except Exception as e:                                  # noqa: BLE001
            log.debug(f"lessons unavailable: {e}")

        # What this run has already tried, in the agent's own words.
        try:
            from .agent_memory import memory_for
            notebook = memory_for(self, "architect").block()
            if notebook:
                base += "\n\n    " + notebook.replace("\n", "\n    ")
        except Exception as e:                                  # noqa: BLE001
            log.debug(f"agent memory unavailable: {e}")

        # The builder can inspect existing source.
        try:
            from .workspace import TOOL_HELP
            base += "\n\n    " + TOOL_HELP.replace("\n", "\n    ")
        except Exception as e:  # noqa: BLE001
            log.debug(f"workspace tools unavailable: {e}")

        idx = (docsindex.index_block(self.project_dir)
               if self.stack == "next" else "")
        if not idx:
            return base
        return base + "\n\n    NEXT.JS DOCUMENTATION — READ IT:\n" + idx

    def _planner_sys(self) -> str:
        return self._fit_vocab(self._P["planner"]).replace(
            "{stack}", self._P["rules"])

    @property
    def source_roots(self) -> tuple:
        return self._P["roots"]

    def is_source(self, path: str) -> bool:
        return path.startswith(self.source_roots) and path.endswith((".jsx", ".js"))

    EDIT_TIMEOUT = 150

    # No legitimate turn writes this much.
    MAX_TURN_CHARS = 250_000

    LOUD_TURN_CHARS = 100_000

    LOOP_BLOCK = 240
    LOOP_REPEATS = 6

    # The turn that thinks and never speaks.
    MAX_SILENT_THINKS = 20_000
    MAX_SILENT_SECONDS = 240

    def _stream(self, messages, on_delta, tools=None, temperature=0.6,
                model=None, timeout=None):
        """Stream a chat completion. `on_delta(text)` gets content deltas."""
        calls, stalled = self._stream_once(
            messages, on_delta, tools=tools, temperature=temperature,
            model=model, timeout=timeout, think=self.think)

        if stalled and self.think:
            self._log("WARN", "   ↻ taking that turn again with the reasoning "
                              "pass off — it is what stalled")
            calls, _ = self._stream_once(
                messages, on_delta, tools=tools, temperature=temperature,
                model=model, timeout=timeout, think=False)
        return calls

    def ctx_for(self, model: str = "") -> int:
        """The context window of the model this ONE call really runs on."""
        name = str(model or "").strip() or self.model
        if not name:
            return self.num_ctx
        if name in self._ctx_cache:
            return self._ctx_cache[name]
        try:
            ctx = max_context(name) or self.num_ctx
        except Exception as e:
            log.debug(f"context window for {name}: {e}")
            ctx = self.num_ctx
        self._ctx_cache[name] = ctx
        return ctx

    def _stream_once(self, messages, on_delta, *, tools, temperature, model,
                     timeout, think):
        """One attempt at the turn."""
        tool_calls, thought, stalled = [], False, False
        options = {"temperature": temperature, "top_p": 0.9,
                   "num_ctx": self.ctx_for(model)}
        kw = {"timeout": timeout} if timeout else {}
        started = time.time()
        spoke = started
        thinks = 0
        chars = 0
        chunks = 0
        first_at = 0.0
        tail = []  # Deltas not yet folded into a block
        tail_len = 0
        seen_blocks = {}  # Block hash -> how many times it has arrived
        looping = 0
        for chunk in self.client.chat_stream(
                model or self.model, messages, tools=tools, options=options,
                keep_alive="10m", think=think, **kw):
            msg = chunk.get("message") or {}

            if msg.get("thinking"):
                thinks += 1
                if not thought:
                    thought = True
                    self._log("INFO", "   🤔 Thinking…")

            delta = msg.get("content") or ""
            if delta:
                chars += len(delta)
                if not first_at:
                    first_at = time.time()

                tail.append(delta)
                tail_len += len(delta)
                while tail_len >= self.LOOP_BLOCK:
                    joined = "".join(tail)
                    block, rest = (joined[:self.LOOP_BLOCK],
                                   joined[self.LOOP_BLOCK:])
                    tail, tail_len = ([rest], len(rest)) if rest else ([], 0)
                    if not block.strip():
                        continue
                    key = hash(block)
                    seen_blocks[key] = seen_blocks.get(key, 0) + 1
                    looping = max(looping, seen_blocks[key])

            now = time.time()
            if now - spoke >= 30:
                spoke = now
                elapsed = now - started
                if chars:
                    wrote = f"{chars:,} characters written"
                    if looping > 1:
                        wrote += (f" — {looping} of them identical, so it is "
                                  f"repeating itself; stopping it at "
                                  f"{self.LOOP_REPEATS}")
                    elif chars >= self.LOUD_TURN_CHARS:
                        wrote += (f" — far past what one turn should write; "
                                  f"stopping it at {self.MAX_TURN_CHARS:,}")
                elif thinks:
                    wrote = f"thinking, {thinks:,} token(s), nothing written yet"
                else:

                    wrote = (f"{chunks:,} empty chunk(s) — the model has sent "
                             f"nothing usable yet")
                self._log("INFO", f"   ⏳ still working — {elapsed:.0f}s, {wrote}")
            chunks += 1
            if delta:
                on_delta(delta)
            for tc in (msg.get("tool_calls") or []):
                tool_calls.append(tc)
            if chunk.get("done"):
                self.tokens_in += chunk.get("prompt_eval_count", 0) or 0
                self.tokens_out += chunk.get("eval_count", 0) or 0

            # Both checks run AFTER the delta is delivered.
            if looping >= self.LOOP_REPEATS:
                self._log("WARN",
                          f"   ✂ stopped the model — it sent the same passage "
                          f"{looping} times ({chars:,} characters, "
                          f"{time.time() - started:.0f}s). It is looping, not "
                          f"working. Carrying on with what it wrote.")
                break
            if chars >= self.MAX_TURN_CHARS:
                self._log("WARN",
                          f"   ✂ stopped the model after {chars:,} characters "
                          f"in one turn ({time.time() - started:.0f}s) — that "
                          f"is a runaway, not an answer. Carrying on with what "
                          f"it wrote.")
                break

            if not chars and (thinks >= self.MAX_SILENT_THINKS
                              or now - started >= self.MAX_SILENT_SECONDS):
                self._log("WARN",
                          f"   ✂ stopped the model — {thinks:,} thinking "
                          f"token(s) over {now - started:.0f}s and not one "
                          f"character written. That is not a slow answer, it "
                          f"is a turn that was never going to end.")
                stalled = True
                break
        return tool_calls, stalled

    # The frontend craft the plan never carries.
    DESIGN_SKILL = """\
# Frontend design — how this app is built

The plan says WHAT to build. This says how it should look and behave. Both
are binding; where they disagree about a name or a screen, the plan wins.

## The look this app is going for

{LOOK}

## Ground rules

- If the look above contains an **Approved HTML theme receipt**, every colour, font, radius, spacing, width, border and shadow value in that receipt is binding. It overrides generic examples below. Do not "improve" it into another design system.
- Tailwind utilities only. No inline `style=`, no CSS modules, no styled
  components. `app/globals.css` already has base/components/utilities.
- Icons come from `lucide-react`, at `size-4` in text and `size-5` in
  headers. Never an emoji as an icon.
- One page = one job. If a page needs two headings of equal weight, it is
  two pages.

## Layout

- Reuse the approved content width, page padding and section spacing from the look above. Only when the approved design omits one of those values may you fall back to `mx-auto w-full max-w-5xl px-4 sm:px-6` and `space-y-6`.
- A page starts with a clear header block when its job needs one. Never a bare table.

## Type

- Reuse the approved font family, sizes, weights, line heights and tracking. Generic Tailwind type sizes are fallback values only when the approved design has no evidence for that level.
- Numbers a person compares — money, counts, dates — get `tabular-nums` without changing the approved type scale.

## Colour and depth

- Reuse the approved page/surface/text/border/accent colours exactly. Do not reduce a multi-accent approved design to one accent, and do not add colours absent from it except semantic success/warning/error states.
- Reuse the approved border-versus-shadow strategy and radius scale. Do not replace shadows with borders or rounded cards with square ones because a generic rule prefers them.
- Green means done, amber means attention, red means failed or destructive.
  Never use red for a heading or a border just to decorate it.
- Body text against its background must stay readable: `text-neutral-700` or
  darker on white, `text-neutral-200` or lighter on a dark surface. Never
  put `text-neutral-400` on white and call it body copy.

## The three states every screen needs

This is the most-skipped part of a generated app. A list, a table or a
fetched panel has THREE outcomes and all three are visible work:

1. **Loading** — a skeleton block of the right shape, not a centred spinner
   and never a blank screen.
2. **Empty** — a short sentence saying what would be here and the button
   that creates the first one. "No results" alone is not an empty state.
3. **Failed** — what went wrong in a plain sentence, and a Retry button.
   Never a silent `catch {}` and never raw JSON on screen.

## Forms

- Every input has a real `<label>`, not a placeholder standing in for one.
- Errors show under the field they belong to, in red, in words a customer
  understands — "Enter an email address", not "validation failed".
- The submit button disables and says what it is doing ("Saving…") while the
  request is still running, so nothing is submitted twice.
- After a successful write: say so, and either navigate or refresh the list.

## Tables and lists

- Right-align money and counts, left-align text, `whitespace-nowrap` on
  dates.
- Row actions live in the last column, never a row-wide click that hides
  where the click goes.
- Over ~20 rows: paginate or scroll inside the table, not the page.

## Responsive

- Build the small screen first; `sm:` and `lg:` add to it.
- A table that cannot fit becomes stacked cards under `sm`, not a sideways
  scrollbar the reader has to find.
- Nothing may overflow horizontally at 360px wide.

## Never

- Never a `<div>` with an `onClick` where a `<button>` belongs.
- Never a colour or a size that appears exactly once — reuse the scale.
- Never leave a nav link pointing at a page that does not exist.
"""

    def _design_md(self) -> str:
        """The design skill, with this project's own look spliced in."""
        look = str((self.plan or {}).get("look_and_feel") or "").strip()
        if not look:
            m = re.search(r"^#+\s*Look and feel\s*$(.+?)(?=^#|\Z)",
                          self.plan_md or "", re.M | re.S | re.I)
            if m:
                look = " ".join(m.group(1).split()).strip()

        if self.theme_html:
            receipt = themes.theme_facts_markdown(self.theme_html)
            try:
                r = self.client.chat(
                    self.model,
                    [{"role": "system", "content": themes.DESIGN_SYSTEM},
                     {"role": "user", "content": themes.design_prompt(
                         self.theme_html, self.theme_page)}],
                    options={"temperature": 0.2}, timeout=240)
                got = ((r.get("message") or {}).get("content") or "").strip()
                got = re.sub(r"^```[a-z]*\n|\n```$", "", got).strip()
                if len(got) > 200:
                    look_contract = receipt + "\n\n### Interpreted design rules\n" + got if receipt else got
                    self._log("INFO", "   🎨 design.md written from the design "
                                      "you approved")
                    return self.DESIGN_SKILL.replace("{LOOK}", look_contract)
                if receipt:
                    self._log("WARN", "   ⚠ the design interpretation came back thin — "
                                      "using the exact CSS receipt")
                    return self.DESIGN_SKILL.replace("{LOOK}", receipt)
                self._log("WARN", "   ⚠ the design read came back thin — "
                                  "using the plan's look and feel")
            except Exception as e:
                if receipt:
                    self._log("WARN", f"   ⚠ design interpretation failed ({str(e)[:80]}) — "
                                      "using the exact CSS receipt")
                    return self.DESIGN_SKILL.replace("{LOOK}", receipt)
                self._log("WARN", f"   ⚠ could not read the approved design "
                                  f"({str(e)[:80]}) — using the plan's look "
                                  f"and feel")

        if not look:
            look = ("Nothing specific was asked for, so: clean, plain and "
                    "modern. Neutral greys, one accent colour, generous "
                    "whitespace, and no decoration that does not carry "
                    "meaning.")
        return self.DESIGN_SKILL.replace("{LOOK}", look)

    def start_conversation(self, user_prompt: str):
        """Open the single chat thread the whole build runs in."""
        self.convo = [
            {"role": "system", "content": self._builder_sys()},
            {"role": "user", "content": textwrap.dedent(f"""\
                We are building this app together, in one continuous pass.

                ## The idea
                {user_prompt}

                ## The approved plan
                {self.plan_md}

                ## design.md — how the frontend is built
                This is in the project as `design.md`. It is not advice: every
                screen you write follows it, and the three states (loading,
                empty, failed) are required work, not extras.

                {self.design_md or self._design_md()}

                In a moment I will give you the whole build, broken into
                numbered tasks. Write straight down it and do NOT stop at a
                task boundary — stop only when you run out of room, at a file
                boundary, and I will say "Continue" and you pick up at the next
                unwritten file. I will never ask you to start over.

                This thread is trimmed as it grows, so your own earlier code
                stops being visible in it. That is why every "Continue"
                rebuilds the important truth from scratch and sends it to you
                again: what every file already on disk exports and what props
                it takes; the capability proof ledger (every user-visible job
                the app still has to make real); the canonical Mongo
                collection/field vocabulary; the user journeys; and the exact
                cross-file contracts that say which control calls which URL,
                with which method/fields, and what must happen on success. The
                design, roles and UI routes
                are rebuilt too. Those lists are the truth about the app and
                your recollection is not, so import, fetch, render and navigate
                against them. Never invent a synonym for a field or a second
                URL for an action. Do not write any code yet.
                """)},
            {"role": "assistant", "content":
                "Understood. I have the plan and design.md. I will work "
                "straight down the tasks without stopping at a boundary, and "
                "stop only at a file boundary when I run out of room. When a "
                "Continue turn lists what a file exports and what props it "
                "takes, I will take the contract from that list rather than "
                "from my memory of writing the file — the list is rebuilt "
                "from disk every turn and my memory of the code is not. Send "
                "me the tasks."},
        ]
