# PI Director

*[Русский](readme.md) | English*

A planetary industry planner for EVE Online. Works out how many colonies
of which kind you need for the products you pick, assigns them to
characters, and shows exactly where the plan hits its limits — character
count, planet size, skill levels.

Built on real data: recipes and costs are cross-checked against four
sources (DalShooth's templates, their README, eve-webtools.com, the EVE
University wiki), and unverified numbers are honestly flagged, never
passed off as exact (`docs/DOMAIN.md`). Logging in with EVE SSO pulls a
character's real colonies — what's actually built in game, not just the
computed plan.

**The plan calculation currently works for the Fountain region only** —
resource density and planet radius data only exist for it
(`data/planet_industry.csv`). Radius and planet type do exist in CCP's
official SDE for any region, but extraction density doesn't: neither ESI
nor the SDE expose it, the only way to find out is in-game scanning or a
verified third-party database (see "Planned" below) — extending to
another region needs the same kind of manual export.

## What it does

- Builds a P0→P4 production chain for the products you pick, works out
  how many colonies and characters you need and where to place them
  (region, constellations, home system for processing).
- Picks planets for processing by radius and type (P4 requires
  Barren/Temperate — a game rule, not just a preference), and warns
  honestly when not enough suitable planets exist.
- Computes command centre CPU/Power load against the cap, launchpad
  capacity, and link cost — from a verified model, not a rough guess.
- Ranks products by ISK per colony-hour and suggests what's more
  profitable to produce at the same cost.
- Computes a monthly profitability forecast with POCO tax — for the
  computed plan (revenue and export+import tax at every move along the
  chain) and separately, by actual output, for real synced colonies
  (real current output rate, tax on export only from each planet). The
  rate is filled in automatically from the region's data export for that
  specific planet; a planet with an unknown rate is honestly excluded
  from the calculation rather than treated as tax-free.
- Stores and compares saved plans — they survive a page reload and a
  change of device.
- Can skip extraction entirely — buy all P1 on the market (at the price
  from `refresh_market_prices.py`'s snapshot) and build only the
  processing colonies P2→P4; a monthly raw-material shopping list with
  prices as of plan build shows up both on the dashboard and in the
  Excel export.
- Connects to EVE SSO: pulls a character's real skills and colonies on a
  schedule, shows real extractor timers and an honest projection of
  factory state from the routes between pins (ESI only updates a factory
  when the colony is opened in game — the app projects the rest forward
  itself, without guessing).
- Warns about extraction deficits and about raw material running out in
  a processing colony's launchpad.
- Bilingual interface (RU/EN), no build step — a single file,
  `web/index.html`.

The full list is the "Help" section inside the app itself; what the
program **doesn't know** is listed right there too, honestly, not by
silence.

## Requirements

| | |
|---|---|
| Python | 3.12+ (tested: 3.12 in production, 3.14 in development; not tested below that) |
| DB in development | nothing to install — a SQLite file is created automatically |
| DB in production | MariaDB (since 2026-09-20); Postgres was proven in production earlier and is fully supported — switching is a `PI_DATABASE_URL` change, see `deploy/README.md` |
| Dependencies | installed via `pip install -r requirements.lock.txt` |

Docker, Node.js, npm, frontend build tools — not needed and never will
be: the frontend is a single HTML file with no build step, and the DB
schema is created by `alembic upgrade head`.

## Installation

```
git clone https://github.com/mefffodiy-n/EVE-PI-app.git
cd EVE-PI-app
python -m venv .venv
.venv\Scripts\pip install -r requirements.lock.txt   # Linux/macOS: .venv/bin/pip
```

`requirements.lock.txt` — exact versions, verified in production on both
dev and prod (`requirements.txt` only holds lower bounds — a
compatibility intent, not what actually gets installed).

## First-time data

A fresh clone can't compute plans without a few one-off steps:

```
.venv\Scripts\python -m scripts.extract_schematics --write   # data/schematics.json — input/output quantities
.venv\Scripts\python -m alembic upgrade head                 # database tables
.venv\Scripts\python -m scripts.migrate_planets_csv_to_db    # planet reference data (Fountain region) — once per DB
.venv\Scripts\python -m scripts.seed_dev_characters           # dev characters (PI_ENV=dev only)
```

`python -m scripts.diagnose` checks that the data, database and
endpoints are all in place, and says specifically what's missing.

## Running it

```
.venv\Scripts\python run.py
```

Open **http://127.0.0.1:8000/** — the root path, Flask serves the
interface from `web/` itself.

Editing the frontend with Live Server (auto-reload) needs CORS:

```
set PI_DEV_CORS=1 && python run.py     # Windows
PI_DEV_CORS=1 python run.py            # Linux/macOS
```

The frontend detects the different origin itself. **Never set this
variable in production.**

## Configuration

The profile is chosen with `PI_ENV` (`dev` by default, `prod`
explicitly) — it decides whether `seed_dev_characters` is allowed (dev
only) and which database engine is used by default when
`PI_DATABASE_URL` isn't set.

| Variable | Required | Purpose |
|---|---|---|
| `PI_ENV` | no (defaults to `dev`) | `dev` / `prod` — see above |
| `PI_DATABASE_URL` | no | database address; without it — a SQLite file `data/pi_director.db` in dev |
| `PI_LOG_DIR` | no | where the collectors write their logs (`infra/logging.py`) |
| `PI_BACKUP_DIR` | no (required for `scripts/backup.py` in production) | where daily DB copies go |
| `PI_DEV_CORS` | no | `1` — allow CORS for Live Server (never in production) |
| `PI_ESI_CLIENT_ID` | for EVE SSO login | client_id obtained from developers.eveonline.com |
| `PI_ESI_CLIENT_SECRET` | no | only needed for actual token revocation on CCP's side (`POST /v2/oauth/revoke`) — without it, "Unlink" only erases the token locally |
| `PI_TOKEN_KEY` | for EVE SSO login | token encryption key — `python -c "from infra.crypto import generate_key; print(generate_key())"` |
| `PI_ESI_CALLBACK_URL` | no (defaults to `http://localhost:8000/api/auth/callback`) | OAuth callback address |
| `PI_ESI_SCOPES` | no | space-separated scope list, if the default set isn't right |

Without `PI_ESI_CLIENT_ID`/`PI_TOKEN_KEY`, `/api/auth/*` returns 503;
the rest of the app runs on dev character stand-ins.

## Scheduled data collection

No HTTP handler that serves data ever calls ESI itself — only the
background collectors reach the network (`CLAUDE.md`, rule 3):

| Collector | Interval | What it does |
|---|---|---|
| `refresh_server_status` | 10 min | online player count |
| `refresh_market_prices` | 60 min | PI product prices (Fuzzwork) |
| `refresh_tokens` | 15 min | refreshes ESI access tokens |
| `sync_character_skills` | 6 h | characters' PI skill levels |
| `sync_colony_status` | 30 min | a snapshot of real colonies, extractor timers, routes |
| `backup` | 24 h | a copy of the DB and cache snapshots |

One process, its own log:

```
.venv\Scripts\python -m scripts.scheduler
```

If your system already has a task scheduler (cron, Task Scheduler),
it's more reliable to set up a separate job per collector — that's also
supported (see the docstring in `scripts/scheduler.py`).

## Logging in with EVE SSO

OAuth2 Authorization Code + PKCE, without relying on a `client_secret`
in the flow itself. The full rules for working with ESI and SSO are in
`docs/ESI.md`. In short: logging in gives access to a character's real
colonies and skills, and the app works fully without it too — the
computed plan doesn't need a single in-game character, only dev
stand-ins or characters already logged in through SSO.

## Deployment

Production run — `python -m scripts.serve` (waitress) instead of
`run.py`, behind nginx, both processes (`serve` and `scheduler`) run as
system services. Step by step, with real commands for Ubuntu and Windows
(NSSM) — `deploy/README.md`.

## Tests

```
.venv\Scripts\python -m pytest tests/ -q
```

The tests are written against things that have actually broken before —
half the suite exists because the corresponding bug was made and found
(details in `CLAUDE.md`, "How we work here").

## Architecture

Repository layout, development rules and what's covered by tests —
`CLAUDE.md`. Game mechanics and assumptions — `docs/DOMAIN.md`. History
of decisions by phase — `docs/ROADMAP.md`.

## Known limitations

Honest and by name, not by silence (the same is visible in the app
itself, under "Help" → "What the program doesn't know yet"):

- The plan calculation works for the Fountain region only — resource
  density data for other New Eden regions doesn't exist yet (see
  "Planned").
