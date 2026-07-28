---
name: faro-i18n
description: Use when adding or changing any text a user reads in Faro — UI copy, error messages, email or WhatsApp bodies — when adding a backend error, when a raw key like "inventory.source_file" appears on screen, or before committing a change that touched translations.ts. Enforces the es/en catalogue rules and ships the two scripts that verify them.
---

# Copy in Faro

The rule (CLAUDE.md, "Language"): **all code is English; the only Spanish is
end-user copy, and it lives in one place.** Getting this wrong is not cosmetic —
an English-mode user has been shown Spanish errors, and `/hoy` once printed raw
i18n key names at buyers.

## Where copy is allowed to live

| Kind | Where |
|---|---|
| UI copy | the `es` / `en` values of `Frontend/src/i18n/translations.ts` — keys are English |
| Backend user-visible failure | `AppError(code, english_fallback, status_code=…, params=…)`. The frontend renders `errors.<code>`. **Never** Spanish prose in backend logic. |
| Email / WhatsApp / PDF (the frontend never sees these) | `backend/notifications/locale.py`, keyed by English identifiers |

Numbers go in `params`, never baked into a sentence — the sentence must be
reorderable by a translator.

## Deliberately Spanish, do NOT "fix"

These are values read from real user input or persisted data, not copy:
CSV header aliases (`fecha`, `ventas`, `categoria`), payment terms (`contado`,
`quincenal`), calendar catalog keys, the persisted signals (`PEDIR_YA`,
`SOBRESTOCK`, …), app routes (`/hoy`, `/pedidos`, `/skus`), downloadable
template headers, and the landing page's marketing copy.

## Voice

**Tuteo, never voseo.** "Sube el archivo", not "Subí el archivo"; "puedes", not
"podés". Read neighbouring entries before writing — a mismatched register reads
as a different product.

## Strings that become persisted names

`"{name} (editado)"` and `"{name} (copia)"` are not messages: they become the
stored name of a dataset and of a run. Hardcoding them left English users with
Spanish filenames forever. Route them through i18n like anything else.

## Libraries with no React context

Do not smuggle a hook into a pure function. Return a stable code + params and
let the caller translate, or take `t` as an argument — the pattern
`Frontend/src/lib/explanationCopy.ts` and `csvCheck.ts` already use.

## Never render a raw key

`t()` echoes an unmapped key back, so a missing entry puts `inventory.source_file`
on screen. Always fall back to something meaningful, the way `enumLabels.ts` does.

## Verify before committing

Both scripts live in `.claude/skills/faro-i18n/scripts/`. Run both:

```bash
python .claude/skills/faro-i18n/scripts/check_parity.py    # es/en counts, gaps, duplicates
python .claude/skills/faro-i18n/scripts/check_missing.py   # keys the UI asks for that the catalogue lacks
```

Parity must be exact and duplicates zero — a duplicate key in a JS object
literal is legal and silently last-wins, which is how a catalogue drifts.

## Merging a batch of keys

Pasting dozens of lines by hand is how a key lands in the wrong language block.
Splice with a script that refuses to run when the two blocks disagree, and that
skips keys already present instead of duplicating them.
