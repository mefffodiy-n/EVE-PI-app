"""
Загрузка данных по планетам региона Fountain: плотности сырья, радиус,
POCO. Единственный регион, для которого сейчас есть данные (подтверждено
пользователем 16.09.2026, docs/ROADMAP.md раздел 7) — расчёт для планет
за его пределами недоступен, см. regions()/systems_in_regions() ниже и
Фазу 10 в docs/ROADMAP.md про мультирегиональность.

Источник данных (с 21.09.2026, Фаза 1 мультирегиональности): таблицы БД
`regions`/`planets` (infra/models.py) — `load_planets()` строит из них
тот же по форме DataFrame, что раньше строился из `data/
planet_industry.csv`, и тот же `PlanetBook` с тем же публичным API:
методы ниже (radius_km, poco_rate, resolve_resource_column и т.д.)
ничего не знают о том, что источник сменился с файла на БД, — они
по-прежнему ищут значения по именам колонок DataFrame.

Сам CSV остаётся источником ИСТОРИИ данных (968 планет Fountain,
перенесены в БД один раз скриптом `scripts/migrate_planets_csv_to_db.py`,
переиспользующим CSV-парсинг из `_load_from_csv()` ниже — единственного
оставшегося потребителя этой функции). Формат файла (после служебной
шапки из 2 строк):
    Constellation; System; Planet; Type; Radius [km]; POCO Tax Rate [%];
    POCO Owner; <30 колонок R0-ресурсов (плотность на планете)>;
    <24 колонки P2-ресурсов для сценария "прямое R0 -> P2">

ВАЖНО: в исходном CSV встречались опечатки в заголовках колонок
("Polyramids" вместо "Polyaramids", "Supertensil Plastics" вместо
"Supertensile Plastics" — см. docs/ROADMAP.md, раздел 1). Эти опечатки
нормализовались при переносе в БД (`_normalize_columns` ниже), поэтому
в `r0_densities`/`p2_direct_densities` уже лежат исправленные имена.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_PLANETS_PATH = Path(__file__).resolve().parent.parent / "data" / "planet_industry.csv"

# Соответствие "как называется в исходном CSV" -> "как называется в recipes.json".
# Заполняется по мере обнаружения расхождений при валидации (см. _normalize_columns).
COLUMN_NAME_FIXES = {
    "Polyramids": "Polyaramids",
    "Supertensil Plastics": "Supertensile Plastics",
}

# Служебные/технические строки-артефакты экспорта из Excel, которые
# встречались в исходном файле и должны отбрасываться при импорте.
IGNORED_CONSTELLATION_VALUES = {"max P2"}

RADIUS_COLUMN = "Radius [km]"
POCO_RATE_COLUMN = "POCO Tax Rate [%]"

# Дата, на которую сделана выгрузка data/planet_industry.csv — не поле
# из самого файла (в нём такой метки нет), а дата последнего изменения
# файла в репозитории (`git log -1 -- data/planet_industry.csv`),
# честно как единственный доступный ориентир. Нужна пользователю, чтобы
# понимать, насколько свежа автоматическая ставка/владелец POCO (см.
# PlanetBook.poco_rate() ниже) — в отличие от плотности сырья и радиуса,
# которые не меняются в игре никогда, ставку и владельца POCO игрок
# может сменить в любой момент без предупреждения.
POCO_SNAPSHOT_DATE = "2026-09-07"

# Одно и то же сырьё называется по-разному в разных источниках.
# Например, в recipes.json источник для Bacteria записан как
# "Microorganisms", а в игре, на eve-webtools и в CSV — "Micro-Organisms".
# Без сопоставления поиск планет с этим сырьём молча ничего не находил бы,
# и планировщик сообщал бы о несуществующем дефиците.
RESOURCE_NAME_ALIASES = {
    "Microorganisms": ["Micro-Organisms", "Micro Organisms"],
    "Micro-Organisms": ["Microorganisms", "Micro Organisms"],
    "Non-CS Crystals": ["Noncomplex Crystals", "Non CS Crystals"],
}

# ПРЕДПОЧТИТЕЛЬНЫЕ типы планет под переработку.
#
# Это предпочтение, а не запрет. Различие принципиальное:
#   - для P4 Barren и Temperate — ПРАВИЛО ИГРЫ (allowed_planet_types
#     в data/pi_reference.json), обойти его нельзя;
#   - для P2/P3 источник разрешает любой тип, и Barren с Temperate
#     выбраны нами лишь потому, что в среднем они мельче, а значит
#     дешевле по линкам.
#
# Поэтому при нехватке таких планет P2/P3 ставятся на другие типы
# с предупреждением, а P4 — не ставятся вовсе.
PREFERRED_FACTORY_PLANET_TYPES = ("Barren", "Temperate")

# Прежнее имя оставлено: на него могли ссылаться внешние скрипты.
FACTORY_PLANET_TYPES = PREFERRED_FACTORY_PLANET_TYPES

_ROMAN_NUMERALS = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
    (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def planet_number_to_roman(value: float | int | str | None) -> str:
    """
    Номер планеты -> римская цифра, как его показывает сама игра
    («WMH-SO IV», не «WMH-SO 4») — 18.09.2026, по прямому запросу
    пользователя после того, как номер планеты расчётного плана
    отображался как «16.0» (CSV-колонка «Planet» читается pandas как
    float64 — в файле нет строки без дробной части, дробность у неё не
    убрана нигде). Не просто "убрать .0": весь номер планеты везде
    (план и настоящие колонии) должен выглядеть как в игре.

    Источники отдают номер по-разному (CSV — float «16.0», ESI —
    int, JSON с фронта — то и другое как строка) — здесь принимается
    что угодно приводимое к целому. Нечисловое или отсутствующее
    значение возвращается как есть (честно, не выдумывая цифру).
    """
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value) if value is not None else ""
    if number <= 0:
        return str(number)
    result = []
    remaining = number
    for magnitude, numeral in _ROMAN_NUMERALS:
        count, remaining = divmod(remaining, magnitude)
        result.append(numeral * count)
    return "".join(result)


@dataclass(frozen=True)
class Planet:
    constellation: str
    system: str
    planet_number: str
    planet_type: str
    radius_km: float
    poco_tax_rate: float | None
    poco_owner: str | None
    r0_density: dict[str, float]  # {"Aqueous Liquids": 36, ...} — только заполненные
    p2_direct_density: dict[str, float]  # правая часть матрицы (сценарий прямого R0->P2)


class PlanetBook:
    """Обёртка над таблицей планет с методами выборки под нужды planner.py."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    @property
    def dataframe(self) -> pd.DataFrame:
        """Прямой доступ к DataFrame для случаев, где pandas-операции уместнее объектов."""
        return self._df

    def constellations(self) -> list[str]:
        return sorted(self._df["Constellation"].dropna().unique())

    def regions(self) -> dict[str, list[str]]:
        """
        Регионы и входящие в них созвездия.

        Колонка «Region» в выгрузке необязательна: экспорт, с которого
        начинался проект, её не содержит — там только созвездия одного
        региона. Если колонка появится, группировка заработает сама;
        пока её нет, все созвездия считаются одним регионом, и выбор
        «весь регион» просто отмечает их все.
        """
        if "Region" in self._df.columns:
            grouped: dict[str, list[str]] = {}
            for region, part in self._df.groupby("Region"):
                name = str(region).strip()
                if name and name.lower() != "nan":
                    grouped[name] = sorted(part["Constellation"].dropna().unique())
            return dict(sorted(grouped.items()))
        return {}

    def systems_in_regions(self, regions: list[str]) -> list[str]:
        if "Region" not in self._df.columns:
            return []
        subset = self._df[self._df["Region"].isin(regions)]
        return sorted(subset["System"].dropna().unique())

    def systems_in(self, constellations: list[str]) -> list[str]:
        subset = self._df[self._df["Constellation"].isin(constellations)]
        return sorted(subset["System"].dropna().unique())

    def planets_in_system(self, system: str) -> pd.DataFrame:
        """Все планеты системы."""
        return self._df[self._df["System"] == system]

    def constellation_of(self, system: str) -> str | None:
        """
        Констелляция по имени системы — для настоящих колоний (ESI её
        не отдаёт, а тут она есть). None, если системы нет в этом файле:
        честный пробел, не пустая строка без объяснения (18.09.2026,
        найдено пользователем — колонка «Констелляция» в выгрузке
        настоящих колоний в Excel оставалась пустой без причины, хотя
        данные для неё есть в этом же файле).
        """
        subset = self._df[self._df["System"] == system]
        if subset.empty:
            return None
        value = subset.iloc[0]["Constellation"]
        return None if pd.isna(value) else str(value)

    def radius_km(self, system: str, planet_number: float | int | str) -> float | None:
        """
        Радиус конкретной планеты по системе и номеру — для расчёта
        загрузки командного центра НАСТОЯЩЕЙ колонии из игры (ESI радиус
        не отдаёт, а тут он есть). None, если планеты нет в этом файле:
        он покрывает только загруженный регион (968 планет), а не весь
        New Eden — у колонии за его пределами радиуса просто нет источника.
        """
        try:
            number = float(planet_number)
        except (TypeError, ValueError):
            return None
        subset = self._df[(self._df["System"] == system) & (self._df["Planet"] == number)]
        if subset.empty:
            return None
        value = subset.iloc[0][RADIUS_COLUMN]
        return None if pd.isna(value) else float(value)

    def poco_rate(self, system: str, planet_number: float | int | str) -> float | None:
        """
        Ставка POCO (доля 0..1) КОНКРЕТНОЙ планеты — из data/
        planet_industry.csv, статичный снимок на POCO_SNAPSHOT_DATE.

        Единственный источник ставки (17.09.2026 — ручной ввод по типу
        планеты убран решением пользователя при переходе к
        мультирегиональности: с несколькими регионами переопределение
        «по типу» перестаёт быть однозначным и только усложняет ввод).
        Не «живая» ставка — владелец POCO может сменить её в игре в
        любой момент без предупреждения, а сама структура сменить
        владельца после войны за суверенитет.

        None, если планеты нет в этом файле или ставка не указана —
        как и у radius_km(), не оценка «шире региона», а честный пробел.
        """
        try:
            number = float(planet_number)
        except (TypeError, ValueError):
            return None
        subset = self._df[(self._df["System"] == system) & (self._df["Planet"] == number)]
        if subset.empty or POCO_RATE_COLUMN not in subset.columns:
            return None
        value = subset.iloc[0][POCO_RATE_COLUMN]
        if pd.isna(value):
            return None
        try:
            return float(value) / 100.0
        except (TypeError, ValueError):
            return None

    def factory_candidates(self, system: str, preferred_only: bool = False) -> pd.DataFrame:
        """
        Планеты системы под перерабатывающие шаблоны, отсортированные
        ПО ВОЗРАСТАНИЮ РАДИУСА.

        Порядок важен: стоимость линков растёт с радиусом, поэтому
        меньшая планета всегда лучше при прочих равных — именно на ней
        с большей вероятностью поместятся два шаблона.

        preferred_only=True вернёт только Barren и Temperate. По умолчанию
        возвращаются все планеты системы: пригодность по типу решает
        вызывающий код, потому что для P4 это правило игры, а для P2/P3 —
        лишь предпочтение.
        """
        subset = self._df[self._df["System"] == system]
        if preferred_only:
            subset = subset[subset["Type"].isin(PREFERRED_FACTORY_PLANET_TYPES)]
        return subset.sort_values(RADIUS_COLUMN, na_position="last")

    def resolve_resource_column(self, resource_name: str) -> str | None:
        """
        Найти колонку CSV, соответствующую названию сырья.

        Сначала точное совпадение, затем известные варианты написания,
        затем сравнение без дефисов и регистра. Возвращает None, если
        колонки нет вовсе — чтобы вызывающий код мог сказать об этом
        прямо, а не выдать пустой результат как «сырья нет в регионе».
        """
        if resource_name in self._df.columns:
            return resource_name

        for alias in RESOURCE_NAME_ALIASES.get(resource_name, []):
            if alias in self._df.columns:
                return alias

        def simplify(text: str) -> str:
            return text.lower().replace("-", "").replace(" ", "")

        target = simplify(resource_name)
        for column in self._df.columns:
            if simplify(str(column)) == target:
                return str(column)
        return None

    def planets_with_all_resources(
        self, resources: list[str], constellations: list[str] | None = None
    ) -> pd.DataFrame:
        """
        Планеты, где есть СРАЗУ ВСЁ перечисленное сырьё.

        Нужно для прямого производства P2: цепочка целиком помещается
        на планету только если оба вида сырья добываются на ней же.
        Таких планет мало, и это ожидаемо.

        Сортировка по возрастанию радиуса: линки дешевле, а значит
        на планету поместится больше фабрик.
        """
        columns = [self.resolve_resource_column(r) for r in resources]
        if any(c is None for c in columns):
            return self._df.iloc[0:0]

        subset = self._df
        if constellations:
            subset = subset[subset["Constellation"].isin(constellations)]

        mask = None
        for column in columns:
            values = pd.to_numeric(subset[column], errors="coerce")
            present = values.notna() & (values > 0)
            mask = present if mask is None else (mask & present)

        result = subset[mask] if mask is not None else subset.iloc[0:0]
        return result.sort_values(RADIUS_COLUMN, na_position="last")

    def system_coverage(
        self, resources: list[str], constellations: list[str] | None = None
    ) -> dict[str, int]:
        """
        Сколько из нужных видов сырья есть в каждой системе.

        Система, где добывается сразу четыре нужных ресурса, логистически
        лучше четырёх систем с одним каждая: один персонаж соберёт всё
        за один заход. Плотность при этом остаётся вторым критерием —
        богатое месторождение в одиночной системе может перевесить.
        """
        subset = self._df
        if constellations:
            subset = subset[subset["Constellation"].isin(constellations)]

        columns = [c for c in (self.resolve_resource_column(r) for r in resources) if c]
        if not columns:
            return {}

        coverage: dict[str, int] = {}
        for system, part in subset.groupby("System"):
            count = 0
            for column in columns:
                values = pd.to_numeric(part[column], errors="coerce")
                if (values > 0).any():
                    count += 1
            if count:
                coverage[str(system)] = count
        return coverage

    def resource_scarcity(
        self, resources: list[str], constellations: list[str] | None = None
    ) -> list[dict]:
        """
        Ранжировать сырьё по дефицитности в выбранных констелляциях.

        Дефицитность считается по двум признакам сразу: сколько планет
        вообще содержат это сырьё и какова там плотность. Одного мало:
        сырьё может встречаться на многих планетах, но всюду скудно, —
        и наоборот.

        Отдаётся по убыванию дефицитности: первым то, чего добывать
        труднее всего, а значит именно оно первым станет узким местом.
        """
        rows: list[dict] = []
        for name in resources:
            column = self.resolve_resource_column(name)
            if column is None:
                rows.append({"resource": name, "planets": 0, "median_density": 0.0,
                             "total_density": 0.0, "known": False})
                continue

            subset = self._df
            if constellations:
                subset = subset[subset["Constellation"].isin(constellations)]
            values = pd.to_numeric(subset[column], errors="coerce")
            values = values[values.notna() & (values > 0)]

            rows.append({
                "resource": name,
                "planets": int(len(values)),
                "median_density": float(values.median()) if len(values) else 0.0,
                "total_density": float(values.sum()) if len(values) else 0.0,
                "known": True,
            })

        # Меньше суммарной плотности — дефицитнее. При равенстве
        # решает число планет: разбросанное сырьё добывать проще.
        rows.sort(key=lambda r: (r["total_density"], r["planets"]))
        return rows

    def planets_with_resource(
        self, resource_name: str, constellations: list[str] | None = None
    ) -> pd.DataFrame:
        """
        Планеты с ненулевой плотностью заданного R0-сырья,
        отсортированные по убыванию плотности.

        Сортировка по плотности, а не по радиусу: у добывающего шаблона
        запас по CPU/PG велик (он помещается на планету практически
        любого размера), поэтому решает выход сырья, а не стоимость линков.

        Заменяет разрозненные dropna()/sort_values(), разбросанные
        по calculate_plan() в старом main.py.
        """
        column = self.resolve_resource_column(resource_name)
        if column is None:
            return self._df.iloc[0:0]
        resource_name = column

        subset = self._df
        if constellations:
            subset = subset[subset["Constellation"].isin(constellations)]

        values = pd.to_numeric(subset[resource_name], errors="coerce")
        subset = subset[values.notna() & (values > 0)]
        return subset.assign(_density=values[values.notna() & (values > 0)]).sort_values(
            "_density", ascending=False
        )

    def best_direct_p2_planet(self, p2_product: str, constellations: list[str] | None = None) -> pd.Series | None:
        """
        Планета для сценария "прямое R0 -> P2" (правая часть CSV-матрицы).

        В v1 эта часть данных загружалась, но нигде не использовалась —
        это отдельный пункт Фазы 4 в docs/ROADMAP.md, сюда добавляется метод
        уже сейчас, чтобы структура не менялась задним числом.
        """
        raise NotImplementedError("TODO(Фаза 4): сценарий прямого R0->P2")


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Устранить опечатки в заголовках, привести имена колонок к виду из recipes.json."""
    return df.rename(columns=COLUMN_NAME_FIXES)


def _normalize_radius(df: pd.DataFrame) -> pd.DataFrame:
    """
    Привести колонку радиуса к числу.

    В исходном CSV радиус записан с пробелом-разделителем разрядов
    ("11 860"), в том числе с неразрывными и узкими неразрывными
    пробелами из Excel. Без очистки pandas читает колонку как строку,
    и любое сравнение радиуса молча даёт неверный результат.
    """
    if RADIUS_COLUMN not in df.columns:
        return df
    cleaned = (
        df[RADIUS_COLUMN]
        .astype(str)
        .str.replace("\u00a0", "", regex=False)   # неразрывный пробел
        .str.replace("\u202f", "", regex=False)   # узкий неразрывный пробел
        .str.replace(" ", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df[RADIUS_COLUMN] = pd.to_numeric(cleaned, errors="coerce")
    return df


def _clean_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Отфильтровать служебные/битые строки:
    - строки без значения Constellation (пустые разделители в исходном экспорте);
    - строку-легенду "max P2";
    - строки, где закрались ошибки формул Excel (#REF!).
    """
    df = df.dropna(subset=["Constellation"])
    df["Constellation"] = df["Constellation"].astype(str).str.strip()
    df = df[~df["Constellation"].isin(IGNORED_CONSTELLATION_VALUES)]
    df = df[~df["Constellation"].str.contains("#REF", case=False, na=False)]
    return df


