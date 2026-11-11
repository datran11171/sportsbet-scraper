"""MySQL storage for resulted races: results, dividends, Betfair SP, comments and
price fluctuations.

The web app runs save_resulted_races() in the background every few minutes, so
every race is stored once it's resulted. It can also be run by hand:

    .venv/bin/python db.py init                 # create the tables
    .venv/bin/python db.py save                 # save today's + yesterday's resulted races now
    .venv/bin/python db.py save --days 7        # backfill the last week (Sportsbet keeps ~1 week)
    .venv/bin/python db.py stats                # what's in the database

Connection settings come from .env (MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE,
MYSQL_USER, MYSQL_PASSWORD). Start the database with `docker compose up -d`.

Tables
------
races               one row per race (venue, time, distance, race comment, Betfair market, BSP status)
runners             one row per runner (placing, Sportsbet SP / fixed / tote prices, BSP, comment)
price_fluctuations  each runner's Sportsbet price history, oldest first
exotic_results      Quinella / Exacta / Trifecta / First 4 dividends
"""

import argparse
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pymysql

from sportsbet_prices import extract_results, get_day, get_race_by_id

log = logging.getLogger(__name__)

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS races (
        race_id           BIGINT       PRIMARY KEY COMMENT 'Sportsbet event id',
        race_date         DATE         NOT NULL COMMENT 'Sportsbet racing day',
        venue             VARCHAR(100) NOT NULL,
        race_type         ENUM('horse','greyhound','harness') NOT NULL,
        region            VARCHAR(60),
        international     BOOLEAN      NOT NULL DEFAULT FALSE,
        race_number       SMALLINT,
        race_name         VARCHAR(255),
        distance_m        INT,
        start_time_utc    DATETIME,
        race_comment      TEXT,
        betfair_market_id VARCHAR(20),
        bsp_status        VARCHAR(20)  NOT NULL COMMENT 'ok, pending, not_saved, not_found, missing, no_betfair',
        first_saved_at    DATETIME     NOT NULL,
        updated_at        DATETIME     NOT NULL,
        INDEX idx_date (race_date),
        INDEX idx_venue (venue, race_date)
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS runners (
        race_id             BIGINT       NOT NULL,
        runner_number       SMALLINT     NOT NULL,
        runner_name         VARCHAR(120) NOT NULL,
        barrier             SMALLINT,
        jockey              VARCHAR(120) COMMENT 'jockey or driver',
        trainer             VARCHAR(120),
        weight_kg           DECIMAL(5,1),
        scratched           BOOLEAN      NOT NULL DEFAULT FALSE,
        finish_position     SMALLINT     COMMENT 'NULL if unplaced or scratched',
        open_price          DECIMAL(8,2) COMMENT 'Sportsbet opening fixed win price',
        fixed_win           DECIMAL(8,2) COMMENT 'Sportsbet final fixed win price',
        fixed_place         DECIMAL(8,2),
        sportsbet_sp        DECIMAL(8,2) COMMENT 'Sportsbet starting price (placegetters only)',
        tote_win            DECIMAL(8,2) COMMENT 'tote win dividend (winner only)',
        tote_place          DECIMAL(8,2) COMMENT 'tote place dividend',
        bsp                 DECIMAL(10,4) COMMENT 'Betfair starting price',
        runner_comment      TEXT,
        PRIMARY KEY (race_id, runner_number),
        INDEX idx_name (runner_name),
        FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS price_fluctuations (
        race_id       BIGINT       NOT NULL,
        runner_number SMALLINT     NOT NULL,
        seq           SMALLINT     NOT NULL COMMENT '0 = opening price, last = final price',
        price         DECIMAL(8,2) NOT NULL,
        PRIMARY KEY (race_id, runner_number, seq),
        FOREIGN KEY (race_id, runner_number) REFERENCES runners(race_id, runner_number) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS exotic_results (
        race_id     BIGINT       NOT NULL,
        bet_type    VARCHAR(30)  NOT NULL,
        runners     VARCHAR(60)  NOT NULL COMMENT 'e.g. 6-2-11',
        dividend    DECIMAL(12,2),
        PRIMARY KEY (race_id, bet_type),
        FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4
    """,
]

PENDING_GIVE_UP = timedelta(hours=24)  # stop waiting for a BSP after this long
MAX_RACES_PER_RUN = 60                 # keep each background run short and polite
PAUSE_BETWEEN_RACES = 0.5              # seconds


def _load_env(path=Path(__file__).with_name(".env")):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _weight(text):
    match = re.match(r"([\d.]+)", text or "")
    return float(match.group(1)) if match else None


def _place(text):
    match = re.match(r"(\d+)", text or "")
    return int(match.group(1)) if match else None


def _utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None) if epoch else None


class Database:
    def __init__(self, host, port, user, password, database):
        self.params = dict(host=host, port=int(port), user=user, password=password, database=database,
                           charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, connect_timeout=5)

    @classmethod
    def from_env(cls):
        """A Database from MYSQL_* settings, or None if they aren't set."""
        _load_env()
        if not (os.environ.get("MYSQL_USER") and os.environ.get("MYSQL_PASSWORD")):
            return None
        return cls(os.environ.get("MYSQL_HOST", "127.0.0.1"), os.environ.get("MYSQL_PORT", "3306"),
                   os.environ["MYSQL_USER"], os.environ["MYSQL_PASSWORD"],
                   os.environ.get("MYSQL_DATABASE", "racing"))

    def connect(self):
        return pymysql.connect(**self.params)

    def init_schema(self):
        with self.connect() as conn, conn.cursor() as cur:
            for statement in SCHEMA:
                cur.execute(statement)
            conn.commit()

    def race_status(self, race_ids):
        """{race_id: (bsp_status, first_saved_at)} for the given ids already in the database."""
        if not race_ids:
            return {}
        with self.connect() as conn, conn.cursor() as cur:
            marks = ",".join(["%s"] * len(race_ids))
            cur.execute(f"SELECT race_id, bsp_status, first_saved_at FROM races WHERE race_id IN ({marks})",
                        list(race_ids))
            return {row["race_id"]: (row["bsp_status"], row["first_saved_at"]) for row in cur.fetchall()}

    def save_race(self, race_date, event, runners, placings, exotics, bsp_status,
                  betfair_market_id=None, bsp_by_number=None):
        """Insert or update one race and everything under it (one transaction)."""
        bsp_by_number = bsp_by_number or {}
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        placed = {p["No"]: p for p in placings}
        distance = event.get("distance")

        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO races (race_id, race_date, venue, race_type, region, international, race_number,
                                   race_name, distance_m, start_time_utc, race_comment, betfair_market_id,
                                   bsp_status, first_saved_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS new
                ON DUPLICATE KEY UPDATE
                    race_comment = new.race_comment,
                    betfair_market_id = COALESCE(new.betfair_market_id, races.betfair_market_id),
                    bsp_status = new.bsp_status, start_time_utc = new.start_time_utc,
                    updated_at = new.updated_at
                """,
                (event["id"], race_date, event.get("competitionName") or "", event.get("type", "horse"),
                 event.get("country"), bool(event.get("isInternational")), event.get("raceNumber"),
                 event.get("name"), int(distance) if str(distance or "").isdigit() else None,
                 _utc(event.get("startTime")), (event.get("overview") or "").strip() or None,
                 betfair_market_id, bsp_status, now, now))

            for r in runners:
                p = placed.get(r["No"], {})
                cur.execute(
                    """
                    INSERT INTO runners (race_id, runner_number, runner_name, barrier, jockey, trainer,
                                         weight_kg, scratched, finish_position, open_price, fixed_win,
                                         fixed_place, sportsbet_sp, tote_win, tote_place, bsp, runner_comment)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS new
                    ON DUPLICATE KEY UPDATE
                        finish_position = new.finish_position, scratched = new.scratched,
                        fixed_win = new.fixed_win, fixed_place = new.fixed_place,
                        sportsbet_sp = new.sportsbet_sp, tote_win = new.tote_win,
                        tote_place = new.tote_place, bsp = COALESCE(new.bsp, runners.bsp),
                        runner_comment = new.runner_comment
                    """,
                    (event["id"], r["No"], r["Runner"], r["Barrier"] or None, r["Jockey/Driver"] or None,
                     r["Trainer"] or None, _weight(r["Weight"]), r["Status"] == "SCR",
                     _place(p.get("Pos")), r.get("Open"), r["Win"], r["Place"], p.get("SP"),
                     p.get("Tote Win"), p.get("Tote Place"), bsp_by_number.get(r["No"]),
                     r.get("Comment") or None))

                # Price history: replace the whole series.
                cur.execute("DELETE FROM price_fluctuations WHERE race_id=%s AND runner_number=%s",
                            (event["id"], r["No"]))
                cur.executemany(
                    "INSERT INTO price_fluctuations (race_id, runner_number, seq, price) VALUES (%s,%s,%s,%s)",
                    [(event["id"], r["No"], i, price) for i, price in enumerate(r.get("Flucs") or [])])

            cur.execute("DELETE FROM exotic_results WHERE race_id=%s", (event["id"],))
            cur.executemany(
                "INSERT INTO exotic_results (race_id, bet_type, runners, dividend) VALUES (%s,%s,%s,%s)",
                [(event["id"], x["Bet"], x["Runners"], x["Dividend"]) for x in exotics])
            conn.commit()

    # ------------------------------------------------------------------ #
    # Reading, for the Results page
    # ------------------------------------------------------------------ #
    @staticmethod
    def _where(filters):
        """SQL WHERE clause + params from the Results page filters (all optional)."""
        clauses, params = [], []
        if filters.get("date_from"):
            clauses.append("r.race_date >= %s"); params.append(filters["date_from"])
        if filters.get("date_to"):
            clauses.append("r.race_date <= %s"); params.append(filters["date_to"])
        if filters.get("type") in ("horse", "greyhound", "harness"):
            clauses.append("r.race_type = %s"); params.append(filters["type"])
        if filters.get("region") == "aus":
            clauses.append("NOT r.international")
        elif filters.get("region") == "intl":
            clauses.append("r.international")
        if filters.get("venue"):
            clauses.append("r.venue LIKE %s"); params.append(f"%{filters['venue']}%")
        if filters.get("search"):
            clauses.append("""EXISTS (SELECT 1 FROM runners x WHERE x.race_id = r.race_id
                              AND (x.runner_name LIKE %s OR x.jockey LIKE %s OR x.trainer LIKE %s))""")
            params += [f"%{filters['search']}%"] * 3
        if filters.get("bsp_only"):
            clauses.append("r.bsp_status = 'ok'")
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params

    def find_races(self, filters, limit=100, offset=0):
        """(total, [race rows with their winner]) newest first."""
        where, params = self._where(filters)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n, SUM(r.bsp_status = 'ok') AS with_bsp FROM races r {where}", params)
            counts = cur.fetchone()
            # One winner per race (lowest number if there was a dead heat).
            cur.execute(f"""
                SELECT r.race_id, r.race_date, r.venue, r.race_type, r.region, r.international,
                       r.race_number, r.race_name, r.distance_m, r.start_time_utc, r.bsp_status,
                       w.runner_number AS winner_no, w.runner_name AS winner, w.jockey AS winner_jockey,
                       w.trainer AS winner_trainer, w.fixed_win AS winner_fixed, w.sportsbet_sp AS winner_sp,
                       w.tote_win AS winner_tote, w.bsp AS winner_bsp,
                       (SELECT COUNT(*) FROM runners x WHERE x.race_id = r.race_id AND NOT x.scratched) AS starters
                FROM races r
                LEFT JOIN runners w ON w.race_id = r.race_id AND w.runner_number =
                     (SELECT MIN(y.runner_number) FROM runners y WHERE y.race_id = r.race_id AND y.finish_position = 1)
                {where}
                ORDER BY r.start_time_utc DESC, r.race_id DESC
                LIMIT %s OFFSET %s""", params + [int(limit), int(offset)])
            return {"total": counts["n"], "with_bsp": int(counts["with_bsp"] or 0)}, cur.fetchall()

    def race_detail(self, race_id):
        """One saved race with all its runners, price histories and exotics, or None."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM races WHERE race_id = %s", (race_id,))
            race = cur.fetchone()
            if not race:
                return None
            cur.execute("""SELECT * FROM runners WHERE race_id = %s
                           ORDER BY scratched, finish_position IS NULL, finish_position, runner_number""",
                        (race_id,))
            runners = cur.fetchall()
            cur.execute("SELECT runner_number, price FROM price_fluctuations WHERE race_id = %s ORDER BY runner_number, seq",
                        (race_id,))
            flucs = {}
            for row in cur.fetchall():
                flucs.setdefault(row["runner_number"], []).append(row["price"])
            for r in runners:
                r["flucs"] = flucs.get(r["runner_number"], [])
            cur.execute("SELECT bet_type, runners, dividend FROM exotic_results WHERE race_id = %s", (race_id,))
            exotics = cur.fetchall()
        return {"race": race, "runners": runners, "exotics": exotics}

    def export_rows(self, filters, limit=50000):
        """Every runner in the matching races, flattened for a CSV download."""
        where, params = self._where(filters)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(f"""
                SELECT r.race_date, r.venue, r.race_type, r.region, r.race_number, r.race_name, r.distance_m,
                       r.start_time_utc, r.bsp_status, u.runner_number, u.runner_name, u.barrier, u.jockey,
                       u.trainer, u.weight_kg, u.scratched, u.finish_position, u.open_price, u.fixed_win,
                       u.fixed_place, u.sportsbet_sp, u.tote_win, u.tote_place, u.bsp, u.runner_comment,
                       (SELECT GROUP_CONCAT(f.price ORDER BY f.seq SEPARATOR ' > ') FROM price_fluctuations f
                        WHERE f.race_id = u.race_id AND f.runner_number = u.runner_number) AS flucs
                FROM races r JOIN runners u ON u.race_id = r.race_id
                {where}
                ORDER BY r.start_time_utc DESC, r.race_id, u.scratched, u.finish_position IS NULL,
                         u.finish_position, u.runner_number
                LIMIT %s""", params + [int(limit)])
            return cur.fetchall()

    def stats(self):
        with self.connect() as conn, conn.cursor() as cur:
            out = {}
            for table in ("races", "runners", "price_fluctuations", "exotic_results"):
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                out[table] = cur.fetchone()["n"]
            cur.execute("SELECT bsp_status, COUNT(*) AS n FROM races GROUP BY bsp_status ORDER BY n DESC")
            out["bsp_status"] = {row["bsp_status"]: row["n"] for row in cur.fetchall()}
            cur.execute("SELECT MIN(race_date) AS first, MAX(race_date) AS last FROM races")
            out["dates"] = cur.fetchone()
            return out


# ---------------------------------------------------------------------- #
# Finding and saving resulted races
# ---------------------------------------------------------------------- #
def _bsp_for(betfair, event, runners, placings):
    """(bsp_status, market_id, {runner number: bsp}) using the app's Betfair client."""
    if betfair is None or not betfair.configured:
        return "no_betfair", None, {}
    from betfair import BetfairError  # local import: betfair.py is optional for the CLI
    try:
        market, prices = betfair.race_prices(event, runners)
    except (BetfairError, OSError, ValueError) as e:
        log.warning("Betfair lookup failed for race %s: %s", event.get("id"), e)
        return "pending", None, {}
    if market is None:
        # not_saved: Betfair markets were seen at this venue, but not this race's (it closed first).
        # not_found: no market seen at this venue at all - either Betfair doesn't cover it,
        #            or the app wasn't running while its markets were open. Can't tell which.
        return ("not_saved" if betfair.covers_venue(event) else "not_found"), None, {}
    bsp = {no: p["BSP"] for no, p in prices.items() if p.get("BSP") is not None}
    market_id = market.get("url", "").rsplit("/", 1)[-1] or None
    return ("ok" if bsp else "pending"), market_id, bsp


def save_resulted_races(db, betfair=None, days=2, limit=MAX_RACES_PER_RUN):
    """Save resulted races from the last `days` Sportsbet racing days (today counts as 1).

    Skips races already saved, except ones still waiting for a BSP ("pending"),
    which are retried until PENDING_GIVE_UP. Returns how many races were saved.
    """
    today = date.today()
    todo = []
    for back in range(days):
        day = today - timedelta(days=back)
        for meeting in get_day(day.isoformat()):
            for race in meeting["races"]:
                if race["status"] == "resulted":
                    todo.append((day, race["id"]))

    saved_status = db.race_status([race_id for _, race_id in todo])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    saved = 0
    for day, race_id in todo:
        if saved >= limit:
            break
        status = saved_status.get(race_id)
        if status and status[0] != "pending":
            continue
        try:
            event, runners = get_race_by_id(race_id)
            placings, exotics = extract_results(event)
            if not placings:
                continue  # resulted flag but no results yet
            bsp_status, market_id, bsp = _bsp_for(betfair, event, runners, placings)
            if bsp_status == "pending" and status and now - status[1] > PENDING_GIVE_UP:
                bsp_status = "missing"
            db.save_race(day, event, runners, placings, exotics, bsp_status, market_id, bsp)
            saved += 1
        except (ValueError, OSError, pymysql.MySQLError) as e:
            log.warning("Couldn't save race %s: %s", race_id, e)
        time.sleep(PAUSE_BETWEEN_RACES)
    return saved


def main():
    ap = argparse.ArgumentParser(description="Race results database (MySQL).")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the tables")
    save = sub.add_parser("save", help="save resulted races now")
    save.add_argument("--days", type=int, default=2, help="how many racing days back, including today")
    save.add_argument("--limit", type=int, default=10000, help="maximum races to save")
    sub.add_parser("stats", help="show what's in the database")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    db = Database.from_env()
    if db is None:
        raise SystemExit("Set MYSQL_USER and MYSQL_PASSWORD in .env (see docker-compose.yml).")
    db.init_schema()
    if args.command == "save":
        betfair = None
        if os.environ.get("BETFAIR_APP_KEY"):
            from betfair import Betfair
            betfair = Betfair(os.environ["BETFAIR_APP_KEY"], os.environ.get("BETFAIR_USERNAME") or None,
                              os.environ.get("BETFAIR_PASSWORD") or None,
                              os.environ.get("BETFAIR_SESSION_TOKEN") or None)
        print("Saved", save_resulted_races(db, betfair, days=args.days, limit=args.limit), "races")
    if args.command in ("init", "stats", "save"):
        for key, value in db.stats().items():
            print(f"{key:20} {value}")


if __name__ == "__main__":
    main()
