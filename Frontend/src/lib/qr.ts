/**
 * A small QR Code encoder (ISO/IEC 18004), byte mode, error-correction level M,
 * versions 1 to 10. Dependency-free on purpose.
 *
 * Why it exists: the two-step sign-in enrollment shows the authenticator
 * secret as a QR code, and the secret must never leave the browser: no
 * third-party image service, and no package added for 200 lines of arithmetic.
 * `otpauth://` URIs are 100 to 200 bytes, which fits in versions up to 10.
 *
 * Pure: `qrMatrix(text)` returns the modules, `qrPath(matrix)` an SVG path.
 * Correctness is checked by decoding its output with an independent reader
 * (see `scripts/` notes in docs/rust-migration.md, "MFA") and by the fixed
 * property checks in the test script, not by eye.
 */

const MAX_VERSION = 10

// Level M, versions 1..10 (index = version).
const ECC_CODEWORDS_PER_BLOCK = [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26]
const NUM_ERROR_CORRECTION_BLOCKS = [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5]
const ECC_FORMAT_BITS_M = 0

function getBit(x: number, i: number): boolean {
  return ((x >>> i) & 1) !== 0
}

function numRawDataModules(ver: number): number {
  let result = (16 * ver + 128) * ver + 64
  if (ver >= 2) {
    const numAlign = Math.floor(ver / 7) + 2
    result -= (25 * numAlign - 10) * numAlign - 55
    if (ver >= 7) result -= 36
  }
  return result
}

function numDataCodewords(ver: number): number {
  return (
    Math.floor(numRawDataModules(ver) / 8) -
    ECC_CODEWORDS_PER_BLOCK[ver] * NUM_ERROR_CORRECTION_BLOCKS[ver]
  )
}

// ── Reed-Solomon over GF(2^8) / 0x11D ────────────────────────────────────────

function gfMultiply(x: number, y: number): number {
  let z = 0
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d)
    z ^= ((y >>> i) & 1) * x
  }
  return z
}

function rsDivisor(degree: number): number[] {
  const result: number[] = new Array(degree).fill(0)
  result[degree - 1] = 1
  let root = 1
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      result[j] = gfMultiply(result[j], root)
      if (j + 1 < degree) result[j] ^= result[j + 1]
    }
    root = gfMultiply(root, 0x02)
  }
  return result
}

function rsRemainder(data: number[], divisor: number[]): number[] {
  const result: number[] = divisor.map(() => 0)
  for (const b of data) {
    const factor = b ^ (result.shift() as number)
    result.push(0)
    divisor.forEach((coef, i) => { result[i] ^= gfMultiply(coef, factor) })
  }
  return result
}

// ── Data ─────────────────────────────────────────────────────────────────────

function encodeData(bytes: number[], ver: number): number[] {
  const bits: number[] = []
  const push = (val: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1)
  }
  push(0x4, 4) // byte mode
  push(bytes.length, ver <= 9 ? 8 : 16)
  for (const b of bytes) push(b, 8)
  const capacityBits = numDataCodewords(ver) * 8
  push(0, Math.min(4, capacityBits - bits.length))
  push(0, (8 - (bits.length % 8)) % 8)
  for (let pad = 0xec; bits.length < capacityBits; pad ^= 0xec ^ 0x11) push(pad, 8)
  const out: number[] = new Array(bits.length / 8).fill(0)
  bits.forEach((b, i) => { out[i >>> 3] |= b << (7 - (i & 7)) })
  return out
}

function addEccAndInterleave(data: number[], ver: number): number[] {
  const numBlocks = NUM_ERROR_CORRECTION_BLOCKS[ver]
  const blockEccLen = ECC_CODEWORDS_PER_BLOCK[ver]
  const rawCodewords = Math.floor(numRawDataModules(ver) / 8)
  const numShortBlocks = numBlocks - (rawCodewords % numBlocks)
  const shortBlockLen = Math.floor(rawCodewords / numBlocks)
  const blocks: number[][] = []
  const divisor = rsDivisor(blockEccLen)
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const dat = data.slice(k, k + shortBlockLen - blockEccLen + (i < numShortBlocks ? 0 : 1))
    k += dat.length
    const ecc = rsRemainder(dat, divisor)
    if (i < numShortBlocks) dat.push(0)
    blocks.push(dat.concat(ecc))
  }
  const result: number[] = []
  for (let i = 0; i < blocks[0].length; i++) {
    blocks.forEach((block, j) => {
      if (i !== shortBlockLen - blockEccLen || j >= numShortBlocks) result.push(block[i])
    })
  }
  return result
}

