"""
LatAm commercial event catalog (feature 3.4).

This is **data, not new logic**: every entry describes a recurring commercial
event (payday fortnights, statutory bonuses, Mother's Day, Holy Week, back to
school, Christmas, Black Friday) and knows how to materialise itself into
concrete dates for a given year. Those dates are seeded into
`inventory_events`, the same table that already feeds the existing simulator
(`simulate_event_impact`), so the simulator does not change — it just stops
starting empty.

Target market: **Costa Rica** (`CR`, see `DEFAULT_COUNTRY`). Colombia (`CO`)
is included too. Adding a country means adding `CATALOG` entries with another
`country` — nothing in the engine is coupled to a specific one.

CR is not a translated CO: the Costa Rican statutory bonus is a single one
(December, Law 2412 — there is no Colombian mid-year bonus), Mother's Day
falls on 15 August rather than the second Sunday of May, and the school year
starts in February instead of late January. Reusing the Colombian rules would
have shifted the biggest sales date of the year by ~3 months.

Moveable dates (Holy Week, Father's Day, Black Friday) are computed, not
hardcoded — see `easter_sunday`, `nth_weekday_of_month`.

The multipliers are reasonable starting estimates, not data-fitted values:
they are the starting point the user then edits.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Iterable

# ── Moveable-date computation ───────────────────────────────────────────────


def easter_sunday(year: int) -> date:
    """
    Easter Sunday (Gregorian calendar) — the "Anonymous Gregorian
    computus" (Meeus/Jones/Butcher). Semana Santa se ancla a esta date.

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
    Nth `weekday` (0=Monday … 6=Sunday) of the month. n=1 is the first one.

    Used for Mother's Day in Colombia (second Sunday of May) and for Black
    Friday (the Friday after the fourth Thursday of November).
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
    """Mother's Day in Colombia: the second Sunday of May."""
    return nth_weekday_of_month(year, 5, weekday=6, n=2)


def mothers_day_cr(year: int) -> date:
    """
    Mother's Day in Costa Rica: **15 August**, a fixed date that coincides
    with the Assumption and is a national holiday.

    Note: it is NOT the second Sunday of May. Copying the Colombian rule here
    would be a ~3 month error on one of the highest-selling dates of the
    year.
    """
    return date(year, 8, 15)


def fathers_day_cr(year: int) -> date:
    """Father's Day in Costa Rica: the third Sunday of June."""
    return nth_weekday_of_month(year, 6, weekday=6, n=3)


