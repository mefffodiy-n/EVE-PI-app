"""
Аккаунт браузерного визита — единая точка разделения данных между
пользователями (найдено 11.09.2026: разные реальные игроки, вошедшие
через EVE SSO, видели персонажей и колонии друг друга — таблицы
`characters`/`colonies`/`plans` читались целиком, без учёта того, кто
именно сейчас смотрит страницу).

account_id — случайная строка в подписанной cookie сессии Flask, не
привязанная к конкретному EVE-персонажу: один визит может войти
несколькими персонажами (альтами), все они получают один и тот же
account_id при входе (см. api/blueprints/auth.py::_store()). Без входа
через SSO account_id отсутствует — анонимный визит честно не видит
ничьих данных, а не видит все (правило 1: правдоподобное, но чужое,
хуже честного пробела).
"""

from __future__ import annotations

import secrets

from flask import session


def current_account_id() -> str | None:
    """ID аккаунта текущего визита, если он хоть раз входил через SSO."""
    return session.get("account_id")


def ensure_account_id() -> str:
    """
    Вызывается только из успешного /api/auth/callback: если у визита ещё
    нет account_id — завести. Дальше все персонажи, добавленные в ЭТОМ
    визите (в том числе другие альты), получают один и тот же account_id.
    """
    account_id = session.get("account_id")
    if not account_id:
        account_id = secrets.token_hex(16)
        session["account_id"] = account_id
        session.permanent = True
    return account_id
