'use client'

import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Image as ImageIcon, Loader2, RefreshCw, Sparkles, Upload, X }
  from 'lucide-react'
import { api } from '@/lib/api'
import { Button, Modal, Tag, TextArea } from './ui'
import SitePictures from './SitePictures'
import { uploadMap } from '@/lib/picture-keys'
import { cn } from '@/lib/utils'

const ACCEPT_LOGO = '.png,.jpg,.jpeg,.webp,.gif,.bmp,image/*'

export default function LogoPanel({ idea, model, onAccept, onSkip }) {
  // Pictures the person brought. Empty is the normal case and means the
  // build draws everything, exactly as it did before this existed.
  const [pictures, setPictures] = useState([])
  const [prompt, setPrompt] = useState('')
  const [state, setState] = useState('writing')
  const [img, setImg] = useState(null)
  const [file, setFile] = useState('')
  const [error, setError] = useState('')
  const [own, setOwn] = useState('')
  const picker = useRef(null)

  const [waited, setWaited] = useState(0)
  // Only for the label on the sheet — "draft 2" is the difference between
  // looking at the mark you just asked for and looking at the one before it.
  const [draft, setDraft] = useState(0)

  useEffect(() => {
    let alive = true
    api.logoPrompt(idea, model)
      .then(r => {
        if (!alive) return
        setPrompt(r.prompt || idea.slice(0, 90))
        setState('ready')
      })
      .catch(e => { if (alive) { setError(e.message); setState('ready') } })
    return () => { alive = false }
  }, [idea, model])

  async function draw() {
    setState('drawing')
    setError('')
    setWaited(0)
    try {
      const r = await api.image(
        { prompt, name: 'logo', aspect: 'square', force: true },
        { onWait: setWaited })
      setImg(r.data_uri || '')
      setFile(r.file || '')
      setOwn('')
      setDraft(n => n + 1)
    } catch (e) {
      setError(e.message)
    }
    setState('ready')
  }

  async function upload(chosen) {
    if (!chosen) return
    setState('uploading')
    setError('')
    try {
      // Answers in the same shape `draw` does, so everything below this point
      // — the preview, Accept, and the build that copies the file to
      // public/logo.png — cannot tell the two apart.
      const r = await api.imageUpload(chosen, { name: 'logo' })
      setImg(r.data_uri || '')
      setFile(r.file || '')
      setOwn(chosen.name || 'your file')
    } catch (e) {
      setError(e.message)
    }
    setState('ready')
  }

  return (
    <Modal onClose={onSkip} className="max-w-[880px] p-0">
      <header className="flex items-center gap-3 border-b-2 border-line2 px-[22px] py-[15px]">
        <ImageIcon className="size-[15px] shrink-0 text-accent" />
        <h3 className="min-w-0 truncate font-display text-[16px] font-extrabold
                       uppercase tracking-[-.01em] text-ink">
          A mark for your app
        </h3>
        <span className="flex-1" />
        <span className="shrink-0 font-mono text-[10.5px] text-muted2">
          before the build
        </span>
        <Button variant="outline" size="icon" className="size-[28px]" onClick={onSkip}
                title="Skip the logo">
          <X className="size-3.5" />
        </Button>
      </header>

      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,300px)]">
        <div className="min-w-0 border-r-2 border-line2 px-[22px] py-5">
          <div className="border-b border-line pb-1.5">
            <span className="label-xs text-ink">The prompt the agent wrote</span>
          </div>

          <TextArea value={prompt} rows={3} disabled={state === 'writing'}
                    placeholder={state === 'writing' ? 'Writing a prompt…' : ''}
                    onChange={e => setPrompt(e.target.value)}
                    className="mt-3 w-full resize-y border-2 border-line2 bg-panel2
                               px-3.5 py-3 text-[13px] leading-[1.6] text-ink
                               caret-accent outline-none focus:border-accent
                               disabled:opacity-60" />

          <div className="mt-3.5 flex flex-wrap gap-2">
            <Button variant="solid" onClick={draw}
                    disabled={state !== 'ready' || !prompt.trim()}>
              {img ? <><RefreshCw className="size-[13px]" /> Draw again</>
                   : <><Sparkles className="size-[13px]" /> Draw it</>}
            </Button>
            {/* Not gated on `state === 'ready'` like Draw is: uploading needs
                no prompt, so it should not wait behind the model writing one. */}
            <Button variant="outline"
                    disabled={state === 'drawing' || state === 'uploading'}
                    onClick={() => picker.current?.click()}
                    title="Use a logo you already have, instead of drawing one">
              <Upload className="size-[13px]" /> Upload your own
            </Button>
          </div>

          <div className="mt-5 border-t border-line pt-3.5">
            <span className="label-xs text-ink">What it is used for</span>
            <div className="mt-1.5">
              {USES.map(([where, what], i) => (
                <div key={where}
                     className={cn('grid grid-cols-[120px_1fr] gap-3 py-[5px]',
                                   i < USES.length - 1 && 'border-b border-line')}>
                  <span className="text-[12px] font-semibold text-ink">{where}</span>
                  <span className="text-[12px] text-muted">{what}</span>
                </div>
              ))}
            </div>
          </div>

          <p className="mt-4 text-[11.5px] leading-snug text-muted">
            Skipping is fine — the app ships with a plain wordmark and you can
            add a picture later.
          </p>
        </div>

        <div className="flex min-w-0 flex-col px-[22px] py-5">
          <div className="border-b border-line pb-1.5">
            <span className="label-xs text-ink">
              {state === 'drawing' ? 'Drawing' : img || file ? 'Drawn' : 'Nothing yet'}
            </span>
          </div>

          <div className="mt-3 grid h-[240px] place-items-center overflow-hidden
                          border-2 border-line2 bg-panel2">
            {state === 'drawing'
              ? <span className="flex flex-col items-center gap-1.5 text-[12px] text-muted">
                  <span className="flex items-center gap-2">
                    <Loader2 className="size-4 animate-spin" /> Drawing…
                  </span>
                  <span className="font-mono text-[10.5px] text-muted2">
                    {waited ? `${Math.round(waited)}s` : ''}
                    {waited > 75 ? ' · a cold model load takes the first minute' : ''}
                  </span>
                </span>
              : state === 'uploading'
                ? <span className="flex items-center gap-2 text-[12px] text-muted">
                    <Loader2 className="size-4 animate-spin" /> Reading your file…
                  </span>
                : img
                                    ? <img src={img}
                         alt={own ? `the logo you uploaded, ${own}` : 'the generated logo'}
                         className="max-h-full max-w-full" />
                  // `file` without `img` must not read as "nothing here": the
                  // panel would then deny the upload in the box and confirm it
                  // in the line underneath, with Accept live.
                  : file
                    ? <span className="px-6 text-center text-[12px] text-muted">
                        Saved, but it could not be shown here. Accept only if
                        you are sure of the file.
                      </span>
                    : <span className="px-6 text-center text-[12px] text-muted2">
                        Nothing drawn yet — draw one, or upload your own.
                      </span>}
          </div>

          {(img || file) && (
            <div className="mt-2 flex items-center gap-2">
              <span className="min-w-0 truncate font-mono text-[10.5px] text-muted">
                {own || 'logo.png'}
              </span>
              <span className="flex-1" />
              {draft > 0 && !own && <Tag>draft {draft}</Tag>}
            </div>
          )}

          <p className="mt-2 text-[10.5px] leading-snug text-muted2">
            Shown exactly as it will be written into the project.
          </p>

          {error && (
            <p className="mt-2 border-l-[3px] border-accent bg-tint px-2.5 py-1.5
                          text-[11.5px] text-deep">
              {error}
            </p>
          )}

          <SitePictures pictures={pictures} onChange={setPictures} />
        </div>
      </div>

      <input ref={picker} type="file" hidden accept={ACCEPT_LOGO}
             onChange={e => {
               const chosen = e.target.files?.[0]
               e.target.value = ''   // so re-picking the same file fires again
               upload(chosen)
             }} />

      <footer className="flex items-stretch border-t-2 border-line2">
        <button onClick={() => onSkip(uploadMap(pictures))}
                className="inline-flex items-center px-[22px] py-3 font-display
                           text-[11.5px] font-extrabold text-ink transition-colors
                           hover:bg-ink/[.07]">
          {pictures.length ? 'No mark, keep my pictures' : 'Skip for now'}
        </button>
        <span className="flex-1" />
        <button disabled={!file} onClick={() => onAccept(file, uploadMap(pictures))}
                className="inline-flex items-center gap-2 bg-accent px-[22px] py-3
                           font-display text-[11.5px] font-extrabold uppercase
                           tracking-[.08em] text-bg transition-colors
                           hover:bg-press disabled:pointer-events-none
                           disabled:opacity-45">
          Use this mark <ArrowRight className="size-3.5" />
        </button>
      </footer>
    </Modal>
  )
}

// What the mark ends up on, so "skip" is a decision rather than a guess.
const USES = [
  ['App icon', 'favicon and the header lockup'],
  ['Sign-in page', 'above the form'],
  ['Emails', 'the header band'],
]
