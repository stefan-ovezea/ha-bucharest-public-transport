"""Streaming, atomic GTFS imports and indexed timetable queries."""

import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import date
from itertools import groupby
from pathlib import Path

from .search import normalize
from .time import seconds, service_dates, service_epoch

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE agencies (agency_id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE routes (route_id TEXT PRIMARY KEY, agency_id TEXT, short_name TEXT, long_name TEXT, type INTEGER, color TEXT);
CREATE TABLE stops (stop_id TEXT PRIMARY KEY, name TEXT, latitude REAL, longitude REAL, parent TEXT, location_type INTEGER, platform TEXT);
CREATE INDEX stops_parent ON stops(parent);
CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, headsign TEXT, direction INTEGER, shape_id TEXT);
CREATE TABLE calendar (service_id TEXT PRIMARY KEY, weekdays TEXT, start_date TEXT, end_date TEXT);
CREATE TABLE exceptions (service_id TEXT, day TEXT, type INTEGER, PRIMARY KEY(service_id,day));
CREATE TABLE stop_times (trip_id TEXT, stop_id TEXT, sequence INTEGER, arrival INTEGER, departure INTEGER, pickup INTEGER, headsign TEXT, PRIMARY KEY(trip_id,sequence));
CREATE TABLE stop_routes (stop_id TEXT, route_id TEXT, direction INTEGER, headsign TEXT);
CREATE INDEX stop_routes_stop ON stop_routes(stop_id);
CREATE TABLE estimates (trip_id TEXT, sequence INTEGER, time INTEGER, before_sequence INTEGER, after_sequence INTEGER, PRIMARY KEY(trip_id,sequence));
"""


def _rows(archive: zipfile.ZipFile, name: str):
    if name not in archive.namelist():
        return
    with (
        archive.open(name) as raw,
        io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream,
    ):
        yield from csv.DictReader(stream)


def _int(value, default=0):
    return int(value) if value not in ("", None) else default


def _batches(connection, sql, rows):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) == 5000:
            connection.executemany(sql, batch)
            batch.clear()
    if batch:
        connection.executemany(sql, batch)


def _estimate_metro_times(db):
    """Bound missing subway calls by published anchors; never extrapolate.

    Equal intervals between actual calls are an approximation, not published
    times. Sequence numbers are identifiers and may have gaps at branch joins.
    Keep estimates separate so raw times and realtime delay bases stay intact.
    """
    calls = db.execute(
        "SELECT s.* FROM stop_times s JOIN trips t USING(trip_id) "
        "JOIN routes r USING(route_id) WHERE r.type=1 ORDER BY s.trip_id,s.sequence"
    )
    for trip_id, rows in groupby(calls, key=lambda row: row[0]):
        rows = list(rows)
        before = None
        for index, row in enumerate(rows):
            # SQL column order: trip, stop, sequence, arrival, departure, pickup, headsign.
            if row[3] is None and row[4] is None:
                continue
            if before is not None:
                left = rows[before]
                start = left[4] if left[4] is not None else left[3]
                end = row[3] if row[3] is not None else row[4]
                if end > start:
                    db.executemany(
                        "INSERT INTO estimates VALUES (?,?,?,?,?)",
                        (
                            (
                                trip_id,
                                rows[j][2],
                                round(start + (end - start) * (j - before) / (index - before)),
                                left[2],
                                row[2],
                            )
                            for j in range(before + 1, index)
                        ),
                    )
            before = index


def import_feed(payload: bytes, destination: Path, headers: dict | None = None) -> bool:
    """Build and validate a replacement; never damage the previous database on error."""
    if len(payload) > 32 * 1024 * 1024:
        raise ValueError("GTFS archive exceeds 32 MiB")
    checksum = hashlib.sha256(payload).hexdigest()
    previous = Schedule(destination)
    if previous.ready and previous.meta.get("checksum") == checksum:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix="gtfs-", suffix=".sqlite", dir=destination.parent)
    os.close(fd)
    temporary = Path(filename)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive, sqlite3.connect(temporary) as db:
            if sum(i.file_size for i in archive.infolist()) > 256 * 1024 * 1024:
                raise ValueError("Uncompressed GTFS exceeds 256 MiB")
            if not {"agency.txt", "routes.txt", "stops.txt", "trips.txt", "stop_times.txt"} <= set(
                archive.namelist()
            ):
                raise ValueError("GTFS is missing required files")
            db.executescript(SCHEMA)
            _batches(
                db,
                "INSERT INTO agencies VALUES (?,?)",
                ((r["agency_id"], r["agency_name"]) for r in _rows(archive, "agency.txt")),
            )
            _batches(
                db,
                "INSERT INTO routes VALUES (?,?,?,?,?,?)",
                (
                    (
                        r["route_id"],
                        r.get("agency_id", ""),
                        r.get("route_short_name", ""),
                        r.get("route_long_name", ""),
                        _int(r.get("route_type")),
                        r.get("route_color", ""),
                    )
                    for r in _rows(archive, "routes.txt")
                ),
            )
            _batches(
                db,
                "INSERT INTO stops VALUES (?,?,?,?,?,?,?)",
                (
                    (
                        r["stop_id"],
                        r["stop_name"],
                        float(r.get("stop_lat") or 0),
                        float(r.get("stop_lon") or 0),
                        r.get("parent_station", ""),
                        _int(r.get("location_type")),
                        r.get("platform_code", ""),
                    )
                    for r in _rows(archive, "stops.txt")
                ),
            )
            _batches(
                db,
                "INSERT INTO trips VALUES (?,?,?,?,?,?)",
                (
                    (
                        r["trip_id"],
                        r["route_id"],
                        r["service_id"],
                        r.get("trip_headsign", ""),
                        _int(r.get("direction_id"), None),
                        r.get("shape_id", ""),
                    )
                    for r in _rows(archive, "trips.txt")
                ),
            )
            weekdays = (
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            )
            _batches(
                db,
                "INSERT INTO calendar VALUES (?,?,?,?)",
                (
                    (
                        r["service_id"],
                        "".join(r[d] for d in weekdays),
                        r["start_date"],
                        r["end_date"],
                    )
                    for r in _rows(archive, "calendar.txt")
                ),
            )
            _batches(
                db,
                "INSERT INTO exceptions VALUES (?,?,?)",
                (
                    (r["service_id"], r["date"], int(r["exception_type"]))
                    for r in _rows(archive, "calendar_dates.txt")
                ),
            )
            _batches(
                db,
                "INSERT INTO stop_times VALUES (?,?,?,?,?,?,?)",
                (
                    (
                        r["trip_id"],
                        r["stop_id"],
                        int(r["stop_sequence"]),
                        seconds(r.get("arrival_time", "")),
                        seconds(r.get("departure_time", "")),
                        _int(r.get("pickup_type")),
                        r.get("stop_headsign", ""),
                    )
                    for r in _rows(archive, "stop_times.txt")
                ),
            )
            for table in ("agencies", "routes", "stops", "trips", "stop_times"):
                if not db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    raise ValueError(f"GTFS table {table} is empty")
            if db.execute(
                "SELECT 1 FROM stop_times s LEFT JOIN trips t USING(trip_id) LEFT JOIN stops p USING(stop_id) WHERE t.trip_id IS NULL OR p.stop_id IS NULL LIMIT 1"
            ).fetchone():
                raise ValueError("GTFS stop times reference missing trips or stops")
            if db.execute(
                "SELECT 1 FROM trips t LEFT JOIN routes r USING(route_id) WHERE r.route_id IS NULL LIMIT 1"
            ).fetchone():
                raise ValueError("GTFS trips reference missing routes")
            bounds = db.execute(
                "SELECT min(d),max(d) FROM (SELECT start_date d FROM calendar UNION ALL SELECT end_date FROM calendar UNION ALL SELECT day FROM exceptions WHERE type=1)"
            ).fetchone()
            if not bounds[0]:
                raise ValueError("GTFS has no service calendar")
            _estimate_metro_times(db)
            db.execute(
                "INSERT INTO stop_routes SELECT DISTINCT s.stop_id,t.route_id,t.direction,t.headsign FROM stop_times s JOIN trips t USING(trip_id)"
            )
            db.execute(
                "CREATE INDEX stop_times_stop ON stop_times(stop_id,coalesce(arrival,departure))"
            )
            db.execute("ANALYZE")
            metadata = {
                "schema": SCHEMA_VERSION,
                "checksum": checksum,
                "start_date": bounds[0],
                "end_date": bounds[1],
                "max_seconds": db.execute(
                    "SELECT max(max(coalesce(arrival,0),coalesce(departure,0))) FROM stop_times"
                ).fetchone()[0],
                "etag": (headers or {}).get("ETag"),
                "last_modified": (headers or {}).get("Last-Modified"),
            }
            db.executemany(
                "INSERT INTO meta VALUES (?,?)", ((k, json.dumps(v)) for k, v in metadata.items())
            )
        os.replace(temporary, destination)
        return True
    finally:
        temporary.unlink(missing_ok=True)


class Schedule:
    """Connections are short lived and opened on the executor thread using them."""

    def __init__(self, path: Path):
        self.path = path
        self.meta: dict = {}
        self.routes: dict[str, dict] = {}
        self.stops: dict[str, dict] = {}
        self.calendars: list[dict] = []
        self.exceptions: list[dict] = []
        self.ready = False
        if not path.exists():
            return
        try:
            with self.connect() as db:
                self.meta = {r[0]: json.loads(r[1]) for r in db.execute("SELECT * FROM meta")}
                if self.meta.get("schema") != SCHEMA_VERSION:
                    return
                self.routes = {
                    r["route_id"]: dict(r)
                    for r in db.execute(
                        "SELECT r.*,a.name agency FROM routes r LEFT JOIN agencies a USING(agency_id)"
                    )
                }
                self.stops = {r["stop_id"]: dict(r) for r in db.execute("SELECT * FROM stops")}
                links = defaultdict(list)
                for r in db.execute("SELECT * FROM stop_routes"):
                    route = self.routes[r["route_id"]]
                    links[r["stop_id"]].append(
                        {
                            "route_id": r["route_id"],
                            "short_name": route["short_name"],
                            "agency": route["agency"],
                            "direction_id": r["direction"],
                            "headsign": r["headsign"],
                        }
                    )
                for sid, stop in self.stops.items():
                    stop["normalized"] = normalize(stop["name"])
                    stop["routes"] = links[sid]
                for stop in self.stops.values():
                    if stop["parent"] in self.stops:
                        parent = self.stops[stop["parent"]]
                        parent["routes"].extend(
                            r for r in stop["routes"] if r not in parent["routes"]
                        )
                self.calendars = [dict(r) for r in db.execute("SELECT * FROM calendar")]
                self.exceptions = [dict(r) for r in db.execute("SELECT * FROM exceptions")]
                self.ready = db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.Error, ValueError, KeyError:
            self.ready = False

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if checksum := self.meta.get("checksum"):
                current = connection.execute(
                    "SELECT value FROM meta WHERE key='checksum'"
                ).fetchone()
                if current is None or json.loads(current[0]) != checksum:
                    raise ValueError("Timetable cache changed; retry with the current schedule")
            yield connection
        finally:
            connection.close()

    def stop_ids(self, stop_id: str) -> list[str]:
        if stop_id not in self.stops:
            raise ValueError("Unknown stop_id")
        found = {stop_id}
        while (
            children := {s["stop_id"] for s in self.stops.values() if s["parent"] in found} - found
        ):
            found.update(children)
        return sorted(s for s in found if self.stops[s]["location_type"] == 0)

    def active_services(self, day: date) -> set[str]:
        value = day.strftime("%Y%m%d")
        active = {
            r["service_id"]
            for r in self.calendars
            if r["start_date"] <= value <= r["end_date"] and r["weekdays"][day.weekday()] == "1"
        }
        for row in self.exceptions:
            if row["day"] == value:
                if row["type"] == 1:
                    active.add(row["service_id"])
                else:
                    active.discard(row["service_id"])
        return active

    def lookup_trips(self, trip_ids: set[str]) -> dict[str, dict]:
        if not trip_ids:
            return {}
        with self.connect() as db:
            return {
                r["trip_id"]: dict(r)
                for r in db.execute(
                    f"SELECT * FROM trips WHERE trip_id IN ({','.join('?' for _ in trip_ids)})",
                    sorted(trip_ids),
                )
            }

    def position_stops(self, calls: list[tuple[str, int | None, str]]) -> set[tuple]:
        """Only explicitly consistent trip/sequence/stop references confer stop status."""
        valid = set()
        with self.connect() as db:
            for trip_id, sequence, stop_id in calls:
                if not stop_id:
                    continue
                if sequence is None:
                    exists = db.execute(
                        "SELECT 1 FROM stop_times WHERE trip_id=? AND stop_id=? LIMIT 1",
                        (trip_id, stop_id),
                    ).fetchone()
                else:
                    exists = db.execute(
                        "SELECT 1 FROM stop_times WHERE trip_id=? AND sequence=? AND stop_id=?",
                        (trip_id, sequence, stop_id),
                    ).fetchone()
                if exists:
                    valid.add((trip_id, sequence, stop_id))
        return valid

    def trip_calls(self, trip_ids: set[str]) -> dict[tuple, dict]:
        calls = {}
        ids = sorted(trip_ids)
        with self.connect() as db:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                for row in db.execute(
                    f"SELECT trip_id,sequence,stop_id,arrival,departure FROM stop_times WHERE trip_id IN ({','.join('?' for _ in chunk)})",
                    chunk,
                ):
                    calls[(row["trip_id"], row["sequence"])] = dict(row)
        return calls

    def board_rows(
        self,
        stop_id: str,
        now: float,
        horizon: int,
        live_trip_ids: set[str],
        estimate_missing_times: bool = False,
    ):
        stop_ids = self.stop_ids(stop_id)
        if not stop_ids:
            return [], [], False
        placeholder = ",".join("?" for _ in stop_ids)
        effective = (
            "coalesce(s.arrival,s.departure,e.time)"
            if estimate_missing_times
            else "coalesce(s.arrival,s.departure)"
        )
        sql = f"SELECT s.*,t.route_id,t.service_id,t.direction,t.headsign trip_headsign,e.time estimated,e.before_sequence,e.after_sequence FROM stop_times s JOIN trips t USING(trip_id) LEFT JOIN estimates e ON s.trip_id=e.trip_id AND s.sequence=e.sequence WHERE s.stop_id IN ({placeholder})"
        scheduled = []
        with self.connect() as db:
            for day in service_dates(now, horizon, self.meta["max_seconds"]):
                active = self.active_services(day)
                if not active:
                    continue
                epoch = service_epoch(day)
                query = (
                    sql
                    + f" AND t.service_id IN ({','.join('?' for _ in active)}) AND {effective} BETWEEN ? AND ?"
                )
                for row in db.execute(
                    query, [*stop_ids, *sorted(active), now - epoch, now + horizon * 60 - epoch]
                ):
                    scheduled.append(
                        {**dict(row), "service_date": day.strftime("%Y%m%d"), "epoch": epoch}
                    )
            live_rows = []
            # Chunk to stay below SQLite variable limits even on unusually large feeds.
            trip_ids = sorted(live_trip_ids)
            for start in range(0, len(trip_ids), 500):
                chunk = trip_ids[start : start + 500]
                query = sql + f" AND s.trip_id IN ({','.join('?' for _ in chunk)})"
                live_rows.extend(dict(r) for r in db.execute(query, [*stop_ids, *chunk]))
            has_times = (
                db.execute(
                    f"SELECT 1 FROM stop_times s LEFT JOIN estimates e ON s.trip_id=e.trip_id AND s.sequence=e.sequence WHERE s.stop_id IN ({placeholder}) AND {effective} IS NOT NULL LIMIT 1",
                    stop_ids,
                ).fetchone()
                is not None
            )
        return scheduled, live_rows, has_times

    def public_stop(self, stop_id: str) -> dict:
        return {k: v for k, v in self.stops[stop_id].items() if k != "normalized"}
