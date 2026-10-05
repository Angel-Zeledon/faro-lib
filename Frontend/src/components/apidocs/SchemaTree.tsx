'use client'
// An expandable schema tree with type chips: the one renderer behind every
// request body, parameter list and response in the API reference (both
// /desarrolladores and the in-app /api screen).
//
// It draws what the data says and nothing else. A list is a row typed
// `array of object` whose children are the fields of ONE element, under a rail
// that is labelled as the element; an object's fields sit under their parent
// without that label. Native <details> does the folding, so every row is in the
// HTML for crawlers and find-in-page, keyboard and screen readers work without
// code, and "expand all" is a loop over the open attribute.
//
// Colours come from --st-* custom properties the host page sets (see
// SCHEMA_TREE_CSS), so the same markup follows the landing's tokens on one
// screen and the app's on the other.
import { useRef } from 'react'
import { childrenOf, constraintsOf, typeChip, type SchemaNode } from '@/lib/apiSchema'

export interface SchemaLabels {
  required: string
  optional: string
  nullable: string
  expandAll: string
  collapseAll: string
  /** "array item": the label of the rail under an array row. */
  item: string
  /** The label of the rail under a map row: "each value". */
  eachValue: string
  freeForm: string
  oneOf: string
  recursive: string
  constraint: { min: string; max: string; default: string; minLength: string; maxLength: string }
  more: (n: number) => string
  /** The sentence above a root list: "The body is an array of objects". */
  rootArray: string
  rootObject: string
  rootMap: string
  empty: string
}

const MAX_ENUM_SHOWN = 8

function Enum({ values, L }: { values: unknown[]; L: SchemaLabels }) {
  const shown = values.slice(0, MAX_ENUM_SHOWN)
  return (
    <div className="st-enum">
      {shown.map(v => <code key={String(v)}>{typeof v === 'string' ? v : JSON.stringify(v)}</code>)}
      {values.length > shown.length && <span>{L.more(values.length - shown.length)}</span>}
    </div>
  )
}

function Head({ name, node, required, showRequired, L }: {
  name?: string; node: SchemaNode; required?: boolean; showRequired: boolean; L: SchemaLabels
}) {
  const cons = constraintsOf(node)
  return (
    <>
      {name !== undefined && <code className="st-name">{name}</code>}
      <span className="st-chip">{typeChip(node)}</span>
      {showRequired && (required
        ? <span className="st-req">{L.required}</span>
        : <span className="st-opt">{L.optional}</span>)}
      {node.nullable && <span className="st-null">{L.nullable}</span>}
      {cons.map(c => (
        <span key={c.key} className="st-cons">{L.constraint[c.key]} <code>{c.value}</code></span>
      ))}
    </>
  )
}

function Body({ node, L }: { node: SchemaNode; L: SchemaLabels }) {
  return (
    <>
      {node.description && <p className="st-desc">{node.description}</p>}
      {node.enum && node.enum.length > 0 && <Enum values={node.enum} L={L} />}
      {node.free_form && !node.fields && <p className="st-note">{L.freeForm}</p>}
      {node.ref && <p className="st-note">{L.recursive}</p>}
    </>
  )
}

/** The children block of a node, with the rail label that says what the
 *  children ARE: the fields of an object, the fields of each array item, the
 *  value shape of a map, or the alternatives of a union. */
function Children({ node, L, depth, defaultOpen, showRequired }: {
  node: SchemaNode; L: SchemaLabels; depth: number; defaultOpen: number; showRequired: boolean
}) {
  const kids = childrenOf(node)
  if (kids.length === 0) return null
  let rail: string | null = null
  if (node.type === 'array' && node.items) rail = L.item
  else if (node.values && !node.fields) rail = L.eachValue
  else if (node.any_of) rail = L.oneOf
  // An array of arrays/maps nests its rail label; keep the innermost honest.
  return (
    <div className="st-rail">
      {rail && <div className="st-rail-label">{rail}</div>}
      <ul className="st-list">
        {kids.map((k, i) => (
          <Row
            key={k.field?.name ?? `${k.label}-${i}`}
            name={k.field?.name ?? k.label}
            node={k.node}
            required={k.field?.required}
            showRequired={showRequired && !!k.field}
            L={L}
            depth={depth + 1}
            defaultOpen={defaultOpen}
          />
        ))}
      </ul>
    </div>
  )
}

function Row({ name, node, required, showRequired, L, depth, defaultOpen }: {
  name?: string; node: SchemaNode; required?: boolean; showRequired: boolean; L: SchemaLabels; depth: number; defaultOpen: number
}) {
  const expandable = childrenOf(node).length > 0
  if (!expandable) {
    return (
      <li className="st-row">
        <div className="st-head"><Head name={name} node={node} required={required} showRequired={showRequired} L={L} /></div>
        <Body node={node} L={L} />
      </li>
    )
  }
  return (
    <li className="st-row">
      <details open={depth < defaultOpen}>
        <summary className="st-head is-fold">
          <span className="st-chev" aria-hidden />
          <Head name={name} node={node} required={required} showRequired={showRequired} L={L} />
        </summary>
        <Body node={node} L={L} />
        <Children node={node} L={L} depth={depth} defaultOpen={defaultOpen} showRequired={showRequired} />
      </details>
    </li>
  )
}

/** The whole tree for a body or response. `showRequired` is off for responses
 *  (a response's keys are not "required" by the caller). */
