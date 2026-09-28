"""
Кэширование ответов и вспомогательные функции.

Задача модуля — выполнить требование «приложение не должно сильно
нагружать сервер при большом количестве пользователей».

Почему это важно именно для Flask: он синхронный (WSGI). Пока обработчик
считает план, воркер занят целиком и не обслуживает никого другого.
Значит, работу надо либо не делать вовсе (отдать из кэша), либо делать
один раз на процесс.

Три уровня, от дешёвого к дорогому:

  1. СТАТИКА НА ПРОЦЕСС. Справочники (констелляции, продукты, пороги
     радиусов) не зависят от запроса вообще. Считаются один раз при
     первом обращении и живут в памяти процесса.

  2. ETag. Ответ справочника не меняется, пока не поменялись данные,
     поэтому отдаём ETag и на повторный запрос отвечаем 304 без тела.
     Браузер и обратный прокси перестают дёргать расчёт совсем.

  3. КЭШ ПО ПАРАМЕТРАМ. Расчёт плана дорогой, но детерминированный:
     одни и те же входные данные дают один и тот же результат. Разные
     пользователи с одинаковым выбором (а он ограничен списком
     констелляций и продуктов) получают ответ из кэша.

ОГРАНИЧЕНИЕ, о котором надо помнить: кэш живёт в памяти ОДНОГО процесса.
При нескольких воркерах gunicorn каждый прогревается сам. Это осознанный
компромисс: он ничего не стоит и не требует Redis. Если воркеров станет
много или появится реальная нагрузка — выносить в Redis, но не раньше,
чем это подтвердится измерением.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from functools import wraps
from threading import Lock
from typing import Any, Callable

from flask import jsonify, request

from api.errors import Err, render_error, request_lang


class LruResultCache:
    """
    Потокобезопасный LRU-кэш результатов, с TTL на запись.

    Не functools.lru_cache, потому что нужен контроль над размером в
    элементах и над временем жизни записи.

    НАЙДЕНО (внешняя рецензия кода, перепроверено grep'ом по
    репозиторию): `clear()` НИКОГДА не вызывалась — ни одним сборщиком,
    ни одним обработчиком, нигде, хотя её докстринг прямо утверждал
    обратное («вызывается сборщиками после обновления данных»). На
    практике это означало: после `sync_character_skills` (персонаж
    прокачал CCU/IC в игре) тот же запрос с теми же параметрами получал
    ИЗ КЭША план, посчитанный на СТАРЫХ скиллах — до вытеснения записи
    из LRU (256 записей) или перезапуска процесса, то есть потенциально
    неделями на боевом сервере. Тот же класс бага, что уже чинили у
    `domain/planets.py::load_planets()` неделей раньше (там кэш тоже
    был не вечным изначально, а обнаружился при добавлении второго
    писателя данных) — здесь кэш был "вечным" с самого начала.

    Исправление — TTL на запись (`ttl_seconds`), тот же принцип и тот
    же порядок величины, что и `PLANETS_CACHE_TTL_SECONDS` в
    `domain/planets.py` (900с): план не может быть старше того времени,
    за которое реально обновляются его входные данные (скиллы
    персонажей — раз в 6 часов по расписанию, но пользователь мог
    синхронизировать раньше вручную; справочник планет — TTL 900с там
    же). Не версия данных в ключе (второй вариант из рецензии) — она
    потребовала бы протаскивать `updated_at` через несколько слоёв и
    держать её синхронной с ЛЮБым будущим источником входных данных
    плана; TTL проще, самодостаточен и не может «забыться» так же, как
    забылась ручная инвалидация.
    """

    def __init__(self, maxsize: int = 128, ttl_seconds: float | None = None):
        self._maxsize = maxsize
        self._ttl_seconds = ttl_seconds
        self._data: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    def _is_expired(self, stored_at: float) -> bool:
        return self._ttl_seconds is not None and (time.monotonic() - stored_at) >= self._ttl_seconds

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._data:
                value, stored_at = self._data[key]
                if not self._is_expired(stored_at):
                    self._data.move_to_end(key)
                    self.hits += 1
                    return value
                del self._data[key]  # протухла — считаем как промах, не как хит
            self.misses += 1

        # Считаем ВНЕ блокировки: иначе один долгий расчёт заблокирует
        # все остальные запросы к кэшу, что ровно противоположно цели.
        value = compute()

        with self._lock:
            self._data[key] = (value, time.monotonic())
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
        return value

    def clear(self) -> None:
        """Немедленный сброс — тесты (изоляция) и ручное обслуживание."""
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._data), "hits": self.hits, "misses": self.misses}


# Планы: разных комбинаций «констелляции + продукты + система» немного,
# поэтому небольшого кэша достаточно. TTL — тот же порядок величины,
# что у domain/planets.py::PLANETS_CACHE_TTL_SECONDS (900с) — план не
# должен пережить пересинхронизацию скиллов/справочника планет надолго
# (см. докстринг класса выше).
PLAN_CACHE_TTL_SECONDS = 900
plan_cache = LruResultCache(maxsize=256, ttl_seconds=PLAN_CACHE_TTL_SECONDS)


def cache_key(*parts: Any) -> str:
    """Стабильный ключ из произвольных JSON-совместимых частей."""
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def json_ok(**payload) -> Any:
    """Успешный ответ в формате, который ожидает web/index.html."""
    return jsonify(status="success", **payload)


def json_error(error: Any, status: int = 400, **params: Any):
    """
    Ответ с ошибкой. `error` — код из api/errors.py (плюс параметры) либо
    объект с `.code`/`.params` (Err, PlanStorageError); текст собирается
    на языке запроса (правило 9).
    """
    if isinstance(error, str):
        code = error
    else:
        code, params = error.code, {**getattr(error, "params", {}), **params}
    return jsonify(status="error", message=render_error(code, request_lang(), **params)), status


def with_etag(view: Callable) -> Callable:
    """
    Добавить ETag к ответу и отвечать 304 на повторный запрос.

    Применяется только к справочникам — данным, которые не зависят от
    пользователя и меняются редко. Это самый дешёвый способ снять
    нагрузку: сервер вообще не формирует тело ответа.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        response = view(*args, **kwargs)
        # Обработчик мог вернуть кортеж (body, code) — тогда ETag не ставим.
        if isinstance(response, tuple):
            return response
        response.add_etag()
        return response.make_conditional(request)

    return wrapper


def parse_json_body(required: dict[str, type]) -> tuple[dict | None, Err | None]:
    """
    Разобрать и проверить тело JSON-запроса.

    Замена pydantic-моделей из FastAPI-версии. Специально сделано
    вручную и минимально: единственная задача — не пустить в domain-слой
    мусор, а не строить полноценную схему валидации.

    Возвращает (данные, None) либо (None, Err) — её принимает json_error.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, Err("body_not_object")

    for field, expected in required.items():
        if field not in payload:
            return None, Err("field_missing", field=field)
        if not isinstance(payload[field], expected):
            return None, Err(
                "field_type", field=field, expected=expected.__name__,
                actual=type(payload[field]).__name__,
            )
    return payload, None
