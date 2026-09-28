"""
Тесты api/cache.py::LruResultCache.

НАЙДЕНО 28.09.2026 (внешняя рецензия кода, перепроверено grep'ом):
`plan_cache.clear()` не вызывалась НИГДЕ в проекте, хотя докстринг
`clear()` прямо утверждал, что её вызывают сборщики после обновления
данных. На практике план, посчитанный на старых скиллах персонажа
(CCU/IC), мог отдаваться из кэша сколь угодно долго после
`sync_character_skills` — до вытеснения записи из LRU или перезапуска
процесса. Исправлено TTL на запись — эти тесты фиксируют новое
поведение и главное: что запись реально протухает, а не живёт вечно.
"""

from __future__ import annotations

import time

import pytest

from api.cache import LruResultCache, cache_key


class TestTtl:
    def test_returns_cached_value_before_ttl_expires(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        cache = LruResultCache(ttl_seconds=900)
        calls = []
        compute = lambda: calls.append(1) or "first"

        assert cache.get_or_compute("k", compute) == "first"
        clock[0] += 899
        assert cache.get_or_compute("k", compute) == "first"
        assert len(calls) == 1  # второй вызов — из кэша, compute() не звался снова

    def test_recomputes_after_ttl_expires(self, monkeypatch):
        """
        Ровно тот баг, что был найден: раньше запись не протухала
        никогда, и повторный запрос с теми же параметрами после
        обновления входных данных (например, скиллов персонажа) молча
        отдавал старый результат.
        """
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        cache = LruResultCache(ttl_seconds=900)
        results = iter(["stale", "fresh"])

        assert cache.get_or_compute("k", lambda: next(results)) == "stale"
        clock[0] += 901
        assert cache.get_or_compute("k", lambda: next(results)) == "fresh"

    def test_no_ttl_means_cache_forever_by_default(self, monkeypatch):
        """Явный ttl_seconds=None (умолчание) — прежнее поведение, для других возможных потребителей класса."""
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        cache = LruResultCache()  # ttl_seconds не передан
        calls = []
        compute = lambda: calls.append(1) or "value"

        cache.get_or_compute("k", compute)
        clock[0] += 10**9
        cache.get_or_compute("k", compute)
        assert len(calls) == 1

    def test_expired_entry_counts_as_miss_not_hit(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        cache = LruResultCache(ttl_seconds=900)
        cache.get_or_compute("k", lambda: "v")
        clock[0] += 901
        cache.get_or_compute("k", lambda: "v")

        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 2


class TestLru:
    def test_evicts_oldest_when_maxsize_exceeded(self):
        cache = LruResultCache(maxsize=2)
        cache.get_or_compute("a", lambda: 1)
        cache.get_or_compute("b", lambda: 2)
        cache.get_or_compute("c", lambda: 3)  # вытесняет "a"

        calls = []
        cache.get_or_compute("a", lambda: calls.append(1) or 99)
        assert calls == [1]  # "a" пересчитан — его правда вытеснили

    def test_clear_forces_immediate_recompute(self):
        cache = LruResultCache()
        cache.get_or_compute("k", lambda: "first")
        cache.clear()
        assert cache.get_or_compute("k", lambda: "second") == "second"


class TestCacheKey:
    def test_order_independent_for_same_data(self):
        assert cache_key(["a", "b"], "x") == cache_key(["a", "b"], "x")
        assert cache_key(["a", "b"], "x") != cache_key(["b", "a"], "x")
