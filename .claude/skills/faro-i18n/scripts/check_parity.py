"""es/en parity of Frontend/src/i18n/translations.ts.

Exits non-zero on any gap or duplicate. A duplicate key in a JS object literal
is legal and silently last-wins, so it never surfaces as an error — only as
copy that mysteriously ignores an edit.

Usage: python .claude/skills/faro-i18n/scripts/check_parity.py [path]
"""
import io
import re
import sys

DEFAULT = r"Frontend/src/i18n/translations.ts"
# The catalogue mixes quoted ('a.b') and bare (email:) keys.
KEY = re.compile(r"\s*(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))\s*:")


def keys_between(lines, start, end):
    out = []
    for ln in lines[start:end]:
        m = KEY.match(ln)
        if m:
            out.append(m.group(1) or m.group(2))
    return out


def main(path):
    with io.open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    es_at = next((i for i, l in enumerate(lines) if l.startswith("  es: {")), None)
    en_at = next((i for i, l in enumerate(lines) if l.startswith("  en: {")), None)
    if es_at is None or en_at is None:
        sys.exit("could not find the `es: {` / `en: {` blocks")

    es = keys_between(lines, es_at + 1, en_at)
    en = keys_between(lines, en_at + 1, len(lines))

    missing_en = [k for k in es if k not in set(en)]
    missing_es = [k for k in en if k not in set(es)]
    dup_es = sorted({k for k in es if es.count(k) > 1})
    dup_en = sorted({k for k in en if en.count(k) > 1})

    print(f"es={len(es)} en={len(en)}")
    ok = True
    for label, items in (("missing in en", missing_en), ("missing in es", missing_es),
                         ("duplicated in es", dup_es), ("duplicated in en", dup_en)):
        if items:
            ok = False
            print(f"{label} ({len(items)}): {items[:10]}")
    if not ok:
        sys.exit(1)
    print("parity OK, no duplicates")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
