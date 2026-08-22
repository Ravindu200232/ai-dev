'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Monitor, Tablet, Smartphone, MousePointerClick, Pencil, Undo2, RotateCw,
  Image as ImageIcon, ChevronLeft, ChevronRight, Upload, Loader2, Globe,
} from 'lucide-react'
import { useStore } from '@/lib/store'
import { send } from '@/lib/ws'
import { api } from '@/lib/api'
import {
  attachPicker, frameDoc, elementInfo, pickedFrom, pickLabel,
} from '@/lib/picker'
import { consoleReport, forgetConsole, watchFrame } from '@/lib/console-log'
import { Button, Input, Tip } from './ui'
import { useEditAttachments } from '@/lib/use-edit-attachments'
import EditAttach from './EditAttach'
import BuildOverlay from './BuildOverlay'
import TunePrompt from './TunePrompt'
import LiveE2EOverlay from './LiveE2EOverlay'
import PreviewConsoleDrawer from './PreviewConsoleDrawer'
import { cn } from '@/lib/utils'

const ACCEPT_PICTURE = '.png,.jpg,.jpeg,.webp,.gif,.bmp,image/*'

const VIEWPORTS = [
  { id: 'desktop', label: 'Desktop', w: null, Icon: Monitor },
  { id: 'tablet', label: 'Tablet', w: 834, Icon: Tablet },
  { id: 'mobile', label: 'Mobile', w: 390, Icon: Smartphone },
]

function currentPath(frame) {
  try {
    const loc = frame?.contentWindow?.location
    if (!loc) return '/'
    return `${loc.pathname || '/'}${loc.search || ''}${loc.hash || ''}`
  } catch {
    return '/'
  }
}

