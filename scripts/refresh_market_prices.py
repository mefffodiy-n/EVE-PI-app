"""
Сборщик рыночных цен на продукцию планетарного производства.

ЗАЧЕМ ОТДЕЛЬНЫМ СКРИПТОМ. Правило проекта: пользователь не инициирует
обращений наружу. В первой версии кнопка «SYNCING FUZZWORK» заставляла
сервер идти на market.fuzzwork.co.uk прямо в обработчике запроса —
при десятке одновременных пользователей это десяток запросов к чужому
сервису. Здесь цены собираются по расписанию, а приложение читает снимок.

ИСТОЧНИК. market.fuzzwork.co.uk/aggregates/ — сводка ордеров по региону,
один запрос сразу на все type_id. Это не ESI: ESI отдаёт ордера
постранично, и на сотню товаров ушли бы сотни запросов. Fuzzwork —
сторонний сервис, поэтому обращение к нему изолировано в этом файле:
если он исчезнет, менять надо только здесь.

ЧТО СОХРАНЯЕТСЯ. Цена покупки (buy max) и продажи (sell min) по
торговому узлу. Для оценки выгоды берётся buy: это то, за что товар
можно продать немедленно, не выставляя ордер и не ожидая. Оценка
получается консервативной, и это правильнее — завышенные ожидания
хуже заниженных.

Запуск вручную:
    python -m scripts.refresh_market_prices

По расписанию (раз в 30-60 минут: цены на PI меняются медленно,
а нагружать чужой сервис незачем):
    Linux, cron:  0 * * * * cd /path/to/app && python -m scripts.refresh_market_prices
    Windows:      то же действие в планировщике заданий раз в час.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
SNAPSHOT = CACHE_DIR / "market_prices.json"
TYPE_IDS_PATH = ROOT / "data" / "type_ids.json"

FUZZWORK_URL = "https://market.fuzzwork.co.uk/aggregates/"


def _user_agent() -> str:
    """Тот же User-Agent, что у остальных обращений: см. version.py."""
    import sys
    sys.path.insert(0, str(ROOT))
    from version import user_agent
    return user_agent()


USER_AGENT = _user_agent()
TIMEOUT_SECONDS = 20

# Торговые узлы: id станции или региона, как их принимает Fuzzwork.
# Jita 4-4 — станция, а не регион: цены в самом узле точнее средних
# по The Forge, где встречаются ордера из глухих систем.
STATIONS = {
    "jita": 60003760,      # Jita IV - Moon 4 - Caldari Navy Assembly Plant
}
DEFAULT_STATION = "jita"

# Fuzzwork принимает список type_id в строке запроса. Слишком длинный
# адрес отвергнут сервером, поэтому режем на части.
CHUNK_SIZE = 100


def pi_type_ids() -> dict[str, int]:
    """
    type_id продукции PI из статического снимка.

    Служебные ключи (структуры, планеты, командные центры) пропускаем:
    они не торгуются как продукция и в оценке выгоды не участвуют.
    """
    if not TYPE_IDS_PATH.is_file():
        raise FileNotFoundError(
            "Нет data/type_ids.json. Он нужен, чтобы знать, какие товары "
            "запрашивать. Соберите его: python -m scripts.extract_schematics --write"
        )
    raw = json.loads(TYPE_IDS_PATH.read_text(encoding="utf-8"))
    return {
        name: int(type_id)
        for name, type_id in raw.items()
        if not name.startswith(("_", "structure:", "planet:")) and "Command Center" not in name
    }


def _chunks(items: list[int], size: int) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def fetch_prices(type_ids: list[int], station: int, opener=None) -> dict[int, dict]:
    """
    Забрать сводку ордеров.

    opener передаётся в тестах, чтобы проверить разбор ответа, не выходя
    в сеть. В обычной работе используется стандартный urllib.
    """
    result: dict[int, dict] = {}
    for chunk in _chunks(sorted(type_ids), CHUNK_SIZE):
        query = urllib.parse.urlencode(
            {"station": station, "types": ",".join(str(t) for t in chunk)}
        )
        url = f"{FUZZWORK_URL}?{query}"

        if opener is not None:
            payload = opener(url)
        else:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))

        for key, entry in payload.items():
            buy = (entry or {}).get("buy") or {}
            sell = (entry or {}).get("sell") or {}
            result[int(key)] = {
                "buy_max": _number(buy.get("max")),
                "sell_min": _number(sell.get("min")),
                "buy_volume": _number(buy.get("volume")),
                "sell_volume": _number(sell.get("volume")),
            }
    return result


def _number(value) -> float | None:
    """Fuzzwork отдаёт числа строками; пустой рынок даёт 0 — это не цена."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def save(prices: dict[str, dict], station_key: str, error: str | None = None) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "station": station_key,
        "station_id": STATIONS[station_key],
        "source": FUZZWORK_URL,
        "prices": prices,
    }
    if error:
        snapshot["error"] = error
    SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")


def main(station_key: str | None = None) -> int:
    """
    Собрать цены.

    Узел передаётся аргументом, а не читается из sys.argv напрямую:
    иначе при вызове из планировщика сюда попадали его собственные
    флаги — «--once» принимался за название торгового узла.
    """
    if station_key is None:
        station_key = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STATION
    if station_key not in STATIONS:
        print(f"Неизвестный торговый узел: {station_key}. Известные: {', '.join(STATIONS)}")
        return 2

    try:
        names = pi_type_ids()
    except FileNotFoundError as exc:
        print(exc)
        return 2

    by_id = {type_id: name for name, type_id in names.items()}
    print(f"Запрашиваю цены на {len(by_id)} товаров в узле {station_key}…")

    try:
        raw = fetch_prices(list(by_id), STATIONS[station_key])
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Прошлый снимок не трогаем: устаревшие цены полезнее их отсутствия,
        # а возраст снимка приложение показывает отдельно.
        print(f"Не удалось получить цены: {type(exc).__name__}: {exc}")
        print("Прошлый снимок сохранён без изменений.")
        return 1

    prices = {
        by_id[type_id]: entry
        for type_id, entry in raw.items()
        if type_id in by_id
    }
    save(prices, station_key)

    with_price = sum(1 for e in prices.values() if e["buy_max"])
    print(f"Сохранено: {SNAPSHOT}")
    print(f"  товаров в ответе: {len(prices)}")
    print(f"  с ценой покупки:  {with_price}")
    if with_price < len(prices):
        print(f"  без цены покупки: {len(prices) - with_price} — на них нет ордеров в узле")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
