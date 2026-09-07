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
        raise NotImplementedError("TODO(Фаза 1): sorted(df['Constellation'].unique())")

    def systems_in(self, constellations: list[str]) -> list[str]:
        raise NotImplementedError("TODO(Фаза 1): перенести логику из /api/systems main.py v1")

    def planets_with_resource(self, resource_name: str, constellations: list[str] | None = None) -> pd.DataFrame:
        """
        Планеты, где есть ненулевая плотность заданного R0-ресурса.

        Заменяет разрозненные dropna()/sort_values() вызовы, разбросанные
        по calculate_plan() в старом main.py.
        """
        raise NotImplementedError("TODO(Фаза 1)")

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
    при старте FastAPI-приложения), здесь загрузка изолирована от веб-слоя —
    её можно вызывать и из тестов, и из воркеров синхронизации, и из API.
    """
    df = pd.read_csv(path, sep=";", skiprows=1, encoding="utf-8")
    df = _normalize_columns(df)
    df = _clean_rows(df)
    return PlanetBook(df)
