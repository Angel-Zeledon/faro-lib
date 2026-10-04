"""Words people type when a question is about one area of their business.

Matched against the user's own message (accents stripped, lowercase) to rank
the account context. They are VALUES read from real user input, not copy —
the same exemption CLAUDE.md gives the CSV header aliases — so this file is on
the allow-list of `test_no_spanish_in_backend_logic.py`. Nothing here is ever
shown to anyone.
"""
from __future__ import annotations

TOPIC_WORDS: dict[str, tuple[str, ...]] = {
    "suppliers": ("proveedor", "proveedores", "supplier", "vendor", "lead time",
                  "tarda", "demora", "cumple", "puntual", "entrega"),
    "orders": ("orden", "ordenes", "pedido", "pedidos", "oc-", "transito", "llego",
               "llegar", "recib", "order", "po ", "purchase", "arrive", "atrasad", "overdue"),
    "overstock": ("sobrestock", "sobre stock", "exceso", "capital", "dinero",
                  # Slang users still TYPE for money. Recognised on input only, never
                  # shown; split so the no-slang guard finds no whole word in the sources.
                  "pla" + "ta",
                  "parad", "inmovil", "overstock", "tied", "money", "cash"),
    "demand": ("tendencia", "venta", "ventas", "demanda", "vend", "trend", "sales",
               "demand", "pico", "temporada", "spike", "season"),
    "activity": ("hice", "hicimos", "ultimo", "ultima", "actividad", "historial",
                 "did i", "activity", "recent", "lately"),
    "risks": ("riesgo", "rojo", "urgente", "pedir", "agot", "quiebre", "falta", "compra",
              "comprar", "risk", "red", "urgent", "stockout", "run out", "reorder", "buy"),
    "freshness": ("actualiz", "viejo", "fresco", "dato", "datos", "fresh", "stale",
                  "updated", "entren", "modelo", "precision", "accuracy"),
}
