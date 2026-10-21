"""Install through the real HA loader, forward platforms, call the action, then unload."""

import socket
import time
from pathlib import Path
from unittest.mock import patch

from homeassistant import auth, loader
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import area_registry, device_registry, floor_registry, label_registry
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.bucharest_transport.coordinator import TransportCoordinator

from .conftest import archive, message, trip_update, vehicle

COMPONENTS = Path(__file__).resolve().parents[1] / "custom_components"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_full_install_action_entities_and_unload(hass, entry):
    root = Path(hass.config.config_dir)
    await hass.async_add_executor_job((root / "custom_components").symlink_to, COMPONENTS, True)
    device_registry.async_setup(hass)
    await floor_registry.async_load(hass)
    await area_registry.async_load(hass)
    await label_registry.async_load(hass)
    await device_registry.async_load(hass)
    await er.async_load(hass)
    hass.auth = await auth.auth_manager_from_config(hass, [], [])
    loader.async_setup(hass)
    await hass.config_entries.async_initialize()
    now = time.time()
    positions = message(now)
    vehicle(positions, now)
    predictions = message(now)
    # Today's date is intentionally kept in the fixture timetable's supported interval.
    trip_update(predictions, now)
    responses = {
        "BUCHAREST-REGION.zip": archive(),
        "vehiclePositions": positions.SerializeToString(),
        "tripUpdates": predictions.SerializeToString(),
        "serviceAlerts": message(now).SerializeToString(),
    }

    async def fetch(self, url, state, maximum):
        return responses[url.rsplit("/", 1)[1]], {}

    # HA's optional C compression accelerator is unrelated to integration behavior.
    with patch.object(TransportCoordinator, "_fetch", fetch), patch("aiohttp_fast_zlib.enable"):
        port = await hass.async_add_executor_job(free_port)
        assert await async_setup_component(hass, "http", {"http": {"server_port": port}})
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert hass.services.has_service("bucharest_transport", "get_arrivals")
        response = await hass.services.async_call(
            "bucharest_transport",
            "get_arrivals",
            {"stop_id": "U"},
            blocking=True,
            return_response=True,
        )
        assert response["stop"]["stop_id"] == "U"
        registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert {e.domain for e in entities} >= {"sensor", "binary_sensor", "device_tracker"}
        tracker_ids = {e.unique_id for e in entities if e.domain == "device_tracker"}
        assert tracker_ids == {"tpbi_regional:vehicle:1:12"}
        assert any(e.config_subentry_id == "board" for e in entities)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
