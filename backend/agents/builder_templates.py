"""Config/component prompt helpers and generated component extraction."""
from .builder_common import *


class BuilderTemplateMixin:
    def _extract(self, text: str) -> str:
        """Extract JSX code from LLM output, stripping markdown fences."""
        if not text:
            return ""

        for lang in ["jsx", "tsx", "javascript", "js", "typescript", "ts", ""]:
            m = re.search(rf"```{lang}\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
            if m:
                return m.group(1).strip()

        t = text.strip()
        if any(k in t for k in ["import ", "export default", "function ", "const ", "return ("]):
            return t
        return ""

    def _config_files(self, title: str) -> dict:
        name = re.sub(r"[^a-z0-9-]", "-", title.lower())[:28].strip("-") or "app"
        pkg = {
            "name": name, "private": True, "version": "0.0.0", "type": "module",
            "scripts": {
                "dev":     "vite",
                "build":   "vite build",
                "preview": "vite preview",
            },
            "dependencies": {
                "react": "^18.2.0", "react-dom": "^18.2.0",
                "framer-motion": "^11.0.0", "react-icons": "^5.0.0",
            },
            "devDependencies": {
                "@vitejs/plugin-react": "^4.2.0",
                "autoprefixer": "^10.4.0",
                "postcss": "^8.4.0",
                "tailwindcss": "^3.4.0",
                "vite": "^5.0.0",
            },
        }
        return {
            "package.json": json.dumps(pkg, indent=2),
            "vite.config.js": textwrap.dedent(f"""\
                import {{ defineConfig }} from 'vite'
                import react from '@vitejs/plugin-react'
                export default defineConfig({{
                  plugins: [react()],
                  server: {{ port: 5173 }},
                }})
                """),
            "tailwind.config.js": textwrap.dedent("""\
                export default {
                  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
                  theme: {
                    extend: {
                      colors: {
                        accent:  '#6366f1',
                        accent2: '#22d3ee',
                        dark:    '#0a0a0f',
                        dark2:   '#12121a',
                        card:    '#1e1e2e',
                      },
                      fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
                    },
                  },
                  plugins: [],
                }
                """),
            "postcss.config.js": "export default { plugins: { tailwindcss: {}, autoprefixer: {} } }\n",
        }

    def _index_html(self, title: str) -> str:
        return textwrap.dedent(f"""\
            <!DOCTYPE html>
            <html lang="en">
            <head>
              <meta charset="UTF-8" />
              <meta name="viewport" content="width=device-width,initial-scale=1.0" />
              <title>{title}</title>
              <link rel="preconnect" href="https://fonts.googleapis.com" />
              <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet" />
            </head>
            <body>
              <div id="root"></div>
              <script type="module" src="/src/main.jsx"></script>
            </body>
            </html>
            """)

    def _main_jsx(self) -> str:
        return textwrap.dedent("""\
            import React from 'react'
            import ReactDOM from 'react-dom/client'
            import App from './App.jsx'
            import './index.css'

            ReactDOM.createRoot(document.getElementById('root')).render(
              <React.StrictMode>
                <App />
              </React.StrictMode>
            )
            """)

    def _index_css(self, color: str) -> str:
        acc = "#6366f1"; acc2 = "#22d3ee"
        cl  = color.lower()
        if   "red"    in cl or "mario" in cl: acc, acc2 = "#ff4444", "#ff9f43"
        elif "green"  in cl:                  acc, acc2 = "#10b981", "#059669"
        elif "orange" in cl:                  acc, acc2 = "#f59e0b", "#ef4444"
        elif "pink"   in cl:                  acc, acc2 = "#ec4899", "#8b5cf6"
        elif "gold"   in cl or "yellow" in cl: acc, acc2 = "#fbbf24", "#f59e0b"
        elif "purple" in cl:                  acc, acc2 = "#a855f7", "#6366f1"
        return textwrap.dedent(f"""\
            @tailwind base;
            @tailwind components;
            @tailwind utilities;

            @layer base {{
              * {{ scroll-behavior: smooth; box-sizing: border-box; }}
              /* Safety net: ensure body always has a dark bg + visible text.
                 Prevents blank-looking pages when a component forgets to set
                 a background or uses text that blends into the default white. */
              html, body, #root {{
                min-height: 100vh;
                background-color: #0a0a0f;
                color: #e2e8f0;
              }}
              body {{ @apply font-sans; }}
              ::-webkit-scrollbar {{ width: 5px; }}
              ::-webkit-scrollbar-track {{ @apply bg-dark2; }}
              ::-webkit-scrollbar-thumb {{ background: {acc}; border-radius: 99px; }}
            }}
            @layer utilities {{
              .gradient-text {{
                background: linear-gradient(135deg, {acc}, {acc2});
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
              }}
              .glass {{
                backdrop-filter: blur(20px);
                background: rgba(30,30,46,0.55);
                border: 1px solid rgba(255,255,255,0.08);
              }}
              .glow {{ box-shadow: 0 0 30px {acc}33; border: 1px solid {acc}44; }}
            }}
            """)

    def _app_prompt(self, title, description, color, style, instructions, features, site_type):
        return textwrap.dedent(f"""\
            Build a complete, fully functional React single-page {site_type} for:
            Title: {title}
            Style: {style} | Colors: {color}
            Description: {description[:250]}
            Key features: {', '.join(features[:6]) if features else 'standard features for this type'}
            Instructions: {instructions[:250]}

            Requirements:
            - All interactive logic with useState/useEffect
            - Visually stunning, production-quality design
            - Tailwind CSS + framer-motion animations + react-icons
            - Real content — no placeholders
            - Export default function App()

            Output ONLY the JSX starting with imports.
            """)

    def _navbar_prompt(self, title, sections):
        links = [{"label": s, "href": f"#{s.lower()}"} for s in sections if s != "Navbar"]
        return textwrap.dedent(f"""\
            Write a React Navbar component for '{title}'.
            Navigation links: {json.dumps(links)}

            Requirements:
            - Fixed top position, z-index: 50
            - Glassmorphism background that appears on scroll (useEffect + useState)
            - Gradient logo text
            - Smooth scroll to section on link click
            - Mobile hamburger menu (useState)
            - Export default function Navbar()

            Output ONLY the JSX starting with imports.
            """)

    def _section_prompt(self, section, title, description, color, style, features, site_type, instructions):
        return textwrap.dedent(f"""\
            Write a complete React '{section}' section component.
            Website: {title} ({site_type})
            Style: {style} | Colors: {color}
            Description: {description[:180]}
            Instructions: {instructions[:180]}

            Requirements:
            - Production quality, visually stunning
            - framer-motion whileInView animations (initial={{opacity:0,y:30}} → animate={{opacity:1,y:0}})
            - Tailwind CSS — use dark backgrounds, gradients, glass effects
            - Real, specific content matching the website theme (not placeholder text)
            - Fully responsive (mobile-first)
            - Export default function {section}()

            Output ONLY the JSX starting with imports.
            """)

    def _fallback_navbar(self, title: str, sections: list) -> str:
        links = [s for s in sections if s != "Navbar"]
        items = "\n          ".join(
            f'<a href="#{s.lower()}" onClick={{smoothScroll}} className="text-sm text-gray-400 hover:text-white transition-colors uppercase tracking-widest">{s}</a>'
            for s in links
        )
        return textwrap.dedent(f"""\
            import {{ useState, useEffect }} from 'react'
            export default function Navbar() {{
              const [scrolled, setScrolled] = useState(false)
              const [open, setOpen] = useState(false)
              useEffect(() => {{
                const fn = () => setScrolled(window.scrollY > 50)
                window.addEventListener('scroll', fn)
                return () => window.removeEventListener('scroll', fn)
              }}, [])
              const smoothScroll = (e) => {{
                e.preventDefault()
                const id = e.target.getAttribute('href')?.slice(1)
                document.getElementById(id)?.scrollIntoView({{ behavior: 'smooth' }})
                setOpen(false)
              }}
              return (
                <nav className={{`fixed top-0 w-full z-50 transition-all duration-300 ${{scrolled ? 'backdrop-blur-xl bg-black/60 border-b border-white/10' : 'bg-transparent'}}`}}>
                  <div className="max-w-7xl mx-auto px-6 py-4 flex justify-between items-center">
                    <a href="#" className="text-xl font-black gradient-text">{title}</a>
                    <div className="hidden md:flex gap-8">
                      {items}
                    </div>
                    <button className="md:hidden text-white text-xl" onClick={{() => setOpen(!open)}}>☰</button>
                  </div>
                  {{open && (
                    <div className="md:hidden bg-black/90 px-6 py-4 flex flex-col gap-3">
                      {chr(10).join(f'<a href="#{s.lower()}" onClick={{smoothScroll}} className="text-gray-300 py-2 border-b border-white/10">{s}</a>' for s in links)}
                    </div>
                  )}}
                </nav>
              )
            }}
            """)

    def _write(self, files: dict):
        for fname, content in files.items():
            self._write_one(fname, content)

    def _write_one(self, fname: str, content: str):
        is_component = (
            fname.startswith("src/components/")
            and fname.endswith((".jsx", ".tsx"))
            and "import" in content
        )
        if is_component:
            component_name = Path(fname).stem

            content = self._extract_valid_component(content, component_name)

            content = self._sanitize_jsx(content, fname)

        fp = self.project_dir / fname
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        self.built_files[fname] = content
        sz = f"{len(content)//1024:.1f}KB" if len(content) >= 1024 else f"{len(content)}B"
        log.info(f"   ✎ {fname} ({sz})")
        self._on_write(fname, sz, content)

    def _extract_valid_component(self, code: str, component_name: str) -> str:
        """Extract a valid React component from messy LLM output."""

        code = re.sub(r"```[a-z]*", "", code).replace("```", "").strip()

        lines = code.splitlines()

        imports = []
        seen = set()
        for line in lines:
            s = line.strip()
            if re.match(r"^import\s", s) and s not in seen:
                imports.append(s)
                seen.add(s)

        pat = re.compile(
            rf"^\s*export\s+default\s+function\s+{re.escape(component_name)}\s*\(",
            re.MULTILINE,
        )
        m = pat.search(code)
        if not m:
            m = re.search(r"^\s*export\s+default\s+function\s+\w+\s*\(", code, re.MULTILINE)
        if not m:
            log.warning(f"   _extract: no export default function in {component_name} -> safe fallback")
            return _safe_component(component_name)

        export_start = m.start()

        def brace_extract(src: str, start_pos: int):
            bp = src.find("{", start_pos)
            if bp == -1:
                return None, -1
            depth = 0; pos = bp
            while pos < len(src):
                if src[pos] == "{": depth += 1
                elif src[pos] == "}": depth -= 1
                if depth == 0: break
                pos += 1
            return src[start_pos: pos + 1].strip(), pos

        helper_pat = re.compile(
            r"^(?!export)\s*"
            r"(?:function\s+([A-Z]\w*)\s*\(|"
            r"const\s+([A-Z]\w*)\s*=\s*(?:\([^)]*\)\s*=>|function)\s*\{)",
            re.MULTILINE,
        )
        helpers_code = []
        seen_helpers = set()
        for hm in helper_pat.finditer(code):
            fn_name = hm.group(1) or hm.group(2)
            if not fn_name or fn_name == component_name:
                continue
            if fn_name in seen_helpers:
                continue

            if hm.start() == export_start:
                continue
            block, end_pos = brace_extract(code, hm.start())
            if block and len(block) > 30:
                helpers_code.append((fn_name, block))
                seen_helpers.add(fn_name)

        func_body, _ = brace_extract(code, export_start)
        if not func_body:
            log.warning(f"   _extract: no opening brace in {component_name} -> safe fallback")
            return _safe_component(component_name)

        func_lines = func_body.splitlines()
        if func_lines:
            indent = len(func_lines[0]) - len(func_lines[0].lstrip())
            if indent > 0:
                func_lines = [fl[indent:] if fl.startswith(" " * indent) else fl for fl in func_lines]
            func_body = "\n".join(func_lines)

        used_helpers = [
            (name, block) for name, block in helpers_code
            if (f"<{name}" in func_body or f"<{name}/" in func_body
                or f"{{{name}" in func_body)
        ]
        if used_helpers:
            log.info(f"   _extract: including helper(s): {[n for n,_ in used_helpers]}")

        if not used_helpers and len(func_body) < 350 and helpers_code:

            for name, block in helpers_code:
                if name in func_body:
                    used_helpers = [(name, block)]
                    log.info(f"   _extract: loose-match helper '{name}' included")
                    break

        if not used_helpers and len(func_body) < 350 and helpers_code:
            largest = max(helpers_code, key=lambda x: len(x[1]))
            log.warning(
                f"   _extract: thin wrapper ({len(func_body)}B) — adopting "
                f"'{largest[0]}' as main component"
            )
            adopted = largest[1]

            adopted = re.sub(
                rf"\bfunction\s+{re.escape(largest[0])}\b",
                f"function {component_name}",
                adopted, count=1
            )
            adopted = re.sub(
                rf"\bconst\s+{re.escape(largest[0])}\b",
                f"const {component_name}",
                adopted, count=1
            )
            if adopted.lstrip().startswith("function "):
                adopted = "export default " + adopted.lstrip()
            func_body = adopted
            used_helpers = []

        if not imports:
            imports = ["import { motion } from 'framer-motion'"]

        parts = ["\n".join(imports), ""]
        for _, block in used_helpers:
            parts.append(block)
            parts.append("")
        parts.append(func_body)
        result = "\n".join(parts) + "\n"

        if abs(result.count("{") - result.count("}")) > 4:
            log.warning(f"   _extract: unbalanced braces in {component_name} -> safe fallback")
            return _safe_component(component_name)

        log.info(f"   _extract: OK {component_name} ({len(imports)} imports, {len(result)}B)")
        return result
