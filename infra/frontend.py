"""Сборка исходника интерфейса: index.html + app.css + app.js в один текст."""

from __future__ import annotations

from pathlib import Path

_LINK = '<link rel="stylesheet" href="app.css">'
_SCRIPT = '<script src="app.js"></script>'


def read_frontend_source(web_dir: Path) -> str:
    """
    index.html с подставленными inline-версиями стилей и скрипта.

    Браузер получает три файла, а проверки (тесты целостности, справка,
    диагностика) читают их как единый документ, каким интерфейс был раньше.
    """
    text = (web_dir / "index.html").read_text(encoding="utf-8")
    css_path, js_path = web_dir / "app.css", web_dir / "app.js"
    if _LINK in text and css_path.is_file():
        text = text.replace(_LINK, "<style>\n" + css_path.read_text(encoding="utf-8") + "</style>")
    if _SCRIPT in text and js_path.is_file():
        text = text.replace(_SCRIPT, "<script>\n" + js_path.read_text(encoding="utf-8") + "</script>")
    return text
