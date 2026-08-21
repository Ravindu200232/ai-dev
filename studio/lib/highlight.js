
/**
 * Syntax colouring for the code pane, written here rather than installed.
 *
 * Prism and highlight.js both do this better and neither is here: AgentForge
 * runs with no network by design, so a CDN tag is out, and a package added for
 * one pane is 200KB in every studio build for four languages the generator
 * actually emits. What it emits is JS/JSX, CSS, JSON and Markdown — a
 * tokeniser for those four is a regex each.
 *
 * ONE pass per language, alternatives ordered so the greedy things win.
 * Comments and strings come first in every pattern: a `//` inside a string is
 * not a comment and a keyword inside a comment is not a keyword, and matching
 * them first is what makes both true without a state machine.
 *
 * The output is HTML, so every captured run is escaped on the way out. The
 * input is a file the user is editing — `</textarea>` typed into it must land
 * as text and not as markup.
 */

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;' }
const esc = (s) => s.replace(/[&<>]/g, c => ESC[c])

const wrap = (cls, text) => `<span class="tk-${cls}">${esc(text)}</span>`


const KEYWORDS = new Set([
  'await', 'async', 'break', 'case', 'catch', 'class', 'const', 'continue',
  'default', 'delete', 'do', 'else', 'export', 'extends', 'finally', 'for',
  'from', 'function', 'if', 'import', 'in', 'instanceof', 'let', 'new', 'of',
  'return', 'static', 'super', 'switch', 'this', 'throw', 'try', 'typeof',
  'var', 'void', 'while', 'yield', 'as', 'get', 'set',
])

const LITERALS = new Set(['true', 'false', 'null', 'undefined', 'NaN', 'Infinity'])


// Order matters more than cleverness here. Comment, then the three string
// forms, then JSX tag names, then everything a word can be.
const JS = new RegExp([
  /\/\*[\s\S]*?\*\/|\/\/[^\n]*/,                       // 1 comment
  /`(?:\\[\s\S]|\$\{[^}]*\}|[^\\`])*`/,                // 2 template
  /'(?:\\[\s\S]|[^\\'])*'|"(?:\\[\s\S]|[^\\"])*"/,     // 3 string
  /<\/?[A-Za-z][\w.]*/,                                // 4 jsx tag open
  /\b0[xX][\da-fA-F]+\b|\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b/,  // 5 number
  /[A-Za-z_$][\w$]*(?=\s*\()/,                         // 6 call
  /[A-Za-z_$][\w$]*/,                                  // 7 word
].map(r => r.source).join('|'), 'g')


function js(code) {
  let out = ''
  let at = 0
  for (const m of code.matchAll(JS)) {
    const t = m[0]
    out += esc(code.slice(at, m.index))
    at = m.index + t.length

    if (t.startsWith('//') || t.startsWith('/*')) out += wrap('cmt', t)
    else if (t[0] === '`') out += wrap('str', t)
    else if (t[0] === '"' || t[0] === "'") out += wrap('str', t)
    else if (t[0] === '<') out += wrap('tag', t)
    else if (/^[\d.]/.test(t)) out += wrap('num', t)
    else if (KEYWORDS.has(t)) out += wrap('kw', t)
    else if (LITERALS.has(t)) out += wrap('lit', t)
    else if (/^[A-Z]/.test(t)) out += wrap('type', t)   // components, classes
    else if (code[at] === '(') out += wrap('fn', t)
    else out += esc(t)
  }
  return out + esc(code.slice(at))
}


const CSS = new RegExp([
  /\/\*[\s\S]*?\*\//,                       // comment
  /'(?:\\[\s\S]|[^\\'])*'|"(?:\\[\s\S]|[^\\"])*"/,
  /@[\w-]+/,                                // at-rule
  /[\w-]+(?=\s*:)/,                         // property
  /#[\da-fA-F]{3,8}\b/,                     // hex colour
  /\b\d[\d.]*(?:px|rem|em|%|vh|vw|s|ms|deg|fr)?\b/,
].map(r => r.source).join('|'), 'g')


function css(code) {
  let out = ''
  let at = 0
  for (const m of code.matchAll(CSS)) {
    const t = m[0]
    out += esc(code.slice(at, m.index))
    at = m.index + t.length
    if (t.startsWith('/*')) out += wrap('cmt', t)
    else if (t[0] === '"' || t[0] === "'") out += wrap('str', t)
    else if (t[0] === '@') out += wrap('kw', t)
    else if (t[0] === '#' || /^\d/.test(t)) out += wrap('num', t)
    else out += wrap('prop', t)
  }
  return out + esc(code.slice(at))
}


const JSON_RE = /"(?:\\[\s\S]|[^\\"])*"(\s*:)?|\b-?\d[\d.eE+-]*\b|\b(?:true|false|null)\b/g

function json(code) {
  let out = ''
  let at = 0
  for (const m of code.matchAll(JSON_RE)) {
    const t = m[0]
    out += esc(code.slice(at, m.index))
    at = m.index + t.length
    if (t[0] === '"') out += wrap(m[1] ? 'key' : 'str', t)
    else if (/^[-\d]/.test(t)) out += wrap('num', t)
    else out += wrap('lit', t)
  }
  return out + esc(code.slice(at))
}


const MD_RE = /^#{1,6} .*$|^```.*$|\*\*[^*\n]+\*\*|`[^`\n]+`|^[-*+] |^\s*\d+\. |\[[^\]\n]*\]\([^)\n]*\)/gm

function md(code) {
  let out = ''
  let at = 0
  for (const m of code.matchAll(MD_RE)) {
    const t = m[0]
    out += esc(code.slice(at, m.index))
    at = m.index + t.length
    if (t.startsWith('#')) out += wrap('type', t)
    else if (t.startsWith('```')) out += wrap('kw', t)
    else if (t.startsWith('**')) out += wrap('lit', t)
    else if (t[0] === '`') out += wrap('str', t)
    else if (t[0] === '[') out += wrap('fn', t)
    else out += wrap('kw', t)
  }
  return out + esc(code.slice(at))
}


const BY_EXT = {
  js: js, jsx: js, mjs: js, cjs: js, ts: js, tsx: js,
  css: css, scss: css,
  json: json,
  md: md,
}


/**
 * `code` as HTML with token spans, for a file called `name`.
 *
 * Falls back to escaped plain text for anything unrecognised — a `.txt`, a
 * `.env`, a file whose extension the map has never heard of. Colourless is a
 * fine answer; guessing a grammar is not.
 *
 * There is a size ceiling because this runs on every keystroke. Past it the
 * text goes through escaped and uncoloured: a 200KB `package-lock.json` is not
 * worth a re-tokenise per character, and it is not read for its syntax.
 */
export const HIGHLIGHT_MAX = 120_000

export function highlight(code, name = '') {
  const text = String(code ?? '')
  if (text.length > HIGHLIGHT_MAX) return esc(text)
  const ext = String(name).includes('.')
    ? String(name).split('.').pop().toLowerCase() : ''
  const fn = BY_EXT[ext]
  return fn ? fn(text) : esc(text)
}
