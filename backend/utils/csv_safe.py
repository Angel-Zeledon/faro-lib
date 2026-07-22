"""
CSV formula-injection neutralization.

Spreadsheet apps (Excel, LibreOffice, Google Sheets) treat a cell whose text
starts with = + - @ (or the tab/CR control chars) as a FORMULA when the file
is opened — so a value like `=cmd|' /C calc'!A0` imported into Faro and then
exported in a purchase order becomes executable in the buyer's spreadsheet.
Prefix such values with a single quote so they render as literal text.

Only user-controlled text cells need this; numeric/enum cells we generate
ourselves are safe.
"""

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    """Return `value` as a string that no spreadsheet will treat as a formula."""
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in _DANGEROUS_PREFIXES:
        return "'" + text
    return text
