

const PICK_CSS =
  '.__lc_hi{outline:2px solid #5b7cf7!important;' +
  'outline-offset:-2px;background:rgba(91,124,247,.10)!important;' +
  'cursor:crosshair!important}'

export function frameDoc(frame) {
  try { return frame?.contentDocument || null } catch { return null }
}


export function elementInfo(frame, el, vpMode = 'desktop') {
  const w = frame.contentWindow
  const attrs = {}
  for (const k of ['href', 'type', 'placeholder', 'name', 'alt',
                   'aria-label', 'data-testid', 'role']) {
    const v = el.getAttribute && el.getAttribute(k)
    if (v) attrs[k] = v
  }
  const chain = []
  let par = el.parentElement
  for (let i = 0; i < 6 && par; i++, par = par.parentElement) {
    chain.push({
      tag: par.tagName.toLowerCase(), id: par.id || '',
      className: par.getAttribute('class') || '',
      text: (par.innerText || '').trim().split('\n')[0].slice(0, 80),
    })
  }
  const r = el.getBoundingClientRect()
  let bg = ''
  try {
    const m = /url\(["']?([^"')]+)/.exec(w.getComputedStyle(el).backgroundImage || '')
    if (m) bg = m[1]
  } catch {  }
  const src = (el.getAttribute && el.getAttribute('src')) || ''
  return {
    tag: el.tagName.toLowerCase(), id: el.id || '',
    src, bg,
    isImage: el.tagName.toLowerCase() === 'img' || !!bg,
    classes: Array.from(el.classList || []),
    className: el.getAttribute('class') || '',
    text: (el.innerText || '').trim().slice(0, 160),
    attrs, chain,
    rect: { x: r.x, y: r.y, w: r.width, h: r.height },
    route: w.location.pathname + w.location.search,
    scroll: { x: w.scrollX, y: w.scrollY },

    viewport: {
      w: frame.clientWidth || w.innerWidth || 1280,
      h: frame.clientHeight || w.innerHeight || 800,
      mode: vpMode,
    },
    outerHTML: (el.outerHTML || '').slice(0, 1200),
  }
}


export function pickedFrom(frame, el, vpMode) {
  const info = elementInfo(frame, el, vpMode)
  const kids = [...el.children].filter(k => k.getBoundingClientRect().height > 0)
  if (kids.length > 1) {
    info.section = {
      start: elementInfo(frame, kids[0], vpMode),
      end: elementInfo(frame, kids[kids.length - 1], vpMode),
    }
  }
  return info
}


export function pickLabel(info) {
  if (!info) return ''
  if (info.isImage) {
    const name = info.attrs?.alt
      || (info.src || info.bg || 'image').split('/').pop()
    return `${name.slice(0, 60)} — describe the picture you want instead ${info.route}`
  }
  return `<${info.tag}>${info.text ? ' ' + info.text.slice(0, 60) : ''}   ${info.route}`
}


export function attachPicker(frame, onPick) {
  const d = frameDoc(frame)
  if (!d) return null
  if (!d.getElementById('__lc_pick_style')) {
    const st = d.createElement('style')
    st.id = '__lc_pick_style'
    st.textContent = PICK_CSS
    d.head.appendChild(st)
  }
  const hover = (e) => e.target.classList && e.target.classList.add('__lc_hi')
  const leave = (e) => e.target.classList && e.target.classList.remove('__lc_hi')
  const click = (e) => {
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation()
    onPick(e.target)
  }
  d.addEventListener('mouseover', hover, true)
  d.addEventListener('mouseout', leave, true)
  d.addEventListener('click', click, true)

  return () => {
    const doc = frameDoc(frame)
    if (!doc) return
    doc.removeEventListener('mouseover', hover, true)
    doc.removeEventListener('mouseout', leave, true)
    doc.removeEventListener('click', click, true)
    doc.querySelectorAll('.__lc_hi').forEach(e => e.classList.remove('__lc_hi'))
    doc.getElementById('__lc_pick_style')?.remove()
  }
}