def _load_from_csv(path: Path = DEFAULT_PLANETS_PATH) -> PlanetBook:
    """
    Загрузить и нормализовать data/planet_industry.csv напрямую из файла.

    Не публичный путь загрузки с 21.09.2026 (см. load_planets() ниже) —
    единственный оставшийся вызывающий код: `scripts/
    migrate_planets_csv_to_db.py`, переносящий CSV в БД один раз на
    окружение. Оставлена отдельной функцией (не удалена вместе с
    переключением на БД), чтобы не дублировать уже отлаженный парсинг
    (опечатки в заголовках, битые строки, радиус с пробелами-разделителями)
    во втором месте.
    """
    df = pd.read_csv(path, sep=";", skiprows=1, encoding="utf-8")
    df = _normalize_columns(df)
    df = _clean_rows(df)
    df = _normalize_radius(df)
    return PlanetBook(df)


def _expand_densities(rows: list[dict]) -> pd.DataFrame:
    """
    {"r0_densities": {...}, "p2_direct_densities": {...}, ...} по каждой
    строке -> один DataFrame с отдельной колонкой на каждый встретившийся
    ресурс (как раньше давал pd.read_csv на широкой таблице). Строится
    через pd.DataFrame(rows) с уже развёрнутыми словарями, а не
    pd.json_normalize, — проще, и число ресурсов в проекте небольшое
    (30+24), объединение словарей построчно не создаёт узкого места.
    """
    flat_rows = []
    for row in rows:
        flat = {k: v for k, v in row.items() if k not in ("r0_densities", "p2_direct_densities")}
        flat.update(row.get("r0_densities") or {})
        flat.update(row.get("p2_direct_densities") or {})
        flat_rows.append(flat)
    return pd.DataFrame(flat_rows)


