# Sportsbet Race Prices

A small Python web app that shows every Sportsbet race for the day, and for any
race the runners' Sportsbet fixed odds and tote prices, the matching **Betfair
Exchange back and lay prices**, and once the race is run the **results, dividends
and Betfair Starting Price (BSP)**.

It runs on your own computer at http://127.0.0.1:5050. The Sportsbet side needs
no account. The Betfair columns are optional and need your own Betfair API app
key and login.

![Race page with Sportsbet and Betfair prices](docs/screenshots/race-live.png)

---

## Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [Betfair setup (optional)](#betfair-setup-optional)
- [Using the app](#using-the-app)
- [Results database (MySQL)](#results-database-mysql)
- [Command-line tool](#command-line-tool)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Web API](#web-api)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

---

## Features

**Home page**
- Every Sportsbet race for the day: horses, greyhounds and harness, Australian/NZ and international.
- Filter by race type and region. Step back or forward a day with ‹ ›.
- Each race shows its start time, or its placings (e.g. `2-5-1`) once run. Abandoned races are crossed out.

**"Next to jump" bar**
- Stays at the top of every page.
- Shows the next 12 open races with live countdowns, which turn red in the last 2 minutes.
- Has its own **race type** and **region** dropdowns, available on every page. They share
  the home page's filters, so changing either one changes both, and your choice is remembered.
- Highlights the race you're viewing.

**Race page**
- Every runner, with barrier, jockey/driver, trainer and weight.
- **Sportsbet** fixed Win/Place odds and the tote win price.
- **Betfair** best Back (blue) and Lay (pink) prices with the $ available, the same as betfair.com.au shows (virtual/cross-matched prices included).
- Click any column header to sort. The favourite is highlighted, and scratched runners are struck out and listed last.
- **EV**: the expected value of a bet at Sportsbet's fixed price, using Betfair's lay price as
  the true odds. There are four options: **No promo**, **Bonus bet conversion**,
  **Run 2nd (+5%)** and **Run 2nd / 3rd (+10%)**. Hover a cell to see the working.
  See [How EV is calculated](#how-ev-is-calculated).
- **Flucs**: a small graph of each runner's Sportsbet price history from open to now.
  Green steps mean the price drifted (went up) and red steps mean it shortened (went down).
  Hover a point to see that price, and read "open → now" underneath.
- **Comments**: Sportsbet's form comment under each runner, plus a race overview.
  Turn them off with the **Comments** checkbox; the page remembers your choice.
- Refresh button and optional 30-second auto-refresh. ▲/▼ markers show which prices moved
  since the last refresh, using the same green/red colours as Flucs.

**Resulted races**
- A results table showing:
  - the finishing order
  - Sportsbet's starting price (SP)
  - final fixed odds
  - tote win/place dividends
  - the **Betfair SP**
- Exotic dividends: Quinella, Exacta, Trifecta and First 4.

**Command-line tool**
- Prints the same tables in the terminal, or saves them as CSV. It needs only the Python standard library.

---

## Screenshots

### Home page: all of today's races

![Home page listing every meeting and race](docs/screenshots/home.png)

The blue-outlined race in each meeting is its next race to run. The bar along the
top is "Next to jump", which stays visible on every page.

### Race page: Sportsbet vs Betfair prices

![Race page for an upcoming race](docs/screenshots/race-live.png)

**Win / Place** are Sportsbet's fixed odds. **Back** (blue) and **Lay** (pink) are
the best Betfair Exchange prices, with the amount available underneath. The line
under the race name shows the Betfair market, the $ matched so far, and a link to
it on Betfair.

### Resulted race: results, dividends and Betfair SP

![Resulted race with results table and BSP](docs/screenshots/race-resulted.png)

**SP** is Sportsbet's starting price. **Betfair SP** is the Betfair Starting
Price. They come from different places and usually differ: the bookmaker SP
includes the bookmaker's margin, while BSP is set by exchange bettors.

### On a phone

<img src="docs/screenshots/home-phone.png" alt="Home page on a phone" width="320">

---

## Quick start

You need **Python 3.10 or newer**. The app was developed with Python 3.13 on macOS.

```bash
git clone <this repo>
cd sportsbet-scraper

# First time only: create a virtual environment and install Flask
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Start the app
.venv/bin/python app.py
```

Open **http://127.0.0.1:5050** in your browser. To stop the app, press **Ctrl+C**
in the terminal it's running in.

> On Windows, use `.venv\Scripts\python app.py` and `.venv\Scripts\pip ...`.

---

## Betfair setup (optional)

Without Betfair settings the app still works. It just shows a note instead of the
Back/Lay/BSP columns.

1. **Get an app key.** Follow the "application keys" guide on the
   [Betfair Developer Program](https://developer.betfair.com/) site while logged in to your Betfair account.
   - A **delayed** key is free, but its prices can lag the live market slightly.
   - A **live** key has to be requested from Betfair.
2. **Create a file called `.env`** in the project folder:

   ```ini
   BETFAIR_APP_KEY=your-app-key
   BETFAIR_USERNAME=your-betfair-username
   BETFAIR_PASSWORD=your-betfair-password
   # Or, instead of username/password, an existing session token:
   # BETFAIR_SESSION_TOKEN=...
   ```

   `.env` is listed in `.gitignore`, so your login is never committed. Keep it that way.
3. **Restart the app.** It logs in to Betfair for you (`identitysso.betfair.com.au`) and
   logs in again automatically when the session expires.

Real environment variables with the same names take priority over `.env`.

---

## Using the app

### Home page (`/`)

| Control | What it does |
|---|---|
| ‹ › / **Today** | Previous / next day, back to today. Sportsbet keeps roughly the last week of results. |
| **All · Horses · Greyhounds · Harness** | Filter by race type. Your choice is remembered. |
| **All regions · Aus / NZ · International** | Filter by region. Also remembered. |
| Race chip | Opens that race's page. |
| Link box | Paste any Sportsbet race link to open it directly. |

Race statuses update every minute.

### Race page (`/race?race=<sportsbet link>`)

- **⌂ Home** goes back to the race list.
- **Refresh** reloads prices. **Auto-refresh 30s** keeps reloading until the race is resulted, then switches off.
- Click a column header to sort, and click again to reverse.
- The address bar holds the race link, so you can bookmark or share a race page.
- You can also paste a **"Copy as cURL"** command from your browser's DevTools instead of a link. This is useful if Sportsbet ever starts requiring browser cookies.

### How EV is calculated

The **EV** column (next to Win) uses Betfair's lay price as the true odds for each runner. It
only appears when Betfair has live prices. Choose one of four options with the **EV** selector:

| Option | Column shows | Formula |
|---|---|---|
| No promo | EV per $1 cash bet | `p_win × sportsbet_price − 1` |
| Bonus bet conversion | Share of a bonus bet you'd keep as cash | `p_win × (sportsbet_price − 1)` |
| Run 2nd | EV per $1 cash bet | no-promo EV **+ 5%** |
| Run 2nd / 3rd | EV per $1 cash bet | no-promo EV **+ 10%** |

- **`p_win = 1 / Betfair lay price`.** The lay price is used rather than the back price because
  it's the cautious choice: it gives the runner its highest chance of winning, so it never
  makes a bet look better than it is.
- **The Run 2nd and Run 2nd / 3rd promos add a fixed 5% and 10%** on top of the no-promo EV.
  They're set as `PROMO_BOOST` in `templates/race.html`, so you can adjust them there.
- **Bonus bet conversion** is for placing a bonus bet, whose stake isn't returned:
  - You only get the winnings, `sportsbet_price − 1`, if the runner wins.
  - **75%** means a $100 bonus bet is worth about $75 cash on average.
  - Longer-priced runners usually convert better. The best runner is highlighted.
- **EV highlighting:** `+5.0%` means about $5 profit per $100 staked over many such bets.
  Positive EV cells are highlighted.
- **Faded** cells mean the Betfair back/lay gap is over 25%, so the market is too thin for
  the estimate to mean much. Faded runners are never highlighted.
- **Hover any cell** to see its working: Sportsbet vs lay price, win chance, what the promo
  adds, and $ per $100.

Your choice is remembered in your browser. Things the EV doesn't account for:

- promo refund caps (e.g. "up to $50")
- Sportsbet's eligibility rules (e.g. which races qualify)
- prices moving after you look

The bonus conversion is the *expected* value. Matched bettors who also lay on Betfair to lock in
a result get a slightly different, fixed figure, because Betfair commission is charged on that lay.

### Getting BSP for resulted races

Betfair stops listing a market once it closes. While the app is running, it saves
every open Betfair racing market for the next 24 hours (all countries) to
`data/betfair_markets.json`, refreshing every 10 minutes.

When you open a resulted race, the app looks up that saved market and asks Betfair
for its final BSP.

**So keep the app running on race day.** Races that closed before the app saw them
show a message explaining why there's no BSP. Venues Betfair doesn't cover at all,
such as some smaller US tracks, say so.

---

## Results database (MySQL)

The app can save every resulted race to a MySQL database. That includes:

- results and dividends
- the Betfair SP
- runner and race comments
- each runner's price fluctuations

This builds up a history you can query. It's optional: without MySQL the app works the same, it just doesn't save anything.

### Setup

MySQL runs in Docker, so you need [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and open.

1. **Add database settings to `.env`.** Pick your own passwords:

   ```ini
   MYSQL_HOST=127.0.0.1
   MYSQL_PORT=3306
   MYSQL_DATABASE=racing
   MYSQL_USER=racing
   MYSQL_PASSWORD=choose-a-password
   MYSQL_ROOT_PASSWORD=choose-another-password
   ```

2. **Start the database:**

   ```bash
   docker compose up -d       # first run downloads MySQL 8.4 and creates the database
   ```

   It only listens on `127.0.0.1`, so it can't be reached from other computers. Its data
   lives in a Docker volume and survives restarts. `docker compose stop` stops it, and
   `docker compose down -v` deletes it **including all saved data**.

3. **Start the app** as usual. It creates the tables and then, **every 5 minutes**,
   saves any newly resulted races from today and yesterday. You'll see
   `Saving resulted races to MySQL every 5 minutes` when it starts.

### Saving by hand / backfilling

```bash
.venv/bin/python db.py save              # save today's and yesterday's resulted races now
.venv/bin/python db.py save --days 7     # backfill the last week (Sportsbet keeps about a week)
.venv/bin/python db.py stats             # counts per table and BSP status
```

Backfilled races get results, comments and price history. They only get **BSP if the app
saved their Betfair market while it was open** (see
[Getting BSP for resulted races](#getting-bsp-for-resulted-races)).

### Tables

| Table | One row per | Main columns |
|---|---|---|
| `races` | race | `race_id` (Sportsbet event id), `race_date`, `venue`, `race_type`, `region`, `race_number`, `distance_m`, `start_time_utc`, `race_comment`, `betfair_market_id`, `bsp_status` |
| `runners` | runner in a race | `finish_position`, `runner_name`, `barrier`, `jockey`, `trainer`, `weight_kg`, `scratched`, `open_price`, `fixed_win`, `fixed_place`, `sportsbet_sp`, `tote_win`, `tote_place`, `bsp`, `runner_comment` |
| `price_fluctuations` | price move | `race_id`, `runner_number`, `seq` (0 = opening price), `price` |
| `exotic_results` | exotic bet | `bet_type` (Quinella, Exacta, Trifecta, First 4), `runners` (e.g. `6-2-11`), `dividend` |

`sportsbet_sp` and the tote dividends only exist for placegetters, because that's all
Sportsbet publishes. `fixed_win` is every runner's final fixed price.

**`bsp_status`** says whether a race has BSP:

| Value | Meaning |
|---|---|
| `ok` | BSP saved |
| `pending` | Betfair market found but BSP not settled yet (or Betfair errored). Retried each run for up to 24 hours, then `missing`. |
| `not_saved` | Betfair has markets at this venue, but this race closed before the app saved its market |
| `not_found` | No Betfair market seen at this venue. Either Betfair doesn't cover it, or the app wasn't running while its markets were open. |
| `no_betfair` | Betfair isn't set up in `.env` |

### Example queries

```sql
-- Winners: Sportsbet SP vs Betfair SP
SELECT r.race_date, r.venue, r.race_number, u.runner_name, u.sportsbet_sp, u.bsp
FROM runners u JOIN races r USING (race_id)
WHERE u.finish_position = 1 AND u.bsp IS NOT NULL
ORDER BY r.start_time_utc DESC;

-- How often the Sportsbet favourite (lowest final fixed price) wins, by race type
SELECT r.race_type, AVG(u.finish_position = 1) AS fav_win_rate, COUNT(*) AS races
FROM races r
JOIN runners u ON u.race_id = r.race_id AND NOT u.scratched
WHERE u.fixed_win = (SELECT MIN(fixed_win) FROM runners x WHERE x.race_id = r.race_id AND NOT x.scratched)
GROUP BY r.race_type;

-- One runner's price history, oldest first
SELECT seq, price FROM price_fluctuations
WHERE race_id = 11143279 AND runner_number = 4 ORDER BY seq;
```

To browse the data in a GUI such as TablePlus, DBeaver or MySQL Workbench, connect to
`127.0.0.1:3306` with `MYSQL_USER` / `MYSQL_PASSWORD` from `.env`. From the terminal:

```bash
docker exec -it sportsbet-mysql mysql -u racing -p racing    # asks for MYSQL_PASSWORD
```

---

## Command-line tool

`sportsbet_prices.py` works on its own with plain `python3` (no Flask, no Betfair):

```bash
python3 sportsbet_prices.py "https://www.sportsbet.com.au/horse-racing/australia-nz/mornington/race-1-11139916"
python3 sportsbet_prices.py "<race link>" --csv race.csv     # also save as CSV
pbpaste | python3 sportsbet_prices.py                        # link or cURL from the clipboard (macOS)
python3 sportsbet_prices.py --json saved_response.json       # parse a saved Sportsbet response
```

Example output, shortened:

```
R1 Mornington  |  Tue 10 Nov 2026 13:30
R1 Sportsbet Race Previews Mdn Plate  (2430m)
+----+------------------+---------+-----------------+--------+-------+--------+--------+
| No | Runner           | Barrier | Jockey/Driver   |    Win | Place |   Tote | Status |
+====+==================+=========+=================+========+=======+========+========+
|  6 | Molesworth       |       9 | Celine Gaudray  |   9.00 |  2.20 |   7.50 | 1st    |
| 10 | Dejavast         |       5 | Jackson Radley  |      - |     - |      - | SCR    |
...
Result
+-----+----+-----------------+-------+-----------+-------------+----------+------------+
| Pos | No | Runner          |    SP | Fixed Win | Fixed Place | Tote Win | Tote Place |
| 1st |  6 | Molesworth      | 10.00 |      9.00 |        2.20 |     7.50 |       1.90 |
...
```

---

## How it works

Nothing is scraped from HTML. Sportsbet's website loads its data from internal
JSON endpoints, and the app calls the same ones directly. Betfair data comes from
the official Betfair Exchange API.

```
            browser                          app.py (Flask)                      outside
 ┌──────────────────────────┐      ┌──────────────────────────────┐
 │ home.html  ──────────────┼─────▶│ GET  /api/races?date=…       │────▶ Sportsbet AllRacing/<date>
 │ race.html  ──────────────┼─────▶│ POST /api/prices {source}    │────▶ Sportsbet RacecardWithContext
 │ jumpbar.js (every page) ─┼─────▶│                              │────▶ Betfair listMarketBook
 └──────────────────────────┘      │ background: every 10 min     │────▶ Betfair listMarketCatalogue
                                   │   → data/betfair_markets.json│        (saved market list)
                                   └──────────────────────────────┘
```

### Data sources

| Data | Where it comes from |
|---|---|
| Races for a day | `https://www.sportsbet.com.au/apigw/sportsbook-racing/Sportsbook/Racing/AllRacing/<YYYY-MM-DD>` |
| One race: runners, prices, results, dividends | `…/Sportsbook/Racing/Events/<event id>/RacecardWithContext` |
| Betfair login | `https://identitysso.betfair.com.au/api/login` |
| Finding Betfair markets | `listMarketCatalogue`, WIN markets only (horse racing = 7, greyhounds = 4339; Betfair lists harness under horse racing) |
| Betfair prices and BSP | `listMarketBook` with `EX_BEST_OFFERS`, `SP_AVAILABLE` and `virtualise: true` |

The **event id** is the number at the end of every Sportsbet race link
(`…/race-1-11139916` → `11139916`). It's all the app needs to fetch a race.

### Matching a Sportsbet race to its Betfair market

The two sites share no IDs, so the app matches on:

1. **Race type**: horse/harness vs greyhound.
2. **Venue name**, ignoring case and punctuation, and allowing a prefix
   (Sportsbet "Riccarton" = Betfair "Riccarton Park", "Mountaineer" = "Mountaineer Park").
3. **Start time** within 20 minutes, preferring the market named for the same race
   number (`R3 …`) and then the closest time.

Runners are matched by saddlecloth number. Betfair names Australian runners like
`1. Molesworth`. If there's no number, as with UK and US runners, the app matches
by name instead.

### Sportsbet field notes

- **Price codes:** `L` = fixed odds (win/place), `NTP` = tote win.
- **Price history:** `recentOddsFluctuations` holds up to 10 earlier prices, *newest first*,
  without the current price. `statistics.openPrice` is the opening price.
  The app builds the history in time order: open → the list reversed → the current price.
- **Comments:** each runner's comment is `statistics.overview`, and the race comment is the race's `overview`.
- **Before a race,** runner status `S` means scratched. **After it,** every runner
  becomes `S` ("settled"), and scratchings are marked with a result of `V` (void).
- **Dividends** look like `SP:10.0`, `STAB/PL:7.5/1.9` (winner's tote win/place) and
  `STAB:3.5` (a placegetter's tote place).

---

## Project structure

```
app.py                 Flask web app: pages, JSON API, background Betfair market saver
sportsbet_prices.py    Sportsbet client + command-line tool (standard library only)
betfair.py             Betfair client: login, market matching, prices, BSP, saved markets
db.py                  MySQL storage of resulted races + `db.py save/stats` command
docker-compose.yml     Runs MySQL 8.4 in Docker
templates/
  home.html            Home page (race list)
  race.html            Race page (prices, Betfair, results)
  _jumpbar.html        "Next to jump" bar included on every page
static/
  style.css            Shared styles (light and dark mode)
  jumpbar.js           "Next to jump" bar logic
docs/screenshots/      Images used in this README
requirements.txt       Python dependencies (Flask, PyMySQL)
.env                   Your Betfair settings (git-ignored, you create it)
data/                  Saved Betfair markets (git-ignored, created automatically)
```

`main.py`, `project_list.csv` and `chromedriver-mac-arm64/` are left over from an
early Selenium experiment and aren't used by the app.

---

## Web API

The pages use these JSON endpoints, which you can call yourself:

```bash
# All races for a day (default: today)
curl "http://127.0.0.1:5050/api/races?date=2026-11-11"

# One race: runners, Sportsbet + Betfair prices, results
curl -X POST http://127.0.0.1:5050/api/prices \
     -H "Content-Type: application/json" \
     -d '{"source": "https://www.sportsbet.com.au/horse-racing/australia-nz/mornington/race-1-11139916"}'
```

`/api/prices` returns:

- `race`: title, name, distance, start
- `runners`: No, Runner, Barrier, Jockey/Driver, Trainer, Weight, Win, Place, Tote, Back, BackSize, Lay, LaySize, BSP, Status
- `betfair`: the market, or an `error` explaining why there are no Betfair prices
- `results` and `exotics`: empty until the race is run

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Port 5050 is in use` | The app is already running somewhere. Stop it with Ctrl+C in its terminal, or run `lsof -ti tcp:5050 \| xargs kill`. |
| Changes don't show up | Restart the app. It only reads the code when it starts. |
| "Betfair not set up" | Add `BETFAIR_USERNAME` and `BETFAIR_PASSWORD` (and `BETFAIR_APP_KEY`) to `.env`, then restart. |
| "Betfair login failed: …" | Check your username and password. The error code after the colon comes from Betfair, e.g. `INVALID_USERNAME_OR_PASSWORD`. |
| "No matching Betfair market found" | Betfair may not list that race, or the venue is spelled very differently on the two sites. |
| "No Betfair BSP for this race" | The race closed before the app saved its Betfair market. Keep the app running on race day. |
| Betfair prices look worse than the website | Markets far from the jump are thin and genuinely have wide gaps. A delayed app key can also lag the live market. |
| "MySQL not reachable … is it running?" when the app starts | Open Docker Desktop, then run `docker compose up -d`. The app keeps working without the database. |
| `docker compose up` says "set MYSQL_PASSWORD in .env" | Add the `MYSQL_*` settings to `.env` (see [Results database](#results-database-mysql)). |
| "Couldn't load races from Sportsbet" on an old date | Sportsbet only keeps about a week of past race days. |
| Home page shows the wrong day late at night | "Today" is your computer's date. Use Australian time to match Sportsbet's racing day. |

---

## Limitations

- **Unofficial Sportsbet endpoints.** The Sportsbet URLs are internal endpoints
  that Sportsbet's own website uses, not a published API. They can change without
  notice, which would break the app until it's updated.
- **BSP needs the app running.** BSP for resulted races is only available for
  markets the app saved while they were open. Betfair's public BSP files are
  published a day or more late and aren't used.
- **Coverage differs.** Betfair doesn't cover every venue Sportsbet does.
- **Personal use only.** The app runs on `127.0.0.1` and isn't meant to be exposed
  to the internet. Keep request rates modest: the app caches the race list for 30
  seconds and refreshes prices at most every 30 seconds. Check Sportsbet's and
  Betfair's terms of use before relying on it.
- **EV is an estimate.** It's only as good as the Betfair market it's based on, and the
  promo maths uses an approximate model. A positive EV is not a guaranteed profit.
- **Not betting advice.** Prices can be delayed or wrong. Always confirm on the
  bookmaker or exchange before betting, and gamble responsibly.
