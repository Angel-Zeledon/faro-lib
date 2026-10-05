// Phone numbers: country list, E.164 conversion and default-country inference.
//
// A small table instead of `libphonenumber`: the app only needs to (1) let a
// person pick a country, (2) store `+<dial><national digits>`, and (3) read a
// stored E.164 back into country + national number. That is a ~250-row table,
// not a 100 kB metadata blob. What this deliberately does NOT do is validate
// per-country number lengths or formats — the rule enforced everywhere
// (signup, API, WhatsApp link) is E.164: `+`, a non-zero digit, 8 to 15 digits
// in total. Country names come from the browser's own `Intl.DisplayNames`, so
// there is no per-language country-name catalogue to keep in step.

export interface Country {
  iso: string          // ISO 3166-1 alpha-2
  dial: string         // calling code, digits only
  /** NANP members share +1; the area code tells them apart when parsing. */
  areaCodes?: string[]
}

// iso:dial pairs. NANP countries (dial 1) carry area codes for the ones whose
// numbers a LatAm buyer is likely to type; the rest of +1 resolves to US.
const RAW = `
AF:93 AL:355 DZ:213 AS:1684 AD:376 AO:244 AI:1264 AG:1268 AR:54 AM:374 AW:297 AU:61 AT:43 AZ:994
BS:1242 BH:973 BD:880 BB:1246 BY:375 BE:32 BZ:501 BJ:229 BM:1441 BT:975 BO:591 BA:387 BW:267 BR:55
IO:246 VG:1284 BN:673 BG:359 BF:226 BI:257 KH:855 CM:237 CA:1 CV:238 KY:1345 CF:236 TD:235 CL:56
CN:86 CO:57 KM:269 CK:682 CR:506 HR:385 CU:53 CW:599 CY:357 CZ:420 CD:243 DK:45 DJ:253 DM:1767
DO:1 TL:670 EC:593 EG:20 SV:503 GQ:240 ER:291 EE:372 SZ:268 ET:251 FK:500 FO:298 FJ:679 FI:358
FR:33 GF:594 PF:689 GA:241 GM:220 GE:995 DE:49 GH:233 GI:350 GR:30 GL:299 GD:1473 GP:590 GU:1671
GT:502 GG:44 GN:224 GW:245 GY:592 HT:509 HN:504 HK:852 HU:36 IS:354 IN:91 ID:62 IR:98 IQ:964
IE:353 IM:44 IL:972 IT:39 CI:225 JM:1876 JP:81 JE:44 JO:962 KZ:7 KE:254 KI:686 XK:383 KW:965
KG:996 LA:856 LV:371 LB:961 LS:266 LR:231 LY:218 LI:423 LT:370 LU:352 MO:853 MG:261 MW:265 MY:60
MV:960 ML:223 MT:356 MH:692 MQ:596 MR:222 MU:230 YT:262 MX:52 FM:691 MD:373 MC:377 MN:976 ME:382
MS:1664 MA:212 MZ:258 MM:95 NA:264 NR:674 NP:977 NL:31 NC:687 NZ:64 NI:505 NE:227 NG:234 NU:683
KP:850 MK:389 MP:1670 NO:47 OM:968 PK:92 PW:680 PS:970 PA:507 PG:675 PY:595 PE:51 PH:63 PL:48
PT:351 PR:1 QA:974 CG:242 RE:262 RO:40 RU:7 RW:250 BL:590 SH:290 KN:1869 LC:1758 MF:590 PM:508
VC:1784 WS:685 SM:378 ST:239 SA:966 SN:221 RS:381 SC:248 SL:232 SG:65 SX:1721 SK:421 SI:386
SB:677 SO:252 ZA:27 KR:82 SS:211 ES:34 LK:94 SD:249 SR:597 SE:46 CH:41 SY:963 TW:886 TJ:992
TZ:255 TH:66 TG:228 TK:690 TO:676 TT:1868 TN:216 TR:90 TM:993 TC:1649 TV:688 VI:1340 UG:256
UA:380 AE:971 GB:44 US:1 UY:598 UZ:998 VU:678 VA:39 VE:58 VN:84 WF:681 YE:967 ZM:260 ZW:263
`

