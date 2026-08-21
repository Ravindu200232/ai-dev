'use client'

import { useEffect, useState } from 'react'
import { ArrowRight, Check, Expand, Loader2, RefreshCw, Sparkles, X } from 'lucide-react'
import { api } from '@/lib/api'
import { Button, Modal } from './ui'
import { cn } from '@/lib/utils'


/**
 * What the wait actually means. Five whole-app designs are five calls to the
 * build model at once — the heaviest moment in the product — so a long wait
 * here is normal and a silent spinner is not a status.
 */
function drawingNote(waited) {
  if (waited > 240) return 'the model is taking much longer than usual — it may be busy'
  if (waited > 150) return 'still drawing — five designs run at the same time'
  if (waited > 60) return 'still drawing the full app'
  return 'drawing the pages of each direction'
}

export default function ThemePicker({ idea, srsId, model, onPick, onSkip }) {
  const [state, setState] = useState('drawing')
  const [themes, setThemes] = useState([])
  const [page, setPage] = useState('')
  const [dir, setDir] = useState('')
  const [chosen, setChosen] = useState('')
  const [big, setBig] = useState(null)
  const [error, setError] = useState('')
  const [waited, setWaited] = useState(0)

  async function draw() {
    setState('drawing')
    setError('')
    setWaited(0)
    try {
      const r = await api.themes({ prompt: idea, srs_id: srsId || '', model }, { onWait: setWaited })
      setThemes(r.themes || [])
      setPage(r.page || '')
      setDir(r.designs || '')
      setChosen((r.themes || [])[0]?.id || '')
      setState('ready')
    } catch (e) {
      setError(e.message)
      setState('ready')
    }
  }

  useEffect(() => { draw() }, [])

  const picked = themes.find(t => t.id === chosen)

  return (
    <Modal onClose={onSkip} className="max-w-[1240px] overflow-hidden rounded-[34px] p-0">
      <div className="bg-[radial-gradient(circle_at_12%_0%,rgba(93,106,251,.13),transparent_34%),radial-gradient(circle_at_95%_10%,rgba(175,134,255,.10),transparent_26%)]">
        <header className="flex items-center gap-4 px-7 pb-5 pt-6">
          <div className="grid size-11 place-items-center rounded-[17px] bg-accent text-white shadow-[0_12px_28px_rgba(93,106,251,.26)]">
            <Sparkles className="size-5" />
          </div>
          <div className="min-w-0">
            <p className="text-[18px] font-semibold tracking-[-.025em] text-ink">Choose the visual direction</p>
            <p className="mt-0.5 text-[11.5px] text-muted">Preview the whole product before the build starts{page ? ` · ${page}` : ''}.</p>
          </div>
          <span className="flex-1" />
          {state === 'ready' && themes.length > 0 && (
            <button onClick={draw} className="inline-flex h-9 items-center gap-2 rounded-full bg-white/70 px-4 text-[11px] font-semibold text-ink shadow-sm ring-1 ring-line/70 transition hover:bg-white dark:bg-white/[.06] dark:hover:bg-white/[.1]">
              <RefreshCw className="size-3.5" /> New directions
            </button>
          )}
          <button onClick={onSkip} className="grid size-9 place-items-center rounded-full text-muted transition hover:bg-black/[.05] hover:text-ink dark:hover:bg-white/[.06]">
            <X className="size-4" />
          </button>
        </header>

        {state === 'drawing' ? (
          <div className="grid min-h-[520px] place-items-center px-8 pb-8">
            <div className="max-w-[420px] text-center">
              <span className="mx-auto grid size-14 place-items-center rounded-full bg-accent/10 text-accent"><Loader2 className="size-6 animate-spin" /></span>
              <p className="mt-5 text-[16px] font-semibold text-ink">Creating five complete directions</p>
              <p className="mt-2 text-[11.5px] leading-relaxed text-muted">Each direction uses the approved product plan, not a generic template. You can inspect every page before choosing.</p>
              {waited > 0 && (
                <p className="mt-4 text-[10.5px] text-muted2">
                  {Math.round(waited)}s · {drawingNote(waited)}
                </p>
              )}
              {waited > 240 && (
                <button onClick={onSkip}
                        className="mt-5 rounded-full border border-line px-4 py-2
                                   text-[11px] font-medium text-muted transition
                                   hover:bg-ink/[.05]">
                  Skip the designs and build anyway
                </button>
              )}
            </div>
          </div>
        ) : (
          <>
            {error && (
              <div className="mx-7 mb-4 rounded-[18px] bg-warn-tint px-4 py-3 text-[11.5px] text-warn">
                <p>{error}</p>
                {!themes.length && (
                  <div className="mt-2 flex gap-2">
                    <button onClick={draw}
                            className="rounded-full bg-warn/15 px-3 py-1 font-semibold">
                      Try again
                    </button>
                    <button onClick={onSkip}
                            className="rounded-full px-3 py-1 font-medium underline">
                      Build without choosing a design
                    </button>
                  </div>
                )}
              </div>
            )}

            <div className="px-7 pb-6">
              <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(205px,1fr))]">
                {themes.map(t => {
                  const selected = t.id === chosen
                  return (
                    <button key={t.id} onClick={() => setChosen(t.id)} onDoubleClick={() => setBig(t)}
                            className={cn('group relative overflow-hidden rounded-[24px] p-2.5 text-left transition-all duration-300',
                              selected
                                ? 'bg-white shadow-[0_18px_42px_rgba(30,41,59,.13)] ring-2 ring-accent/45 dark:bg-white/[.07]'
                                : 'bg-white/55 shadow-[0_10px_28px_rgba(30,41,59,.06)] ring-1 ring-line/70 hover:-translate-y-1 hover:bg-white dark:bg-white/[.035]')}>
                      <span className="relative block h-[240px] overflow-hidden rounded-[18px] bg-[#f4f6fb] ring-1 ring-black/[.035]">
                        <iframe title={t.name} srcDoc={t.html} sandbox="allow-scripts" tabIndex={-1}
                                className="pointer-events-none h-[960px] w-[400%] origin-top-left scale-[0.25] border-0 bg-white" />
                        <span className="absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-black/10 to-transparent opacity-0 transition group-hover:opacity-100" />
                        <span onClick={e => { e.stopPropagation(); setBig(t) }}
                              className="absolute right-3 top-3 grid size-8 place-items-center rounded-full bg-white/90 text-ink opacity-0 shadow-lg backdrop-blur transition group-hover:opacity-100">
                          <Expand className="size-3.5" />
                        </span>
                      </span>

                      <span className="flex items-start gap-3 px-1 pb-1 pt-3">
                        <span className={cn('mt-px grid size-5 shrink-0 place-items-center rounded-full border transition', selected ? 'border-accent bg-accent text-white' : 'border-line2 bg-white/70 text-transparent dark:bg-white/[.04]')}>
                          <Check className="size-3" />
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate text-[12.5px] font-semibold text-ink">{t.name}</span>
                          <span className="mt-0.5 line-clamp-2 block text-[10.5px] leading-relaxed text-muted">{t.blurb || 'A complete visual direction for the app.'}</span>
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>

            <footer className="flex items-center gap-3 bg-white/42 px-7 py-4 backdrop-blur-xl dark:bg-white/[.025]">
              <p className="text-[10.5px] text-muted">The selected HTML becomes the visual source of truth for colors, typography, spacing and component style.</p>
              <span className="flex-1" />
              <button onClick={onSkip} className="h-10 rounded-full px-4 text-[11px] font-semibold text-muted transition hover:bg-black/[.045] hover:text-ink dark:hover:bg-white/[.06]">Choose for me</button>
              <button disabled={!picked} onClick={() => onPick({ id: picked.id, html: picked.html, page, dir })}
                      className="inline-flex h-10 items-center gap-2 rounded-full bg-accent px-5 text-[11.5px] font-semibold text-white shadow-[0_10px_24px_rgba(93,106,251,.26)] transition hover:bg-press disabled:pointer-events-none disabled:opacity-45">
                Use {picked?.name || 'this design'} <ArrowRight className="size-3.5" />
              </button>
            </footer>
          </>
        )}
      </div>

      {big && (
        <div onClick={() => setBig(null)} className="fixed inset-0 z-[700] grid place-items-center bg-[#080b12]/72 p-5 backdrop-blur-lg">
          <div onClick={e => e.stopPropagation()} className="flex h-[min(900px,94vh)] w-full max-w-[1480px] flex-col overflow-hidden rounded-[30px] bg-[#eef2f8] shadow-[0_35px_100px_rgba(0,0,0,.38)] ring-1 ring-white/20 dark:bg-[#0e141e]">
            <div className="flex h-14 shrink-0 items-center gap-3 bg-white/82 px-5 backdrop-blur-xl dark:bg-[#151c28]/94">
              <div className="min-w-0">
                <p className="truncate text-[13px] font-semibold text-ink">{big.name}</p>
                <p className="truncate text-[10.5px] text-muted">{big.blurb || 'Full product preview'}</p>
              </div>
              <span className="flex-1" />
              <button onClick={() => { setChosen(big.id); setBig(null) }} className="inline-flex h-9 items-center gap-2 rounded-full bg-accent px-4 text-[11px] font-semibold text-white shadow-sm"><Check className="size-3.5" /> Select design</button>
              <button onClick={() => setBig(null)} className="grid size-9 place-items-center rounded-full text-muted hover:bg-black/[.05] hover:text-ink dark:hover:bg-white/[.06]"><X className="size-4" /></button>
            </div>
            <div className="min-h-0 flex-1 p-3">
              <div className="h-full overflow-hidden rounded-[22px] bg-white shadow-[0_18px_55px_rgba(15,23,42,.12)] ring-1 ring-black/[.045]">
                <iframe title={big.name} srcDoc={big.html} sandbox="allow-scripts" className="h-full w-full border-0 bg-white" />
              </div>
            </div>
          </div>
        </div>
      )}
    </Modal>
  )
}