export default function PreviewPane({ hidden }) {
  const frameRef = useRef(null)
  const canvasRef = useRef(null)
  const detachRef = useRef(null)
  const strokesRef = useRef([])
  const drawingRef = useRef(false)
  const swapRef = useRef(null)
  const lastPathRef = useRef('/')
  const files = useEditAttachments()
  const [reading, setReading] = useState(false)

  const project = useStore(s => s.project)
  const busy = useStore(s => s.busy)
  const addLog = useStore(s => s.addLog)
  const setBusy = useStore(s => s.setBusy)
  const setPreviewRoute = useStore(s => s.setPreviewRoute)
  const models = useStore(s => s.models)
  const think = useStore(s => s.think)
  const undo = useStore(s => s.undo)
  const setUndo = useStore(s => s.setUndo)
  const tests = useStore(s => s.tests)
  const e2eLive = useStore(s => s.e2eLive)

  const [vp, setVp] = useState('desktop')
  const [pickOn, setPickOn] = useState(false)
  const [pencilOn, setPencilOn] = useState(false)
  const [imageOn, setImageOn] = useState(false)
  const [picked, setPicked] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [tuning, setTuning] = useState(false)
  const [ask, setAsk] = useState(null)
  const [path, setPath] = useState('/')
  const trail = useRef(['/'])
  const at = useRef(0)
  const jumping = useRef(false)
  const [nav, setNav] = useState({ back: false, forward: false })

  const syncPath = useCallback((reason = 'navigation') => {
    const here = currentPath(frameRef.current)
    if (!here) return
    if (lastPathRef.current === here && reason !== 'load') return
    lastPathRef.current = here
    setPath(here)
    setPreviewRoute(here)

    if (jumping.current) {
      jumping.current = false
    } else if (trail.current[at.current] !== here) {
      trail.current = trail.current.slice(0, at.current + 1).concat(here)
      at.current = trail.current.length - 1
    }
    setNav({ back: at.current > 0, forward: at.current < trail.current.length - 1 })
  }, [setPreviewRoute])

  const attach = useCallback(() => {
    detachRef.current?.()
    detachRef.current = attachPicker(frameRef.current, (el) => {
      setPicked(pickedFrom(frameRef.current, el, vp))
      setPrompt('')
    })
    if (!detachRef.current) addLog('WARN', 'The preview is not loaded yet')
  }, [vp, addLog])

  useEffect(() => {
    if (pickOn || imageOn) attach()
    else { detachRef.current?.(); detachRef.current = null }
    return () => { detachRef.current?.(); detachRef.current = null }
  }, [pickOn, imageOn, attach])

  useEffect(() => {
    const f = frameRef.current
    if (!f) return
    const onLoad = () => {
      watchFrame(f)
      syncPath('load')
      if (pickOn || imageOn) attach()
    }
    f.addEventListener('load', onLoad)
    return () => f.removeEventListener('load', onLoad)
  }, [pickOn, imageOn, attach, syncPath])

  useEffect(() => {
    const id = setInterval(() => syncPath('poll'), 250)
    return () => clearInterval(id)
  }, [syncPath])

    // Mirror the Playwright route in the visible preview.
  useEffect(() => {
    const route = String(e2eLive?.route || '')
    if (!tests.running || !route.startsWith('/')) return
    const f = frameRef.current
    if (!f || currentPath(f) === route) return
    jumping.current = true
    f.src = route
  }, [e2eLive?.route, tests.running])

  const syncCanvas = useCallback(() => {
    const f = frameRef.current, c = canvasRef.current
    if (!f || !c) return
    c.style.left = f.offsetLeft + 'px'
    c.style.top = f.offsetTop + 'px'
    c.style.width = f.clientWidth + 'px'
    c.style.height = f.clientHeight + 'px'
    const dpr = window.devicePixelRatio || 1
    const want = Math.round(f.clientWidth * dpr)
    if (c.width !== want) {
      c.width = want
      c.height = Math.round(f.clientHeight * dpr)
      const ctx = c.getContext('2d')
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.strokeStyle = '#0f6fff'
      ctx.lineWidth = 3
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
    }
  }, [])

  const clearStrokes = useCallback(() => {
    strokesRef.current = []
    const c = canvasRef.current
    if (!c) return
    const ctx = c.getContext('2d')
    ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, c.width, c.height); ctx.restore()
  }, [])

  const point = useCallback((e) => {
    const c = canvasRef.current
    const r = c.getBoundingClientRect()
    let sx = 0, sy = 0
    try {
      const w = frameRef.current.contentWindow
      sx = w.scrollX; sy = w.scrollY
    } catch { }
    return {
      cx: e.clientX - r.left, cy: e.clientY - r.top,
      x: Math.round(e.clientX - r.left + sx),
      y: Math.round(e.clientY - r.top + sy),
    }
  }, [])

  useEffect(() => {
    const c = canvasRef.current
    if (!c || !pencilOn) return
    syncCanvas()

    const down = (e) => {
      drawingRef.current = true
      const pt = point(e)
      strokesRef.current.push([{ x: pt.x, y: pt.y }])
      const ctx = c.getContext('2d')
      ctx.beginPath(); ctx.moveTo(pt.cx, pt.cy)
      try {
        const d = frameDoc(frameRef.current)
        const el = d && d.elementFromPoint(pt.cx, pt.cy)
        if (el) setPicked(elementInfo(frameRef.current, el, vp))
      } catch { }
    }
    const move = (e) => {
      if (!drawingRef.current) return
      const pt = point(e)
      strokesRef.current[strokesRef.current.length - 1].push({ x: pt.x, y: pt.y })
      const ctx = c.getContext('2d')
      ctx.lineTo(pt.cx, pt.cy); ctx.stroke()
    }
    const up = () => {
      if (!drawingRef.current) return
      drawingRef.current = false
      if (strokesRef.current.reduce((a, s) => a + s.length, 0) < 3) return
      setPrompt('')
      setPicked(p => p || { tag: '', route: path })
    }

    c.addEventListener('pointerdown', down)
    c.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    const onResize = () => syncCanvas()
    window.addEventListener('resize', onResize)
    return () => {
      c.removeEventListener('pointerdown', down)
      c.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('resize', onResize)
    }
  }, [pencilOn, point, syncCanvas, vp, path])

  function togglePick() {
    if (!project) return addLog('WARN', 'Open a project first')
    if (!pickOn) { setPencilOn(false); setImageOn(false) }
    setPickOn(v => !v)
    setPicked(null)
  }

  function togglePencil() {
    if (!project) return addLog('WARN', 'Open a project first')
    if (!pencilOn) { setPickOn(false); setImageOn(false) }
    setPencilOn(v => { if (v) clearStrokes(); return !v })
    setPicked(null)
  }

  function toggleImage() {
    if (!project) return addLog('WARN', 'Open a project first')
    if (!imageOn) { setPickOn(false); setPencilOn(false); clearStrokes() }
    setImageOn(v => !v)
    setPicked(null)
  }

  async function undoLast() {
    if (!project || !undo) return
    try {
      const r = await api.undo(project, undo.id || '')
      addLog('SUCCESS', 'Restored ' + (r.restored || []).join(', '))
      setUndo(null)
      const f = frameRef.current
      if (f) f.src = currentPath(f)
    } catch (e) {
      addLog('WARN', 'Undo failed: ' + e.message)
    }
  }

  function step(by) {
    const f = frameRef.current
    const next = at.current + by
    if (!f || next < 0 || next >= trail.current.length) return
    at.current = next
    jumping.current = true
    f.src = trail.current[next]
    setNav({ back: next > 0, forward: next < trail.current.length - 1 })
  }

  function reloadPreview() {
    const f = frameRef.current
    if (!f) return
    try {
      f.contentWindow.location.reload()
    } catch {
      f.src = currentPath(f)
    }
  }

  const wasBusy = useRef(false)
  useEffect(() => {
    if (wasBusy.current && !busy) reloadPreview()
    wasBusy.current = busy
  }, [busy])

  async function submit() {
    const v = prompt.trim()
    if (!v || !picked) return

    const kind = pencilOn ? 'pencil_edit'
      : (imageOn || picked.isImage) ? 'image_edit' : 'element_edit'

    let full = v
    if (files.items.length) {
      setReading(true)
      try {
        full = v + await files.collect(project)
      } catch (e) {
        addLog('WARN', `${e.message}`)
      }
      setReading(false)
    }

    const payload = {
      type: kind, project, element: picked,
      model: models.builder || models.agent, think,
      route: picked.route || path, scroll: picked.scroll, viewport: picked.viewport,
      strokes: pencilOn ? strokesRef.current : undefined,
      console: consoleReport(),
    }

    setTuning(true)
    let said = full
    try {
      const r = await api.tune({ prompt: full, element: picked, project,
                                 route: payload.route, model: payload.model })
      said = (r?.prompt || '').trim() || full
    } catch (e) {
      addLog('WARN', `Could not reword the request (${e.message}) — sending it as typed`)
    }
    setTuning(false)

    if (said.trim() === full.trim()) return fire(payload, full, kind, v)
    setAsk({ payload, kind, typed: full, shown: v, tuned: said })
  }

  function fire(payload, text, kind, shown) {
    send({ ...payload, prompt: text })
    forgetConsole()
    files.reset()
    addLog('INFO', (pencilOn ? 'Redesign: '
      : kind === 'image_edit' ? 'New picture: ' : 'Edit: ') + shown)
    setBusy(true)
    setPicked(null)
    setPrompt('')
    setAsk(null)
    if (pencilOn) clearStrokes(); else if (!imageOn) setPickOn(false)
  }

  async function swapPicture(file) {
    if (!file || !picked) return
    const target = picked
    setBusy(true)
    setPicked(null)
    setPrompt('')
    addLog('INFO', `Your picture: ${file.name}`)
    try {
      await api.imageSwap(file, { project, element: target })
    } catch (e) {
      setBusy(false)
      addLog('WARN', `${e.message}`)
    }
  }

  const width = VIEWPORTS.find(x => x.id === vp)?.w
  const shownPath = tests.running && e2eLive?.route ? e2eLive.route : path

  return (
    <div className={cn('flex min-h-0 flex-1 flex-col bg-transparent', hidden && 'hidden')}>
      <div className="flex h-[54px] shrink-0 items-center gap-3 border-b border-line/70 bg-white/72 px-4 backdrop-blur-2xl dark:bg-white/[.03]">
        <div className="flex items-center gap-1 rounded-full border border-line/80 bg-panel/90 p-1 shadow-sm">
          <Cell tip={nav.back ? 'Back' : 'Nothing to go back to'}
                disabled={!nav.back} onClick={() => step(-1)} className="rounded-full px-3">
            <ChevronLeft className="size-3.5" />
          </Cell>
          <Cell tip={nav.forward ? 'Forward' : 'Nothing to go forward to'}
                disabled={!nav.forward} onClick={() => step(1)} className="rounded-full px-3">
            <ChevronRight className="size-3.5" />
          </Cell>
          <Cell tip="Reload the preview" onClick={reloadPreview} className="rounded-full px-3">
            <RotateCw className="size-3.5" />
          </Cell>
        </div>

        <div className="flex min-w-0 flex-1 items-center gap-2 rounded-[14px] bg-black/[.035] px-4 py-2 text-[12px] text-muted ring-1 ring-black/[.045] dark:bg-white/[.045] dark:ring-white/[.06]">
          <Globe className="size-3.5 shrink-0 text-accent" />
          <span className="truncate font-medium text-ink">localhost:5173{shownPath === '/' ? '' : shownPath}</span>
        </div>

        <div className="hidden items-center gap-1 rounded-full border border-line/80 bg-panel/80 p-1 shadow-sm md:flex">
          {VIEWPORTS.map(({ id, label, Icon }) => (
            <Cell key={id} tip={label} side="left" on={vp === id}
                  onClick={() => setVp(id)} className="rounded-full px-3">
              <Icon className="size-3.5" />
            </Cell>
          ))}
        </div>

        <div className="flex items-center gap-1 rounded-full border border-line/80 bg-panel/80 p-1 shadow-sm">
          <Cell tip="Click an element in the preview to edit it" side="left"
                on={pickOn} onClick={togglePick} className="rounded-full">
            <MousePointerClick className="size-3.5" />
          </Cell>
          <Cell tip="Draw over a region and describe the redesign" side="left"
                on={pencilOn} onClick={togglePencil} className="rounded-full">
            <Pencil className="size-3.5" />
          </Cell>
          <Cell tip="Click a picture and describe the one you want instead"
                side="left" on={imageOn} onClick={toggleImage} className="rounded-full">
            <ImageIcon className="size-3.5" />
          </Cell>
          <Cell tip={undo ? `Undo the last edit (${undo.files.join(', ')})`
                          : 'Nothing to undo yet'}
                side="left" disabled={!undo} onClick={undoLast} className="rounded-full">
            <Undo2 className="size-3.5" />
          </Cell>
        </div>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden bg-[radial-gradient(circle_at_top,#f8fbff_0%,#edf2fb_45%,#dfe7f5_100%)] p-4 dark:bg-[radial-gradient(circle_at_top,#1d2333_0%,#151a26_45%,#0f141d_100%)]">
        <BuildOverlay />

        {ask && (
          <TunePrompt
            typed={ask.shown}
            tuned={ask.tuned}
            onSend={(text) => fire(ask.payload, text, ask.kind, ask.shown)}
            onSendTyped={() => fire(ask.payload, ask.typed, ask.kind, ask.shown)}
            onCancel={() => setAsk(null)}
            onRetune={async (text) => {
              try {
                const r = await api.tune({
                  prompt: text, element: ask.payload.element, project,
                  route: ask.payload.route, model: ask.payload.model })
                return (r?.prompt || '').trim()
              } catch (e) {
                addLog('WARN', `Could not reword it (${e.message})`)
                return ''
              }
            }} />
        )}

        <canvas ref={canvasRef}
                className={cn('absolute z-[5]', pencilOn ? 'block' : 'hidden')}
                style={{ pointerEvents: pencilOn ? 'auto' : 'none' }} />

        {picked && (
          <div className="absolute inset-x-4 bottom-16 z-[46] rounded-[24px] border border-line/80 bg-panel/95 px-4 py-3 shadow-[0_18px_36px_rgba(15,23,42,.12)] backdrop-blur-xl dark:shadow-[0_18px_36px_rgba(0,0,0,.35)]">
            <div className="mb-2.5 flex items-center gap-2">
              <span className="rounded-full bg-accent/12 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">
                {pencilOn ? 'Redesign region' : imageOn ? 'Replace image' : 'Edit selection'}
              </span>
              <span className="truncate text-[12px] text-muted">
                {pencilOn
                  ? `Selected region · ${picked.tag ? '<' + picked.tag + '>' : ''} · ${path}`
                  : imageOn && !picked.isImage
                  ? `That is not a picture · ${picked.tag ? '<' + picked.tag + '>' : ''} · click an image`
                  : pickLabel(picked)}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              <Input autoFocus value={prompt}
                     placeholder={pencilOn ? 'Describe how this area should look'
                       : (imageOn || picked.isImage)
                         ? 'Describe the picture you want instead — or upload one'
                       : 'Describe the change you want'}
                     onChange={e => setPrompt(e.target.value)}
                     onKeyDown={e => {
                       if (e.key === 'Enter') submit()
                       if (e.key === 'Escape') setPicked(null)
                     }} />

              {picked.isImage && (
                <>
                  <input ref={swapRef} type="file" hidden accept={ACCEPT_PICTURE}
                         onChange={e => {
                           const chosen = e.target.files?.[0]
                           e.target.value = ''
                           swapPicture(chosen)
                         }} />
                  <Tip text="Use your own picture instead of generating a new one">
                    <Button variant="outline" onClick={() => swapRef.current?.click()}>
                      <Upload className="size-3" /> Upload
                    </Button>
                  </Tip>
                </>
              )}

              <Button variant="solid" onClick={submit} disabled={reading || tuning}>
                {reading || tuning ? <Loader2 className="size-3 animate-spin" /> : 'Review request'}
              </Button>
              <Button variant="outline" onClick={() => { files.reset(); setPicked(null) }}>
                Cancel
              </Button>
            </div>

            <EditAttach attach={files} disabled={reading || tuning} className="mt-2" />
          </div>
        )}

        {tests.running && e2eLive && <LiveE2EOverlay event={e2eLive} />}

        <div className="relative mx-auto flex h-full max-w-full items-start justify-center overflow-auto rounded-[28px] bg-white/28 p-2 ring-1 ring-white/55 dark:bg-white/[.02] dark:ring-white/[.06]">
          <div className="relative h-full w-full max-w-full overflow-hidden rounded-[22px] bg-white shadow-[0_24px_70px_rgba(15,23,42,.14)] ring-1 ring-black/[.06] dark:bg-[#0f1720] dark:shadow-[0_24px_70px_rgba(0,0,0,.45)] dark:ring-white/[.06]"
               style={{ width: width ? width + 'px' : '100%' }}>
            <iframe ref={frameRef} id="frame" title="preview" src="/"
                    className="absolute inset-0 block h-full w-full border-0 bg-white" />
            {tests.running && e2eLive?.frame && (
              <img src={e2eLive.frame} alt="Live Playwright browser"
                   className="pointer-events-none absolute inset-0 z-[4] h-full w-full object-cover object-top transition-opacity duration-200" />
            )}
          </div>
        </div>

        <PreviewConsoleDrawer />
      </div>
    </div>
  )
}

function Cell({ tip, on, className, children, ...rest }) {
  return (
    <Tip text={tip}>
      <button {...rest}
              className={cn('grid h-9 place-items-center px-2 text-ink transition-colors',
                on ? 'bg-accent text-white shadow-sm'
                   : 'hover:bg-ink/[.06] dark:hover:bg-white/[.06]',
                'disabled:pointer-events-none disabled:text-faint', className)}>
        {children}
      </button>
    </Tip>
  )
}
