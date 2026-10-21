"""Validate rider-visible behavior using real protobuf messages and SQLite caches."""

import io
import sqlite3
import zipfile
from datetime import UTC, date, datetime

import pytest
from google.protobuf.message import DecodeError

from custom_components.bucharest_transport.transport.realtime import decode, fresh
from custom_components.bucharest_transport.transport.schedule import Schedule, import_feed
from custom_components.bucharest_transport.transport.search import search
from custom_components.bucharest_transport.transport.time import TZ, service_epoch

from .conftest import archive, message, trip_update, vehicle


@pytest.mark.parametrize("query", ["Piata Unirii", "Pta Uniri", "Pța Unirii", "unirri", "UNIRII"])
def test_permissive_search_preserves_physical_stops(schedule, query):
    results = search(list(schedule.stops.values()), query)
    assert {"U", "U2"} <= {r["stop_id"] for r in results}
    assert len({r["stop_id"] for r in results}) == len(results)
    assert all("normalized" not in r for r in results)


def test_search_metadata_and_parent_stations(schedule):
    assert schedule.routes["041"]["short_name"] == "41"
    assert schedule.stop_ids("MP") == ["MA"]
    assert schedule.stop_ids("U") == ["U"]
    assert schedule.public_stop("MP")["routes"][0]["short_name"] == "M1"
    assert search(list(schedule.stops.values()), "x") == []


def test_timetable_loop_and_direction(engine, now):
    board = engine.board("U", now)
    assert board["status"] == "timetable"
    assert len(board["arrivals"]) == 2
    assert [r["stop_sequence"] for r in board["arrivals"]] == [0, 2]
    assert all(r["route_id"] == "041" for r in board["arrivals"])
    assert engine.board("U", now, direction_id=1)["arrivals"] == []
    assert len(engine.board("U2", now)["arrivals"]) == 1


def test_metro_partial_timetable(engine, now):
    assert engine.board("MP", now)["arrivals"][0]["source"] == "timetable"
    assert engine.board("MB", now)["status"] == "times_unavailable"
    assert engine.board("MB", now)["arrivals"] == []
    assert engine.board("MB", now)["realtime_status"] == "unavailable"


def test_realtime_replaces_schedule_without_losing_loop(engine, schedule, now):
    feed = message(now)
    trip_update(feed, now, arrival_offset=480)
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    rows = engine.board("U", now)["arrivals"]
    assert len(rows) == 2
    assert rows[0]["source"] == "live"
    assert rows[0]["delay_seconds"] == 180
    assert rows[0]["scheduled_time"] == "2026-10-21T09:05:00+00:00"
    assert rows[0]["predicted_time"] == "2026-10-21T09:08:00+00:00"
    assert rows[1]["source"] == "live"
    assert rows[1]["delay_seconds"] == 180


@pytest.mark.parametrize(
    "age,source",
    [
        (0, "live"),
        (90, "live"),
        (91, "timetable"),
        (-60, "live"),
        (-61, "timetable"),
        (3600, "timetable"),
    ],
)
def test_record_freshness_is_independent_of_header(engine, schedule, now, age, source):
    feed = message(now)
    trip_update(feed, now - age, arrival_offset=600 + age)
    snapshot = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    engine.feeds["tripUpdates"] = snapshot
    assert engine.board("U", now)["arrivals"][0]["source"] == source
    assert snapshot.health(now)["stale_records"] == (source == "timetable")


def test_stale_feed_and_missing_record_timestamp(engine, schedule, now):
    feed = message(now - 91)
    update = trip_update(feed, now)
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    assert engine.board("U", now)["realtime_status"] == "stale"
    assert engine.board("U", now)["arrivals"][0]["source"] == "timetable"
    feed.header.timestamp = int(now)
    update.ClearField("timestamp")
    snapshot = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    assert snapshot.records[0]["timestamp_basis"] == "feed"
    engine.feeds["tripUpdates"] = snapshot
    assert engine.board("U", now)["arrivals"][0]["source"] == "live"
    feed.header.ClearField("timestamp")
    assert not decode(feed.SerializeToString(), "tripUpdates", schedule, now).fresh(now)