def _clamp_day(year: int, month: int, day: int) -> date:
    """Day of month clamped to the last real day (avoids 30 February)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


# ── Catalog definition ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Occurrence:
    """One concrete materialisation of a catalog event in a given year."""
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
    # year -> lista de (sufijo_clave, name_completo, inicio, fin)
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

def _payday_15(year: int):
    """15th payday: a 3-day uplift in each of the 12 months."""
    for m in range(1, 13):
        start = date(year, m, 15)
        yield (f"m{m:02d}", f"Quincena (pago 15 de {_MONTH_ABBR_ES[m]})",
               start, start + timedelta(days=2))


def _payday_month_end(year: int):
    """Month-end payday: from the 30th (or last day) through the 2nd of the next month."""
    for m in range(1, 13):
        start = _clamp_day(year, m, 30)
        yield (f"m{m:02d}", f"Quincena (pago fin de {_MONTH_ABBR_ES[m]})",
               start, start + timedelta(days=3))


def _prima_junio(year: int):
    # By law the mid-year bonus is paid no later than 30 June.
    yield ("", "Prima de mitad de año", date(year, 6, 15), date(year, 6, 30))


def _prima_diciembre(year: int):
    # The year-end bonus is paid within the first 20 days of December.
    yield ("", "Prima de fin de año", date(year, 12, 1), date(year, 12, 20))


def _dia_madre(year: int):
    d = mothers_day_co(year)
    # Buying concentrates in the preceding week, not only on the Sunday.
    yield ("", "Día de la Madre", d - timedelta(days=6), d)


def _semana_santa(year: int):
    pascua = easter_sunday(year)
    # Domingo de Ramos → Domingo de Pascua.
    yield ("", "Semana Santa", pascua - timedelta(days=7), pascua)


def _temporada_escolar(year: int):
    # Calendar A (the majority one in Colombia): classes start in late January.
    yield ("", "Temporada escolar", date(year, 1, 5), date(year, 2, 5))


def _navidad(year: int):
    yield ("", "Navidad", date(year, 12, 1), date(year, 12, 24))


def _black_friday(year: int):
    bf = black_friday(year)
    # Black Friday → Cyber Monday.
    yield ("", "Black Friday", bf, bf + timedelta(days=3))


# ── Costa Rica specific builders ────────────────────────────────────────────
# Costa Rica is not Colombia under another name: there is ONE aguinaldo
# (December; no June bonus), Mother's Day is fixed on 15 August, and the school
# year starts in February rather than at the end of January.

def _cr_aguinaldo(year: int):
    # Law 2412: paid within the first 20 days of December. It is the
    # single biggest liquidity injection of the year.
    yield ("", "Aguinaldo", date(year, 12, 1), date(year, 12, 20))


def _cr_dia_madre(year: int):
    d = mothers_day_cr(year)
    yield ("", "Día de la Madre", d - timedelta(days=6), d)


def _cr_dia_padre(year: int):
    d = fathers_day_cr(year)
    yield ("", "Día del Padre", d - timedelta(days=5), d)


def _cr_temporada_escolar(year: int):
    # The Costa Rican school year starts in February: buying of supplies and
    # uniformes se concentra en enero y la primera semana de febrero.
    yield ("", "Temporada escolar (entrada a clases)",
           date(year, 1, 8), date(year, 2, 10))


def _cr_romeria(year: int):
    # Pilgrimage to the Virgen de los Angeles (Cartago), 2 August.
    yield ("", "Romería a Cartago", date(year, 7, 30), date(year, 8, 2))


def _cr_independencia(year: int):
    yield ("", "Fiestas patrias (15 de setiembre)",
           date(year, 9, 10), date(year, 9, 15))


def _cr_fin_de_ano(year: int):
    yield ("", "Fiestas de fin de año", date(year, 12, 25), date(year, 12, 31))


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
                 _payday_15),
    CatalogEvent("co_quincena_30", "Quincenas (pago fin de mes)", "CO", 1.4,
                 "Repunte quincenal de consumo tras el pago de nómina de fin de mes.",
                 _payday_month_end),

    # ── Costa Rica ───────────────────────────────────────────────────────────
    CatalogEvent("cr_temporada_escolar", "Temporada escolar", "CR", 1.7,
                 "El curso lectivo arranca en febrero: útiles, uniformes y "
                 "loncheras se compran en enero y la primera semana de febrero.",
                 _cr_temporada_escolar),
    CatalogEvent("cr_semana_santa", "Semana Santa", "CR", 1.6,
                 "Fecha móvil atada al Domingo de Pascua. El país se detiene "
                 "jueves y viernes santo: pico en pescado, turismo interno y "
                 "abarrotes, y muchos comercios cierran esos dos días.",
                 _semana_santa),
    CatalogEvent("cr_dia_padre", "Día del Padre", "CR", 1.4,
                 "Tercer domingo de junio.", _cr_dia_padre),
    CatalogEvent("cr_romeria", "Romería a Cartago", "CR", 1.5,
                 "Peregrinación del 2 de agosto a la Virgen de los Ángeles: "
                 "pico en agua, bebidas, snacks y calzado sobre la ruta.",
                 _cr_romeria),
    CatalogEvent("cr_dia_madre", "Día de la Madre", "CR", 2.0,
                 "15 de agosto (fijo, feriado nacional) — no el segundo domingo "
                 "de mayo. Una de las mayores fechas de venta del año.",
                 _cr_dia_madre),
    CatalogEvent("cr_independencia", "Fiestas patrias", "CR", 1.3,
                 "Semana del 15 de setiembre: desfiles, faroles y consumo local.",
                 _cr_independencia),
    CatalogEvent("cr_black_friday", "Black Friday", "CR", 2.2,
                 "Viernes siguiente al cuarto jueves de noviembre, extendido "
                 "hasta el Cyber Monday.", _black_friday),
    CatalogEvent("cr_aguinaldo", "Aguinaldo", "CR", 1.9,
                 "Ley 2412: se paga en los primeros 20 días de diciembre. Es el "
                 "mayor inyector de liquidez del año. A diferencia de Colombia, "
                 "en Costa Rica no hay una prima de mitad de año.",
                 _cr_aguinaldo),
    CatalogEvent("cr_navidad", "Navidad", "CR", 2.2,
                 "Temporada decembrina completa hasta el 24.", _navidad),
    CatalogEvent("cr_fin_de_ano", "Fiestas de fin de año", "CR", 1.8,
                 "Del 25 al 31 de diciembre: festejos populares, turismo interno "
                 "y consumo de bebidas y carnes.", _cr_fin_de_ano),
    CatalogEvent("cr_quincena_15", "Quincenas (pago 15)", "CR", 1.4,
                 "Repunte quincenal de consumo tras el pago de nómina del 15.",
                 _payday_15),
    CatalogEvent("cr_quincena_30", "Quincenas (pago fin de mes)", "CR", 1.4,
                 "Repunte quincenal de consumo tras el pago de nómina de fin de mes.",
                 _payday_month_end),
]

# ── More LatAm countries (2026-10) ───────────────────────────────────────────
# Every entry below ships with a NEUTRAL multiplier (1.0): the calendar dates
# are facts (fixed or rule-based), but nobody has measured what each event does
# to demand in these markets, and an invented x1.8 is worse than no number.
# The user (or a future per-tenant learning step) raises it; until then the
# event is visible in the calendar and the simulator but moves no decision.
# The CO / CR entries above predate this rule and keep their original
# starting estimates.
#
# Left out ON PURPOSE because the date has no rule we can compute offline:
# Mexico's Buen Fin and Hot Sale, Chile's CyberDay and Peru's CyberWow (their
# organisers announce the window every year), Ecuador's school start (differs
# by region: Sierra in September, Costa in April/May) and Uruguay (not
# covered). Wrong dates are worse than missing ones.

_NEUTRAL = 1.0
_NEUTRAL_NOTE = (
    " Multiplicador neutro (x1.0): StockAI aún no tiene una medición propia "
    "para este evento; ajústalo con tu historial."
)


def last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last `weekday` (0=Monday … 6=Sunday) of the month."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _lead_up_to(day_of: Callable[[int], date], days_before: int):
    """Builder for a gift date: the buying week up to and including the day."""
    def build(year: int):
        d = day_of(year)
        yield ("", "", d - timedelta(days=days_before), d)
    return build


def _fixed_window(m1: int, d1: int, m2: int, d2: int):
    """Builder for a fixed calendar window (inclusive)."""
    def build(year: int):
        yield ("", "", date(year, m1, d1), date(year, m2, d2))
    return build


def _fixed_days(days: list[tuple[int, int]], span: int):
    """Builder for several fixed single days, each `span` days long (suffix m<MM>)."""
    def build(year: int):
        for m, d in days:
            yield (f"m{m:02d}", "", date(year, m, d), date(year, m, d) + timedelta(days=span - 1))
    return build


def _carnival(year: int):
    # Saturday before Carnival Monday through Carnival Tuesday: Easter -50 .. -47
    # (Ash Wednesday is Easter -46).
    e = easter_sunday(year)
    yield ("", "", e - timedelta(days=50), e - timedelta(days=47))


def _third_sunday(month: int):
    return lambda y: nth_weekday_of_month(y, month, weekday=6, n=3)


def _second_sunday(month: int):
    return lambda y: nth_weekday_of_month(y, month, weekday=6, n=2)


def _last_sunday(month: int):
    return lambda y: last_weekday_of_month(y, month, weekday=6)


def _fixed_day(month: int, day: int):
    return lambda y: date(y, month, day)


def _neutral(country: str, suffix: str, name: str, notes: str, builder) -> CatalogEvent:
    """One neutral catalog entry; the builder's empty names are filled with `name`."""
    def named(year: int):
        for sfx, own_name, start, end in builder(year):
            if own_name:
                label = own_name
            elif sfx:
                label = f"{name} ({_MONTH_ABBR_ES[int(sfx[1:])]})"
            else:
                label = name
            yield (sfx, label, start, end)
    return CatalogEvent(f"{country.lower()}_{suffix}", name, country, _NEUTRAL,
                        notes + _NEUTRAL_NOTE, named)


