"""Check the guided tours: every step has copy, in both languages.

Tour copy lives with each tour rather than in translations.ts (see
components/tour/types.ts for why), so `check_parity.py` cannot see it. This is
the equivalent guard for that layer.

Fails on:
  · a step whose title/body key is missing from `es` or `en`
  · `es` and `en` disagreeing on which keys they define
  · a tour with no `name` entry
  · two tours sharing an id or a route
  · a `data-tour` anchor referenced by a step that appears nowhere in src/

Run:  python .claude/skills/faro-i18n/scripts/check_tours.py
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
TOURS_DIR = os.path.normpath(os.path.join(ROOT, "Frontend", "src", "components", "tour", "tours"))
SRC_DIR = os.path.normpath(os.path.join(ROOT, "Frontend", "src"))

STEP_RE = re.compile(
    r"\{\s*(?:anchor:\s*'([^']+)',\s*)?title:\s*'([^']+)',\s*body:\s*'([^']+)'\s*\}"
)
# Keys are bare identifiers in these modules: `intro_title: '…'`
ENTRY_RE = re.compile(r"^\s{6}([a-zA-Z0-9_]+):\s*'", re.M)
ID_RE = re.compile(r"\bid:\s*'([^']+)'")
ROUTE_RE = re.compile(r"\broute:\s*'([^']+)'")
NAME_RE = re.compile(r"\bname:\s*'([^']+)'")

failures: list[str] = []
ids: dict[str, str] = {}
routes: dict[str, str] = {}
all_anchors: set[str] = set()

modules = sorted(
    f for f in os.listdir(TOURS_DIR) if f.endswith(".ts") and f != "index.ts"
)
if not modules:
    sys.exit("no tour modules found — did the directory move?")

for name in modules:
    path = os.path.join(TOURS_DIR, name)
    src = io.open(path, encoding="utf-8").read()

    steps = STEP_RE.findall(src)
    if not steps:
        failures.append(f"{name}: no steps parsed")
        continue

    # Split the copy block into its two halves to compare them.
    try:
        es_start = src.index("es: {")
        en_start = src.index("en: {", es_start)
    except ValueError:
        failures.append(f"{name}: missing an es/en copy block")
        continue

    es_keys = set(ENTRY_RE.findall(src[es_start:en_start]))
    en_keys = set(ENTRY_RE.findall(src[en_start:]))

    only_es, only_en = es_keys - en_keys, en_keys - es_keys
    if only_es:
        failures.append(f"{name}: only in es -> {sorted(only_es)}")
    if only_en:
        failures.append(f"{name}: only in en -> {sorted(only_en)}")

    name_key = NAME_RE.search(src)
    if not name_key:
        failures.append(f"{name}: no `name:` on the definition")
    elif name_key.group(1) not in es_keys:
        failures.append(f"{name}: name key '{name_key.group(1)}' has no copy")

    for anchor, title, body in steps:
        for key in (title, body):
            if key not in es_keys:
                failures.append(f"{name}: step key '{key}' missing from es")
            if key not in en_keys:
                failures.append(f"{name}: step key '{key}' missing from en")
        if anchor:
            all_anchors.add(anchor)

    tid = ID_RE.search(src)
    if tid:
        if tid.group(1) in ids:
            failures.append(f"{name}: id '{tid.group(1)}' also used by {ids[tid.group(1)]}")
        ids[tid.group(1)] = name
    route = ROUTE_RE.search(src)
    if route:
        if route.group(1) in routes:
            failures.append(f"{name}: route '{route.group(1)}' also used by {routes[route.group(1)]}")
        routes[route.group(1)] = name

# An anchor a tour points at must exist somewhere in the app, or the step will
# silently degrade to a centred panel forever.
declared: set[str] = set()
# Prefixes whose suffix is built at runtime — see the template-literal case
# below. Anchors under these cannot be verified statically.
wildcards: set[str] = set()
for dirpath, dirs, files in os.walk(SRC_DIR):
    dirs[:] = [d for d in dirs if d not in {"node_modules", ".next"}]
    # The tour modules are where anchors are REFERENCED. Scanning them for
    # declarations would make every anchor trivially satisfy itself and turn
    # this whole check into a no-op — which is exactly what happened the first
    # time the pattern below was broadened.
    if os.path.normpath(dirpath).startswith(TOURS_DIR):
        continue
    for f in files:
        if not f.endswith((".ts", ".tsx")):
            continue
        text = io.open(os.path.join(dirpath, f), encoding="utf-8", errors="replace").read()
        # An anchor reaches the DOM three ways in this codebase: written
        # directly as `data-tour`, or handed to a component through a
        # `dataTour` / `tourAnchor` prop that forwards it. A page-local
        # component taking the prop is the normal shape wherever the same
        # component renders more than once (compare mode, list rows), so
        # missing these would flag perfectly good anchors as orphans.
        declared.update(re.findall(r"data-tour=[\"']([^\"']+)[\"']", text))
        declared.update(re.findall(r"(?:dataTour|tourAnchor)=[\"']([^\"']+)[\"']", text))
        # Braced forms: tourAnchor={idx === 0 ? 'x' : undefined}, and object
        # maps of anchors such as { card: 'int.provider', ... }.
        for expr in re.findall(r"(?:dataTour|tourAnchor)=\{([^}]*)\}", text):
            declared.update(re.findall(r"'([^']+)'", expr))
        declared.update(re.findall(r"\?\s*'([a-z]+\.[a-z_-]+)'\s*:\s*undefined", text))
        # An anchor map, e.g. { card: 'int.provider', form: 'int.credentials' }.
        declared.update(re.findall(
            r"\b[a-z]+:\s*'((?:[a-z]+)\.[a-z_-]+)'\s*[,}]", text))
        # Computed anchors: data-tour={`settings.${id}`}. The suffix only
        # exists at runtime, so record the prefix as a wildcard. This is a
        # genuine blind spot, not a pass — it is reported below.
        for prefix in re.findall(r"data-tour=\{`([a-z]+)\.\$\{", text):
            wildcards.add(prefix)

unverifiable = sorted(
    a for a in all_anchors
    if a not in declared and a.split(".", 1)[0] in wildcards
)
orphans = sorted(
    a for a in all_anchors
    if a not in declared and a.split(".", 1)[0] not in wildcards
)
for a in orphans:
    failures.append(f"anchor '{a}' is referenced by a step but declared nowhere in src/")

print(f"{len(modules)} tour module(s), {len(all_anchors)} distinct anchor(s)")
if unverifiable:
    # Named, never silently swallowed: a reader has to know these were not
    # actually checked.
    print(f"  {len(unverifiable)} built at runtime, NOT verified here "
          f"(check in a browser): {', '.join(unverifiable)}")
if failures:
    for f in failures:
        print(f"  FAIL  {f}")
    sys.exit(1)
print("tours OK")