- Resource density in deposits is treated as constant — in game it
  depletes, and there's no live data on that from the server.
- Direct P2 on an extraction planet is paused: the layout has never
  been checked in game (`docs/ROADMAP.md`, Phase 9).
- POCO export tax isn't accounted for in the "most profitable to
  produce" table (it compares templates, not an actual layout) — but it
  is accounted for in the separate profitability forecast, both for the
  computed plan and by actual output for real colonies
  (`docs/ROADMAP.md`, Phase 9).
- The actual-output profitability forecast doesn't count import tax
  between different colonies — ESI doesn't expose which colony feeds
  which.

## Planned

Logged in the queue, not implemented without a separate explicit
request (details and discussion history — `docs/ROADMAP.md`):

- **Direct P0→P2 production on a single planet** — the code already
  exists (`domain/direct_p2.py`), but is switched off in the interface:
  the layout wasn't taken from a verified in-game template (there is no
  such template in the DalShooth set), but computed from individual
  structure costs, and has never been checked in game. Turning it back
  on is an in-game check, not new development.
- **Multi-region support** — plan approved 2026-09-17, split into 5
  phases/PRs (`docs/ROADMAP.md`, Phase 10). Phase 1 (planet reference
  data moved into the database, `regions`/`planets` tables instead of a
  CSV) shipped 2026-09-21. Data still only covers the **Fountain**
  region (confirmed by the user on 2026-09-16) — Phases 2-5 (a
  region-wide skeleton collector from the SDE, an admin panel for
  manual density entry, region availability in the planner) remain
  queued, each waiting on its own explicit request. Neither ESI nor the
  SDE expose resource density for planets (verified the same day) —
  extending to each further region needs the same kind of manual export
  that produced the current Fountain data.

## Version

Lives in exactly one place — `version.py`, which the API, the "About"
window and the `User-Agent` all read from.

## License and rights

MIT — see `LICENSE`. Author — `mefffodiy-n`,
github.com/mefffodiy-n/EVE-PI-app.

EVE Online® and Fenris Creations™, and all related logos and other
elements, are trademarks of Fenris Creations. This application is made
independently, and Fenris Creations™ does not support or endorse it.