# Фаза 2 мультирегиональности (21.09.2026) впервые заводит писателя
# regions/planets, работающего в ДРУГОМ ПРОЦЕССЕ (scripts/refresh_sde.py,
# запускается scripts/scheduler.py) одновременно с уже запущенным
# веб-процессом. Вечный @lru_cache (как было в Фазе 1) означал бы, что
# веб-процесс никогда не увидит новые регионы/планеты без ручного
# перезапуска — межпроцессный кэш сбросить нечем. TTL вместо вечного
# кэша — тот же порядок величины, что у других «протухающих через
# время» данных проекта (access-токен ESI ~20 мин, sync_colonies раз в
# 30 мин): правило 5 («справочники считаются раз на процесс») по-прежнему
# соблюдено, просто «раз на процесс» теперь ограничено этим временем,
# а не длится вечно.
PLANETS_CACHE_TTL_SECONDS = 900

_cache: tuple[PlanetBook, float] | None = None


def load_planets() -> PlanetBook:
    """
    Построить PlanetBook из таблиц БД `regions`/`planets` (Фаза 1
    мультирегиональности, 21.09.2026 — см. докстринг модуля), с TTL-
    кэшем на `PLANETS_CACHE_TTL_SECONDS` (см. комментарий выше).

    Собирает DataFrame ТОЙ ЖЕ формы, что раньше строилась из CSV: те же
    имена колонок (`Constellation`, `System`, `Planet`, `Type`,
    RADIUS_COLUMN, POCO_RATE_COLUMN, `POCO Owner`, плюс по одной колонке
    на каждый ресурс из r0_densities/p2_direct_densities), плюс новая
    колонка `Region` — её не было ни в одном CSV (данные покрывали один
    регион без явной пометки), поэтому PlanetBook.regions() всегда
    возвращал {}; теперь она есть по-честному, и уже существующий,
    ранее мёртвый код regions()/systems_in_regions() оживает без правки
    самого PlanetBook.

    Пустая БД или отсутствующие таблицы (свежий клон без `alembic upgrade
    head`/переноса) — PlanetBook с пустым DataFrame, не исключение:
    страница должна открыться, а честное "нет данных" сообщит уже
    вызывающий код (как и раньше для отсутствующего файла); тот же
    принцип, что у `domain/plan_storage.py::list_plans()`.
    """
    global _cache
    import time

    if _cache is not None and (time.monotonic() - _cache[1]) < PLANETS_CACHE_TTL_SECONDS:
        return _cache[0]

    book = _build_planet_book()
    _cache = (book, time.monotonic())
    return book


