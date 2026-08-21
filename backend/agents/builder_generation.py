"""Build/fix model calls and broken-file context selection."""
from .builder_common import *


class BuilderGenerationMixin:
    def __init__(self, ollama_url: str, model: str, project_dir: Path):
        self.url          = f"{ollama_url}/api/chat"
        self.model        = model
        self.project_dir  = Path(project_dir)
        self.built_files: dict[str, str] = {}

    def build(self, refined_prompt: str) -> bool:
        spec = {}
        try: spec = json.loads(refined_prompt)
        except: pass

        title        = spec.get("title", "My App")
        description  = spec.get("description", refined_prompt[:300])
        color        = spec.get("color_scheme", "dark with indigo and cyan accents")
        style        = spec.get("style", "modern")
        features     = spec.get("key_features", spec.get("features", []))
        site_type    = spec.get("site_type", "general")
        strategy     = spec.get("strategy", "react-sections")
        sections     = spec.get("sections", ["Hero", "Features", "About", "Contact"])
        instructions = spec.get("special_instructions", description)

        log.info(f"Strategy: {strategy} | Sections: {sections}")
        _emit(f"Strategy: {strategy} | Sections: {sections}")

        files: dict[str, str] = {}
        files.update(self._config_files(title))
        files["index.html"]    = self._index_html(title)
        files["src/main.jsx"]  = self._main_jsx()
        files["src/index.css"] = self._index_css(color)

        if strategy == "react-app":
            files["src/App.jsx"] = _single_app_shell()
            log.info("   Generating App component...")
            code = self._gen("App", self._app_prompt(title, description, color, style, instructions, features, site_type))
            files["src/components/App.jsx"] = code or _safe_component("App")
        else:
            files["src/App.jsx"] = _app_shell(title, sections)
            log.info("   Generating Navbar...")
            files["src/components/Navbar.jsx"] = (
                self._gen("Navbar", self._navbar_prompt(title, sections))
                or self._fallback_navbar(title, sections)
            )
            for section in [s for s in sections if s != "Navbar"]:
                log.info(f"   Generating {section}...")
                code = self._gen(section, self._section_prompt(
                    section, title, description, color, style, features, site_type, instructions))
                files[f"src/components/{section}.jsx"] = code or _safe_component(section)

        self._write(files)
        return self._install_deps()

    def fix(self, errors: list):
        """1. Run `npm run build` to get the real compile error with exact file+line."""
        log.info(f"   🔧 Starting fix pass ({len(errors)} tester errors)")

        build_errors = self._npm_build_errors()
        log.info(f"   npm build errors:\n{build_errors[:400] if build_errors else '  (none)'}")

        all_error_text = "\n".join(errors) + "\n" + build_errors

        broken = self._identify_broken(all_error_text)
        if not broken:

            broken = [f for f in self.built_files
                      if f.startswith("src/components/") and f.endswith(".jsx")]
            log.info(f"   No specific file found — regenerating all {len(broken)} components")
        else:
            log.info(f"   Broken files: {broken}")

        codebase_ctx = self._build_codebase_context()

        for fpath in broken:
            name = fpath.split("/")[-1].replace(".jsx", "").replace(".tsx", "")
            current = self.built_files.get(fpath, "")
            log.info(f"   Re-generating {fpath}...")

            file_errors = self._filter_errors_for_file(all_error_text, name, fpath)

            fixed = self._fix_component(name, current, file_errors, codebase_ctx)
            if not fixed:
                log.warning(f"   LLM fix failed for {fpath} — using safe fallback")
                fixed = _safe_component(name)

            self._write_one(fpath, fixed)
            log.info(f"   ✓ Saved {fpath} ({len(fixed)}B)")

    def _gen(self, component_name: str, user_prompt: str) -> str:
        """Stream generation, forward tokens to UI, return extracted code."""
        try:
            resp = requests.post(self.url, json={
                "model":   self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                "stream":  True,
                "options": {"temperature": 0.15, "num_predict": 4096},
            }, stream=True, timeout=240)
            resp.raise_for_status()

            _emit(f"\x00START:{component_name}")
            full = ""
            for line in resp.iter_lines():
                if not line: continue
                try:
                    chunk = json.loads(line)
                    tok   = chunk.get("message", {}).get("content", "")
                    if tok:
                        full += tok
                        _emit(tok)
                    if chunk.get("done"): break
                except: continue
            _emit("\x00END")

            if not hasattr(self, '_raw_llm_outputs'):
                self._raw_llm_outputs = {}
            self._raw_llm_outputs[component_name] = full
            return self._extract(full)

        except Exception as e:
            log.error(f"   LLM gen failed ({component_name}): {e}")
            _emit("\x00END")
            return ""

    def _fix_component(self, name: str, broken: str, errors: str, codebase: str, raw_context: str = "") -> str:
        """Ask LLM to fix a component, giving it full error context + full codebase."""

        console_errors = []
        for line in errors.splitlines():
            if "Console error" in line or "PageError" in line or "does not provide" in line:
                console_errors.append(line.strip())

        specific_fixes = []
        for err in console_errors:

            m = re.search(r"does not provide an export named '(\w+)'", err)
            if m:
                bad = m.group(1)
                specific_fixes.append(
                    f"- REMOVE \'{bad}\' from your imports — it does NOT EXIST in react-icons. "
                    f"Replace with a real icon: FiCircle for circles, FiX for X marks, FiGrid for grids."
                )
        for err in console_errors:
            if "Cannot find module" in err or "Failed to resolve" in err:
                specific_fixes.append(f"- Fix broken import: {err[:100]}")
            if "is not defined" in err:
                missing = re.search(r"(\w+) is not defined", err)
                if missing:
                    specific_fixes.append(
                        f"- '{missing.group(1)}' is not defined because you split it into a separate "
                        f"function. You MUST put ALL code into ONE single export default function {name}(). "
                        f"NO separate helper components allowed."
                    )

        if "appears blank" in errors or "no visible content" in errors or "readable text" in errors:
            specific_fixes.append(
                "- The page renders BLANK. The component must have an EXPLICIT dark background. "
                "Add className='min-h-screen bg-gray-900 text-white' to your outermost div. "
                "Do NOT rely on tailwind defaults or transparent containers."
            )

        console_section = ""
        if console_errors:
            console_section = (
                "\n═══ BROWSER CONSOLE ERRORS (these are the REAL runtime errors) ═══\n"
                + "\n".join(f"  {e[:200]}" for e in console_errors[:5])
                + "\n"
            )
        fixes_section = ""
        if specific_fixes:
            fixes_section = (
                "\n═══ SPECIFIC THINGS YOU MUST FIX ═══\n"
                + "\n".join(specific_fixes)
                + "\n"
            )

        raw_section = ""
        if raw_context:
            raw_section = (
                f"\n═══ PREVIOUS FULL OUTPUT (contains logic to merge into one function) ═══\n"
                f"{raw_context[:2000]}\n"
            )

        prompt = textwrap.dedent(f"""\
            Fix the broken React component below.
            {console_section}{fixes_section}
            ═══ ALL ERRORS ═══
            {errors[:400]}

            ═══ CODEBASE CONTEXT ═══
            {codebase[:1800]}

            ═══ BROKEN COMPONENT: {name} ═══
            {broken[:2500]}
            {raw_section}
            ═══ INSTRUCTIONS ═══
            - Fix EVERY error listed above — the browser console errors are the true cause
            - ONLY import from: react, react-dom, framer-motion, react-icons/*
            - BANNED packages (not installed, will crash): react-leaflet, react-router-dom,
              axios, lodash, chart.js, d3, three, @mui/material, @chakra-ui/react,
              react-query, zustand, styled-components, react-hot-toast, react-helmet
            - If you were using react-leaflet: replace with a <div> map placeholder
            - Only use icons that actually exist: FiHome, FiX, FiCircle, FiGrid, FiStar, FiMenu, etc.
            - Do NOT invent icon names — if unsure, use FiBox or FiSquare as a safe fallback
            - NEVER write /regex/ literals inside JSX — hoist them to const before return()
            - ALL logic must go inside the single export default function {name}() — no split components
            - Keep the same visual design and structure
            - Output ONLY the complete fixed JSX. Start with imports. No explanation.
            - Must end with: export default function {name}()
            """)
        try:
            resp = requests.post(self.url, json={
                "model":   self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "stream":  True,
                "options": {"temperature": 0.05, "num_predict": 4096},
            }, stream=True, timeout=180)
            resp.raise_for_status()

            _emit(f"\x00START:{name} (fix)")
            full = ""
            for line in resp.iter_lines():
                if not line: continue
                try:
                    chunk = json.loads(line)
                    tok   = chunk.get("message", {}).get("content", "")
                    if tok:
                        full += tok
                        _emit(tok)
                    if chunk.get("done"): break
                except: continue
            _emit("\x00END")
            result = self._extract(full)
            return result if "export default" in result else ""
        except Exception as e:
            log.error(f"   fix LLM call failed: {e}")
            _emit("\x00END")
            return ""

    def _npm_build_errors(self) -> str:
        """Run `npm run build` (vite build) which exits with real compile errors."""
        try:
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env={**__import__("os").environ, "CI": "true"}
            )

            output = (result.stdout + "\n" + result.stderr).strip()
            if result.returncode != 0:
                log.info(f"   npm build failed (good — we have the error)")
                return output[:2000]
            log.info("   npm build succeeded — no compile errors!")
            return ""
        except Exception as e:
            log.warning(f"   npm build check failed: {e}")
            return ""

    def _identify_broken(self, error_text: str) -> list:
        """Parse error text to find exactly which files are broken."""

        compile_match = re.search(
            r'\[plugin:vite[^\]]*\][^\n]*/src/components/(\w{1,50})\.(?:jsx?|tsx?)',
            error_text, re.IGNORECASE
        )
        if compile_match:
            fpath = f"src/components/{compile_match.group(1)}.jsx"
            log.info(f"   _identify_broken → [{fpath}] (Vite compile error)")
            return self._filter_owned([fpath])

        react_match = re.search(
            r'The above error occurred in the <(\w{1,50})> component',
            error_text, re.IGNORECASE
        )
        if react_match:
            fpath = f"src/components/{react_match.group(1)}.jsx"
            log.info(f"   _identify_broken → [{fpath}] (React runtime error)")
            return self._filter_owned([fpath])

        found = []
        for line in error_text.splitlines():
            if len(line) > 300:
                continue

            if re.search(r'at \w+ \(http', line):
                continue
            for m in re.finditer(
                r'[/\\]src[/\\]components[/\\](\w{1,50})\.(?:jsx?|tsx?)',
                line, re.IGNORECASE
            ):
                fpath = f"src/components/{m.group(1)}.jsx"
                if fpath not in found:
                    found.append(fpath)

        if not found:
            for line in error_text.splitlines():
                if re.search(r'at \w+ \(http', line):
                    continue
                for m in re.finditer(r"components?[/\\](\w{1,50})['\".:]", line):
                    fpath = f"src/components/{m.group(1)}.jsx"
                    if fpath not in found:
                        found.append(fpath)

        result = self._filter_owned(found)
        log.info(f"   _identify_broken → {result}")
        return result

    def _filter_owned(self, fpaths: list) -> list:
        """Filter file paths to only those we generated (in built_files or on disk)."""
        result = []
        for f in fpaths:
            if len(f) > 120:
                continue
            if f in self.built_files:
                result.append(f)
            else:
                try:
                    if (self.project_dir / f).exists():
                        result.append(f)
                except OSError:
                    pass
        return result

    def _filter_errors_for_file(self, all_errors: str, name: str, fpath: str) -> str:
        """Return lines from error text relevant to the given file."""
        relevant = []
        for line in all_errors.splitlines():
            if name in line or fpath in line or fpath.split("/")[-1] in line:
                relevant.append(line)
        return "\n".join(relevant) if relevant else all_errors[:600]

    def _build_codebase_context(self) -> str:
        """Return a concise summary of all generated files so the LLM has full context."""
        parts = []

        priority = ["src/App.jsx", "src/main.jsx", "src/index.css"]
        all_files = priority + [f for f in sorted(self.built_files) if f not in priority]
        for fname in all_files:
            content = self.built_files.get(fname, "")
            if not content:
                fp = self.project_dir / fname
                if fp.exists():
                    content = fp.read_text(encoding="utf-8", errors="replace")
            if not content:
                continue
            limit = 800 if fname.startswith("src/components/") else 400
            snippet = content[:limit] + (" ...[truncated]" if len(content) > limit else "")
            parts.append(f"── {fname} ──\n{snippet}")
        return "\n\n".join(parts)
