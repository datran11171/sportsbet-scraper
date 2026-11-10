#!/usr/bin/env python3
"""Fetch the runner prices for a Sportsbet race and print them as a table.

Easiest: pass the race link straight from your browser's address bar:
  python3 sportsbet_prices.py "https://www.sportsbet.com.au/horse-racing/australia-nz/mornington/race-2-11139920"

The number at the end of the link is the event id. The script uses it to call
the same JSON endpoint the web page calls (RacecardWithContext), so no cURL
copying or cookies are needed.

Alternatively, use a cURL command copied from DevTools:
  1. Open the race page on sportsbet.com.au in Chrome.
  2. Open DevTools (Cmd+Opt+I) -> Network tab, filter by "Racecard", reload.
  3. Right-click the "RacecardWithContext" request -> Copy -> Copy as cURL.
  4. Paste it into a file (e.g. curl.txt) or pipe it on stdin.

Usage:
  python3 sportsbet_prices.py curl.txt          # cURL command saved in a file
  pbpaste | python3 sportsbet_prices.py         # cURL command from the clipboard
  python3 sportsbet_prices.py --csv out.csv curl.txt
  python3 sportsbet_prices.py --json saved.json # parse a saved API response

If the copied cURL is for the race *page* (URL ending in the event id, e.g.
.../warrnambool/race-3-11132541) the script builds the API URL itself.

Only the Python standard library is used.
"""

import argparse
import csv
import json
import re
import shlex
import sys
import urllib.parse
import urllib.request
from datetime import datetime

API_URL = ("https://www.sportsbet.com.au/apigw/sportsbook-racing/"
           "Sportsbook/Racing/Events/{event_id}/RacecardWithContext")
DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")


# --------------------------------------------------------------------------- #
# cURL parsing
# --------------------------------------------------------------------------- #
def parse_curl(command):
    """Turn a 'Copy as cURL' string into (url, headers, data)."""
    # Join backslash line continuations (bash) and caret continuations (cmd).
    command = re.sub(r"\\\r?\n", " ", command)
    command = re.sub(r"\^\r?\n", " ", command)
    tokens = shlex.split(command)
    if not tokens or tokens[0] != "curl":
        raise ValueError("Input does not look like a cURL command (must start with 'curl').")

    url, headers, data = None, {}, None
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok in ("-H", "--header"):
            name, _, value = nxt.partition(":")
            headers[name.strip()] = value.strip()
            i += 2
        elif tok in ("-b", "--cookie"):
            headers["Cookie"] = nxt
            i += 2
        elif tok in ("-A", "--user-agent"):
            headers["User-Agent"] = nxt
            i += 2
        elif tok in ("-e", "--referer"):
            headers["Referer"] = nxt
            i += 2
        elif tok in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii"):
            data = nxt
            i += 2
        elif tok in ("-X", "--request", "-u", "--user", "-o", "--output"):
            i += 2  # options with a value we don't need
        elif tok == "--url":
            url = nxt
            i += 2
        elif tok.startswith("-"):
            i += 1  # flags such as --compressed, -s, -L
        else:
            url = tok
            i += 1

    if not url:
        raise ValueError("No URL found in the cURL command.")
    return url, headers, data


def to_api_url(url):
    """Return the RacecardWithContext API URL for a page or API URL."""
    if "/Racing/Events/" in url:
        return url
    match = re.search(r"-(\d{6,})/?(?:[?#].*)?$", url)
    if match:
        return API_URL.format(event_id=match.group(1))
    raise ValueError(
        "URL is neither the Racecard API nor a race page ending in an event id:\n  " + url)


def fetch_json(url, headers, data=None):
    # Drop headers urllib handles itself; in particular we must not ask for
    # br/zstd encoding because urllib cannot decompress it.
    skip = {"accept-encoding", "content-length", "host", "connection"}
    headers = {k: v for k, v in headers.items() if k.lower() not in skip}
    headers.setdefault("User-Agent", DEFAULT_UA)
    headers.setdefault("Accept", "application/json")

    body = data.encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Response was not JSON. First 300 chars:\n" + raw[:300])


# --------------------------------------------------------------------------- #
# Price extraction
# --------------------------------------------------------------------------- #
def _price(prices, code, field="winPrice"):
    for p in prices:
        if p.get("priceCode") == code and p.get(field) is not None:
            return p[field]
    return None