export function SchemaTree({ node, L, defaultOpen = 2, showRequired = true, toolbar = true, bare = false }: {
  node: SchemaNode; L: SchemaLabels; defaultOpen?: number; showRequired?: boolean; toolbar?: boolean
  /** No header line ("An object."): for a plain list of named fields such as parameters. */
  bare?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const setAll = (open: boolean) => {
    ref.current?.querySelectorAll('details').forEach(d => { d.open = open })
  }
  const kids = childrenOf(node)
  const isRootList = node.type === 'array'
  const rootText = isRootList ? L.rootArray : node.values && !node.fields ? L.rootMap : L.rootObject
  const hasFold = kids.some(k => childrenOf(k.node).length > 0) || isRootList
  return (
    <div className={bare ? 'st is-bare' : 'st'} ref={ref}>
      {!bare && <div className="st-bar">
        <span className="st-root"><span className="st-chip">{typeChip(node)}</span> <span>{rootText}</span></span>
        {toolbar && hasFold && (
          <span className="st-tools">
            <button type="button" onClick={() => setAll(true)}>{L.expandAll}</button>
            <button type="button" onClick={() => setAll(false)}>{L.collapseAll}</button>
          </span>
        )}
      </div>}
      {!bare && node.description && <p className="st-desc st-root-desc">{node.description}</p>}
      {kids.length === 0
        ? (isRootList ? null : <p className="st-note st-note-root">{node.free_form ? L.freeForm : L.empty}</p>)
        : <Children node={isRootList ? node : { ...node }} L={L} depth={0} defaultOpen={defaultOpen} showRequired={showRequired} />}
    </div>
  )
}

/** Tokens the host sets: --st-fg --st-muted --st-dim --st-border --st-surface
 *  --st-accent --st-warn. The rules below only read them. */
export const SCHEMA_TREE_CSS = `
.st { color: var(--st-fg); font-size: 13.5px; min-width: 0; }
.st-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; padding-bottom: 10px; border-bottom: 1px solid var(--st-border); }
.st-root { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 8px; color: var(--st-muted); font-size: 13px; min-width: 0; }
.st-tools { display: inline-flex; gap: 4px; }
.st-tools button { all: unset; cursor: pointer; font-size: 12px; font-weight: 600; color: var(--st-muted); padding: 5px 8px; border-radius: 6px; min-height: 18px; }
.st-tools button:hover { color: var(--st-fg); background: var(--st-surface); }
.st-tools button:focus-visible { outline: 2px solid var(--st-accent); outline-offset: 1px; }
.st-list { list-style: none; margin: 0; padding: 0; }
.st-row { padding: 10px 0; border-bottom: 1px solid var(--st-border); min-width: 0; }
.st-row:last-child { border-bottom: none; }
.st-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 5px 8px; min-width: 0; }
.st-head.is-fold { cursor: pointer; list-style: none; position: relative; padding-left: 18px; }
.st-head.is-fold::-webkit-details-marker { display: none; }
.st-head.is-fold:focus-visible { outline: 2px solid var(--st-accent); outline-offset: 2px; border-radius: 4px; }
.st-chev { position: absolute; left: 2px; top: 0.55em; width: 6px; height: 6px; border-right: 1.5px solid var(--st-dim); border-bottom: 1.5px solid var(--st-dim); transform: rotate(-45deg); transition: transform 140ms ease; }
details[open] > .st-head > .st-chev { transform: rotate(45deg); }
.st-name { font-family: var(--st-mono, ui-monospace, monospace); font-size: 13px; font-weight: 600; color: var(--st-fg); overflow-wrap: anywhere; }
.st-chip { font-family: var(--st-mono, ui-monospace, monospace); font-size: 11.5px; color: var(--st-muted); background: var(--st-surface); border: 1px solid var(--st-border); border-radius: 5px; padding: 1px 6px; overflow-wrap: anywhere; }
.st-req { font-size: 11px; font-weight: 700; color: var(--st-warn); }
.st-opt { font-size: 11px; color: var(--st-dim); }
.st-null { font-size: 11px; color: var(--st-dim); border: 1px dashed var(--st-border); border-radius: 5px; padding: 0 5px; }
.st-cons { font-size: 11.5px; color: var(--st-muted); }
.st-cons code { font-family: var(--st-mono, ui-monospace, monospace); color: var(--st-fg); }
.st-desc { margin: 5px 0 0 0; padding-left: 18px; font-size: 13px; line-height: 1.6; color: var(--st-muted); max-width: 72ch; }
.st-row > .st-desc, .st-row > .st-enum, .st-row > .st-note { padding-left: 0; }
.st-root-desc { padding-left: 0; margin-top: 10px; }
.st .st-note-root { padding-left: 0; }
.st-note { margin: 5px 0 0; font-size: 12.5px; color: var(--st-dim); padding-left: 18px; }
.st-enum { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0 0 18px; align-items: center; font-size: 12px; color: var(--st-dim); }
.st-enum code { font-family: var(--st-mono, ui-monospace, monospace); font-size: 11.5px; color: var(--st-fg); background: var(--st-surface); border: 1px solid var(--st-border); border-radius: 5px; padding: 0 6px; overflow-wrap: anywhere; }
.st-rail { margin: 8px 0 0 7px; padding-left: 14px; border-left: 1px solid var(--st-border); min-width: 0; }
.st-rail-label { font-size: 11px; font-weight: 600; letter-spacing: 0.02em; color: var(--st-dim); padding-top: 2px; }
.st-rail .st-row:first-child { padding-top: 6px; }
.st.is-bare > .st-rail { margin: 0; padding: 0; border-left: none; }
.st.is-bare > .st-rail > .st-list > .st-row:first-child { padding-top: 0; }
@media (prefers-reduced-motion: reduce) { .st-chev { transition: none; } }
@media (max-width: 760px) { .st-tools button { min-height: 32px; padding: 7px 10px; } }
`
