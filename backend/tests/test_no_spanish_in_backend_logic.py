"""
CLAUDE.md, Language: backend logic never holds a Spanish string.

What the user reads is either English (the frontend renders `errors.<code>` /
its own catalogue entry from the code and params the API ships) or, for the
channels the frontend never sees — email, WhatsApp, PDF, a downloaded CSV — a
Spanish value pulled from `backend/notifications/locale.py`, keyed in English.

The rule kept being broken in the same quiet way: a sentence composed in Spanish
inside a service, returned as a plain string, and printed verbatim. Measured on
screen with the UI set to English, before this guard existed:

  · "/hoy" printed whole Spanish paragraphs under the heading "Suggested action:"
  · "/inventario" listed excluded products with a Spanish explanation
  · "/proveedores/scorecard" said "Acme está tardando 12 días, no 7"

None of those raised anything. This test is the thing that fails instead.

Deliberately narrow, so it stays a signal rather than noise:
  · only STRING CONSTANTS in the AST — comments and docstrings are prose about
    the code, and domain words like "semáforo" belong in them;
  · only Spanish-specific characters plus a short list of unambiguous words. A
    Spanish sentence with no accent and none of those words gets through, which
    is fine: this is a regression guard, not a proof.
"""

import ast
import pathlib

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

_SPANISH_CHARS = set("áéíóúÁÉÍÓÚñÑ¿¡")

# Words that are Spanish and cannot plausibly be an English identifier, a column
# name or a code. Kept short on purpose.
_SPANISH_WORDS = {
    "pedido", "pedidos", "pedir", "proveedor", "proveedores", "unidades",
    "inventario", "sobrestock", "quiebre", "quiebres", "cobertura", "bodega",
    "existencias", "recepcion", "orden", "ordenes", "reabastecer",
}

# Values, not copy: the semáforo's signals are persisted on stock rows and in PO
# history, and renaming them is a data migration (CLAUDE.md lists them as
# deliberately Spanish). A literal made only of these is not a finding — and
# neither is an English sentence that names them, which is how the query
# parameter's own description reads.
_PERSISTED_VOCABULARY = {
    "pedir_ya", "pedir_pronto", "ok", "sobrestock", "sin_datos", "principal",
}

# Files whose Spanish is the product, not a leak.
_ALLOWED = {
    # THE catalogue. Every Spanish string for a backend-only channel lives here.
    "notifications/locale.py",
    # LatAm commercial calendar: event names and descriptions are reference DATA
    # the user picks from, and their keys are matched against real user records.
    "inventory/calendar_catalog.py",
    # Demo/seed rows: fake customer data in the language of the market. Product
    # and supplier names are what a LatAm distributor's catalogue looks like.
    "scripts/seed_demo.py",
    "api/v1/demo.py",
    # Header aliases matched against the columns of real uploaded files
    # ("fecha", "ventas", "proveedor"). These read user input; they are not copy.
    "utils/stock_import.py",
    "datasources/service.py",
    "dataframes/canonical.py",
    # Legacy Spanish DB column names being renamed forward by the migration.
    "db/migrations.py",
}


def _relevant_modules():
    for path in _BACKEND.rglob("*.py"):
        rel = path.relative_to(_BACKEND)
        if any(p.startswith(".") or p == "__pycache__" for p in rel.parts):
            continue
        if rel.parts[0] == "tests" or rel.as_posix() in _ALLOWED:
            continue
        yield rel, path


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id() of every string Constant that is a docstring, so prose is skipped."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(id(body[0].value))
    return out


def _spanish_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    skip = _docstring_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        text = node.value
        words = {w.strip(".,:;!?()").lower() for w in text.split()}
        flagged = (set(text) & _SPANISH_CHARS) or (words & _SPANISH_WORDS)
        if flagged and not (words - _PERSISTED_VOCABULARY):
            continue          # the literal is only persisted signal values
        if words & _SPANISH_WORDS and not (words & _SPANISH_WORDS) - _PERSISTED_VOCABULARY:
            continue          # ...or an English sentence that merely names them
        if flagged:
            found.append((node.lineno, text[:90]))
    return found


def test_the_audit_actually_reads_the_modules_it_claims_to():
    """A check that walks nothing prints OK forever. Pin the files that carried
    the defects this guard exists for."""
    scanned = {rel.as_posix() for rel, _ in _relevant_modules()}
    for expected in ("ai/narrative_service.py", "inventory/service.py",
                     "inventory/supplier_health_service.py", "whatsapp/tools.py",
                     "workers/runner.py", "api/v1/inventory.py"):
        assert expected in scanned, f"the audit never opened {expected}"
    assert len(scanned) > 50, f"only {len(scanned)} modules scanned"


def test_no_spanish_string_literal_in_backend_logic():
    offenders = []
    for rel, path in _relevant_modules():
        for lineno, text in _spanish_literals(path):
            offenders.append(f"{rel.as_posix()}:{lineno}  {text!r}")
    assert not offenders, (
        "Spanish string literal in backend logic. What the user reads must be "
        "English + a code the frontend renders, or a key in "
        "backend/notifications/locale.py for email/WhatsApp/PDF/CSV:\n  "
        + "\n  ".join(offenders)
    )


def test_the_catalogue_renders_every_key_it_declares():
    """A template whose params do not match its call site fails at send time, on
    a channel nobody is watching. Rendering each key with a permissive mapping
    proves the placeholders at least parse."""
    from backend.notifications import locale

    class _Any(dict):
        def __missing__(self, key):  # noqa: D105
            return "x"

    broken = []
    for key, template in locale._ES.items():
        try:
            template.format_map(_Any())
        except (IndexError, KeyError, ValueError) as e:
            broken.append(f"{key}: {e}")
    assert not broken, "unrenderable catalogue templates:\n  " + "\n  ".join(broken)


@pytest.mark.parametrize("key", [
    "wa_help", "wa_apology", "inventory_pdf_title", "po_pdf_heading",
    "inventory_csv_col_sku", "unit_day_one",
])
def test_the_keys_this_sweep_introduced_exist(key):
    """Named explicitly: a rename that drops one of these makes `render_es` raise
    KeyError inside a PDF build or a WhatsApp reply."""
    from backend.notifications.locale import _ES
    assert key in _ES