// Area codes that disambiguate the +1 members a LatAm buyer is likely to meet.
const NANP_AREA_CODES: Record<string, string[]> = {
  DO: ['809', '829', '849'],
  PR: ['787', '939'],
  JM: ['876', '658'],
  CA: ['204', '226', '236', '249', '250', '289', '306', '343', '365', '367', '403', '416', '418',
       '431', '437', '438', '450', '506', '514', '519', '548', '579', '581', '587', '604', '613',
       '639', '647', '672', '705', '709', '778', '780', '782', '807', '819', '825', '867', '873',
       '902', '905'],
}

export const COUNTRIES: Country[] = RAW.trim().split(/\s+/).map(pair => {
  const [iso, dial] = pair.split(':')
  const areaCodes = NANP_AREA_CODES[iso]
  return areaCodes ? { iso, dial, areaCodes } : { iso, dial }
})

const BY_ISO = new Map(COUNTRIES.map(c => [c.iso, c]))

export function countryByIso(iso: string): Country | undefined {
  return BY_ISO.get(iso)
}

// Shown first, in this order: the markets the product is sold in, then the
// neighbours its buyers trade with. Everything else follows alphabetically.
const PRIORITY = [
  'CR', 'MX', 'CO', 'PE', 'AR', 'CL', 'EC', 'GT', 'PA', 'DO', 'UY', 'PY', 'BO',
  'SV', 'HN', 'NI', 'VE', 'CU', 'PR', 'BR', 'ES', 'US',
]

/** Flag emoji from the ISO code (two regional-indicator symbols). */
export function flagOf(iso: string): string {
  return iso.toUpperCase().replace(/./g, ch => String.fromCodePoint(127397 + ch.charCodeAt(0)))
}

/** The country's name in the given language, via the browser's own data. */
export function countryName(iso: string, lang: string): string {
  try {
    const names = new Intl.DisplayNames([lang === 'en' ? 'en' : 'es'], { type: 'region' })
    return names.of(iso) ?? iso
  } catch {
    return iso
  }
}

export interface CountryOption extends Country { name: string }

/** All countries with localized names, LatAm-first, the rest alphabetical. */
export function countryOptions(lang: string): CountryOption[] {
  const collator = new Intl.Collator(lang === 'en' ? 'en' : 'es')
  const named = COUNTRIES.map(c => ({ ...c, name: countryName(c.iso, lang) }))
  const rank = (iso: string) => {
    const i = PRIORITY.indexOf(iso)
    return i === -1 ? PRIORITY.length : i
  }
  return named.sort((a, b) => rank(a.iso) - rank(b.iso) || collator.compare(a.name, b.name))
}

// ── E.164 ────────────────────────────────────────────────────────────────────

/** The rule the backend enforces: '+', a non-zero digit, 8 to 15 digits total. */
export const E164 = /^\+[1-9]\d{7,14}$/

export function isE164(value: string): boolean {
  return E164.test(value)
}

/** Digits only — what a person typed minus spaces, dashes, brackets, dots. */
export function digitsOf(text: string): string {
  return text.replace(/\D/g, '')
}

/**
 * Compose the stored number. Empty national part → '' (not a bare dial code),
 * so an untouched field is "no number", not an invalid one.
 */
export function toE164(iso: string, national: string): string {
  const country = BY_ISO.get(iso)
  const digits = digitsOf(national)
  if (!country || !digits) return ''
  return `+${country.dial}${digits}`
}

export interface PhoneParts {
  iso: string
  national: string
  /** False when `value` was not E.164 (an old free-text entry): `national` is
   *  then the raw text, shown as-is so nothing the user saved disappears. */
  parsed: boolean
}

