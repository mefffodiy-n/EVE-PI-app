"""
Каталог сообщений об ошибках API на двух языках (правило 9).

Обработчик и domain-слой называют ошибку кодом и параметрами, а текст
на языке запроса собирается здесь. Язык берётся из поля `lang` тела
JSON, из `?lang=` или из заголовка Accept-Language (его ставит фронтенд
на каждый запрос к API); по умолчанию — русский, как раньше.
"""

from __future__ import annotations

from typing import Any

from flask import request

_CATALOG: dict[str, dict[str, str]] = {
    "body_not_object": {
        "ru": "Ожидается JSON-объект в теле запроса",
        "en": "A JSON object is expected in the request body",
    },
    "field_missing": {
        "ru": "Отсутствует обязательное поле '{field}'",
        "en": "Required field '{field}' is missing",
    },
    "field_type": {
        "ru": "Поле '{field}' должно быть типа {expected}, получено {actual}",
        "en": "Field '{field}' must be of type {expected}, got {actual}",
    },
    "field_not_list": {
        "ru": "Поле '{field}' должно быть списком",
        "en": "Field '{field}' must be a list",
    },
    "too_many_rows": {
        "ru": "Слишком много строк в '{field}': {count}, максимум {limit}",
        "en": "Too many rows in '{field}': {count}, maximum is {limit}",
    },
    "items_not_objects": {
        "ru": "Каждый элемент '{field}' должен быть объектом",
        "en": "Every item of '{field}' must be an object",
    },
    "region_not_found": {"ru": "Регион не найден", "en": "Region not found"},
    "planet_not_found": {"ru": "Планета не найдена", "en": "Planet not found"},
    "file_missing": {"ru": "Нет файла ('file')", "en": "No file ('file') attached"},
    "csv_unreadable": {
        "ru": "Не удалось разобрать CSV: {detail}",
        "en": "Could not parse the CSV: {detail}",
    },
    "sso_not_configured": {
        "ru": "Вход через EVE SSO не настроен: не задано {missing}. "
              "Зарегистрируйте приложение на developers.eveonline.com.",
        "en": "EVE SSO login is not configured: {missing} is not set. "
              "Register the application at developers.eveonline.com.",
    },
    "no_visit_characters": {
        "ru": "Нет персонажей этого визита — сначала войдите через EVE SSO",
        "en": "No characters in this visit — sign in with EVE SSO first",
    },
    "character_not_in_visit": {
        "ru": "Указанный персонаж не входит в число вошедших в этом визите",
        "en": "The specified character is not among those signed in during this visit",
    },
    "groups_conflict": {
        "ru": "Часть этих персонажей уже состоит в разных постоянных группах — "
              "сначала отвяжите их и войдите заново",
        "en": "Some of these characters already belong to different permanent groups — "
              "unlink them first and sign in again",
    },
    "character_not_found": {"ru": "Персонаж не найден", "en": "Character not found"},
    "character_not_unlinkable": {
        "ru": "Этого персонажа нельзя отвязать",
        "en": "This character cannot be unlinked",
    },
    "nothing_to_export": {
        "ru": "Нечего экспортировать: нет ни плана, ни колоний",
        "en": "Nothing to export: there is neither a plan nor colonies",
    },
    "no_constellations": {
        "ru": "Не выбрано ни одной констелляции",
        "en": "No constellation selected",
    },
    "no_home_system": {
        "ru": "Не выбрана домашняя система",
        "en": "No home system selected",
    },
    "no_targets": {
        "ru": "Не выбрано ни одного целевого продукта",
        "en": "No target product selected",
    },
    "too_many_targets": {
        "ru": "Слишком много целевых продуктов: {count}. Максимум {limit} за один расчёт.",
        "en": "Too many target products: {count}. The maximum is {limit} per calculation.",
    },
    "no_characters": {
        "ru": "Нет персонажей — вместимость считать не от чего",
        "en": "No characters — there is nothing to compute capacity from",
    },
    "plan_rows_not_objects": {
        "ru": "Каждая строка плана должна быть объектом",
        "en": "Every plan row must be an object",
    },
    "colonies_not_objects": {
        "ru": "Каждая колония должна быть объектом",
        "en": "Every colony must be an object",
    },
    "plan_not_found_short": {"ru": "План не найден", "en": "Plan not found"},
    "compare_ids_required": {
        "ru": "Нужны оба идентификатора: left и right",
        "en": "Both identifiers are required: left and right",
    },
    "constellations_empty": {
        "ru": "Список констелляций пуст",
        "en": "The constellation list is empty",
    },
    "system_missing": {"ru": "Не указана система", "en": "No system specified"},
    "game_template_none": {
        "ru": "Для продукта «{product}» нет игрового шаблона",
        "en": "There is no in-game template for \"{product}\"",
    },
    "game_template_planet_unknown": {
        "ru": "Не удалось подобрать шаблон под тип планеты «{planet_type}»",
        "en": "Could not build a template for planet type \"{planet_type}\"",
    },
    "game_template_planet_mismatch": {
        "ru": "Шаблон добычи «{product}» сохранён под планету типа {template_type} и привязан к её "
              "сырью — для {planet_type} его не переделать",
        "en": "The \"{product}\" mining template is saved for a {template_type} planet and tied to its "
              "resource — it cannot be adapted to {planet_type}",
    },
    "ccu_out_of_range": {
        "ru": "Уровень Command Center Upgrades должен быть от 0 до 5",
        "en": "Command Center Upgrades level must be between 0 and 5",
    },
    "plan_bad_id": {
        "ru": "Недопустимый идентификатор плана: {plan_id}",
        "en": "Invalid plan identifier: {plan_id}",
    },
    "plan_login_required": {
        "ru": "Войдите через EVE SSO, чтобы сохранять планы",
        "en": "Sign in with EVE SSO to save plans",
    },
    "plan_empty": {
        "ru": "Пустой план сохранять нечего",
        "en": "There is nothing to save in an empty plan",
    },
    "plan_too_big": {
        "ru": "Слишком большой план: {count} строк, максимум {limit}",
        "en": "Plan is too large: {count} rows, maximum is {limit}",
    },
    "plan_limit": {
        "ru": "Сохранено уже {count} планов, это предел. "
              "Удалите ненужные, чтобы освободить место.",
        "en": "{count} plans are already saved, which is the limit. "
              "Delete unneeded ones to free up space.",
    },
    "plan_gone": {
        "ru": "План не найден — возможно, он был удалён",
        "en": "Plan not found — it may have been deleted",
    },
}


class Err:
    """Ошибка как код + параметры — то, что json_error умеет отрисовать."""

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params


def request_lang() -> str:
    """Язык ответа: тело JSON, ?lang=, Accept-Language; по умолчанию ru."""
    raw = None
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        raw = body.get("lang")
    raw = raw or request.args.get("lang") or request.headers.get("Accept-Language") or "ru"
    return "en" if str(raw).strip().lower().startswith("en") else "ru"


def render_error(code: str, lang: str, **params: Any) -> str:
    entry = _CATALOG.get(code)
    if entry is None:
        return code
    try:
        return entry[lang].format(**params)
    except (KeyError, IndexError):
        return entry[lang]
