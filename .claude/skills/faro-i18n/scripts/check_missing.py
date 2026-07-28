"""Keys the UI asks for that the catalogue does not have.

`t()` echoes an unmapped key back, so a missing entry does not throw — it puts
`inventory.source_file` on screen in front of a buyer. That shipped once.

Static literals only: a key built at runtime (t(`x.${v}`)) cannot be checked
this way, so this under-reports rather than crying wolf.

Usage: python .claude/skills/faro-i18n/scripts/check_missing.py [src_dir]
"""
import io
import os
import re
import sys

DEFAULT_SRC = r"Frontend/src"
KEY = re.compile(r"\s*(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))\s*:")
CALL = re.compile(r"\bt\(\s*'([a-zA-Z0-9_.]+)'")


def main(src):
    catalogue = os.path.join(src, "i18n", "translations.ts")
    with io.open(catalogue, encoding="utf-8") as f:
        have = set()
        for line in f:
            m = KEY.match(line)
            if m:
                have.add(m.group(1) or m.group(2))

    used = {}
    for root, _dirs, files in os.walk(src):
        if "i18n" in root:
            continue
        for name in files:
            if not name.endswith((".ts", ".tsx")):
                continue
            path = os.path.join(root, name)
            with io.open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    for m in CALL.finditer(line):
                        used.setdefault(m.group(1), []).append(
                            f"{os.path.relpath(path, src)}:{i}")

    missing = sorted(k for k in used if k not in have)
    print(f"catalogue={len(have)} used={len(used)} missing={len(missing)}")
    for k in missing:
        print(f"  {k}   <- {used[k][0]}")
    if missing:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC)