def _common(country: str) -> list[CatalogEvent]:
    return [
        _neutral(country, "semana_santa", "Semana Santa",
                 "Fecha móvil atada al Domingo de Pascua (Domingo de Ramos a Domingo de Pascua).",
                 _semana_santa),
        _neutral(country, "black_friday", "Black Friday",
                 "Viernes siguiente al cuarto jueves de noviembre, extendido hasta el Cyber Monday.",
                 _black_friday),
        _neutral(country, "navidad", "Navidad",
                 "Temporada navideña del 1 al 24 de diciembre.", _navidad),
    ]


def _fortnight(country: str) -> list[CatalogEvent]:
    return [
        _neutral(country, "quincena_15", "Quincenas (pago 15)",
                 "Repunte quincenal de consumo tras el pago del 15.", _payday_15),
        _neutral(country, "quincena_30", "Quincenas (pago fin de mes)",
                 "Repunte quincenal de consumo tras el pago de fin de mes.", _payday_month_end),
    ]


def _mothers(country: str, day_of, rule: str) -> CatalogEvent:
    return _neutral(country, "dia_madre", "Día de la Madre", rule,
                    _lead_up_to(day_of, 6))


def _fathers(country: str, day_of, rule: str) -> CatalogEvent:
    return _neutral(country, "dia_padre", "Día del Padre", rule,
                    _lead_up_to(day_of, 5))


