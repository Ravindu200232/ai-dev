'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { FileCode2, Loader2, Save, Undo2 } from 'lucide-react'
import { useStore } from '@/lib/store'
import { api } from '@/lib/api'
import { highlight } from '@/lib/highlight'
import { cn } from '@/lib/utils'
import { plainField } from './ui'
import FileTree, { fileBadge } from './FileTree'


const TAB = '  '


export default function CodePane({ hidden }) {
  const files = useStore(s => s.files)
  const project = useStore(s => s.project)
  const activeFile = useStore(s => s.activeFile)
  const setActiveFile = useStore(s => s.setActiveFile)
  const putFile = useStore(s => s.putFile)
  const addLog = useStore(s => s.addLog)
  const liveFile = useStore(s => s.liveFile)
  const liveBuf = useStore(s => s.liveBuf)
  const follow = useStore(s => s.follow)
  const tailRef = useRef(null)
  const boxRef = useRef(null)
  const gutterRef = useRef(null)
  const paintRef = useRef(null)

  // Edits live here until they are saved, keyed by path, so switching files
  // and coming back does not lose what was typed — the same promise an editor
  // makes with its tabs.
  const [draft, setDraft] = useState({})
  const [saving, setSaving] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (liveFile && follow) tailRef.current?.scrollIntoView({ block: 'end' })
  }, [liveBuf, liveFile, follow])

  // Following the writer is the default and it is what makes a build worth
  // watching — but only until the reader picks a file. `setActiveFile` drops
  // `follow`, so a click always shows what was clicked, whatever the build is
  // doing and whether or not the stream it opened was ever closed.
  const streaming = !!liveFile && follow
  const shown = streaming ? liveFile : activeFile
  const saved = activeFile ? (files[activeFile] ?? '') : ''
  const edited = activeFile != null && draft[activeFile] !== undefined
  const body = streaming ? liveBuf : (edited ? draft[activeFile] : saved)
  const dirty = new Set(Object.keys(draft).filter(p => draft[p] !== files[p]))
  const isDirty = activeFile != null && dirty.has(activeFile)

  async function save() {
    if (!activeFile || !isDirty || !project) return
    const path = activeFile
    const content = draft[path]
    setSaving(path)
    setError('')
    try {
      await api.saveFile(project, path, content)
      putFile(path, content)
      setDraft(d => {
        const next = { ...d }
        delete next[path]
        return next
      })
      addLog('SUCCESS', `Saved ${path}`)
    } catch (e) {
      setError(e.message)
      addLog('WARN', `Could not save ${path} — ${e.message}`)
    }
    setSaving('')
  }

  function revert() {
    if (!activeFile) return
    setDraft(d => {
      const next = { ...d }
      delete next[activeFile]
      return next
    })
    setError('')
  }

  function onKeyDown(e) {
    // Ctrl/Cmd+S, because anybody who types into a code pane will press it and
    // the browser's own "save page" dialog is never what they meant.
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
      e.preventDefault()
      save()
      return
    }
    // Tab indents instead of leaving the editor. Without it the first press
    // moves focus to the next control and the keystroke is lost from the file.
    if (e.key === 'Tab') {
      e.preventDefault()
      const el = e.target
      const { selectionStart: a, selectionEnd: b, value } = el
      const next = value.slice(0, a) + TAB + value.slice(b)
      type(next)
      requestAnimationFrame(() => {
        el.selectionStart = el.selectionEnd = a + TAB.length
      })
    }
  }

  function type(value) {
    if (!activeFile) return
    setDraft(d => ({ ...d, [activeFile]: value }))
  }

  const lines = String(body || '').split('\n')
  const badge = shown ? fileBadge(shown.split('/').pop()) : null

  // Re-tokenised only when the text or the file changes, not on every render —
  // the pane re-renders on any store change, and a build changes the store
  // several times a second.
  //
  // The trailing newline matters. A textarea's last line is a real line to the
  // caret but produces no text node in the <pre>, so a file ending in `\n`
  // renders one line shorter underneath and the last line of a long file sits
  // a row above its own caret.
  const painted = useMemo(
    () => highlight(String(body || '') + '\n', shown || ''),
    [body, shown])

  return (
    <div className={cn('flex min-h-0 flex-1 flex-col', hidden && 'hidden')}>
      <div className="flex h-[48px] shrink-0 items-center gap-2.5 border-b border-line/70 bg-white/42 px-4 backdrop-blur-xl dark:bg-white/[.018]">
        {badge ? (
          <span className="shrink-0 rounded-lg border border-line bg-white/65 px-2 py-1 font-mono text-[9px] text-muted shadow-sm dark:bg-white/5">
            {badge}
          </span>
        ) : (
          <FileCode2 className="size-3.5 shrink-0 text-muted2" />
        )}
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted">
          {shown || 'No file selected'}
          {isDirty && <span className="ml-1.5 text-accent">●</span>}
        </span>

        {error && (
          <span className="max-w-[280px] truncate text-[10px] text-deep"
                title={error}>{error}</span>
        )}

        {isDirty && !streaming && (
          <>
            <button onClick={revert} title="Throw the edit away"
                    className="flex shrink-0 items-center gap-1 rounded-lg border border-line bg-white/60 px-2 py-1 font-mono text-[10px] text-muted shadow-sm transition hover:bg-white dark:bg-white/5">
              <Undo2 className="size-2.5" /> revert
            </button>
            <button onClick={save} disabled={!!saving}
                    title="Save to disk (Ctrl+S)"
                    className="flex shrink-0 items-center gap-1 rounded-lg border border-accent bg-accent px-2 py-1 font-mono text-[10px] text-white shadow-sm transition hover:bg-press disabled:opacity-50">
              {saving ? <Loader2 className="size-2.5 animate-spin" />
                      : <Save className="size-2.5" />}
              save
            </button>
          </>
        )}

        {streaming && (
          <span className="shrink-0 border border-accent px-2 py-0.5 font-mono
                           text-[10px] text-accent">
            writing…
          </span>
        )}
        {/* Clicking a file stops following the writer, so the way back has to
            be on screen — otherwise the live view is gone until the next file
            starts, and on a slow one that is minutes. */}
        {!!liveFile && !follow && (
          <button onClick={() => useStore.setState({ follow: true })}
                  title={`Still writing ${liveFile}`}
                  className="shrink-0 border border-accent px-2 py-0.5 font-mono
                             text-[10px] text-accent transition-colors
                             hover:bg-accent hover:text-bg">
            ● back to live
          </button>
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="m-3 mr-0 flex w-[238px] shrink-0 flex-col overflow-hidden rounded-[18px] border border-line/80 bg-white/50 shadow-sm dark:bg-white/[.025]">
          <div className="flex shrink-0 items-center justify-between border-b border-line/70 px-3.5 py-3">
            <span className="label-xs text-ink">Files</span>
            <span className="font-mono text-[10px] text-muted2">
              {Object.keys(files).length}
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <FileTree files={files} active={activeFile} dirty={dirty}
                      onPick={setActiveFile} />
          </div>
        </div>

        <div className="relative m-3 min-h-0 flex-1 overflow-hidden rounded-[18px] border border-line/80 bg-code shadow-[inset_0_1px_0_rgba(255,255,255,.04)]">
          {body || activeFile ? (
            <div className="flex h-full min-h-0">
              {/* The gutter scrolls with the text rather than inside its own
                  box: one scroll container, two columns, so a long file cannot
                  drift out of step with its own line numbers. */}
              <div ref={gutterRef}
                   className="min-w-[46px] shrink-0 select-none overflow-hidden border-r
                              border-line pt-4 text-right font-mono text-[11px]
                              leading-[1.75] text-faint">
                {lines.map((_, i) => (
                  <div key={i} className="px-2.5">{i + 1}</div>
                ))}
                <div className="h-8" />
              </div>

              {streaming ? (
                <pre className="editor-layer min-h-0 flex-1 overflow-auto"
                     style={{ color: 'var(--tk-plain)', whiteSpace: 'pre-wrap',
                              overflowWrap: 'break-word' }}>
                  <span dangerouslySetInnerHTML={{ __html: painted }} />
                  <span className="ml-0.5 inline-block h-[1em] w-[7px]
                                   animate-pulse bg-accent align-text-bottom" />
                  <span ref={tailRef} />
                </pre>
              ) : (
                // Two layers, one on top of the other. The <pre> underneath
                // carries the colours; the <textarea> on top carries the
                // caret, the selection and every keystroke, with its own text
                // made invisible. `editor-layer` is what keeps the two in
                // register — see globals.css.
                <div className="relative min-h-0 flex-1">
                  <pre aria-hidden
                       ref={paintRef}
                       className="editor-layer pointer-events-none absolute
                                  inset-0 overflow-hidden"
                       style={{ color: 'var(--tk-plain)' }}
                       dangerouslySetInnerHTML={{ __html: painted }} />
                  <textarea
                    ref={boxRef}
                    value={body}
                    {...plainField}
                    onChange={e => type(e.target.value)}
                    onKeyDown={onKeyDown}
                    onScroll={e => {
                      const { scrollTop, scrollLeft } = e.target
                      if (gutterRef.current) gutterRef.current.scrollTop = scrollTop
                      if (paintRef.current) {
                        paintRef.current.scrollTop = scrollTop
                        paintRef.current.scrollLeft = scrollLeft
                      }
                    }}
                    className="editor-layer absolute inset-0 size-full resize-none
                               overflow-auto bg-transparent text-transparent
                               outline-none selection:bg-accent/30"
                    style={{ caretColor: 'var(--tk-plain)' }} />
                </div>
              )}
            </div>
          ) : (
            <p className="p-4 font-mono text-[11px] text-muted2">
              {Object.keys(files).length
                ? 'Pick a file from the tree.'
                : 'Files appear here as they are written.'}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
