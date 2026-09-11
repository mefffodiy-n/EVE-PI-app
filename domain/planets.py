"""
Загрузка данных по планетам региона: плотности сырья, радиус, POCO.

Источник данных: data/planet_industry.csv — перенесён из прошлой реализации
(там файл назывался "planet industry.csv", с пробелом в имени; в новой
структуре переименован и лежит в data/, само содержимое не меняется).

Формат файла (после служебной шапки из 2 строк):
    Constellation; System; Planet; Type; Radius [km]; POCO Tax Rate [%];
    POCO Owner; <34 колонки R0-ресурсов (плотность на планете)>;
    <28 колонок P2-ресурсов для сценария "прямое R0 -> P2">

ВАЖНО: в исходном CSV встречались опечатки в заголовках колонок
("Polyramids" вместо "Polyaramids", "Supertensil Plastics" вместо
"Supertensile Plastics" — см. roadmap.md, раздел 1). Эти опечатки
нормализуются здесь, а не патчатся точечно в местах использования.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
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
        это отдельный пункт Фазы 4 в roadmap.md, сюда добавляется метод
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


@lru_cache(maxsize=1)
def load_planets(path: Path = DEFAULT_PLANETS_PATH) -> PlanetBook:
    """
    Загрузить и нормализовать data/planet_industry.csv.

    В отличие от v1 (df читался прямо в глобальный STATIC_DATA dict в main.py
    при старте веб-приложения), здесь загрузка изолирована от веб-слоя —
    её можно вызывать и из тестов, и из воркеров синхронизации, и из API.
    """
    df = pd.read_csv(path, sep=";", skiprows=1, encoding="utf-8")
    df = _normalize_columns(df)
    df = _clean_rows(df)
    df = _normalize_radius(df)
    return PlanetBook(df)
