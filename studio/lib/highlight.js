
/** Lightweight syntax coloring for the code pane. */

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


// Order matters more than cleverness here.
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


/** `code` as HTML with token spans, for a file called `name`. */
const HIGHLIGHT_MAX = 120_000

export function highlight(code, name = '') {
  const text = String(code ?? '')
  if (text.length > HIGHLIGHT_MAX) return esc(text)
  const ext = String(name).includes('.')
    ? String(name).split('.').pop().toLowerCase() : ''
  const fn = BY_EXT[ext]
  return fn ? fn(text) : esc(text)
}
