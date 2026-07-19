"""
Catálogo de eventos comerciales LatAm (feature 3.4).

Esto es **datos, no lógica nueva**: cada entrada describe un evento comercial
recurrente (quincenas, primas, Día de la Madre, Semana Santa, temporada
escolar, Navidad, Black Friday) y sabe materializarse en fechas concretas para
un año dado. Esas fechas se siembran en `inventory_events`, la misma tabla que
ya alimenta el simulador existente (`simulate_event_impact`), así que el
simulador no cambia — sólo deja de empezar vacío.

Mercado inicial: Colombia (`CO`). El diseño admite más países añadiendo
entradas con otro `country`; nada aquí está acoplado a Colombia salvo los
datos mismos.

Las fechas móviles (Semana Santa, Día de la Madre, Black Friday) se calculan,
no se hardcodean — ver `easter_sunday`, `nth_weekday_of_month`.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Iterable

# ── Cálculo de fechas móviles ────────────────────────────────────────────────


def easter_sunday(year: int) -> date:
    """
    Domingo de Pascua (calendario gregoriano) — algoritmo "Anonymous Gregorian
    computus" (Meeus/Jones/Butcher). Semana Santa se ancla a esta fecha.

    Verificable: 2023-04-09, 2024-03-31, 2025-04-20, 2026-04-05, 2027-03-28.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """
    N-ésimo `weekday` (0=lunes … 6=domingo) del mes. n=1 → el primero.

    Usado para el Día de la Madre en Colombia (segundo domingo de mayo) y para
    Black Friday (viernes siguiente al cuarto jueves de noviembre).
    """
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    day = 1 + offset + (n - 1) * 7
    last_day = calendar.monthrange(year, month)[1]
    if day > last_day:
        raise ValueError(f"No existe el {n}º weekday={weekday} en {year}-{month:02d}")
    return date(year, month, day)


def black_friday(year: int) -> date:
    """Viernes siguiente al cuarto jueves de noviembre (2025-11-28, 2026-11-27)."""
    fourth_thursday = nth_weekday_of_month(year, 11, weekday=3, n=4)
    return fourth_thursday + timedelta(days=1)


def mothers_day_co(year: int) -> date:
    """Día de la Madre en Colombia: segundo domingo de mayo."""
    return nth_weekday_of_month(year, 5, weekday=6, n=2)