/**
 * Read a stored value back into country + national number. Longest calling code
 * wins (+1684 American Samoa before +1). A bare +1 resolves to the NANP member
 * whose area code matches, else the US. Anything that is not E.164 is returned
 * unparsed with `fallbackIso`, so legacy values still display and stay editable.
 */
export function splitE164(value: string, fallbackIso: string): PhoneParts {
  const v = (value ?? '').trim()
  if (!isE164(v)) return { iso: fallbackIso, national: v, parsed: false }
  const digits = v.slice(1)
  let best: Country | null = null
  for (let len = 4; len >= 1; len--) {
    const prefix = digits.slice(0, len)
    const matches = COUNTRIES.filter(c => c.dial === prefix)
    if (!matches.length) continue
    if (prefix === '1') {
      const area = digits.slice(1, 4)
      best = matches.find(c => c.areaCodes?.includes(area)) ?? BY_ISO.get('US')!
    } else if (prefix === '7') {
      best = BY_ISO.get(digits.slice(1, 2) === '7' ? 'KZ' : 'RU')!   // 76xx/77xx are Kazakh
    } else if (prefix === '44') {
      best = BY_ISO.get('GB')!                                          // GG/IM/JE share +44
    } else if (['590', '262', '39'].includes(prefix)) {
      best = BY_ISO.get(prefix === '590' ? 'GP' : prefix === '262' ? 'RE' : 'IT')!
    } else {
      best = matches[0]
    }
    break
  }
  if (!best) return { iso: fallbackIso, national: v, parsed: false }
  return { iso: best.iso, national: digits.slice(best.dial.length), parsed: true }
}

// ── Default country ──────────────────────────────────────────────────────────

const TIMEZONE_COUNTRY: Record<string, string> = {
  'America/Costa_Rica': 'CR', 'America/Mexico_City': 'MX', 'America/Cancun': 'MX',
  'America/Monterrey': 'MX', 'America/Merida': 'MX', 'America/Tijuana': 'MX',
  'America/Chihuahua': 'MX', 'America/Mazatlan': 'MX', 'America/Hermosillo': 'MX',
  'America/Bogota': 'CO', 'America/Lima': 'PE', 'America/Argentina/Buenos_Aires': 'AR',
  'America/Buenos_Aires': 'AR', 'America/Argentina/Cordoba': 'AR', 'America/Santiago': 'CL',
  'America/Punta_Arenas': 'CL', 'America/Guayaquil': 'EC', 'America/Guatemala': 'GT',
  'America/Panama': 'PA', 'America/Santo_Domingo': 'DO', 'America/Montevideo': 'UY',
  'America/Asuncion': 'PY', 'America/La_Paz': 'BO', 'America/El_Salvador': 'SV',
  'America/Tegucigalpa': 'HN', 'America/Managua': 'NI', 'America/Caracas': 'VE',
  'America/Havana': 'CU', 'America/Puerto_Rico': 'PR', 'America/Sao_Paulo': 'BR',
  'America/Bahia': 'BR', 'America/Fortaleza': 'BR', 'America/Manaus': 'BR',
  'Europe/Madrid': 'ES', 'Atlantic/Canary': 'ES', 'America/New_York': 'US',
  'America/Chicago': 'US', 'America/Denver': 'US', 'America/Los_Angeles': 'US',
}

/**
 * Best guess for a person's country, silently — it only pre-selects the
 * dropdown, which they can change. Timezone first (it says where the person
 * IS, even on an English-language browser), then the region of the browser
 * language, then Spanish → Mexico (the largest LatAm market) and English → US.
 */
export function inferCountry(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    const fromTz = tz ? TIMEZONE_COUNTRY[tz] : undefined
    if (fromTz) return fromTz
  } catch { /* fall through */ }
  try {
    for (const tag of navigator.languages ?? [navigator.language]) {
      const region = tag.split('-')[1]?.toUpperCase()
      if (region && BY_ISO.has(region)) return region
    }
    if ((navigator.language ?? '').toLowerCase().startsWith('es')) return 'MX'
  } catch { /* fall through */ }
  return 'US'
}
