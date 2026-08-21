'use client'

import { forwardRef, useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'


/** Shared action button. */
export function Button({ variant = 'ghost', size = 'md', className, ...rest }) {
  const iconOnly = size === 'icon' || size === 'icon-sm'
  return (
    <button
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-xl font-display font-semibold',
        'transition-all duration-200 disabled:pointer-events-none disabled:opacity-45',
        iconOnly ? 'justify-center' : 'justify-start text-left',
        { sm: 'h-[26px] px-2 text-[10.5px]', md: 'h-[30px] px-3 text-[12px]',
          lg: 'h-[34px] px-4 text-[13px]', icon: 'size-[30px] p-0',
          'icon-sm': 'size-[26px] p-0' }[size],
        { solid: 'border border-accent bg-accent text-bg '
               + 'hover:border-press hover:bg-press',
          outline: 'border border-line2 text-ink hover:bg-ink/[.07]',
          ghost: 'border border-transparent text-muted hover:bg-ink/[.07] hover:text-ink',
          accent: 'border border-transparent text-accent hover:bg-accent/10',
          subtle: 'border border-transparent bg-panel2 text-ink hover:bg-ink/[.07]',
        }[variant],
        className)}
      {...rest} />
  )
}

/** Compact status label. */
export function Tag({ tone = 'mute', className, children }) {
  return (
    <span className={cn(
      'inline-flex items-center rounded-full px-2 py-[2px]',
      'text-[10px] font-semibold uppercase tracking-[.08em]',
      { mute: 'bg-panel2 text-label', ok: 'bg-ok-tint text-ok',
        bad: 'bg-tint text-deep', warn: 'bg-warn-tint text-warn',
        accent: 'border border-accent text-accent',
        solid: 'bg-accent text-bg' }[tone],
      className)}>
      {children}
    </span>
  )
}

/** A count, always in the mono face so columns of them line up. */
export function Badge({ tone = 'mute', className, children }) {
  return (
    <span className={cn(
      'inline-flex items-center rounded-full font-mono text-[10px] px-1.5 py-px',
      { mute: 'bg-panel2 text-muted2', ok: 'bg-ok-tint text-ok',
        bad: 'bg-tint text-deep', warn: 'bg-warn-tint text-warn',
        accent: 'bg-accent text-bg' }[tone],
      className)}>
      {children}
    </span>
  )
}

/** The head of a section: the label at the left, its count at the right. */
export function SectionLabel({ children, right, className }) {
  return (
    <div className={cn('flex items-center justify-between gap-2',
      'label-xs text-ink', className)}>
      <span>{children}</span>
      {right}
    </div>
  )
}

/** The same, one step quieter — a sub-head inside a panel that already has one. */
export function SubLabel({ children, right, className }) {
  return (
    <div className={cn('flex items-center justify-between gap-2',
      'label-xs text-label', className)}>
      <span>{children}</span>
      {right}
    </div>
  )
}

/** Segmented choice control. */
export function Seg({ block, className, children }) {
  return (
    // Space between the options, not a rule. The hairline every child after
    // the first used to draw sat right against the rounded ground of the
    // selected one, so the band read as a ruled table cell rather than a
    // switch.
    <div className={cn('flex gap-1',
      block ? 'w-full rounded-xl bg-panel2 p-1' : 'w-fit rounded-xl border border-line bg-panel2 p-1', className)}>
      {children}
    </div>
  )
}

export function SegOpt({ on, block, className, children, ...rest }) {
  return (
    <button {...rest}
      className={cn('inline-flex items-center gap-1.5 font-display font-extrabold',
        'transition-colors disabled:pointer-events-none disabled:opacity-45',
        block ? 'flex-1 justify-start py-[7px] pl-3 pr-2 text-[11px]'
              : 'px-[11px] py-[6px] text-[12px]',
        on ? 'rounded-lg bg-white text-ink shadow-sm dark:bg-white/10'
           : 'text-label hover:bg-ink/[.07] hover:text-ink',
        className)}>
      {children}
    </button>
  )
}

/** Secondary tab row. */
export function SubTabs({ className, children }) {
  return (
    <div className={cn('flex shrink-0 items-stretch overflow-x-auto',
      'border-b border-line/70 bg-white/35 backdrop-blur-xl dark:bg-white/[.015]', className)}>
      {children}
    </div>
  )
}

export function SubTab({ on, className, children, ...rest }) {
  return (
    <button {...rest}
      className={cn('inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap',
        'm-1 rounded-xl border border-transparent px-[12px] py-[7px]',
        'font-display text-[11px] transition-colors',
        on ? 'border-line bg-white font-semibold text-ink shadow-sm dark:bg-white/8'
           : 'font-medium text-label hover:bg-white/50 hover:text-ink dark:hover:bg-white/5',
        className)}>
      {children}
    </button>
  )
}

export const Panel = ({ className, children }) => (
  <div className={cn('rounded-[20px] border border-line bg-panel shadow-sm', className)}>
    {children}
  </div>
)

