// Prints the QR matrix for argv[2] as JSON: used by scripts/check_qr.py.
import { qrMatrix } from '../src/lib/qr.ts'
const m = qrMatrix(process.argv[2])
console.log(JSON.stringify({ size: m.length, rows: m.map(r => r.map(b => (b ? 1 : 0)).join('')) }))