def extract_runners(payload):
    event = payload.get("racecardEvent", payload)
    markets = event.get("markets") or []
    market = next((m for m in markets if m.get("name") == "Win or Place"),
                  markets[0] if markets else None)
    if market is None:
        raise ValueError("No markets found in the response.")

    # Before the race statusCode "S" means scratched; once it is resulted every
    # runner becomes "S" (settled) and scratchings are marked result "V" (void).
    resulted = bool(event.get("results"))
    placings = {r.get("runnerNumber"): r.get("place") for r in event.get("results") or []}

    runners = []
    for sel in market.get("selections", []):
        prices = sel.get("prices", [])
        scratched = (sel.get("isOut") or sel.get("result") == "V"
                     or (not resulted and sel.get("statusCode") == "S"))
        place = placings.get(sel.get("runnerNumber"))
        if scratched:
            status = "SCR"
        elif place:
            status = ordinal(place)
        else:
            status = ""
        weight = sel.get("weightGram")
        stats = sel.get("statistics") or {}
        win = None if scratched else _price(prices, "L", "winPrice")
        runners.append({
            "No": sel.get("runnerNumber", ""),
            "Runner": sel.get("name", ""),
            "Barrier": sel.get("drawNumber", ""),
            "Jockey/Driver": sel.get("jockey") or sel.get("driver") or "",
            "Trainer": sel.get("trainer", ""),
            "Weight": f"{weight / 1000:g}kg" if weight else "",
            "Win": win,
            "Place": None if scratched else _price(prices, "L", "placePrice"),
            "Tote": None if scratched else _price(prices, "NTP", "winPrice"),
            "Status": status,
            # Extra detail for the web app (left out of the terminal table).
            "Comment": (stats.get("overview") or "").strip(),
            "Open": _num(stats.get("openPrice")),
            "Flucs": fluctuations(stats.get("openPrice"), sel.get("recentOddsFluctuations"), win),
        })
    runners.sort(key=lambda r: (r["Status"] == "SCR", r["No"] if r["No"] != "" else 999))
    return event, runners


def fluctuations(open_price, recent, current):
    """Sportsbet fixed-win price history, oldest first, ending at the current price.

    Sportsbet's recentOddsFluctuations holds up to 10 earlier prices, *newest
    first*, not including the current price; when it has fewer than 10 the last
    one is the opening price. A longer history is cut to 10, so the opening price
    is added at the start when it isn't already there.
    """
    series = [_num(p) for p in reversed(recent or [])]
    series = [p for p in series if p is not None]
    open_price = _num(open_price)
    if open_price is not None and (not series or series[0] != open_price):
        series.insert(0, open_price)
    if current is not None and (not series or series[-1] != current):
        series.append(current)
    return series


def ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _tote_dividends(dividends):
    """Pull (win, place) tote dividends out of entries like 'STAB/PL:7.5/1.9'.

    Sportsbet lists several totes (STAB, NSW, UNI, ...); use the first one found.
    The winner has 'TOTE/PL:win/place', other placegetters just 'TOTE:place'.
    """
    parsed = {}
    for item in dividends or []:
        key, _, value = item.partition(":")
        parsed[key] = value
    for tote in ("STAB", "NSW", "UNI"):
        if f"{tote}/PL" in parsed:
            win, _, place = parsed[f"{tote}/PL"].partition("/")
            return _num(win), _num(place)
        if tote in parsed:
            return None, _num(parsed[tote])
    return None, None


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def extract_results(event):
    """Finishing order and exotic dividends for a resulted race.

    Returns (placings, exotics); both are empty lists if the race hasn't run.
    """
    # The results feed sometimes leaves out jockey/trainer (e.g. UK greyhounds),
    # so fall back to the race card entry for the same runner number.
    markets = event.get("markets") or [{}]
    card = {s.get("runnerNumber"): s for s in markets[0].get("selections", [])}

    placings = []
    for r in sorted(event.get("results") or [], key=lambda r: r.get("place") or 99):
        dividends = r.get("dividend") or []
        sp = next((d.partition(":")[2] for d in dividends if d.startswith("SP:")), None)
        tote_win, tote_place = _tote_dividends(dividends)
        c = card.get(r.get("runnerNumber"), {})
        placings.append({
            "Pos": ordinal(r["place"]) if r.get("place") else "",
            "No": r.get("runnerNumber", ""),
            "Runner": r.get("name", ""),
            "Jockey/Driver": (r.get("jockey") or r.get("driver")
                              or c.get("jockey") or c.get("driver") or ""),
            "Trainer": r.get("trainer") or c.get("trainer") or "",
            "SP": _num(sp),
            "Fixed Win": r.get("winPrice"),
            "Fixed Place": r.get("placePrice"),
            "Tote Win": tote_win,
            "Tote Place": tote_place,
        })

    exotics = [{
        "Bet": x.get("name", ""),
        "Runners": "-".join(x.get("runnerNumbers") or []),
        "Dividend": _num(x.get("dividend")),
    } for x in event.get("exoticResults") or []]
    return placings, exotics


DAY_URL = ("https://www.sportsbet.com.au/apigw/sportsbook-racing/"
           "Sportsbook/Racing/AllRacing/{date}")
