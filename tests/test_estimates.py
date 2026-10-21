"""Estimated metro calls stay distinct from published and live times."""

from datetime import datetime

import pytest

from custom_components.bucharest_transport.transport.engine import TransportEngine
from custom_components.bucharest_transport.transport.realtime import decode
from custom_components.bucharest_transport.transport.schedule import Schedule, import_feed
from custom_components.bucharest_transport.transport.time import TZ

from .conftest import archive, message, trip_update


@pytest.fixture
def metro(tmp_path):
    path = tmp_path / "metro.sqlite"
    rows = [
        ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
        ["METRO", "12:02:00", "12:04:00", "MA", "4"],
        ["METRO", "", "", "MB", "6"],
        ["METRO", "", "", "R", "10"],
        ["METRO", "12:10:00", "12:10:00", "U", "11"],
        ["METRO", "", "", "U2", "20"],
        # Surface gaps are not covered by this opt-in feature.
        ["T1", "12:02:00", "12:02:00", "MA", "0"],
        ["T1", "", "", "MB", "1"],
        ["T1", "12:08:00", "12:08:00", "U", "2"],
    ]
    import_feed(archive({"stop_times.txt": rows}), path)
    return TransportEngine(Schedule(path))


def test_estimates_require_opt_in_and_do_not_use_sequence_number_spacing(metro, now):
    assert metro.board("MB", now)["status"] == "times_unavailable"
    board = metro.board("MB", now, estimate_missing_times=True)
    assert board["status"] == "estimated"
    assert len(board["arrivals"]) == 1
    row = board["arrivals"][0]
    assert row["source"] == "estimated"
    assert row["time"] == row["estimated_time"] == "2026-10-21T09:06:00+00:00"
    assert row["scheduled_time"] is row["predicted_time"] is row["observed_at"] is None
    assert row["event_type"] == "arrival"
    assert row["estimate_method"] == "stop_order_interpolation"
    assert row["estimate_anchor_sequences"] == [4, 11]
    with metro.schedule.connect() as db:
        assert db.execute(
            "SELECT arrival,departure FROM stop_times WHERE trip_id='METRO' AND stop_id='MB'"
        ).fetchone()[:] == (None, None)
    # An expired feed never turns an estimate into a stale live observation.
    assert metro.board("MB", now + 100, estimate_missing_times=True)["status"] == "estimated"


def test_estimates_do_not_extrapolate_or_override_published_times(metro, now):
    assert metro.board("U2", now, estimate_missing_times=True)["status"] == "times_unavailable"
    rows = metro.board("MP", now, route_ids=["M1"], estimate_missing_times=True)["arrivals"]
    assert len(rows) == 1
    assert rows[0]["source"] == "timetable"
    assert rows[0]["time"] == "2026-10-21T09:02:00+00:00"
    assert rows[0]["estimated_time"] is None
    assert not metro.board("MB", now, direction_id=1, estimate_missing_times=True)["arrivals"]
    assert metro.board("MB", now + 7 * 60, estimate_missing_times=True)["status"] == "no_service"


def test_live_events_override_estimates_without_inventing_a_delay_base(metro, now):
    feed = message(now)
    update = trip_update(feed, now, trip_id="METRO", stop_id="MB", sequence=6, arrival_offset=420)
    metro.feeds["tripUpdates"] = decode(
        feed.SerializeToString(), "tripUpdates", metro.schedule, now
    )
    row = metro.board("MB", now, estimate_missing_times=True)["arrivals"][0]
    assert row["source"] == "live"
    assert row["time"] == row["predicted_time"] == "2026-10-21T09:07:00+00:00"
    assert row["scheduled_time"] is row["delay_seconds"] is None
    # Stale realtime falls back to a labelled estimate.
    assert (
        metro.board("MB", now + 91, estimate_missing_times=True)["arrivals"][0]["source"]
        == "estimated"
    )
    update.stop_time_update[0].schedule_relationship = 1
    metro.feeds["tripUpdates"] = decode(
        feed.SerializeToString(), "tripUpdates", metro.schedule, now
    )
    assert not metro.board("MB", now, estimate_missing_times=True)["arrivals"]
    update.trip.schedule_relationship = 3
    metro.feeds["tripUpdates"] = decode(
        feed.SerializeToString(), "tripUpdates", metro.schedule, now
    )
    assert not metro.board("MB", now, estimate_missing_times=True)["arrivals"]


@pytest.mark.parametrize("end", ["12:00:00", "12:04:00", ""])
def test_invalid_or_missing_anchor_does_not_generate_estimates(tmp_path, now, end):
    rows = [
        ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
        ["METRO", "12:04:00", "12:04:00", "MA", "0"],
        ["METRO", "", "", "MB", "1"],
        ["METRO", end, end, "U", "2"],
    ]
    path = tmp_path / "invalid.sqlite"
    import_feed(archive({"stop_times.txt": rows}), path)
    engine = TransportEngine(Schedule(path))
    assert engine.board("MB", now, estimate_missing_times=True)["status"] == "times_unavailable"


def test_estimated_calls_obey_service_days_exceptions_midnight_and_expiry(tmp_path):
    rows = [
        ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
        ["METRO", "24:00:00", "24:00:00", "MA", "0"],
        ["METRO", "", "", "MB", "1"],
        ["METRO", "24:10:00", "24:10:00", "U", "2"],
    ]
    path = tmp_path / "night.sqlite"
    import_feed(archive({"stop_times.txt": rows}), path)
    engine = TransportEngine(Schedule(path))
    now = datetime(2026, 10, 22, 0, 0, tzinfo=TZ).timestamp()
    row = engine.board("MB", now, estimate_missing_times=True)["arrivals"][0]
    assert row["service_date"] == "20261021"
    assert row["time"] == "2026-10-21T21:05:00+00:00"
    removed = datetime(2026, 10, 24, 0, 0, tzinfo=TZ).timestamp()
    assert not engine.board("MB", removed, estimate_missing_times=True)["arrivals"]
    expired = datetime(2027, 1, 7, 0, 0, tzinfo=TZ).timestamp()
    assert engine.board("MB", expired, estimate_missing_times=True)["status"] == "times_unavailable"