def _clamp_day(year: int, month: int, day: int) -> date:
    """Día del mes acotado al último día real (evita 30 de febrero)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


# ── Definición del catálogo ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Occurrence:
    """Una materialización concreta de un evento del catálogo en un año."""
    catalog_key: str       # único por tenant — hace la siembra idempotente
    name: str
    start_date: date
    end_date: date
    multiplier: float
    notes: str


@dataclass(frozen=True)
class CatalogEvent:
    key: str
    name: str
    country: str
    multiplier: float
    notes: str
    # year -> lista de (sufijo_clave, nombre_completo, inicio, fin)
    builder: Callable[[int], Iterable[tuple[str, str, date, date]]] = field(repr=False)

    def occurrences(self, year: int) -> list[Occurrence]:
        out: list[Occurrence] = []
        for suffix, name, start, end in self.builder(year):
            key = f"{self.key}:{year}" if not suffix else f"{self.key}:{year}:{suffix}"
            out.append(Occurrence(
                catalog_key=key,
                name=name,
                start_date=start,
                end_date=end,
                multiplier=self.multiplier,
                notes=self.notes,
            ))
        return out


_MONTH_ABBR_ES = [
    "", "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]


# ── Builders ─────────────────────────────────────────────────────────────────

def _quincena_15(year: int):
    """Pago del 15: repunte de 3 días en cada uno de los 12 meses."""
    for m in range(1, 13):
        start = date(year, m, 15)
        yield (f"m{m:02d}", f"Quincena (pago 15 de {_MONTH_ABBR_ES[m]})",
               start, start + timedelta(days=2))


def _quincena_30(year: int):
    """Pago de fin de mes: del 30 (o último día) hasta el 2 del mes siguiente."""
    for m in range(1, 13):
        start = _clamp_day(year, m, 30)
        yield (f"m{m:02d}", f"Quincena (pago fin de {_MONTH_ABBR_ES[m]})",
               start, start + timedelta(days=3))


def _prima_junio(year: int):
    # Por ley la prima de mitad de año se paga a más tardar el 30 de junio.
    yield ("", "Prima de mitad de año", date(year, 6, 15), date(year, 6, 30))


def _prima_diciembre(year: int):
    # La prima de fin de año se paga en los primeros 20 días de diciembre.
    yield ("", "Prima de fin de año", date(year, 12, 1), date(year, 12, 20))


def _dia_madre(year: int):
    d = mothers_day_co(year)
    # La compra se concentra en la semana previa, no sólo el domingo.
    yield ("", "Día de la Madre", d - timedelta(days=6), d)


def _semana_santa(year: int):
    pascua = easter_sunday(year)
    # Domingo de Ramos → Domingo de Pascua.
    yield ("", "Semana Santa", pascua - timedelta(days=7), pascua)


def _temporada_escolar(year: int):
    # Calendario A (mayoritario en Colombia): clases arrancan a finales de enero.
    yield ("", "Temporada escolar", date(year, 1, 5), date(year, 2, 5))


def _navidad(year: int):
    yield ("", "Navidad", date(year, 12, 1), date(year, 12, 24))


def _black_friday(year: int):
    bf = black_friday(year)
    # Black Friday → Cyber Monday.
    yield ("", "Black Friday", bf, bf + timedelta(days=3))


CATALOG: list[CatalogEvent] = [
    CatalogEvent("co_temporada_escolar", "Temporada escolar", "CO", 1.7,
                 "Regreso a clases (calendario A): útiles, uniformes, loncheras.",
                 _temporada_escolar),
    CatalogEvent("co_semana_santa", "Semana Santa", "CO", 1.5,
                 "Fecha móvil atada al Domingo de Pascua. Pico en pescado, "
                 "viajes y abarrotes; muchos negocios cierran el jueves y viernes.",
                 _semana_santa),
    CatalogEvent("co_dia_madre", "Día de la Madre", "CO", 2.0,
                 "Segundo domingo de mayo. Una de las fechas de mayor venta "
                 "minorista del año en Colombia.",
                 _dia_madre),
    CatalogEvent("co_prima_junio", "Prima de mitad de año", "CO", 1.6,
                 "Prima legal pagadera a más tardar el 30 de junio: sube el "
                 "poder de compra de los hogares.",
                 _prima_junio),
    CatalogEvent("co_black_friday", "Black Friday", "CO", 2.5,
                 "Viernes siguiente al cuarto jueves de noviembre, extendido "
                 "hasta el Cyber Monday.",
                 _black_friday),
    CatalogEvent("co_prima_diciembre", "Prima de fin de año", "CO", 1.8,
                 "Prima legal pagadera en los primeros 20 días de diciembre.",
                 _prima_diciembre),
    CatalogEvent("co_navidad", "Navidad", "CO", 2.2,
                 "Temporada decembrina completa, desde novenas hasta el 24.",
                 _navidad),
    CatalogEvent("co_quincena_15", "Quincenas (pago 15)", "CO", 1.4,
                 "Repunte quincenal de consumo tras el pago de nómina del 15.",
                 _quincena_15),
    CatalogEvent("co_quincena_30", "Quincenas (pago fin de mes)", "CO", 1.4,
                 "Repunte quincenal de consumo tras el pago de nómina de fin de mes.",
                 _quincena_30),
]

SUPPORTED_COUNTRIES = sorted({e.country for e in CATALOG})


def catalog_for(country: str = "CO") -> list[CatalogEvent]:
    return [e for e in CATALOG if e.country == country.upper()]


def build_occurrences(country: str = "CO", years: Iterable[int] | None = None) -> list[Occurrence]:
    """
    Materializa el catálogo de `country` en ocurrencias concretas.
    Por defecto: año en curso y el siguiente (para que diciembre no quede ciego
    al arrancar en noviembre).
    """
    if years is None:
        this_year = date.today().year
        years = (this_year, this_year + 1)
    out: list[Occurrence] = []
    for ev in catalog_for(country):
        for y in years:
            out.extend(ev.occurrences(y))
    out.sort(key=lambda o: (o.start_date, o.catalog_key))
    return out


def describe_catalog(country: str = "CO") -> list[dict]:
    """Resumen del catálogo para la UI (qué eventos existen, sin fechas)."""
    return [
        {
            "key": e.key,
            "name": e.name,
            "country": e.country,
            "multiplier": e.multiplier,
            "notes": e.notes,
        }
        for e in catalog_for(country)
    ]