RACE_STATUS = {"A": "open", "R": "resulted", "B": "abandoned"}  # anything else: closed / running


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def get_day(date):
    """All of Sportsbet's races for a date ('YYYY-MM-DD'), grouped by meeting.

    Returns a list of meetings:
      {"name", "type" (horse/greyhound/harness), "region", "international", "races": [...]}
    and each race:
      {"id", "number", "start" (epoch seconds), "status", "result", "name", "distance", "link"}
    """
    payload = fetch_json(DAY_URL.format(date=date), {})
    meetings = []
    for day in payload.get("dates", []):
        for section in day.get("sections", []):
            race_type = section.get("raceType", "horse")
            for m in section.get("meetings", []):
                international = bool(m.get("isInternational"))
                region_path = "international" if international else "australia-nz"
                races = []
                for e in m.get("events", []):
                    # A Sportsbet-style link; get_race() only needs the event id at the end.
                    link = (f"https://www.sportsbet.com.au/{race_type}-racing/{region_path}/"
                            f"{_slug(m.get('name'))}/race-{e.get('raceNumber')}-{e['id']}")
                    races.append({
                        "id": e["id"],
                        "number": e.get("raceNumber"),
                        "start": e.get("startTime"),
                        "status": RACE_STATUS.get(e.get("statusCode"), "closed"),
                        "result": (e.get("result") or "").replace(",", "-"),
                        "name": e.get("name", ""),
                        "distance": e.get("distance"),
                        "link": link,
                    })
                races.sort(key=lambda r: r["number"] or 0)
                meetings.append({
                    "name": m.get("name", ""),
                    "type": race_type,
                    "region": m.get("regionName", ""),
                    "international": international,
                    "races": races,
                })
    return meetings


def get_race(text):
    """Race link or 'Copy as cURL' text -> (event, runners)."""
    text = text.strip()
    if text.startswith("curl"):
        url, headers, data = parse_curl(text)
    else:
        # A plain race link: build the request ourselves, no cookies needed.
        url, headers, data = text, {}, None
    api_url = to_api_url(url)
    host = urllib.parse.urlparse(api_url).hostname or ""
    if host != "sportsbet.com.au" and not host.endswith(".sportsbet.com.au"):
        raise ValueError("Only sportsbet.com.au links are supported.")
    return extract_runners(fetch_json(api_url, headers, data))


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
TEXT_COLS = {"Runner", "Jockey/Driver", "Trainer", "Weight", "Status", "Pos", "Bet", "Runners"}


def fmt(value):
    return "-" if value is None else str(value)


def print_grid(rows, price_cols=()):
    """Print a list of dicts as a boxed text table."""
    cols = list(rows[0].keys())
    cells = [[f"{r[c]:.2f}" if c in price_cols and r[c] is not None else fmt(r[c])
              for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]

    def line(values):
        return "| " + " | ".join(
            v.ljust(w) if col in TEXT_COLS else v.rjust(w)
            for v, w, col in zip(values, widths, cols)) + " |"

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    print(sep)
    print(line(cols))
    print(sep.replace("-", "="))
    for row in cells:
        print(line(row))
    print(sep)


def print_table(event, runners):
    title = event.get("displayName") or event.get("name") or "Race"
    start = event.get("startTime")
    if start:
        title += "  |  " + datetime.fromtimestamp(start).strftime("%a %d %b %Y %H:%M")
    if event.get("name") and event.get("name") != event.get("displayName"):
        title += "\n" + event["name"]
    if event.get("distance"):
        title += f"  ({event['distance']}m)"
    print(title)

    hidden = set(WEB_ONLY_COLS)
    if not any(r["Tote"] for r in runners):
        hidden.add("Tote")
    print_grid([{k: v for k, v in r.items() if k not in hidden} for r in runners],
               price_cols={"Win", "Place", "Tote"})
    print("Win/Place = Sportsbet fixed odds; Tote = tote win price; SCR = scratched")

    placings, exotics = extract_results(event)
    if placings:
        print("\nResult")
        print_grid(placings, price_cols={"SP", "Fixed Win", "Fixed Place", "Tote Win", "Tote Place"})
    if exotics:
        print("\nExotics")
        print_grid(exotics, price_cols={"Dividend"})


WEB_ONLY_COLS = ("Comment", "Open", "Flucs")  # too wide for the terminal table


def write_csv(path, runners):
    # Flucs is a list; write it as "4.5 > 3.1 > 2.7" so it fits in one cell.
    rows = [{**r, "Flucs": " > ".join(f"{p:g}" for p in r["Flucs"])} for r in runners]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="Show Sportsbet race prices as a table.")
    ap.add_argument("source", nargs="?",
                    help="a Sportsbet race link, or a file holding a 'Copy as cURL' "
                         "command (default: read a link or cURL command from stdin)")
    ap.add_argument("--json", metavar="FILE",
                    help="parse a saved RacecardWithContext JSON response instead of fetching")
    ap.add_argument("--csv", metavar="FILE", help="also save the table as CSV")
    args = ap.parse_args()

    try:
        if args.json:
            with open(args.json) as f:
                event, runners = extract_runners(json.load(f))
        else:
            if args.source and re.match(r"https?://", args.source):
                text = args.source
            elif args.source:
                with open(args.source) as f:
                    text = f.read()
            else:
                if sys.stdin.isatty():
                    print("Paste a race link (or cURL command), then press Enter and Ctrl-D:",
                          file=sys.stderr)
                text = sys.stdin.read()
            event, runners = get_race(text)
    except (ValueError, OSError) as e:
        sys.exit(f"Error: {e}")

    if not runners:
        sys.exit("No runners found for this race.")
    print_table(event, runners)
    if args.csv:
        write_csv(args.csv, runners)
        print(f"Saved CSV to {args.csv}")


if __name__ == "__main__":
    main()
