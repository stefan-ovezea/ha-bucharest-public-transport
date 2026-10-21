"""Small, deterministic feeds with duplicate names, metro gaps, loops and night trips."""

import csv
import gc
import io
import zipfile
from datetime import datetime
from types import MappingProxyType

import pytest
from google.transit import gtfs_realtime_pb2 as gtfs
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.bucharest_transport.transport.engine import TransportEngine
from custom_components.bucharest_transport.transport.schedule import Schedule, import_feed
from custom_components.bucharest_transport.transport.time import TZ


@pytest.fixture(autouse=True, scope="module")
def stabilize_homeassistant_gc():
    """Match HA's test harness: collect and freeze module globals before each module."""
    gc.collect()
    gc.freeze()


@pytest.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    instance.config.time_zone = "Europe/Bucharest"
    instance.config_entries = ConfigEntries(instance, {})
    yield instance
    await instance.async_stop(force=True)


@pytest.fixture
def entry():
    return ConfigEntry(
        domain="bucharest_transport",
        title="Transport",
        data={},
        options={"tracked_route_ids": ["041"]},
        source="user",
        unique_id="tpbi_regional",
        version=1,
        minor_version=1,
        discovery_keys=MappingProxyType({}),
        subentries_data=[
            {
                "subentry_id": "board",
                "subentry_type": "board",
                "title": "Piața Unirii",
                "data": {"stop_id": "U"},
                "unique_id": None,
            }
        ],
    )


@pytest.fixture
def now():
    return datetime(2026, 10, 21, 12, 0, tzinfo=TZ).timestamp()


def archive(overrides=None):
    files = {
        "agency.txt": [
            ["agency_id", "agency_name", "agency_url", "agency_timezone"],
            ["1", "STB SA", "https://stbsa.ro", "Europe/Bucharest"],
            ["2", "METROREX SA", "https://metrorex.ro", "Europe/Bucharest"],
        ],
        "routes.txt": [
            [
                "route_id",
                "agency_id",
                "route_short_name",
                "route_long_name",
                "route_type",
                "route_color",
            ],
            ["041", "1", "41", "Ghencea - Piața Presei", "0", "176D8C"],
            ["M1", "2", "M1", "Dristor - Pantelimon", "1", "F5BE00"],
        ],
        "stops.txt": [
            [
                "stop_id",
                "stop_name",
                "stop_lat",
                "stop_lon",
                "parent_station",
                "location_type",
                "platform_code",
            ],
            ["U", "Piața Unirii", "44.426", "26.103", "", "0", "A"],
            ["U2", "Piața Unirii", "44.427", "26.104", "", "0", "B"],
            ["R", "Piața Romană", "44.445", "26.097", "", "0", ""],
            ["MP", "Eroilor", "44.435", "26.075", "", "1", ""],
            ["MA", "Eroilor", "44.435", "26.075", "MP", "0", "1"],
            ["MB", "Universitate", "44.435", "26.10", "", "0", ""],
        ],
        "trips.txt": [
            ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id"],
            ["041", "WK", "T1", "Piața Presei", "0"],
            ["041", "WK", "T2", "Ghencea", "1"],
            ["041", "WK", "NIGHT", "Ghencea", "0"],
            ["M1", "WK", "METRO", "Dristor", "0"],
        ],
        "calendar.txt": [
            [
                "service_id",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
                "start_date",
                "end_date",
            ],
            ["WK", "1", "1", "1", "1", "1", "0", "0", "20260101", "20270105"],
        ],
        "calendar_dates.txt": [
            ["service_id", "date", "exception_type"],
            ["WK", "20261023", "2"],
            ["WK", "20261025", "1"],
        ],
        "stop_times.txt": [
            ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
            ["T1", "12:05:00", "12:05:00", "U", "0"],
            ["T1", "12:15:00", "12:15:00", "R", "1"],
            ["T1", "12:25:00", "12:25:00", "U", "2"],
            ["T2", "12:10:00", "12:10:00", "U2", "0"],
            ["NIGHT", "25:10:00", "25:10:00", "U", "0"],
            ["METRO", "12:08:00", "12:08:00", "MA", "0"],
            ["METRO", "", "", "MB", "1"],
        ],
    }
    files.update(overrides or {})
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in files.items():
            stream = io.StringIO(newline="")
            csv.writer(stream).writerows(rows)
            z.writestr(name, stream.getvalue())
    return data.getvalue()


@pytest.fixture
def schedule(tmp_path):
    path = tmp_path / "schedule.sqlite"
    import_feed(archive(), path)
    return Schedule(path)


@pytest.fixture
def engine(schedule):
    return TransportEngine(schedule)


def message(now):
    feed = gtfs.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(now)
    return feed


def trip_update(feed, now, trip_id="T1", stop_id="U", sequence=0, arrival_offset=360):
    entity = feed.entity.add()
    entity.id = f"trip-{len(feed.entity)}"
    update = entity.trip_update
    update.trip.trip_id = trip_id
    update.trip.start_date = "20261021"
    update.timestamp = int(now)
    if stop_id is not None:
        stop = update.stop_time_update.add()
        stop.stop_id = stop_id
        stop.stop_sequence = sequence
        stop.arrival.time = int(now + arrival_offset)
    return update


def vehicle(feed, now, vehicle_id="12", stop_id="U", sequence=0, status=1):
    entity = feed.entity.add()
    entity.id = f"vehicle-{len(feed.entity)}"
    item = entity.vehicle
    item.trip.trip_id = "T1"
    item.vehicle.id = vehicle_id
    item.position.latitude = 44.426
    item.position.longitude = 26.103
    item.timestamp = int(now)
    if stop_id is not None:
        item.stop_id = stop_id
    if sequence is not None:
        item.current_stop_sequence = sequence
    if status is not None:
        item.current_status = status
    return item
