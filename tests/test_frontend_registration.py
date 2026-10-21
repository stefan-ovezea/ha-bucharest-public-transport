"""Use real HA resource collections to exercise installs and resource migration."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.lovelace import LovelaceData
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.dashboard import LovelaceStorage
from homeassistant.components.lovelace.resources import (
    RESOURCE_STORAGE_KEY,
    RESOURCES_STORAGE_VERSION,
    ResourceStorageCollection,
    ResourceYAMLCollection,
)
from homeassistant.helpers.storage import Store

from custom_components.bucharest_transport.const import CARD_URL
from custom_components.bucharest_transport.frontend import async_register_card


@pytest.fixture
def resources(hass):
    resources = ResourceStorageCollection(hass, LovelaceStorage(hass, None))
    hass.data[LOVELACE_DATA] = LovelaceData("storage", {}, resources, {})
    return resources


async def test_installs_one_module_and_repeated_setup_is_idempotent(hass, resources):
    await async_register_card(hass)
    first = resources.async_items()[0].copy()
    assert first["type"] == "module"
    assert first["url"].startswith(CARD_URL + "?v=")
    await async_register_card(hass)
    assert resources.async_items() == [first]


async def test_migrates_persisted_old_urls_and_only_removes_our_duplicates(hass, resources):
    unrelated = [
        {"id": "other", "url": "/hacsfiles/other-card.js?v=1", "type": "module"},
        {"id": "external", "url": "https://example.com" + CARD_URL, "type": "module"},
        {"id": "similar", "url": CARD_URL + ".backup", "type": "module"},
    ]
    old = [
        {"id": "original", "url": CARD_URL + "?v=0.1.0", "type": "js"},
        {"id": "duplicate", "url": CARD_URL + "?v=0.2.0#old", "type": "module"},
        *unrelated,
    ]
    await Store(hass, RESOURCES_STORAGE_VERSION, RESOURCE_STORAGE_KEY).async_save({"items": old})
    assert not resources.loaded
    await async_register_card(hass)
    assert resources.loaded
    items = resources.async_items()
    assert len(items) == 4
    original = next(r for r in items if r["id"] == "original")
    assert original["type"] == "module"
    assert original["url"].startswith(CARD_URL + "?v=")
    assert original["url"] != old[0]["url"]
    assert all(r in items for r in unrelated)


async def test_new_javascript_updates_url_without_adding_a_second_resource(
    hass, resources, tmp_path
):
    await async_register_card(hass)
    original = resources.async_items()[0].copy()
    replacement = tmp_path / "card.js"
    await hass.async_add_executor_job(replacement.write_text, "// a new compiled card")
    with patch("custom_components.bucharest_transport.frontend.CARD_FILE", replacement):
        await async_register_card(hass)
    updated = resources.async_items()
    assert len(updated) == 1
    assert updated[0]["id"] == original["id"]
    assert updated[0]["url"] != original["url"]


async def test_yaml_resources_are_preserved(hass):
    items = [{"url": CARD_URL, "type": "module"}]
    resources = ResourceYAMLCollection(items.copy())
    hass.data[LOVELACE_DATA] = LovelaceData("yaml", {}, resources, {})
    await async_register_card(hass)
    assert resources.async_items() == items


async def test_resource_storage_failure_does_not_disable_integration(hass, resources, caplog):
    with patch.object(
        resources, "async_get_info", new=AsyncMock(side_effect=OSError("storage offline"))
    ):
        await async_register_card(hass)
    assert "Could not register the dashboard card automatically" in caplog.text
