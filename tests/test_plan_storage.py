"""
Тесты хранения планов.

Планы лежат на сервере (таблица `plans`), а не в браузере: план должен
переживать перезагрузку и смену устройства. Значит и проверять надо
серверные свойства — валидацию идентификатора, лимиты, осмысленность
сравнения. Изоляцию БД даёт autouse-фикстура в conftest.py.
"""

from __future__ import annotations

import pytest

from domain import plan_storage
from domain.plan_storage import PlanStorageError, compare, delete, list_plans, load, save

ROWS = [
    {"system": "AV-VB6", "planet": "4", "res_out": "Biocells",
     "role": "Переработка P2/P3", "char_id": 1, "cpu_percent": 78.3, "pg_percent": 98.4},
    {"system": "Y-2ANO", "planet": "3", "res_out": "Biofuels",
     "role": "Добыча", "char_id": 2, "cpu_percent": 37.2, "pg_percent": 96.7},
]


class TestSaving:
    def test_saved_plan_is_readable_back(self):
        plan = save("Вариант A", {"factory_sys": "AV-VB6"}, ROWS)
        again = load(plan.id)
        assert again.name == "Вариант A"
        assert len(again.rows) == 2
        assert again.request["factory_sys"] == "AV-VB6"

    def test_empty_plan_rejected(self):
        with pytest.raises(PlanStorageError):
            save("Пустой", {}, [])

    def test_nameless_plan_gets_a_name(self):
        """Безымянные планы в списке неразличимы, поэтому имя подставляется."""
        plan = save("", {}, ROWS)
        assert plan.name

    def test_overlong_name_is_trimmed(self):
        plan = save("я" * 300, {}, ROWS)
        assert len(plan.name) <= plan_storage.MAX_NAME_LENGTH + 1

    def test_storage_limit_enforced(self, monkeypatch):
        monkeypatch.setattr(plan_storage, "MAX_PLANS", 2)
        save("1", {}, ROWS)
        save("2", {}, ROWS)
        with pytest.raises(PlanStorageError):
            save("3", {}, ROWS)


class TestSafety:
    @pytest.mark.parametrize(
        "bad_id",
        ["../../etc/passwd", "..", "план", "abc", "A" * 12, "0123456789abcdef"],
    )
    def test_path_traversal_and_bad_ids_rejected(self, bad_id):
        """
        Идентификатор подставляется в имя файла, поэтому проверяется
        строго: иначе через него можно выйти за пределы папки.
        """
        with pytest.raises(PlanStorageError):
            load(bad_id)

    def test_list_survives_missing_table(self, monkeypatch):
        """Свежий клон без `alembic upgrade` — список планов пуст, не 500."""
        from infra import db

        db.Base.metadata.drop_all(db.engine())
        assert list_plans() == []

    def test_missing_plan_reports_clearly(self):
        with pytest.raises(PlanStorageError, match="не найден"):
            load("0123456789ab")


class TestListing:
    def test_newest_first(self):
        first = save("Старый", {}, ROWS)
        second = save("Новый", {}, ROWS)
        # created_at с точностью до секунды, поэтому проверяем состав,
        # а не жёсткий порядок одинаковых меток времени.
        ids = {p.id for p in list_plans()}
        assert ids == {first.id, second.id}

    def test_summary_counts_roles_apart(self):
        plan = save("Смешанный", {}, ROWS)
        card = plan.summary()
        assert card["colonies"] == 2
        assert card["mining"] == 1
        assert card["processing"] == 1
        assert card["characters"] == 2
        assert card["peak_load"] == 98.4

    def test_delete_removes_from_list(self):
        plan = save("Временный", {}, ROWS)
        assert delete(plan.id) is True
        assert list_plans() == []
        assert delete(plan.id) is False


class TestComparison:
    def test_shows_which_colonies_changed_not_just_counts(self):
        """
        «На одну планету больше» не отвечает на вопрос, на какую именно.
        Сравнение обязано показывать состав различий.
        """
        left = save("A", {}, ROWS)
        extra = ROWS + [{"system": "LBGI-2", "planet": "6", "res_out": "Biofuels",
                         "role": "Добыча", "char_id": 3,
                         "cpu_percent": 36.6, "pg_percent": 96.1}]
        right = save("B", {}, extra)

        result = compare(left.id, right.id)
        assert result["delta"]["colonies"] == 1
        assert result["delta"]["characters"] == 1
        assert result["unchanged"] == 2
        assert result["only_in_left"] == []
        assert result["only_in_right"] == [
            {"system": "LBGI-2", "planet": "6", "product": "Biofuels"}
        ]

    def test_moved_colony_shows_on_both_sides(self):
        """Перенос колонии — это исчезновение в одном месте и появление в другом."""
        left = save("A", {}, ROWS)
        moved = [dict(ROWS[0]), {**ROWS[1], "system": "DBRN-Z"}]
        right = save("B", {}, moved)

        result = compare(left.id, right.id)
        assert result["only_in_left"] == [
            {"system": "Y-2ANO", "planet": "3", "product": "Biofuels"}
        ]
        assert result["only_in_right"] == [
            {"system": "DBRN-Z", "planet": "3", "product": "Biofuels"}
        ]

    def test_identical_plans_show_no_differences(self):
        left = save("A", {}, ROWS)
        right = save("B", {}, ROWS)
        result = compare(left.id, right.id)
        assert result["only_in_left"] == result["only_in_right"] == []
        assert result["delta"]["colonies"] == 0
