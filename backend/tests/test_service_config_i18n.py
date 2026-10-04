"""The panel's copy covers exactly the services and variables that exist.

Two halves of one guarantee, and neither alone is enough:

* TypeScript already makes a MISSING translation a compile error — the copy is
  `Record<ServiceKey, ...>` / `Record<FieldKey, string>`, so `tsc` refuses a
  catalogue with a hole in it, in either language.
* Nothing in TypeScript knows what Python declares. Those unions are hand-written
  lists, so a field added to the registry and not to the union compiles happily
  and shows up on screen with no description at all. That is what this file
  checks, from the Python side, where the registry is.

It compares KEY SETS, never wording: a Spanish line may be shorter than the
English `doc`, it may not be about something else.
"""

import re
from pathlib import Path

import pytest

from backend.service_config.registry import SERVICES, all_fields

CATALOGUE = (
    Path(__file__).resolve().parents[2]
    / "Frontend" / "src" / "i18n" / "serviceConfig.ts"
)


@pytest.fixture(scope="module")
def source() -> str:
    if not CATALOGUE.exists():
        pytest.fail(f"Panel copy is missing: {CATALOGUE}")
    return CATALOGUE.read_text(encoding="utf-8")


def _union_members(source: str, type_name: str) -> set[str]:
    """The string literals of `export type <name> = 'a' | 'b' | ...`."""
    match = re.search(
        rf"export type {type_name} =(.*?)\n\n", source, re.DOTALL
    )
    assert match, f"{type_name} union not found in {CATALOGUE.name}"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_service_key_union_matches_the_registry(source):
    declared = _union_members(source, "ServiceKey")
    actual = {s.key for s in SERVICES}
    assert declared == actual, (
        f"ServiceKey is out of date. Missing: {sorted(actual - declared)}; "
        f"stale: {sorted(declared - actual)}"
    )


def test_the_field_key_union_matches_the_registry(source):
    declared = _union_members(source, "FieldKey")
    actual = set(all_fields())
    assert declared == actual, (
        f"FieldKey is out of date. Missing: {sorted(actual - declared)}; "
        f"stale: {sorted(declared - actual)}"
    )


def test_every_field_is_described_in_both_languages(source):
    """One entry per language. `tsc` enforces the same thing on a complete
    union; this catches it without waiting for a typecheck run, and says which
    key is short."""
    for key in sorted(all_fields()):
        hits = len(re.findall(rf"^\s+{key}: '", source, re.MULTILINE))
        assert hits >= 2, (
            f"'{key}' is described {hits} time(s) in {CATALOGUE.name}; "
            "it needs one Spanish and one English line."
        )


def test_every_service_is_described_in_both_languages(source):
    for service in SERVICES:
        hits = len(re.findall(rf"^\s+{service.key}: \{{", source, re.MULTILINE))
        assert hits >= 2, (
            f"Service '{service.key}' is described {hits} time(s) in "
            f"{CATALOGUE.name}; it needs Spanish and English."
        )


def test_the_copy_carries_no_secret_shaped_string(source):
    """An example is a shape, never a credential. The registry's `example`
    values reach `.env.example`; this catches one pasted into the copy."""
    for pattern in (r"sk-[A-Za-z0-9]{16,}", r"AC[0-9a-f]{30,}", r"SG\.[A-Za-z0-9_-]{16,}"):
        assert not re.search(pattern, source), (
            f"{CATALOGUE.name} contains something shaped like a real credential "
            f"({pattern})."
        )
