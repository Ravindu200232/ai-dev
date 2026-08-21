'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Minus, Plus, RotateCcw, X } from 'lucide-react'
import { cn } from '@/lib/utils'

const MIN = 0.4
const MAX = 8
const STEP = 0.25

/** A diagram at the size it deserves. */
export default function DiagramViewer({ svg, title, onClose }) {
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const drag = useRef(null)
  const surface = useRef(null)

  const reset = useCallback(() => { setZoom(1); setPan({ x: 0, y: 0 }) }, [])

  useEffect(() => {
    const key = (e) => {
      if (e.key === 'Escape') onClose?.()
      else if (e.key === '+' || e.key === '=') setZoom(z => Math.min(MAX, z + STEP))
      else if (e.key === '-') setZoom(z => Math.max(MIN, z - STEP))
      else if (e.key === '0') reset()
    }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [onClose, reset])

      // Block page scrolling while the diagram zooms.
  useEffect(() => {
    const el = surface.current
    if (!el) return
    const wheel = (e) => {
      e.preventDefault()
      setZoom(z => Math.min(MAX, Math.max(MIN, z - Math.sign(e.deltaY) * STEP)))
    }
    el.addEventListener('wheel', wheel, { passive: false })
    return () => el.removeEventListener('wheel', wheel)
  }, [])

  const down = (e) => {
    drag.current = { x: e.clientX - pan.x, y: e.clientY - pan.y }
  }
  const move = (e) => {
    if (!drag.current) return
    setPan({ x: e.clientX - drag.current.x, y: e.clientY - drag.current.y })
  }
  const up = () => { drag.current = null }

  return (
    <div onClick={onClose}
         className="fixed inset-0 z-[700] flex flex-col bg-slate-950/70 backdrop-blur-md">
      <div onClick={e => e.stopPropagation()}
           className="flex shrink-0 items-center gap-3 px-5 py-3 text-white">
        <h3 className="min-w-0 flex-1 truncate text-[14px] font-semibold capitalize">
          {String(title || 'Diagram').replace(/_/g, ' ')}
        </h3>
        <span className="rounded-full bg-white/12 px-2.5 py-1 text-[11px] tabular-nums">
          {Math.round(zoom * 100)}%
        </span>
        <Ctl label="Zoom out" onClick={() => setZoom(z => Math.max(MIN, z - STEP))}>
          <Minus className="size-4" />
        </Ctl>
        <Ctl label="Zoom in" onClick={() => setZoom(z => Math.min(MAX, z + STEP))}>
          <Plus className="size-4" />
        </Ctl>
        <Ctl label="Reset" onClick={reset}><RotateCcw className="size-4" /></Ctl>
        <Ctl label="Close" onClick={onClose}><X className="size-4" /></Ctl>
      </div>

      <div ref={surface}
           onClick={e => e.stopPropagation()}
           onPointerDown={down} onPointerMove={move}
           onPointerUp={up} onPointerLeave={up}
           className={cn('min-h-0 flex-1 overflow-hidden',
             drag.current ? 'cursor-grabbing' : 'cursor-grab')}>
        <div className="flex h-full w-full items-center justify-center">
          <div className="srs-diagram origin-center rounded-[18px] bg-white p-6 shadow-2xl [&_svg]:h-auto [&_svg]:max-w-none"
               style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
               dangerouslySetInnerHTML={{ __html: svg }} />
        </div>
      </div>

      <p onClick={e => e.stopPropagation()}
         className="shrink-0 px-5 py-2.5 text-center text-[11px] text-white/45">
        Scroll to zoom · drag to pan · <b className="font-semibold text-white/70">+</b> /
        <b className="font-semibold text-white/70"> −</b> /
        <b className="font-semibold text-white/70"> 0</b> · Esc to close
      </p>
    </div>
  )
}

const Ctl = ({ label, onClick, children }) => (
  <button title={label} aria-label={label} onClick={onClick}
          className="grid size-8 shrink-0 place-items-center rounded-full bg-white/12 text-white transition-colors hover:bg-white/22">
    {children}
  </button>
)
