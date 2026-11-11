"""Web app: paste a Sportsbet race link, see the runners' prices.

Run:
    .venv/bin/python app.py
then open http://127.0.0.1:5050

Betfair back/lay prices are added when .env has BETFAIR_APP_KEY plus a
username/password (or a session token). See betfair.py.
"""

import csv
import io
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pymysql
from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from betfair import Betfair, BetfairError
from db import Database, save_resulted_races
from sportsbet_prices import extract_results, get_day, get_race


def load_env(path=Path(__file__).with_name(".env")):
    """Minimal .env reader: KEY=VALUE lines; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env()
betfair = Betfair(
    app_key=os.environ.get("BETFAIR_APP_KEY"),
    username=os.environ.get("BETFAIR_USERNAME") or None,
    password=os.environ.get("BETFAIR_PASSWORD") or None,
    session_token=os.environ.get("BETFAIR_SESSION_TOKEN") or None,
)

app = Flask(__name__)

SNAPSHOT_EVERY = 10 * 60  # seconds


def snapshot_loop():
    """Save the day's AU/NZ Betfair markets regularly, so resulted races can show BSP.

    Betfair stops listing a market once it closes, so it has to be saved beforehand.
    """
    while True:
        try:
            count = betfair.snapshot()
            app.logger.info("Saved %d Betfair markets", count)
        except (BetfairError, OSError, ValueError) as e:
            app.logger.warning("Betfair snapshot failed: %s", e)
        time.sleep(SNAPSHOT_EVERY)


DB_SAVE_EVERY = 5 * 60  # seconds


def db_save_loop(db):
    """Save every resulted race (results, BSP, comments, price history) to MySQL.

    Waits a minute first so the Betfair market snapshot has run.
    """
    time.sleep(60)
    while True:
        try:
            count = save_resulted_races(db, betfair if betfair.configured else None)
            if count:
                app.logger.info("Saved %d resulted races to MySQL", count)
        except Exception as e:  # keep the loop alive whatever happens; the next run retries
            app.logger.warning("Saving races to MySQL failed: %s", e)
        time.sleep(DB_SAVE_EVERY)


def start_db_saver():
    db = database  # the same connection settings the Results page uses
    if db is None:
        app.logger.info("MySQL not configured (MYSQL_* in .env) - results won't be saved")
        return
    try:
        db.init_schema()
    except Exception as e:
        app.logger.warning("MySQL not reachable (%s) - is it running? `docker compose up -d`", e)
        return
    app.logger.info("Saving resulted races to MySQL every %d minutes", DB_SAVE_EVERY // 60)
    threading.Thread(target=db_save_loop, args=(db,), daemon=True).start()


def betfair_prices(event, runners, placings):
    """Add Back/Lay/BSP to each runner (and BSP to placings); return a status dict for the page."""
    if not betfair.configured:
        return {"configured": False,
                "error": "Betfair not set up: add BETFAIR_USERNAME and BETFAIR_PASSWORD to .env"}
    try:
        market, prices = betfair.race_prices(event, runners)
    except (BetfairError, OSError, ValueError) as e:
        return {"configured": True, "error": str(e)}
    if market is None:
        venue = event.get("competitionName") or "this venue"
        if not betfair.covers_venue(event):
            return {"configured": True, "error": (
                f"No Betfair prices: Betfair doesn't appear to offer markets at {venue} "
                "(none seen in the markets saved so far).")}
        if placings:
            return {"configured": True, "error": (
                "No Betfair BSP for this race: Betfair stops listing markets once they close, "
                "and this one wasn't saved while open. Keep the app running on race day "
                "(it saves Betfair's racing markets every 10 minutes) to get BSP for resulted races.")}
        return {"configured": True,
                "error": "No matching Betfair market found for this race."}
    for r in runners:
        r.update(prices.get(r["No"], {}))
    for p in placings:
        p["BSP"] = prices.get(p["No"], {}).get("BSP")
    return {"configured": True, **market}


@app.get("/")
def home():
    # Old bookmarks used /?race=...; send them to the race page.
    if request.args.get("race"):
        return redirect(url_for("race_page", race=request.args["race"]))
    return render_template("home.html")


@app.get("/race")
def race_page():
    return render_template("race.html")


# --------------------------------------------------------------------------- #
# Results page: browse races saved in MySQL
# --------------------------------------------------------------------------- #
database = Database.from_env()  # None if MYSQL_* isn't set in .env


def _plain(value):
    """MySQL values -> JSON-friendly (Decimal -> float, dates -> ISO strings, UTC marked)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat() + "Z"  # stored as UTC
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _result_filters():
    args = request.args
    return {
        "date_from": args.get("from") or None,
        "date_to": args.get("to") or None,
        "type": args.get("type") or None,
        "region": args.get("region") or None,
        "venue": (args.get("venue") or "").strip() or None,
        "search": (args.get("search") or "").strip() or None,
        "bsp_only": args.get("bsp") == "1",
    }


