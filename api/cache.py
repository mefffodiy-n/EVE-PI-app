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
from collections import OrderedDict
from functools import wraps
from threading import Lock
from typing import Any, Callable

from flask import jsonify, request


class LruResultCache:
    """
    Потокобезопасный LRU-кэш результатов.

    Не functools.lru_cache, потому что нужен контроль над размером в
    элементах и возможность инвалидации из фоновых задач (когда сборщики
    обновят данные, кэш надо сбросить).
    """

    def __init__(self, maxsize: int = 128):
        self._maxsize = maxsize
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1

        # Считаем ВНЕ блокировки: иначе один долгий расчёт заблокирует
        # все остальные запросы к кэшу, что ровно противоположно цели.
        value = compute()

        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
        return value

    def clear(self) -> None:
        """Вызывается сборщиками после обновления данных (Фаза 2)."""
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._data), "hits": self.hits, "misses": self.misses}


# Планы: разных комбинаций «констелляции + продукты + система» немного,
# поэтому небольшого кэша достаточно.
plan_cache = LruResultCache(maxsize=256)


def cache_key(*parts: Any) -> str:
    """Стабильный ключ из произвольных JSON-совместимых частей."""
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def json_ok(**payload) -> Any:
    """Успешный ответ в формате, который ожидает web/index.html."""
    return jsonify(status="success", **payload)


def json_error(message: str, code: int = 400):
    return jsonify(status="error", message=message), code


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


def parse_json_body(required: dict[str, type]) -> tuple[dict | None, str | None]:
    """
    Разобрать и проверить тело JSON-запроса.

    Замена pydantic-моделей из FastAPI-версии. Специально сделано
    вручную и минимально: единственная задача — не пустить в domain-слой
    мусор, а не строить полноценную схему валидации.

    Возвращает (данные, None) либо (None, текст ошибки).
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, "Ожидается JSON-объект в теле запроса"

    for field, expected in required.items():
        if field not in payload:
            return None, f"Отсутствует обязательное поле '{field}'"
        if not isinstance(payload[field], expected):
            return None, (
                f"Поле '{field}' должно быть типа {expected.__name__}, "
                f"получено {type(payload[field]).__name__}"
            )
    return payload, None