// ── Matrix ───────────────────────────────────────────────────────────────────

class Grid {
  readonly version: number
  readonly size: number
  readonly modules: boolean[][]
  readonly isFunction: boolean[][]

  constructor(version: number) {
    this.version = version
    this.size = version * 4 + 17
    this.modules = Array.from({ length: this.size }, () => new Array(this.size).fill(false))
    this.isFunction = Array.from({ length: this.size }, () => new Array(this.size).fill(false))
  }

  setFunction(x: number, y: number, dark: boolean): void {
    this.modules[y][x] = dark
    this.isFunction[y][x] = true
  }

  drawFunctionPatterns(): void {
    const { size, version } = this
    for (let i = 0; i < size; i++) {
      this.setFunction(6, i, i % 2 === 0)
      this.setFunction(i, 6, i % 2 === 0)
    }
    this.drawFinder(3, 3)
    this.drawFinder(size - 4, 3)
    this.drawFinder(3, size - 4)
    const pos = alignmentPositions(version)
    const n = pos.length
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if ((i === 0 && j === 0) || (i === 0 && j === n - 1) || (i === n - 1 && j === 0)) continue
        this.drawAlignment(pos[i], pos[j])
      }
    }
    this.drawFormatBits(0)
    this.drawVersion()
  }

  private drawFinder(x: number, y: number): void {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const dist = Math.max(Math.abs(dx), Math.abs(dy))
        const xx = x + dx
        const yy = y + dy
        if (xx >= 0 && xx < this.size && yy >= 0 && yy < this.size) {
          this.setFunction(xx, yy, dist !== 2 && dist !== 4)
        }
      }
    }
  }

  private drawAlignment(x: number, y: number): void {
    for (let dy = -2; dy <= 2; dy++) {
      for (let dx = -2; dx <= 2; dx++) {
        this.setFunction(x + dx, y + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1)
      }
    }
  }

  drawFormatBits(mask: number): void {
    const { size } = this
    const data = (ECC_FORMAT_BITS_M << 3) | mask
    let rem = data
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537)
    const bits = ((data << 10) | rem) ^ 0x5412
    for (let i = 0; i <= 5; i++) this.setFunction(8, i, getBit(bits, i))
    this.setFunction(8, 7, getBit(bits, 6))
    this.setFunction(8, 8, getBit(bits, 7))
    this.setFunction(7, 8, getBit(bits, 8))
    for (let i = 9; i < 15; i++) this.setFunction(14 - i, 8, getBit(bits, i))
    for (let i = 0; i < 8; i++) this.setFunction(size - 1 - i, 8, getBit(bits, i))
    for (let i = 8; i < 15; i++) this.setFunction(8, size - 15 + i, getBit(bits, i))
    this.setFunction(8, size - 8, true) // the always-dark module
  }

  private drawVersion(): void {
    if (this.version < 7) return
    let rem = this.version
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25)
    const bits = (this.version << 12) | rem
    for (let i = 0; i < 18; i++) {
      const a = this.size - 11 + (i % 3)
      const b = Math.floor(i / 3)
      this.setFunction(a, b, getBit(bits, i))
      this.setFunction(b, a, getBit(bits, i))
    }
  }

  drawCodewords(data: number[]): void {
    const { size } = this
    let i = 0
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5
      for (let vert = 0; vert < size; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j
          const upward = ((right + 1) & 2) === 0
          const y = upward ? size - 1 - vert : vert
          if (!this.isFunction[y][x] && i < data.length * 8) {
            this.modules[y][x] = getBit(data[i >>> 3], 7 - (i & 7))
            i++
          }
        }
      }
    }
  }

  applyMask(mask: number): void {
    for (let y = 0; y < this.size; y++) {
      for (let x = 0; x < this.size; x++) {
        let invert: boolean
        switch (mask) {
          case 0: invert = (x + y) % 2 === 0; break
          case 1: invert = y % 2 === 0; break
          case 2: invert = x % 3 === 0; break
          case 3: invert = (x + y) % 3 === 0; break
          case 4: invert = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; break
          case 5: invert = ((x * y) % 2) + ((x * y) % 3) === 0; break
          case 6: invert = (((x * y) % 2) + ((x * y) % 3)) % 2 === 0; break
          default: invert = (((x + y) % 2) + ((x * y) % 3)) % 2 === 0; break
        }
        if (!this.isFunction[y][x] && invert) this.modules[y][x] = !this.modules[y][x]
      }
    }
  }

  /** The four penalty rules of the spec (a readability score, never a validity one). */
  penalty(): number {
    const { size, modules } = this
    let result = 0
    const lines: boolean[][] = []
    for (let y = 0; y < size; y++) lines.push(modules[y])
    for (let x = 0; x < size; x++) lines.push(modules.map(row => row[x]))
    for (const line of lines) {
      let run = 1
      for (let i = 1; i < size; i++) {
        if (line[i] === line[i - 1]) {
          run++
          if (run === 5) result += 3
          else if (run > 5) result += 1
        } else {
          run = 1
        }
      }
      // finder-like 1:1:3:1:1 pattern with a light margin
      for (let i = 0; i + 6 < size; i++) {
        const core = line[i] && !line[i + 1] && line[i + 2] && line[i + 3] && line[i + 4] && !line[i + 5] && line[i + 6]
        if (!core) continue
        const before = i >= 4 && !line[i - 1] && !line[i - 2] && !line[i - 3] && !line[i - 4]
        const after = i + 10 < size && !line[i + 7] && !line[i + 8] && !line[i + 9] && !line[i + 10]
        if (before || after) result += 40
      }
    }
    for (let y = 0; y < size - 1; y++) {
      for (let x = 0; x < size - 1; x++) {
        const c = modules[y][x]
        if (c === modules[y][x + 1] && c === modules[y + 1][x] && c === modules[y + 1][x + 1]) result += 3
      }
    }
    let dark = 0
    for (const row of modules) for (const m of row) if (m) dark++
    const total = size * size
    result += (Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1) * 10
    return result
  }
}

