import { useEffect, useMemo, useRef, useState } from 'react'
import { Image, Layout, Search, Type } from 'lucide-react'

export interface FloatingLabelBox {
  /** normalized 0..1 image-space corners [x0,y0,x1,y1] */
  bbox: [number, number, number, number]
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

// hotkey -> label name, for the parent's keydown routing
export const LABEL_HOTKEYS: Record<string, string> = Object.fromEntries(
  LABEL_GROUPS.flatMap((group) => group.labels)
    .filter((label) => label.hotkey)
    .map((label) => [label.hotkey!, label.name]),
)

export function FloatingLabelMenu({
  activeBox,
  onSelectLabel,
  onClose,
}: {
  activeBox: FloatingLabelBox | null
  onSelectLabel: (label: string) => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setQuery('')
    inputRef.current?.focus()
  }, [activeBox])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return LABEL_GROUPS.map((group) => ({
      ...group,
      labels: needle ? group.labels.filter((label) => label.name.toLowerCase().includes(needle)) : group.labels,
    })).filter((group) => group.labels.length > 0)
  }, [query])

  if (!activeBox) return null

  const flatFirst = filtered[0]?.labels[0]

  // anchor above the box's top-left corner, in image-percent space
  const [x0, y0] = activeBox.bbox
  const style: React.CSSProperties = {
    left: `${x0 * 100}%`,
    top: `calc(${y0 * 100}% - 10px)`,
    transform: 'translateY(-100%)',
  }

  return (
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
            if (event.key === 'Enter' && flatFirst) {
              onSelectLabel(flatFirst.name)
            } else if (event.key === 'Escape') {
              onClose()
            }
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
    </div>
  )
}
