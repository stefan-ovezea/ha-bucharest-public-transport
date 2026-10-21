"""Exercise the integration against real Home Assistant classes and lifecycle."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.bucharest_transport.api import _subscribe
from custom_components.bucharest_transport.config_flow import (
    BoardSubentryFlow,
    BucharestTransportFlow,
    TransportOptionsFlow,
)
from custom_components.bucharest_transport.coordinator import FeedError, TransportCoordinator
from custom_components.bucharest_transport.device_tracker import VehicleTracker
from custom_components.bucharest_transport.sensor import ArrivalSensor
from custom_components.bucharest_transport.transport.engine import TransportEngine
from custom_components.bucharest_transport.transport.realtime import decode
from custom_components.bucharest_transport.transport.schedule import import_feed

from .conftest import archive, message, trip_update, vehicle


@pytest.fixture
def coordinator(hass, entry, schedule):
    result = TransportCoordinator(hass, entry)
    result.engine = TransportEngine(schedule)
    entry.runtime_data = result
    return result


async def test_shared_polls_backoff_and_independent_stream_failures(coordinator, now, schedule):
    good = message(now)
    trip_update(good, now)
    responses = {
        "vehiclePositions": b"not valid protobuf",
        "tripUpdates": good.SerializeToString(),
        "serviceAlerts": message(now).SerializeToString(),
    }

    async def fetch(url, state, maximum):
        return responses[url.rsplit("/", 1)[1]], {}

    coordinator._fetch = AsyncMock(side_effect=fetch)
    coordinator.polls["static"].due = now + 100000
    with patch("custom_components.bucharest_transport.coordinator.time.time", return_value=now):
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data["boards"]["board"]["status"] == "live"
        assert coordinator.diagnostics()["feeds"]["vehiclePositions"]["error"]
        assert coordinator.diagnostics()["feeds"]["tripUpdates"]["status"] == "fresh"
        assert coordinator._fetch.await_count == 3
        await coordinator._async_update_data()
        assert coordinator._fetch.await_count == 3
        coordinator.polls["vehiclePositions"].due = 0
        coordinator._fetch.side_effect = FeedError("HTTP 429", 120)
        await coordinator._poll("vehiclePositions", "url", 30)
        assert coordinator.polls["vehiclePositions"].due >= now + 120


async def test_board_queries_are_shared_across_cards_and_entities(coordinator, now):
    with (
        patch("custom_components.bucharest_transport.coordinator.time.time", return_value=now),
        patch.object(coordinator.engine, "board", wraps=coordinator.engine.board) as board,
    ):
        results = await asyncio.gather(*(coordinator.async_board("U") for _ in range(8)))
        assert board.call_count == 1
        assert all(r["stop"]["stop_id"] == "U" for r in results)
        coordinator.data = {"boards": {"board": results[0]}}
        sensor = ArrivalSensor(coordinator, "board", "arrival")
        assert sensor.native_value.isoformat() == "2026-10-21T09:05:00+00:00"
        assert sensor.unique_id == "tpbi_regional:board:board:arrival"


async def test_vehicle_identity_and_expiry_without_polling_database_per_entity(
    coordinator, schedule, now
):
    feed = message(now)
    vehicle(feed, now)
    coordinator.engine.feeds["vehiclePositions"] = decode(
        feed.SerializeToString(), "vehiclePositions", schedule, now
    )
    coordinator._vehicles = {v["key"]: v for v in coordinator.engine.vehicles(now)}
    tracker = VehicleTracker(coordinator, "1:12")
    with patch("custom_components.bucharest_transport.coordinator.time.time", return_value=now):
        assert tracker.available
        assert tracker.latitude == pytest.approx(44.426, abs=0.001)
        assert tracker.unique_id == "tpbi_regional:vehicle:1:12"
    with patch(
        "custom_components.bucharest_transport.coordinator.time.time", return_value=now + 91
    ):
        assert not tracker.available
    assert tracker.unique_id == VehicleTracker(coordinator, "1:12").unique_id


async def test_subscriptions_clean_up_on_disconnect_and_unload(hass, coordinator):
    connection = SimpleNamespace(subscriptions={}, send_result=MagicMock(), send_event=MagicMock())
    compute = AsyncMock(return_value={"arrivals": []})
    await _subscribe(hass, connection, {"id": 7}, coordinator, compute)
    assert len(coordinator.subscriptions) == 1
    assert coordinator._listeners
    connection.subscriptions.pop(7)()
    assert not coordinator.subscriptions
    assert not coordinator._listeners
    await _subscribe(hass, connection, {"id": 8}, coordinator, compute)
    coordinator._cleanup_subscriptions()
    assert not coordinator.subscriptions
    assert not coordinator._listeners
    connection.send_event.assert_any_call(8, {"error": "integration_unloaded"})


async def test_subentry_search_and_filters_use_real_selectors(coordinator):
    flow = BoardSubentryFlow()
    flow.hass = coordinator.hass
    flow.context = {"source": "user"}
    with patch.object(flow, "_get_entry", return_value=coordinator.entry):
        result = await flow.async_step_user({"query": "Pta Uniri"})
        assert result["step_id"] == "stop"
        result = await flow.async_step_stop({"stop_id": "U"})
        assert result["step_id"] == "filters"
        result = await flow.async_step_filters(
            {"name": "Home", "route_ids": ["041"], "direction": "both"}
        )
        assert result["data"] == {"stop_id": "U", "route_ids": ["041"]}


async def test_options_can_select_lines_and_config_flow_is_keyless(coordinator):
    flow = BucharestTransportFlow()
    flow.hass = coordinator.hass
    flow.context = {"source": "user"}
    with (
        patch.object(flow, "_abort_if_unique_id_configured"),
        patch.object(flow, "async_set_unique_id", new=AsyncMock()),
    ):
        result = await flow.async_step_user({})
    assert result["data"] == {}
    options = TransportOptionsFlow()
    with patch.object(
        TransportOptionsFlow,
        "config_entry",
        new_callable=__import__("unittest.mock", fromlist=["PropertyMock"]).PropertyMock,
        return_value=coordinator.entry,
    ):
        result = await options.async_step_init()
        validated = result["data_schema"](
            {"tracked_route_ids": ["041"], "tracked_direction": "both"}
        )
    assert validated["tracked_route_ids"] == ["041"]


async def test_failed_static_download_retains_cached_schedule(coordinator):
    await coordinator.hass.async_add_executor_job(import_feed, archive(), coordinator.cache_path)
    coordinator._fetch = AsyncMock(side_effect=FeedError("HTTP 503"))
    await coordinator._async_setup()
    assert coordinator.engine.schedule.ready
    assert coordinator.engine.schedule.routes["041"]["short_name"] == "41"
    assert coordinator.polls["static"].error == "HTTP 503"


async def test_http_not_modified_does_not_renew_old_record_timestamps(coordinator, schedule, now):
    feed = message(now)
    trip_update(feed, now)
    snapshot = decode(feed.SerializeToString(), "tripUpdates", schedule, now)
    coordinator.engine.feeds["tripUpdates"] = snapshot
    coordinator.polls["tripUpdates"].etag = "example-etag"
    coordinator._fetch = AsyncMock(return_value=(None, {"ETag": "example-etag"}))
    with patch(
        "custom_components.bucharest_transport.coordinator.time.time", return_value=now + 100
    ):
        await coordinator._poll("tripUpdates", "url", 30)
        assert coordinator.engine.feeds["tripUpdates"].timestamp == now
        assert not coordinator.engine.feeds["tripUpdates"].fresh(now + 100)