function alignmentPositions(ver: number): number[] {
  if (ver === 1) return []
  const numAlign = Math.floor(ver / 7) + 2
  const step = Math.ceil((ver * 4 + 4) / (numAlign * 2 - 2)) * 2
  const result = [6]
  for (let pos = ver * 4 + 10; result.length < numAlign; pos -= step) result.splice(1, 0, pos)
  return result
}

export class QrTooLong extends Error {
  constructor() {
    super('text does not fit in a version 10 QR code')
    this.name = 'QrTooLong'
  }
}

/** The modules of a QR code (true = dark) for `text`, UTF-8, level M. */
export function qrMatrix(text: string): boolean[][] {
  const bytes = Array.from(new TextEncoder().encode(text))
  let version = 0
  for (let ver = 1; ver <= MAX_VERSION; ver++) {
    const used = 4 + (ver <= 9 ? 8 : 16) + 8 * bytes.length
    if (used <= numDataCodewords(ver) * 8) { version = ver; break }
  }
  if (!version) throw new QrTooLong()

  const codewords = addEccAndInterleave(encodeData(bytes, version), version)
  const grid = new Grid(version)
  grid.drawFunctionPatterns()
  grid.drawCodewords(codewords)

  let best = 0
  let bestPenalty = Infinity
  for (let mask = 0; mask < 8; mask++) {
    grid.applyMask(mask)
    grid.drawFormatBits(mask)
    const p = grid.penalty()
    if (p < bestPenalty) { best = mask; bestPenalty = p }
    grid.applyMask(mask) // undo (XOR)
  }
  grid.applyMask(best)
  grid.drawFormatBits(best)
  return grid.modules
}

/** One SVG path (`M x y h1 v1 h-1 z` per dark module) with a quiet zone. */
export function qrPath(matrix: boolean[][], border = 4): { d: string; size: number } {
  const parts: string[] = []
  matrix.forEach((row, y) => {
    row.forEach((dark, x) => {
      if (dark) parts.push(`M${x + border} ${y + border}h1v1h-1z`)
    })
  })
  return { d: parts.join(''), size: matrix.length + border * 2 }
}