export const Empty = ({ children, bad }) => (
  <p className={cn('px-1 py-7 text-[12px]', bad ? 'text-deep' : 'text-muted')}>
    {children}
  </p>
)

export function Modal({ onClose, children, className }) {
  useEffect(() => {
    const key = (e) => { if (e.key === 'Escape') onClose?.() }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [onClose])
  return (
    // A dialog taller than the window used to be centred with no way to reach
    // its ends: nothing scrolled, so the heading sat above the fold and the
    // buttons below it. The delete confirmation is the longest one there is,
    // and it showed as a strip with "Keep it" and "Delete the resources" half
    // cut off. The backdrop scrolls now, and the sheet is capped so it cannot
    // outgrow the window in the first place.
    <div onClick={onClose}
         className="fixed inset-0 z-[600] flex items-center justify-center
                    overscroll-contain bg-slate-950/35 p-4 backdrop-blur-md">
      <div onClick={e => e.stopPropagation()}
           className={cn('w-full max-w-[520px] max-h-[90vh] overflow-y-auto',
             'rounded-[24px] border border-white/60',
             'bg-panel/95 p-5 shadow-[0_28px_80px_rgba(15,23,42,.25)] backdrop-blur-2xl dark:border-white/10', className)}>
        {children}
      </div>
    </div>
  )
}

export function Dropdown({ open, onClose, children, className }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const away = (e) => {
      if (!ref.current?.parentElement?.contains(e.target)) onClose()
    }
    const key = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('click', away)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('click', away)
      document.removeEventListener('keydown', key)
    }
  }, [open, onClose])
  if (!open) return null
  return (
    <div ref={ref}
         className={cn('absolute z-[500] overflow-hidden rounded-[18px] border border-line',
           'bg-panel/95 shadow-[0_18px_45px_rgba(15,23,42,.18)] backdrop-blur-xl', className)}>
      {children}
    </div>
  )
}

/**
 * A label for something that has no room for one.
 *
 * It used to draw its own dark panel above the child. In a rail this narrow
 * that panel covered the controls underneath it — the thing being described
 * was hidden by its own description — and a hard black rectangle was the only
 * square-cornered, full-contrast surface in a light and rounded interface.
 *
 * The browser's own tooltip has neither problem: it opens beside the pointer
 * rather than over the layout, and it is the one piece of chrome a person
 * already recognises. `side` is kept because callers pass it and because
 * where a tooltip should sit is worth being able to say.
 */
export function Tip({ text, children, className, side = 'top' }) {
  return (
    <span className={cn('relative inline-flex', className)} title={text || undefined}>
      {children}
    </span>
  )
}

/**
 * What every editable field in the studio has to carry.
 *
 * Writing extensions rewrite a field before React hydrates it. Grammarly adds
 * `data-gramm`, QuillBot adds `data-qb-tmp-id` with a fresh random id, and
 * both add `spellcheck` — so the server's HTML and the client's DOM disagree
 * on attributes this app never set, and React reports a hydration mismatch on
 * a page that is perfectly correct. It cannot be fixed by matching them: the
 * QuillBot id is random per load, so there is nothing to match.
 *
 * `suppressHydrationWarning` is React's own answer for exactly this — it stops
 * the warning for this element's attributes and nothing else. The opt-out
 * attributes are set as well, so the extensions that honour them leave the
 * field alone rather than being merely tolerated: in a code editor a grammar
 * checker is not noise, it underlines source and offers to correct it.
 */
export const plainField = {
  suppressHydrationWarning: true,
  spellCheck: false,
  'data-gramm': 'false',
  'data-gramm_editor': 'false',
  'data-enable-grammarly': 'false',
}

export const Input = ({ className, ...rest }) => (
  <input className={cn('h-9 w-full rounded-xl border border-line bg-white/65 px-3 shadow-sm dark:bg-white/5',
    'text-[12.5px] text-ink caret-accent transition-colors',
    'placeholder:text-muted2 focus:border-accent focus-visible:outline-none',
    'disabled:opacity-45', className)}
    {...plainField} {...rest} />
)

/** A textarea with the same protection. Everything else passes through. */
export const TextArea = forwardRef(function TextArea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={className} {...plainField} {...rest} />
})

export const Table = ({ className, children }) => (
  <div className="w-full overflow-x-auto">
    <table className={cn('w-full border-collapse text-[12px]', className)}>
      {children}
    </table>
  </div>
)
export const TR = ({ className, children, ...rest }) => (
  <tr className={cn('border-b border-line last:border-0', className)} {...rest}>
    {children}
  </tr>
)
/* The column head is the one rule in a table that is drawn strong — it is
   what separates the header band from the rows under it. */
export const TH = ({ className, children }) => (
  <th className={cn('border-b-2 border-line2 px-2.5 py-2 text-left',
    'text-[10px] font-semibold uppercase tracking-[.08em] text-label',
    className)}>{children}</th>
)
export const TD = ({ className, children }) => (
  <td className={cn('px-2.5 py-2 align-top', className)}>{children}</td>
)
