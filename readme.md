EVE PI Manager (PI Director)
English | Русский

🚀 English
Overview
EVE PI Manager (also known as PI Director) is an advanced logistics and automation web application designed for EVE Online players to efficiently manage Planetary Industry (PI)[cite: 1, 3] across multiple characters and systems. It combines ESI skill profiling, local JSON template management[cite: 3], concentrated resource scanning, and automated production chain planning with direct Excel export capabilities.

Key Features
Capsuleer-Style Interface: Designed with a strict, immersive dark-theme UI inspired by EVE Online terminals (featuring custom fonts, precise borders, and terminal aesthetics).

Automated Character Profiling: Simulates/reads ESI skill levels (such as Command Center Upgrades and Interplanetary Consolidation) to automatically assign high-skill characters to industrial processing and lower-skill characters to raw resource extraction.

Local JSON Templates: Supports configuration templates from repositories like EVE_PI_Templates to calculate CPU and Powergrid (PG) consumption accurately[cite: 3].

Constellation & System Logistics: Scans regional PI databases to locate optimal planetary hubs, applying advanced distance and radius penalties to prevent powergrid overloads.

Production Chain Generator: Recursively unfolds complex items (P2, P3, P4) down to raw P0/R0 materials using a built-in recipes.json database.

Excel Export with Native Dialog: Saves structured logistics plans (.xlsx) containing character nicknames, systems, input/output resources, and planet types using native system dialog windows.

Tech Stack
Backend: Python, Eel (bridging Python and web UI), Pandas (data processing)

Frontend: HTML5, Tailwind CSS, Google Fonts (Rajdhani, Share Tech Mono)

Data Formats: CSV (planet industry.csv[cite: 1]), JSON (recipes.json, template files[cite: 3])

🚀 Русский
Обзор
EVE PI Manager (PI Director) — это продвинутое веб-приложение для автоматизации и управления планетарной индустрией (PI)[cite: 1, 3] в EVE Online. Программа создана для удобного распределения персонажей (альтов) по системам и планетам, расчета мощностей по шаблонам, анализа концентрации ресурсов и генерации готовых логистических отчетов.

Ключевые особенности
Капсулирский интерфейс: Строгий темный дизайн в стиле игровых терминалов EVE Online (фирменные шрифты, неоновые акценты, угловые маркеры и панели мониторинга).

Профилирование персонажей: Автоматическое распределение ролей на основе навыков (Command Center Upgrades и Interplanetary Consolidation): персонажи с максимальными уровнями направляются на переработку, а развивающиеся альты — на добычу сырья[cite: 3].

Поддержка JSON-шаблонов: Чтение локальных шаблонов застройки (например, из репозитория EVE_PI_Templates) для точного расчета потребления CPU и Powergrid (PG)[cite: 3].

Логистический сканер созвездий: Поиск оптимальных систем для размещения производственных хабов с учетом штрафов на радиус планет (во избежание дефицита мощности линков).

Генератор производственных цепочек: Рекурсивный разбор сложных продуктов (P2, P3, P4) до базового сырья (P0/R0) на основе файла recipes.json.

Нативный экспорт в Excel: Сохранение структурированных отчетов (.xlsx) с выбором пути через стандартное системное окно. Отчет содержит ник персонажа, систему, входящие и исходящие ресурсы.

Технологический стек
Бэкенд: Python, Eel, Pandas

Фронтенд: HTML5, Tailwind CSS, Google Fonts (Rajdhani, Share Tech Mono)

Форматы данных: CSV (planet industry.csv[cite: 1]), JSON (recipes.json, локальные шаблоны[cite: 3])