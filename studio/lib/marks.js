/**
 * What a log line is, drawn rather than spelled.
 *
 * The studio's own messages no longer carry emoji — the icon is chosen here
 * from the line's level. But half the feed comes off the websocket from the
 * server, which is not this project's to change, and those lines still arrive
 * with a picture in front of them. So the table below reads that picture,
 * turns it into a Lucide icon, and strips every pictographic out of the text
 * on the way past. Nothing in the interface renders an emoji.
 *
 * The system is monochrome, and an emoji is not: a colour image in a column
 * of ink is the one thing on the screen the palette has no say over.
 */
import {
  AlertTriangle, Ban, Boxes, Check, ChevronRight, CircleAlert, Clock,
  Cloud, Crosshair, Download, FileText, FlaskConical, Gauge, Image, Info,
  Lock, MapPin, Minus, Package, Palette, Pencil, Play, Plus, RotateCcw,
  Rocket, Save, Search, Trash2, Wrench, X, Zap,
} from 'lucide-react'

/** Every pictographic character, with its optional variation selector. */
const PICTURE = /\p{Extended_Pictographic}️?/gu

const BY_EMOJI = {
  '✅': Check, '🎉': Check,
  '❌': X, '⛔': Ban,
  '⚠': AlertTriangle, '❗': CircleAlert,
  '↩': RotateCcw,
  '🔧': Wrench, '🩹': Wrench,
  '➕': Plus, '🗑': Trash2,
  '📝': Pencil, '🧪': FlaskConical,
  '🔍': Search, '🎯': Crosshair, '📍': MapPin,
  '🔒': Lock, '🔐': Lock,
  '⏱': Clock, '📊': Gauge, '⚡': Zap,
  '💾': Save, '🚀': Rocket, '🤖': Boxes,
  '🖼': Image, '🎨': Palette, '📄': FileText,
  '☁': Cloud, '📂': FileText, '📦': Package,
  '📋': FileText, '▶': Play, '⬇': Download,
}

const BY_LEVEL = {
  SUCCESS: Check,
  ERROR: X,
  WARN: AlertTriangle,
  DEBUG: Minus,
  INFO: ChevronRight,
}

/**
 * `{ Icon, text }` for one log line.
 *
 * The emoji wins when there is one, because it says more than the level does —
 * a repair and a write are both INFO. Otherwise the level decides.
 */
export function logMark(text, level) {
  const raw = String(text || '')
  const lead = /^\s*(\p{Extended_Pictographic})/u.exec(raw)
  const Icon = (lead && BY_EMOJI[lead[1]]) || BY_LEVEL[level] || Info
  return { Icon, text: strip(raw) }
}

/** The same line with every picture taken out of it. */
export function strip(text) {
  return String(text || '').replace(PICTURE, '').replace(/\s{2,}/g, ' ').trim()
}


/**
 * Lines the feed is better off without.
 *
 * The dev server's own chatter, and — the loud one — the source frame Next
 * prints under a runtime error:
 *
 *     ⨯ TypeError: Cannot use 'in' operator to search for 'handler'…
 *     at GET (app\api\auth\[...all]\route.js:23:26)
 *     21 |
 *     22 | export async function GET(request) {
 *     > 23 |   return (await ready()).GET(request)
 *     |                          ^
 *     24 | }
 *
 * Six of those eight lines are the file quoted back, which the reader can
 * open in the Code tab, and the whole block repeats every few seconds for as
 * long as the page keeps polling. The message and the location stay; the
 * gutter and the caret go.
 */
const NOISE = [
  /^\[next\]\s*>?\s*\d+\s*\|/,        // "21 |", "> 23 |  return …"
  /^\[next\]\s*\|\s*\^*\s*$/,         // "|        ^"
  /^\[next\]\s+(GET|POST|PUT|PATCH|DELETE|HEAD)\s/i,
  /^\[next\]\s+[-✓○▲]/,
  /^\[next\]\s+(Local|Network|Environments):/i,
  /^\[browser\]/i,
  /^\$\s/,
  /^\s*$/,
  /Fast Refresh/i,
  /^exit \d+/i,
  /Download the React DevTools/i,
]

export function isNoise(text) {
  const t = String(text || '').trim()
  return !t || NOISE.some(re => re.test(t))
}

/**
 * A feed with the noise dropped and repeats folded into a count.
 *
 * The same error arriving every five seconds is one fact, not twenty rows.
 * Folding only the immediately previous line is not enough: a Next runtime
 * error arrives as a PAIR — the message, then its `at …` location — so the
 * two alternate and neither is ever its own predecessor. The look-back is
 * therefore a few rows deep, and a repeat bumps the count on the row where
 * it first appeared rather than adding another.
 */
const LOOKBACK = 6

export function feedOf(logs, limit = 0) {
  const out = []
  for (const line of logs || []) {
    if (isNoise(line.text)) continue
    const seen = out
      .slice(-LOOKBACK)
      .find(r => r.text === line.text && r.level === line.level)
    if (seen) {
      seen.times += 1
      seen.at = line.at
      continue
    }
    out.push({ ...line, times: 1 })
  }
  return limit ? out.slice(-limit) : out
}