_MX = [
    *_common("MX"), *_fortnight("MX"),
    _mothers("MX", _fixed_day(5, 10), "10 de mayo, fecha fija."),
    _fathers("MX", _third_sunday(6), "Tercer domingo de junio."),
    _neutral("MX", "reyes", "Día de Reyes", "1 al 6 de enero: juguetes y rosca de reyes.",
             _fixed_window(1, 1, 1, 6)),
    _neutral("MX", "regreso_a_clases", "Regreso a clases",
             "El ciclo escolar de la SEP arranca a finales de agosto: útiles y uniformes se "
             "compran de principios de agosto a la primera semana de septiembre.",
             _fixed_window(8, 1, 9, 5)),
    _neutral("MX", "fiestas_patrias", "Fiestas patrias", "Del 10 al 16 de septiembre.",
             _fixed_window(9, 10, 9, 16)),
    _neutral("MX", "dia_muertos", "Día de Muertos", "Del 28 de octubre al 2 de noviembre.",
             _fixed_window(10, 28, 11, 2)),
    _neutral("MX", "aguinaldo", "Aguinaldo",
             "Por ley se paga a más tardar el 20 de diciembre.", _fixed_window(12, 1, 12, 20)),
]

_PE = [
    *_common("PE"),
    _mothers("PE", _second_sunday(5), "Segundo domingo de mayo."),
    _fathers("PE", _third_sunday(6), "Tercer domingo de junio."),
    _neutral("PE", "gratificacion_julio", "Gratificación de julio",
             "Se paga a más tardar el 15 de julio.", _fixed_window(7, 1, 7, 15)),
    _neutral("PE", "fiestas_patrias", "Fiestas patrias", "Del 24 al 29 de julio.",
             _fixed_window(7, 24, 7, 29)),
    _neutral("PE", "temporada_escolar", "Temporada escolar",
             "El año escolar inicia en marzo: útiles y uniformes en febrero y primera "
             "quincena de marzo.", _fixed_window(2, 1, 3, 15)),
    _neutral("PE", "gratificacion_diciembre", "Gratificación de diciembre",
             "Se paga a más tardar el 15 de diciembre.", _fixed_window(12, 1, 12, 15)),
]

_CL = [
    *_common("CL"),
    _mothers("CL", _second_sunday(5), "Segundo domingo de mayo."),
    _fathers("CL", _third_sunday(6), "Tercer domingo de junio."),
    _neutral("CL", "temporada_escolar", "Temporada escolar",
             "El año escolar inicia a comienzos de marzo: útiles y uniformes en febrero y "
             "la primera semana de marzo.", _fixed_window(2, 1, 3, 10)),
    _neutral("CL", "fiestas_patrias", "Fiestas patrias (Dieciocho)", "Del 12 al 19 de septiembre.",
             _fixed_window(9, 12, 9, 19)),
]

_AR = [
    *_common("AR"),
    _neutral("AR", "carnaval", "Carnaval",
             "Fecha móvil atada a la Pascua (lunes y martes de Carnaval, con el sábado previo).",
             _carnival),
    _fathers("AR", _third_sunday(6), "Tercer domingo de junio."),
    _neutral("AR", "aguinaldo_junio", "Aguinaldo de junio",
             "Primera cuota del SAC: vence el 30 de junio.", _fixed_window(6, 15, 6, 30)),
    _neutral("AR", "dia_nino", "Día del Niño",
             "Tercer domingo de agosto.", _lead_up_to(_third_sunday(8), 6)),
    _mothers("AR", _third_sunday(10), "Tercer domingo de octubre (no es en mayo)."),
    _neutral("AR", "aguinaldo_diciembre", "Aguinaldo de diciembre",
             "Segunda cuota del SAC: vence el 18 de diciembre.", _fixed_window(12, 1, 12, 18)),
    _neutral("AR", "temporada_escolar", "Temporada escolar",
             "El ciclo lectivo inicia entre fines de febrero y principios de marzo según la "
             "provincia: útiles y guardapolvos se compran en febrero.",
             _fixed_window(2, 1, 3, 10)),
]

