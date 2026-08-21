import json, logging, os, re, shutil, subprocess, textwrap
from pathlib import Path
import requests

log = logging.getLogger("builder")


def _find_npm_cmd():
    """The npm to shell out to, or None if there is not one."""
    explicit = os.environ.get("AGENTFORGE_NPM") or os.environ.get("NPM")
    if explicit and Path(explicit).exists():
        return [explicit]
    found = shutil.which("npm")
    return [found] if found else None

_stream_callback = None
def set_stream_callback(fn):
    global _stream_callback
    _stream_callback = fn

def _emit(token):
    if _stream_callback:
        _stream_callback(token)


def _app_shell(title: str, sections: list) -> str:
    """Generate App.jsx that imports and renders all sections."""
    non_navbar = [s for s in sections if s != "Navbar"]
    lines = [
        "import { motion } from 'framer-motion'",
        "import Navbar from './components/Navbar'",
    ]
    for s in non_navbar:
        lines.append(f"import {s} from './components/{s}'")
    lines += [
        "",
        "const fadeUp = { hidden:{opacity:0,y:40}, visible:{opacity:1,y:0,transition:{duration:0.65}} }",
        "",
        "export default function App() {",
        "  return (",
        "    <div className='bg-dark min-h-screen overflow-x-hidden'>",
        "      <Navbar />",
    ]
    for s in non_navbar:
        lines += [
            f"      <motion.div id='{s.lower()}' className='py-20 px-6 max-w-7xl mx-auto'",
            "        initial='hidden' whileInView='visible' viewport={{ once:true, amount:0.08 }} variants={fadeUp}>",
            f"        <{s} />",
            "      </motion.div>",
        ]
    lines += [
        "      <footer className='border-t border-white/10 py-6 text-center text-gray-500 text-sm'>",
        f"        <p>© 2024 {title}</p>",
        "      </footer>",
        "    </div>",
        "  )",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _single_app_shell() -> str:
    return textwrap.dedent("""\
        import AppComponent from './components/App'
        export default function App() {
          return <div className='min-h-screen overflow-x-hidden'><AppComponent /></div>
        }
        """)


SAFE_COMPONENT = textwrap.dedent("""\
    import { motion } from 'framer-motion'
    export default function {name}() {
      return (
        <section id='{id}' className='py-20 px-6'>
          <motion.div className='max-w-4xl mx-auto text-center'
            initial={{opacity:0,y:30}} whileInView={{opacity:1,y:0}}
            transition={{duration:0.6}} viewport={{once:true}}>
            <h2 className='text-5xl font-black mb-4' style={{
              background:'linear-gradient(135deg,#6366f1,#22d3ee)',
              WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent'
            }}>{name}</h2>
            <p className='text-gray-400 text-lg'>Section content goes here.</p>
          </motion.div>
        </section>
      )
    }
    """)

def _safe_component(name: str) -> str:
    return SAFE_COMPONENT.replace("{name}", name).replace("{id}", name.lower())


SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert React + Tailwind developer.
    Output ONLY complete, valid JSX code. No markdown fences, no explanation, no preamble.

    ═══════════════════════════════════════════════════════════
    REFERENCE EXAMPLE — your output must follow this structure EXACTLY:
    ═══════════════════════════════════════════════════════════

    import { useState } from 'react'
    import { motion } from 'framer-motion'
    import { FiMail, FiCheck, FiArrowRight } from 'react-icons/fi'

    export default function Newsletter() {
      // ── All data and state go INSIDE the function ──────────
      const plans = [
        { id: 1, name: 'Weekly Digest', desc: 'Best articles every Monday' },
        { id: 2, name: 'Daily Brief',   desc: 'Quick updates every morning' },
      ]
      const [email, setEmail]   = useState('')
      const [plan, setPlan]     = useState(1)
      const [done, setDone]     = useState(false)

      // ── Regex and computed values hoisted ABOVE return() ───
      const reEmail = /[^a-zA-Z0-9@._+-]/g
      const reTrim  = /\\s+/g
      const halfLen = Math.floor(plans.length / 2)   // division OK inside Math.*
      const stepVal = 1 / plans.length                // division outside JSX

      const handleSubmit = (e) => {
        e.preventDefault()
        const cleaned = email.replace(reEmail, '').replace(reTrim, '')
        if (!cleaned.includes('@')) return
        setDone(true)
      }

      if (done) return (
        <div className="min-h-screen bg-gray-900 flex items-center justify-center">
          <motion.div initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            className="text-center text-white">
            <FiCheck className="text-5xl text-green-400 mx-auto mb-4" />
            <h2 className="text-3xl font-bold">You're subscribed!</h2>
          </motion.div>
        </div>
      )

      return (
        <div className="min-h-screen bg-gray-900 text-white py-20 px-6">
          <div className="max-w-2xl mx-auto">
            <motion.h1 initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }}
              className="text-5xl font-black mb-4 gradient-text">
              Stay in the Loop
            </motion.h1>
            <ul className="mb-8 space-y-3">
              {plans.map(p => (
                <li key={p.id}
                  onClick={() => setPlan(p.id)}
                  className={`p-4 rounded-xl cursor-pointer border transition ${
                    plan === p.id ? 'border-indigo-500 bg-indigo-500/10' : 'border-white/10'
                  }`}>
                  <span className="font-semibold">{p.name}</span>
                  <span className="text-gray-400 ml-2 text-sm">{p.desc}</span>
                </li>
              ))}
            </ul>
            <form onSubmit={handleSubmit} className="flex gap-3">
              <input type="email" value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="your@email.com"
                className="flex-1 bg-gray-800 border border-white/10 rounded-xl px-4 py-3 text-white" />
              <button type="submit"
                className="flex items-center gap-2 px-6 py-3 bg-indigo-500 hover:bg-indigo-400 rounded-xl font-semibold transition">
                Subscribe <FiArrowRight />
              </button>
            </form>
            <p className="text-gray-500 text-sm mt-4 flex items-center gap-2">
              <FiMail /> No spam. Unsubscribe anytime.
            </p>
          </div>
        </div>
      )
    }

    ═══════════════════════════════════════════════════════════
    MANDATORY RULES — violations cause runtime errors:
    ═══════════════════════════════════════════════════════════
    1. Imports first. Then IMMEDIATELY export default function. NOTHING between them.
    2. ALL data arrays, constants, state go INSIDE the function body.
    3. NEVER: const Component = () => {}  — arrow components are BANNED.
    4. NEVER: define function then export default separately at the bottom.
    5. NEVER split into multiple named functions.
       NO 'function Calculator()' + 'function App() { return <Calculator/> }'.
       ALL logic lives in ONE export default function. This is the most important rule.
    6. NEVER import from 'react-icons/all' — use 'react-icons/fi', '/fa', '/hi', etc.
    7. ONLY use packages from this EXACT allowed list — NO others:
       ALLOWED: react, react-dom, framer-motion, react-icons
       react-icons usage: import {{ FiHome }} from 'react-icons/fi'
       BANNED (will crash Vite): react-scroll, lucide-react, react-leaflet,
         react-router-dom, axios, lodash, chart.js, d3, three, @mui/material,
         @chakra-ui/react, react-query, zustand, styled-components, classnames,
         react-spring, react-use, @heroicons/react, react-helmet, react-hot-toast.
       If you need a MAP: use a plain <div> with a styled placeholder — no leaflet.
       If you need CHARTS: use pure CSS/SVG bars — no chart.js/d3.
       If you need ROUTING: use useState for view switching — no react-router.
    8. Self-close void elements: <br />, <img />, <input />, <hr />
    9. Outermost div MUST have explicit background: bg-gray-900, bg-slate-950, bg-black.
       NEVER leave root div transparent — causes blank white pages.
    10. Only real icon names: FiHome, FiX, FiCircle, FiStar, FiMenu, FiGrid, FiArrowRight,
        FiPhone, FiMail, FiUser, FiSettings, FiCode, FiHeart, FiPlus, FiTrash2, FiEdit.
        NEVER invent: FiOval, FiMultiply, FiCross, FiGamepad2, FiCalculator.
    11. NEVER write /regex/ inside JSX — hoist to const above return():
          WRONG: onChange={e => setValue(e.target.value.replace(/[^0-9]/g, ''))}
          RIGHT: const reDigits = /[^0-9]/g;  ...  .replace(reDigits, '')
    12. NEVER write division inside JSX {}: Babel misreads / as regex start.
          WRONG: <input step={30/60} />  or  <div>{count/total}</div>
          RIGHT: const stepVal = 30/60;  ...  <input step={stepVal} />
    """)

__all__ = [name for name in globals() if not name.startswith("__")]