def _cache_clear() -> None:
    """Сбросить кэш немедленно — тесты (изоляция БД) и сами сборщики после записи."""
    global _cache
    _cache = None


load_planets.cache_clear = _cache_clear  # type: ignore[attr-defined]


def _build_planet_book() -> PlanetBook:
    """Собственно запрос к БД и сборка DataFrame — см. load_planets() выше."""
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    from infra.db import session_scope
    from infra.models import Planet as PlanetRow
    from infra.models import Region as RegionRow

    try:
        with session_scope() as session:
            query = (
                select(PlanetRow, RegionRow.name)
                .join(RegionRow, PlanetRow.region_id == RegionRow.id)
                .order_by(RegionRow.name, PlanetRow.system, PlanetRow.planet_number)
            )
            rows = [
                {
                    "Region": region_name,
                    "Constellation": planet.constellation,
                    "System": planet.system,
                    "Planet": float(planet.planet_number),
                    "Type": planet.planet_type,
                    RADIUS_COLUMN: planet.radius_km,
                    POCO_RATE_COLUMN: planet.poco_tax_rate,
                    "POCO Owner": planet.poco_owner,
                    "r0_densities": planet.r0_densities,
                    "p2_direct_densities": planet.p2_direct_densities,
                }
                for planet, region_name in session.execute(query)
            ]
    except OperationalError:
        rows = []

    if not rows:
        # Свежая БД без переноса (см. докстринг выше) — пустой DataFrame,
        # но с базовыми колонками, чтобы методы PlanetBook (обращающиеся
        # к ним по имени) вернули честные пустые результаты, а не упали
        # на KeyError.
        df = pd.DataFrame(columns=[
            "Region", "Constellation", "System", "Planet", "Type",
            RADIUS_COLUMN, POCO_RATE_COLUMN, "POCO Owner",
        ])
    else:
        df = _expand_densities(rows)
    return PlanetBook(df)
