/**
 * Screenshot of the current page, taken by the page itself.
 *
 * No screen-share prompt and no dependency: the browser's own renderer draws a
 * copy of the DOM. The page is cloned with every element's *computed* style
 * written inline (an SVG image cannot load the page's stylesheets), wrapped in
 * an SVG `<foreignObject>`, loaded as an image and painted onto a canvas.
 * Inline `<svg>` charts are part of that clone, so they render as they are on
 * screen.
 *
 * What it deliberately does not do, and what that costs:
 * - Web fonts (`next/font`) are not embedded, so text may fall back to a system
 *   face. The layout is the same; the letterforms can differ.
 * - CSS `background-image: url(...)` and `::before/::after` content are not
 *   reproduced. Icons here are inline SVG, so the app is not affected.
 * - Cross-origin images are replaced with a blank box of the same size.
 * - Values typed into password fields are never copied (written as bullets).
 *
 * Any failure rejects; the caller falls back to sending without a screenshot
 * and says so. Nothing here uploads anything.
 */

const XHTML = 'http://www.w3.org/1999/xhtml'
const MAX_NODES = 15000
const MAX_WIDTH_PX = 1600
const RASTER_TIMEOUT_MS = 12000
const BLANK_GIF = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'

/** Elements that never belong in a picture of the page. */
const SKIP = new Set(['SCRIPT', 'STYLE', 'LINK', 'NOSCRIPT', 'TEMPLATE', 'META', 'TITLE'])
/** Replaced by a blank box of the same size (cannot be cloned into an image). */
const BLANK = new Set(['IFRAME', 'VIDEO', 'OBJECT', 'EMBED', 'AUDIO'])

export interface CapturedScreen {
  /** PNG data URL, at most `MAX_WIDTH_PX` wide. */
  dataUrl: string
  width: number
  height: number
}

interface Walk {
  nodes: number
  images: Array<{ clone: HTMLImageElement; src: string }>
}

function inlineStyle(src: Element, clone: Element) {
  const cs = window.getComputedStyle(src)
  let css = ''
  for (let i = 0; i < cs.length; i++) {
    const prop = cs[i]
    css += `${prop}:${cs.getPropertyValue(prop)};`
  }
  clone.setAttribute('style', css)
}

function blankBox(src: Element): HTMLElement {
  const box = document.createElementNS(XHTML, 'div') as HTMLElement
  const r = src.getBoundingClientRect()
  box.setAttribute('style', `display:inline-block;width:${r.width}px;height:${r.height}px;background:rgba(127,127,127,0.12);`)
  return box
}

function cloneTree(src: Element, walk: Walk): Element | null {
  if (SKIP.has(src.tagName.toUpperCase())) return null
  if (++walk.nodes > MAX_NODES) throw new Error('page_too_large')
  const tag = src.tagName.toUpperCase()
  if (BLANK.has(tag)) return blankBox(src)

  if (tag === 'CANVAS') {
    const img = document.createElementNS(XHTML, 'img') as HTMLImageElement
    try {
      img.setAttribute('src', (src as HTMLCanvasElement).toDataURL('image/png'))
    } catch {
      return blankBox(src)
    }
    inlineStyle(src, img)
    return img
  }

  const clone = src.cloneNode(false) as Element
  inlineStyle(src, clone)

  if (tag === 'IMG') {
    const img = clone as HTMLImageElement
    img.removeAttribute('srcset')
    img.removeAttribute('loading')
    const url = (src as HTMLImageElement).currentSrc || (src as HTMLImageElement).src
    if (url && !url.startsWith('data:')) walk.images.push({ clone: img, src: url })
  } else if (tag === 'INPUT') {
    const input = src as HTMLInputElement
    if (input.type === 'password') {
      // A password is never copied, not even as its length's worth of dots
      // read from the real value.
      clone.setAttribute('value', '')
    } else if (input.type === 'checkbox' || input.type === 'radio') {
      if (input.checked) clone.setAttribute('checked', 'checked'); else clone.removeAttribute('checked')
    } else {
      clone.setAttribute('value', input.value)
    }
  } else if (tag === 'TEXTAREA') {
    clone.textContent = (src as HTMLTextAreaElement).value
  } else if (tag === 'SELECT') {
    const sel = src as HTMLSelectElement
    Array.from(clone.querySelectorAll('option')).forEach((o, i) => {
      if (i === sel.selectedIndex) o.setAttribute('selected', 'selected'); else o.removeAttribute('selected')
    })
  }

  // A scrolled container shows a window onto its content; the clone has no
  // scroll position, so the window is reproduced by shifting the children.
  const sl = (src as HTMLElement).scrollLeft || 0
  const st = (src as HTMLElement).scrollTop || 0

  src.childNodes.forEach(child => {
    if (child.nodeType === Node.TEXT_NODE) {
      clone.appendChild(child.cloneNode(false))
    } else if (child.nodeType === Node.ELEMENT_NODE) {
      if (tag === 'SELECT' && (child as Element).tagName === 'OPTION') {
        clone.appendChild(child.cloneNode(true))
        return
      }
      const c = cloneTree(child as Element, walk)
      if (!c) return
      if (sl || st) {
        const el = c as HTMLElement
        const prev = el.style.transform
        el.style.transform = `translate(${-sl}px, ${-st}px) ${prev && prev !== 'none' ? prev : ''}`
      }
      clone.appendChild(c)
    }
  })
  return clone
}

