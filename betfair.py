"""Betfair Exchange back/lay prices and BSP for a Sportsbet race.

Needs a Betfair app key plus either a username/password (the app logs in for
you) or an existing session token. Set them in .env:

    BETFAIR_APP_KEY=...
    BETFAIR_USERNAME=...
    BETFAIR_PASSWORD=...
    # or instead of username/password:
    BETFAIR_SESSION_TOKEN=...

Why markets are saved to disk
-----------------------------
Betfair's market search (listMarketCatalogue) only returns markets that are
still open, but prices and BSP for a closed market are still available from
listMarketBook if you know its market id and runners' selection ids. So every
market we see is saved to data/betfair_markets.json, and snapshot() saves the
next 24 hours of racing markets in every country (the web app runs it every
10 minutes). A
resulted race can then be looked up there to show its BSP.

Only the Python standard library is used.
"""

import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOGIN_URL = "https://identitysso.betfair.com.au/api/login"
API_URL = "https://api.betfair.com/exchange/betting/rest/v1.0/"
STORE_PATH = Path(__file__).with_name("data") / "betfair_markets.json"

# Betfair lists Australian harness racing under Horse Racing (7).
EVENT_TYPE_IDS = {"horse": "7", "harness": "7", "greyhound": "4339"}
SITE_PATHS = {"7": "horse-racing", "4339": "greyhound-racing"}

# How far either side of Sportsbet's start time to look for the Betfair market.
SEARCH_WINDOW = timedelta(minutes=20)
NOT_FOUND_RETRY = 60       # seconds before searching Betfair again for a missing market
STORE_KEEP_DAYS = 14       # forget saved markets older than this
TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"


class BetfairError(Exception):
    pass


def _norm(text):
    """Lower-case and strip everything but letters/digits: 'Bangor-On-Dee' -> 'bangorondee'."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _runner_name(name):
    """Betfair names runners like '1. Molesworth'; drop the number prefix."""
    return _norm(re.sub(r"^\s*\d+\.\s*", "", name or ""))


def _finite(value):
    """Betfair sends NaN/Infinity for an unsettled BSP, which isn't valid JSON for the browser."""
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _parse_time(text):
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def _market_record(m, event_type_id):
    """Keep just what we need from a listMarketCatalogue entry."""
    by_number, by_name = {}, {}
    for r in m.get("runners", []):
        cloth = (r.get("metadata") or {}).get("CLOTH_NUMBER")
        lead = re.match(r"\s*(\d+)\.", r.get("runnerName", ""))
        number = cloth if cloth and cloth.isdigit() else (lead.group(1) if lead else None)
        if number:
            by_number[str(int(number))] = r["selectionId"]
        by_name[_runner_name(r.get("runnerName"))] = r["selectionId"]
    return {
        "marketId": m["marketId"],
        "marketName": m.get("marketName", ""),
        "start": m["marketStartTime"],
        "venue": (m.get("event") or {}).get("venue", ""),
        "eventTypeId": event_type_id,
        "byNumber": by_number,
        "byName": by_name,
    }


