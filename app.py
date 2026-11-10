"""Web app: paste a Sportsbet race link, see the runners' prices.

Run:
    .venv/bin/python app.py
then open http://127.0.0.1:5050
"""

from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from sportsbet_prices import extract_results, get_race

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


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
    return jsonify(
        race={
            "title": event.get("displayName") or event.get("name") or "Race",
            "name": event.get("name", ""),
            "distance": event.get("distance"),
            "start": datetime.fromtimestamp(start, timezone.utc).isoformat() if start else None,
        },
        runners=runners,
        results=placings,
        exotics=exotics,
        fetched=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    # Port 5000 is used by AirPlay Receiver on macOS, so use 5050.
    app.run(host="127.0.0.1", port=5050, debug=False)