def _db_unavailable(e=None):
    if database is None:
        msg = "The results database isn't set up. Add the MYSQL_* settings to .env (see README)."
    else:
        msg = f"Can't reach the results database ({e}). Is Docker running? Try `docker compose up -d`."
    return jsonify(error=msg), 503


def _sportsbet_link(race):
    """A race page link from saved fields (only the event id at the end matters)."""
    region = "international" if race.get("international") else "australia-nz"
    venue = re.sub(r"[^a-z0-9]+", "-", (race.get("venue") or "").lower()).strip("-")
    return (f"https://www.sportsbet.com.au/{race.get('race_type', 'horse')}-racing/{region}/"
            f"{venue}/race-{race.get('race_number')}-{race['race_id']}")


@app.get("/results")
def results_page():
    return render_template("results.html")


@app.get("/api/results")
def results_list():
    if database is None:
        return _db_unavailable()
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        counts, races = database.find_races(_result_filters(), limit=100, offset=offset)
    except ValueError:
        return jsonify(error="offset must be a number"), 400
    except pymysql.MySQLError as e:
        return _db_unavailable(e)
    for r in races:
        r["link"] = _sportsbet_link(r)
    return jsonify(_plain({**counts, "offset": offset, "races": races}))


@app.get("/api/results/<int:race_id>")
def results_detail(race_id):
    if database is None:
        return _db_unavailable()
    try:
        detail = database.race_detail(race_id)
    except pymysql.MySQLError as e:
        return _db_unavailable(e)
    if detail is None:
        return jsonify(error="That race isn't in the database."), 404
    detail["race"]["link"] = _sportsbet_link(detail["race"])
    return jsonify(_plain(detail))


@app.get("/api/results.csv")
def results_csv():
    if database is None:
        return _db_unavailable()
    try:
        rows = database.export_rows(_result_filters())
    except pymysql.MySQLError as e:
        return _db_unavailable(e)
    out = io.StringIO()
    columns = list(rows[0].keys()) if rows else ["race_date", "venue", "race_number", "runner_name"]
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    name = f"race-results-{date.today().isoformat()}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


_day_cache = {}  # date -> (fetched at, meetings)
DAY_CACHE_SECONDS = 30


@app.get("/api/races")
def races():
    """All races for ?date=YYYY-MM-DD (default: today, local time)."""
    day = request.args.get("date") or date.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError:
        return jsonify(error="date must look like 2026-11-11"), 400
    cached = _day_cache.get(day)
    if cached and time.time() - cached[0] < DAY_CACHE_SECONDS:
        meetings = cached[1]
    else:
        try:
            meetings = get_day(day)
        except (ValueError, OSError) as e:
            return jsonify(error=f"Couldn't load races from Sportsbet: {e}"), 502
        _day_cache[day] = (time.time(), meetings)
    return jsonify(date=day, meetings=meetings, now=time.time())


@app.post("/api/prices")
def prices():
    source = (request.get_json(silent=True) or {}).get("source", "").strip()
    if not source:
        return jsonify(error="Paste a Sportsbet race link first."), 400
    try:
        event, runners = get_race(source)
    except (ValueError, OSError) as e:
        return jsonify(error=str(e)), 400
    if not runners:
        return jsonify(error="No runners found for this race."), 404

    start = event.get("startTime")
    placings, exotics = extract_results(event)  # empty lists until the race is run
    betfair_status = betfair_prices(event, runners, placings)
    return jsonify(
        race={
            "title": event.get("displayName") or event.get("name") or "Race",
            "name": event.get("name", ""),
            "distance": event.get("distance"),
            "comment": (event.get("overview") or "").strip(),
            "start": datetime.fromtimestamp(start, timezone.utc).isoformat() if start else None,
        },
        runners=runners,
        betfair=betfair_status,
        results=placings,
        exotics=exotics,
        fetched=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if betfair.configured:
        threading.Thread(target=snapshot_loop, daemon=True).start()
    start_db_saver()
    # Port 5000 is used by AirPlay Receiver on macOS, so use 5050.
    app.run(host="127.0.0.1", port=5050, debug=False)