async function toDataUrl(url: string): Promise<string> {
  const res = await fetch(url, { credentials: 'same-origin' })
  if (!res.ok) throw new Error('image_fetch')
  const blob = await res.blob()
  return await new Promise<string>((resolve, reject) => {
    const fr = new FileReader()
    fr.onload = () => resolve(String(fr.result))
    fr.onerror = () => reject(new Error('image_read'))
    fr.readAsDataURL(blob)
  })
}

function pageBackground(): string {
  for (const el of [document.body, document.documentElement]) {
    const bg = window.getComputedStyle(el).backgroundColor
    if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') return bg
  }
  return '#ffffff'
}

/**
 * Takes the picture. The DOM is copied synchronously before this returns, so
 * anything mounted afterwards (the feedback dialog itself) is not in it; only
 * the rasterising is asynchronous.
 */
export function startCapture(): Promise<CapturedScreen> {
  let prepared: { svg: string; width: number; height: number; images: Walk['images'] }
  try {
    const width = Math.max(1, Math.round(window.innerWidth))
    const height = Math.max(1, Math.round(window.innerHeight))
    const walk: Walk = { nodes: 0, images: [] }
    const body = cloneTree(document.body, walk) as HTMLElement
    if (!body) throw new Error('empty_page')

    const bodyRect = document.body.getBoundingClientRect()
    const prev = body.getAttribute('style') || ''
    // The viewport is the picture: shift the whole body by the page scroll.
    // Fixed-position descendants resolve against the foreignObject, which is
    // exactly the viewport, so they land where the user saw them.
    body.setAttribute('style',
      `${prev}position:absolute;left:${-window.scrollX}px;top:${-window.scrollY}px;margin:0;` +
      `width:${bodyRect.width}px;height:${bodyRect.height}px;`)

    const wrap = document.createElementNS(XHTML, 'div') as HTMLElement
    wrap.setAttribute('xmlns', XHTML)
    wrap.setAttribute('style',
      `position:relative;width:${width}px;height:${height}px;overflow:hidden;background:${pageBackground()};`)
    wrap.appendChild(body)

    const xhtml = new XMLSerializer().serializeToString(wrap)
      // Control characters are legal in HTML text and fatal in XML.
      // eslint-disable-next-line no-control-regex
      .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '')
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">` +
      `<foreignObject x="0" y="0" width="${width}" height="${height}">${xhtml}</foreignObject></svg>`
    prepared = { svg, width, height, images: walk.images }
  } catch (e) {
    return Promise.reject(e)
  }

  return (async () => {
    // Same-origin images become data URLs (an SVG image cannot fetch anything);
    // one that cannot be read becomes a transparent pixel, never a failure.
    const byUrl = new Map<string, Promise<string>>()
    const resolved = await Promise.all(prepared.images.map(async ({ clone, src }) => {
      let p = byUrl.get(src)
      if (!p) { p = toDataUrl(src).catch(() => BLANK_GIF); byUrl.set(src, p) }
      return { clone, data: await p }
    }))
    let svg = prepared.svg
    if (resolved.length) {
      // The clones were serialised before their sources resolved; re-serialise
      // is cheaper than serialising twice, so substitute in the string.
      for (const { clone, data } of resolved) {
        const original = clone.getAttribute('src') || ''
        if (original) svg = svg.split(`src="${original.replace(/&/g, '&amp;')}"`).join(`src="${data}"`)
      }
    }

    const img = new Image()
    const loaded = new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('raster_failed'))
    })
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
    await Promise.race([
      loaded,
      new Promise<void>((_, reject) => setTimeout(() => reject(new Error('raster_timeout')), RASTER_TIMEOUT_MS)),
    ])

    const scale = Math.max(0.5, Math.min(window.devicePixelRatio || 1, MAX_WIDTH_PX / prepared.width))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(prepared.width * scale)
    canvas.height = Math.round(prepared.height * scale)
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('no_canvas')
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    // A tainted canvas throws here: the caller sends without a screenshot.
    const dataUrl = canvas.toDataURL('image/png')
    return { dataUrl, width: canvas.width, height: canvas.height }
  })()
}

/** The most the server accepts (backend/feedback/validation.py), in bytes. */
export const MAX_SCREENSHOT_BYTES = Math.floor(2.5 * 1024 * 1024)

/** Approximate decoded size of a base64 data URL. */
export function dataUrlBytes(dataUrl: string): number {
  const i = dataUrl.indexOf(',')
  const b64 = i >= 0 ? dataUrl.slice(i + 1) : dataUrl
  const pad = b64.endsWith('==') ? 2 : b64.endsWith('=') ? 1 : 0
  return Math.floor((b64.length * 3) / 4) - pad
}

/**
 * Encodes a canvas so it fits the server's cap: PNG first, then JPEG at falling
 * quality, then a smaller canvas. Returns null when nothing fits.
 */
export function encodeUnderLimit(canvas: HTMLCanvasElement, maxBytes = MAX_SCREENSHOT_BYTES - 4096): string | null {
  const png = canvas.toDataURL('image/png')
  if (dataUrlBytes(png) <= maxBytes) return png
  let source = canvas
  for (let shrink = 0; shrink < 3; shrink++) {
    for (const q of [0.88, 0.75, 0.6]) {
      const jpg = source.toDataURL('image/jpeg', q)
      if (dataUrlBytes(jpg) <= maxBytes) return jpg
    }
    const smaller = document.createElement('canvas')
    smaller.width = Math.round(source.width * 0.7)
    smaller.height = Math.round(source.height * 0.7)
    const ctx = smaller.getContext('2d')
    if (!ctx) return null
    ctx.drawImage(source, 0, 0, smaller.width, smaller.height)
    source = smaller
  }
  return null
}
