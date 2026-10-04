// The schema tree the API reference renders.
//
// Produced by backend/scripts/export_public_api.py (request bodies, query/path
// parameters) and backend/scripts/capture_api_examples.py (what each endpoint
// really answers). One shape for both, so one renderer: `components/apidocs/
// SchemaTree.tsx`. Arrays, objects and maps are distinct node kinds all the way
// down — a list of objects is an `array` whose `items` is an `object`, never an
// object that happens to be drawn with a bullet.

export type SchemaNode = {
  /** JSON type: string | integer | number | boolean | object | array | file | null | any,
   *  or a union spelled "string | integer" (then `any_of` holds the branches). */
  type: string
  nullable?: boolean
  format?: string
  enum?: unknown[]
  default?: unknown
  minimum?: number
  maximum?: number
  exclusive_minimum?: number
  min_length?: number
  max_length?: number
  description?: string
  /** array: the shape of one element. */
  items?: SchemaNode
  /** object with declared properties, in declaration order. */
  fields?: SchemaField[]
  /** object used as a map: the shape of every value. */
  values?: SchemaNode
  /** object that declares no properties. */
  free_form?: boolean
  /** union of several non-null shapes. */
  any_of?: SchemaNode[]
  /** a recursive reference that was cut: the schema's name. */
  ref?: string
}

export type SchemaField = SchemaNode & { name: string; required: boolean }

/** The short label a type chip shows: `array of object`, `string (date)`, `enum`. */
export function typeChip(node: SchemaNode): string {
  if (node.enum && node.enum.length > 0 && node.type !== 'array') return node.type === 'string' ? 'enum' : `${node.type} enum`
  if (node.type === 'array') {
    const inner = node.items ? typeChip(node.items) : 'any'
    return `array of ${inner}`
  }
  if (node.type === 'object' && node.values && !node.fields) return `map of ${typeChip(node.values)}`
  if (node.format && node.type === 'string') return `string (${node.format})`
  return node.type
}

/** Whether a node has children worth expanding in a tree. */
export function childrenOf(node: SchemaNode): { label?: string; node: SchemaNode; field?: SchemaField }[] {
  if (node.any_of) return node.any_of.map((n, i) => ({ label: `#${i + 1}`, node: n }))
  if (node.type === 'array' && node.items) return childrenOf(node.items)
  if (node.fields && node.fields.length > 0) return node.fields.map(f => ({ node: f, field: f }))
  if (node.values) return childrenOf(node.values)
  return []
}

/** Flat dotted paths of a node, for tests and search ("items[].sku"). */
export function pathsOf(node: SchemaNode, prefix = ''): string[] {
  const out: string[] = []
  if (node.type === 'array' && node.items) return pathsOf(node.items, `${prefix}[]`)
  for (const f of node.fields ?? []) {
    const p = prefix ? `${prefix}.${f.name}` : f.name
    out.push(p, ...pathsOf(f, p))
  }
  return out
}

/** The constraints of a node as short strings: `min 1`, `max 365`, `default 50`. */
export function constraintsOf(node: SchemaNode): { key: 'min' | 'max' | 'default' | 'minLength' | 'maxLength'; value: string }[] {
  const out: { key: 'min' | 'max' | 'default' | 'minLength' | 'maxLength'; value: string }[] = []
  if (node.minimum !== undefined) out.push({ key: 'min', value: String(node.minimum) })
  if (node.exclusive_minimum !== undefined) out.push({ key: 'min', value: `> ${node.exclusive_minimum}` })
  if (node.maximum !== undefined) out.push({ key: 'max', value: String(node.maximum) })
  if (node.min_length !== undefined) out.push({ key: 'minLength', value: String(node.min_length) })
  if (node.max_length !== undefined) out.push({ key: 'maxLength', value: String(node.max_length) })
  if (node.default !== undefined && node.default !== null && typeof node.default !== 'object') out.push({ key: 'default', value: JSON.stringify(node.default) })
  return out
}
