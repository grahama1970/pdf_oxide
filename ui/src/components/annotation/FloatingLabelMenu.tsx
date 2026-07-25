import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Image, Layout, Search, Type } from 'lucide-react'

export interface FloatingLabelAnchor {
  /** viewport-space rect of the active region (getBoundingClientRect) */
  left: number
  top: number
  width: number
}

interface LabelDef {
  name: string
  hotkey?: string
}

interface LabelGroup {
  title: string
  icon: JSX.Element
  labels: LabelDef[]
}

// Categorized taxonomy over the full ELEMENT_TYPES vocabulary (operator spec).
const LABEL_GROUPS: LabelGroup[] = [
  {
    title: 'Typography',
    icon: <Type className="fl-icon fl-icon--type" aria-hidden="true" />,
    labels: [
      { name: 'Body', hotkey: 'b' },
      { name: 'Title', hotkey: 't' },
      { name: 'Header', hotkey: 'h' },
      { name: 'Subtitle' },
      { name: 'Caption', hotkey: 'c' },
      { name: 'Footnote' },
      { name: 'Reference' },
      { name: 'Section' },
    ],
  },
  {
    title: 'Rich Content',
    icon: <Image className="fl-icon fl-icon--rich" aria-hidden="true" />,
    labels: [
      { name: 'Table', hotkey: 'a' },
      { name: 'Figure', hotkey: 'f' },
      { name: 'Equation', hotkey: 'e' },
      { name: 'Code' },
      { name: 'List', hotkey: 'l' },
      { name: 'Form' },
    ],
  },
  {
    title: 'Structural',
    icon: <Layout className="fl-icon fl-icon--struct" aria-hidden="true" />,
    labels: [{ name: 'block' }, { name: 'region' }, { name: 'page' }],
  },
]

export const LABEL_HOTKEYS: Record<string, string> = Object.fromEntries(
  LABEL_GROUPS.flatMap((group) => group.labels)
    .filter((label) => label.hotkey)
    .map((label) => [label.hotkey!, label.name]),
)

export function FloatingLabelMenu({
  anchor,
  onSelectLabel,
  onClose,
}: {
  anchor: FloatingLabelAnchor | null
  onSelectLabel: (label: string) => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setQuery('')
    inputRef.current?.focus()
  }, [anchor])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return LABEL_GROUPS.map((group) => ({
      ...group,
      labels: needle ? group.labels.filter((label) => label.name.toLowerCase().includes(needle)) : group.labels,
    })).filter((group) => group.labels.length > 0)
  }, [query])

  if (!anchor || typeof document === 'undefined') return null

  const flatFirst = filtered[0]?.labels[0]
  const MENU_WIDTH = 256
  // clamp to viewport so it is never off-screen; render above the box, front-most via portal.
  const left = Math.max(8, Math.min(anchor.left, window.innerWidth - MENU_WIDTH - 8))
  const style: React.CSSProperties = {
    position: 'fixed',
    left,
    top: anchor.top - 10,
    transform: 'translateY(-100%)',
    width: MENU_WIDTH,
  }

  return createPortal(
    <div
      className="pdf-floating-label-menu"
      style={style}
      data-testid="floating-label-menu"
      role="dialog"
      aria-label="Label region"
      onMouseDown={(event) => event.stopPropagation()}
    >
      <div className="pdf-floating-label-menu__search">
        <Search aria-hidden="true" />
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            event.stopPropagation()
            if (event.key === 'Enter' && flatFirst) onSelectLabel(flatFirst.name)
            else if (event.key === 'Escape') onClose()
          }}
          placeholder="Filter labels…"
          aria-label="Filter labels"
          data-qid="annotation-queue:floating-label:filter"
          data-qs-action="ANNOTATION_QUEUE_FILTER_LABELS"
          title="Type to filter labels, Enter to apply the first match"
        />
      </div>
      <div className="pdf-floating-label-menu__groups">
        {filtered.map((group) => (
          <div key={group.title} className="pdf-floating-label-menu__group">
            <div className="pdf-floating-label-menu__group-head">
              {group.icon}
              <span>{group.title}</span>
            </div>
            <div className="pdf-floating-label-menu__grid">
              {group.labels.map((label) => (
                <button
                  key={label.name}
                  type="button"
                  onClick={() => onSelectLabel(label.name)}
                  data-qid={`annotation-queue:floating-label:${label.name.toLowerCase()}`}
                  data-qs-action="ANNOTATION_QUEUE_LABEL_REGION"
                  title={`Label region as ${label.name}${label.hotkey ? ` (${label.hotkey.toUpperCase()})` : ''}`}
                >
                  <span>{label.name}</span>
                  {label.hotkey && <kbd>{label.hotkey.toUpperCase()}</kbd>}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>,
    document.body,
  )
}
