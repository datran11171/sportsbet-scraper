"""Web app: paste a Sportsbet race link, see the runners' prices.

Run:
    .venv/bin/python app.py
then open http://127.0.0.1:5050

Betfair back/lay prices are added when .env has BETFAIR_APP_KEY plus a
username/password (or a session token). See betfair.py.
"""

import logging
import os
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from betfair import Betfair, BetfairError
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
    # Port 5000 is used by AirPlay Receiver on macOS, so use 5050.
    app.run(host="127.0.0.1", port=5050, debug=False)