class Betfair:
    def __init__(self, app_key, username=None, password=None, session_token=None,
                 store_path=STORE_PATH):
        self.app_key = app_key
        self.username = username
        self.password = password
        self.token = session_token
        self.store_path = Path(store_path)
        self._lock = threading.Lock()        # login
        self._store_lock = threading.Lock()  # saved markets
        self._not_found = {}                 # sportsbet event id -> time of last failed search
        self.markets = self._load_store()    # market id -> record from _market_record()

    @property
    def configured(self):
        return bool(self.app_key and (self.token or (self.username and self.password)))

    # ------------------------------------------------------------------ #
    # Saved markets
    # ------------------------------------------------------------------ #
    def _load_store(self):
        try:
            return json.loads(self.store_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_markets(self, records):
        if not records:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=STORE_KEEP_DAYS)
        with self._store_lock:
            for rec in records:
                self.markets[rec["marketId"]] = rec
            self.markets = {k: v for k, v in self.markets.items() if _parse_time(v["start"]) >= cutoff}
            self.store_path.parent.mkdir(exist_ok=True)
            tmp = self.store_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.markets))
            os.replace(tmp, self.store_path)  # atomic, so a crash can't leave half a file

    # ------------------------------------------------------------------ #
    # Low-level API
    # ------------------------------------------------------------------ #
    def _login(self):
        if not (self.username and self.password):
            raise BetfairError("Betfair session expired and no username/password is set to log in again.")
        body = urllib.parse.urlencode({"username": self.username, "password": self.password}).encode()
        req = urllib.request.Request(LOGIN_URL, data=body, headers={
            "X-Application": self.app_key,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
        if data.get("status") != "SUCCESS":
            raise BetfairError(f"Betfair login failed: {data.get('error') or data}")
        self.token = data["token"]

    def _call(self, method, params, retry=True):
        with self._lock:
            if not self.token:
                self._login()
            token = self.token

        req = urllib.request.Request(API_URL + method + "/", data=json.dumps(params).encode(), headers={
            "X-Application": self.app_key,
            "X-Authentication": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if retry and ("INVALID_SESSION" in detail or "NO_SESSION" in detail) and self.username:
                with self._lock:
                    if self.token == token:  # nobody else has logged in meanwhile
                        self.token = None
                return self._call(method, params, retry=False)
            match = re.search(r'"errorCode"\s*:\s*"([A-Z_]+)"', detail)
            raise BetfairError(f"Betfair {method} failed: {match.group(1) if match else detail[:200]}")

    def _catalogue(self, event_type_id, start_from, start_to, countries=None):
        """Open WIN markets for one event type in a time window, as saved records."""
        market_filter = {
            "eventTypeIds": [event_type_id],
            "marketTypeCodes": ["WIN"],
            "marketStartTime": {"from": start_from.strftime(TIME_FMT), "to": start_to.strftime(TIME_FMT)},
        }
        if countries:
            market_filter["marketCountries"] = countries
        # These projections have zero request "weight", so up to 1000 markets per call.
        markets = self._call("listMarketCatalogue", {
            "filter": market_filter,
            "marketProjection": ["EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION"],
            "maxResults": 1000,
        })
        records = [_market_record(m, event_type_id) for m in markets]
        self._save_markets(records)
        return records

    def snapshot(self, countries=None):
        """Save every open racing WIN market (all countries by default) for the next 24 hours.

        Run regularly so markets are on file before they close (see module docstring).
        Requested in 6-hour slices so no single call hits Betfair's 1000-market limit.
        Returns how many markets were saved.
        """
        now = datetime.now(timezone.utc)
        total = 0
        for event_type_id in sorted(set(EVENT_TYPE_IDS.values())):
            for hours in range(-2, 24, 6):
                total += len(self._catalogue(event_type_id, now + timedelta(hours=hours),
                                             now + timedelta(hours=hours + 6),
                                             list(countries) if countries else None))
        return total

    def covers_venue(self, event):
        """True if any saved market is at this race's venue, i.e. Betfair offers it."""
        venue = _norm(event.get("competitionName"))
        with self._store_lock:
            venues = {_norm(m["venue"]) for m in self.markets.values()}
        return any(v and (v == venue or v.startswith(venue) or venue.startswith(v)) for v in venues)

    # ------------------------------------------------------------------ #
    # Finding the Betfair market that matches a Sportsbet race
    # ------------------------------------------------------------------ #
    def _best_match(self, event, records):
        """Same venue (allowing small spelling differences), then the closest start
        time, preferring a market named for the same race number."""
        venue = _norm(event.get("competitionName"))
        start = datetime.fromtimestamp(event["startTime"], timezone.utc)
        event_type_id = EVENT_TYPE_IDS.get(event.get("type", "horse"), "7")
        race_prefix = f"R{event.get('raceNumber')} "

        def same_venue(rec):
            bf = _norm(rec["venue"])
            return bool(bf and venue) and (bf == venue or bf.startswith(venue) or venue.startswith(bf))

        candidates = [
            rec for rec in records
            if rec["eventTypeId"] == event_type_id and same_venue(rec)
            and abs(_parse_time(rec["start"]) - start) <= SEARCH_WINDOW
        ]
        candidates.sort(key=lambda rec: (not rec["marketName"].startswith(race_prefix),
                                         abs((_parse_time(rec["start"]) - start).total_seconds())))
        return candidates[0] if candidates else None

    def _find_market(self, event):
        # 1. Markets already on file - this is how closed (resulted) races are found.
        with self._store_lock:
            saved = list(self.markets.values())
        found = self._best_match(event, saved)
        if found:
            return found

        # 2. Ask Betfair; only works while the market is still open.
        event_id = event.get("id")
        if time.time() - self._not_found.get(event_id, 0) < NOT_FOUND_RETRY:
            return None
        start = datetime.fromtimestamp(event["startTime"], timezone.utc)
        event_type_id = EVENT_TYPE_IDS.get(event.get("type", "horse"), "7")
        records = self._catalogue(event_type_id, start - SEARCH_WINDOW, start + SEARCH_WINDOW)
        found = self._best_match(event, records)
        if not found:
            self._not_found[event_id] = time.time()
        return found

    # ------------------------------------------------------------------ #
    # Public
    # ------------------------------------------------------------------ #
    def race_prices(self, event, runners):
        """Return (market_info, {runner number: prices}) for a Sportsbet race.

        market_info is None if no matching Betfair market could be found.
        """
        market = self._find_market(event)
        if market is None:
            return None, {}

        books = self._call("listMarketBook", {
            "marketIds": [market["marketId"]],
            # SP_AVAILABLE includes actualSP (the BSP), which stays available after the market closes.
            # virtualise=True includes Betfair's cross-matched "virtual" bets, which is what the
            # Betfair website shows; without it the API returns worse, raw prices.
            # availableToBack/availableToLay come best-first (highest back, lowest lay).
            "priceProjection": {"priceData": ["EX_BEST_OFFERS", "SP_AVAILABLE"],
                                "exBestOffersOverrides": {"bestPricesDepth": 1},
                                "virtualise": True},
        })
        book = books[0] if books else {}
        by_selection = {r["selectionId"]: r for r in book.get("runners", [])}

        prices = {}
        for runner in runners:
            sel_id = (market["byNumber"].get(str(runner["No"]))
                      or market["byName"].get(_runner_name(runner["Runner"])))
            r = by_selection.get(sel_id)
            if not r:
                continue
            ex = r.get("ex") or {}
            back = (ex.get("availableToBack") or [{}])[0]
            lay = (ex.get("availableToLay") or [{}])[0]
            removed = r.get("status") == "REMOVED"
            prices[runner["No"]] = {
                "Back": None if removed else back.get("price"),
                "BackSize": None if removed else back.get("size"),
                "Lay": None if removed else lay.get("price"),
                "LaySize": None if removed else lay.get("size"),
                "BSP": None if removed else _finite((r.get("sp") or {}).get("actualSP")),
                "BetfairStatus": r.get("status"),
            }

        info = {
            "marketName": market["marketName"],
            "url": f"https://www.betfair.com.au/exchange/plus/"
                   f"{SITE_PATHS.get(market['eventTypeId'], 'horse-racing')}/market/{market['marketId']}",
            "status": book.get("status"),
            "inplay": book.get("inplay", False),
            "matched": book.get("totalMatched"),
        }
        return info, prices