@pytest.mark.parametrize("relationship", [3, 7])
def test_cancelled_and_deleted_trip_suppress_all_calls(engine, schedule, now, relationship):
    feed = message(now)
    trip_update(feed, now, stop_id=None).trip.schedule_relationship = relationship
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    assert engine.board("U", now)["arrivals"] == []


def test_skipped_and_no_data_calls(engine, schedule, now):
    feed = message(now)
    update = trip_update(feed, now)
    update.stop_time_update[0].schedule_relationship = 1
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    assert [r["stop_sequence"] for r in engine.board("U", now)["arrivals"]] == [2]
    update.stop_time_update[0].schedule_relationship = 2
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    assert engine.board("U", now)["arrivals"][0]["source"] == "timetable"


def test_departures_and_metro_live_data_activate_without_special_casing(engine, schedule, now):
    feed = message(now)
    update = trip_update(feed, now, trip_id="METRO", stop_id="MB", sequence=1)
    stop = update.stop_time_update[0]
    stop.ClearField("arrival")
    stop.departure.time = int(now + 600)
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    row = engine.board("MB", now)["arrivals"][0]
    assert row["source"] == "live"
    assert row["event_type"] == "departure"
    assert row["scheduled_time"] is None


def test_expired_schedule_not_presented_as_current(engine, now):
    future = datetime(2027, 1, 6, 12, tzinfo=TZ).timestamp()
    assert engine.board("U", future)["status"] == "times_unavailable"
    assert not engine.board("U", future)["timetable_valid"]


def test_overnight_service_date_and_exceptions(engine, schedule):
    after_midnight = datetime(2026, 10, 22, 1, 5, tzinfo=TZ).timestamp()
    row = engine.board("U", after_midnight)["arrivals"][0]
    assert row["trip_id"] == "NIGHT"
    assert row["service_date"] == "20261021"
    assert row["time"] == "2026-10-21T22:10:00+00:00"
    assert not schedule.active_services(date(2026, 10, 23))
    assert schedule.active_services(date(2026, 10, 25)) == {"WK"}


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 10, 25), "2026-10-24T22:00:00+00:00"),
        (date(2026, 3, 29), "2026-03-28T21:00:00+00:00"),
    ],
)
def test_gtfs_noon_based_service_origin_on_dst_days(day, expected):
    assert datetime.fromtimestamp(service_epoch(day), UTC).isoformat() == expected


@pytest.mark.parametrize(
    "status,expected",
    [(0, "incoming_at"), (1, "stopped_at"), (2, "in_transit_to"), (None, "unknown")],
)
def test_explicit_vehicle_stop_semantics(engine, schedule, now, status, expected):
    feed = message(now)
    vehicle(feed, now, status=status)
    engine.feeds["vehiclePositions"] = decode(
        feed.SerializeToString(), "vehiclePositions", schedule, now
    )
    row = engine.vehicles(now)[0]
    assert row["key"] == "1:12"
    assert row["direction_id"] == 0
    assert row["headsign"] == "Piața Presei"
    assert row["stop_status"] == expected
    assert engine.vehicles(now + 91) == []


def test_inconsistent_stop_and_disappearing_vehicle(engine, schedule, now):
    feed = message(now)
    vehicle(feed, now, stop_id="R", sequence=0)
    engine.feeds["vehiclePositions"] = decode(
        feed.SerializeToString(), "vehiclePositions", schedule, now
    )
    assert engine.vehicles(now)[0]["stop_status"] == "unknown"
    assert engine.vehicles(now)[0]["stop_name"] is None
    engine.feeds["vehiclePositions"] = decode(
        message(now).SerializeToString(), "vehiclePositions", schedule, now
    )
    assert engine.vehicles(now) == []


def test_alerts_periods_selectors_and_feed_freshness(engine, schedule, now):
    feed = message(now)
    alert = feed.entity.add()
    alert.id = "alert-1"
    alert.alert.header_text.translation.add(text="Line disruption", language="en")
    alert.alert.informed_entity.add(route_id="041", stop_id="U")
    alert.alert.active_period.add(start=int(now - 100), end=int(now + 1000))
    engine.feeds["serviceAlerts"] = decode(feed.SerializeToString(), "serviceAlerts", schedule, now)
    assert len(engine.board("U", now)["alerts"]) == 1
    assert engine.board("U2", now)["alerts"] == []
    assert engine.board("U", now + 601)["alerts_status"] == "stale"
    assert engine.board("U", now + 601)["alerts"] == []