_EC = [
    *_common("EC"),
    _neutral("EC", "carnaval", "Carnaval",
             "Fecha móvil atada a la Pascua (lunes y martes de Carnaval, con el sábado previo).",
             _carnival),
    _mothers("EC", _second_sunday(5), "Segundo domingo de mayo."),
    _fathers("EC", _third_sunday(6), "Tercer domingo de junio."),
    _neutral("EC", "decimocuarto_costa", "Decimocuarto sueldo (Costa y Galápagos)",
             "Se paga a más tardar el 15 de marzo.", _fixed_window(3, 1, 3, 15)),
    _neutral("EC", "decimocuarto_sierra", "Decimocuarto sueldo (Sierra y Oriente)",
             "Se paga a más tardar el 15 de agosto.", _fixed_window(8, 1, 8, 15)),
    _neutral("EC", "decimotercero", "Decimotercer sueldo",
             "Se paga a más tardar el 24 de diciembre.", _fixed_window(12, 1, 12, 24)),
]

_GT = [
    *_common("GT"), *_fortnight("GT"),
    _neutral("GT", "temporada_escolar", "Temporada escolar",
             "El ciclo escolar inicia a mediados de enero: útiles y uniformes a inicios de enero.",
             _fixed_window(1, 2, 1, 20)),
    _mothers("GT", _fixed_day(5, 10), "10 de mayo, fecha fija."),
    _fathers("GT", _fixed_day(6, 17), "17 de junio, fecha fija."),
    _neutral("GT", "bono_14", "Bono 14",
             "Se paga en la primera quincena de julio.", _fixed_window(7, 1, 7, 15)),
    _neutral("GT", "fiestas_patrias", "Fiestas patrias", "Del 10 al 15 de septiembre.",
             _fixed_window(9, 10, 9, 15)),
    _neutral("GT", "aguinaldo", "Aguinaldo",
             "El 50% se paga en la primera quincena de diciembre.", _fixed_window(12, 1, 12, 15)),
]

_PA = [
    *_common("PA"), *_fortnight("PA"),
    _neutral("PA", "carnaval", "Carnaval",
             "Fecha móvil atada a la Pascua (lunes y martes de Carnaval, con el sábado previo).",
             _carnival),
    _neutral("PA", "temporada_escolar", "Temporada escolar",
             "El año escolar inicia a comienzos de marzo: útiles y uniformes desde mediados "
             "de febrero.", _fixed_window(2, 15, 3, 10)),
    _neutral("PA", "decimotercer_mes", "Decimotercer mes",
             "Se paga en tres partidas: 15 de abril, 15 de agosto y 15 de diciembre.",
             _fixed_days([(4, 15), (8, 15), (12, 15)], span=3)),
    _mothers("PA", _fixed_day(12, 8), "8 de diciembre, fecha fija."),
]

_DO = [
    *_common("DO"), *_fortnight("DO"),
    _mothers("DO", _last_sunday(5), "Último domingo de mayo."),
    _fathers("DO", _last_sunday(7), "Último domingo de julio."),
    _neutral("DO", "temporada_escolar", "Temporada escolar",
             "El año escolar inicia a fines de agosto: útiles y uniformes durante agosto.",
             _fixed_window(8, 1, 8, 31)),
    _neutral("DO", "regalia_pascual", "Regalía pascual (salario de Navidad)",
             "Se paga a más tardar el 20 de diciembre.", _fixed_window(12, 1, 12, 20)),
]

for _group in (_MX, _PE, _CL, _AR, _EC, _GT, _PA, _DO):
    CATALOG.extend(_group)

SUPPORTED_COUNTRIES = sorted({e.country for e in CATALOG})

# Current target market. Changing it only moves the UI/API default;
# every country in the catalog stays reachable via ?country=.
DEFAULT_COUNTRY = "CR"


def catalog_for(country: str = DEFAULT_COUNTRY) -> list[CatalogEvent]:
    return [e for e in CATALOG if e.country == country.upper()]


def build_occurrences(country: str = DEFAULT_COUNTRY, years: Iterable[int] | None = None) -> list[Occurrence]:
    """
    Materialise `country`'s catalog into concrete occurrences.
    Defaults to the current year and the next one (so December is not blind
    when starting up in November).
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


def describe_catalog(country: str = DEFAULT_COUNTRY) -> list[dict]:
    """Catalog summary for the UI (which events exist, without dates)."""
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