def test_rejects_malformed_and_differential_feeds(schedule, now):
    with pytest.raises((ValueError, DecodeError)):
        decode(b"this is not a protobuf", "vehiclePositions", schedule, now)
    feed = message(now)
    feed.header.incrementality = 1
    with pytest.raises(ValueError, match="Differential"):
        decode(feed.SerializeToString(), "vehiclePositions", schedule, now)
    assert not fresh(None, now)


def test_repeated_stop_without_sequence_is_ambiguous(engine, schedule, now):
    feed = message(now)
    update = trip_update(feed, now)
    update.stop_time_update[0].ClearField("stop_sequence")
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    rows = engine.board("U", now)["arrivals"]
    assert len(rows) == 2
    assert all(r["source"] == "timetable" for r in rows)


def test_delay_propagation_stops_at_no_data(engine, schedule, now):
    feed = message(now)
    update = trip_update(feed, now)
    update.stop_time_update[0].arrival.ClearField("time")
    update.stop_time_update[0].arrival.delay = 120
    update.stop_time_update.add(stop_sequence=1, stop_id="R", schedule_relationship=2)
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    rows = engine.board("U", now)["arrivals"]
    assert rows[0]["delay_seconds"] == 120
    assert rows[1]["source"] == "timetable"


def test_delayed_trip_scheduled_before_window_is_still_returned(engine, schedule, now):
    feed = message(now)
    trip_update(feed, now, arrival_offset=1800)
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    later = now + 1200
    # Keep observation and feed fresh at the later query; the scheduled 12:05 call is past.
    feed.header.timestamp = int(later)
    feed.entity[0].trip_update.timestamp = int(later)
    engine.feeds["tripUpdates"] = decode(feed.SerializeToString(), "tripUpdates", schedule, later)
    rows = engine.board("U", later)["arrivals"]
    assert any(r["stop_sequence"] == 0 and r["time"] == "2026-10-21T09:30:00+00:00" for r in rows)


def test_atomic_cache_replacement_and_restart(schedule):
    checksum = schedule.meta["checksum"]
    assert not import_feed(archive(), schedule.path)
    with pytest.raises((ValueError, sqlite3.Error)):
        import_feed(
            archive(
                {"trips.txt": [["route_id", "service_id", "trip_id"], ["missing", "WK", "T1"]]}
            ),
            schedule.path,
        )
    assert Schedule(schedule.path).ready
    assert Schedule(schedule.path).meta["checksum"] == checksum
    assert not list(schedule.path.parent.glob("gtfs-*.sqlite"))
    replacement = archive(
        {"agency.txt": [["agency_id", "agency_name"], ["1", "Renamed STB"], ["2", "Metrorex"]]}
    )
    assert import_feed(replacement, schedule.path)
    assert Schedule(schedule.path).routes["041"]["agency"] == "Renamed STB"


def test_large_import_streams_rows_and_rejects_partial_archives(tmp_path):
    rows = [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]]
    rows.extend(["T1", "12:05:00", "12:05:00", "U", str(i)] for i in range(25000))
    path = tmp_path / "large.sqlite"
    import_feed(archive({"stop_times.txt": rows}), path)
    schedule = Schedule(path)
    with schedule.connect() as db:
        assert db.execute("SELECT count(*) FROM stop_times").fetchone()[0] == 25000
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("stops.txt", "bad")
    with pytest.raises(ValueError, match="required files"):
        import_feed(data.getvalue(), path)
    assert Schedule(path).ready


def test_readers_never_mix_old_metadata_with_replaced_database(schedule):
    replacement = archive(
        {"agency.txt": [["agency_id", "agency_name"], ["1", "New STB"], ["2", "Metrorex"]]}
    )
    import_feed(replacement, schedule.path)
    with pytest.raises(ValueError, match="cache changed"):
        schedule.lookup_trips({"T1"})
    assert Schedule(schedule.path).lookup_trips({"T1"})["T1"]["route_id"] == "041"
