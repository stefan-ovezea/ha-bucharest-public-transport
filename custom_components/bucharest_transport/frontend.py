"""Register the bundled card with Home Assistant's dashboard resources."""

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE

from .const import CARD_URL

_LOGGER = logging.getLogger(__name__)
CARD_FILE = Path(__file__).parent / "www" / "bucharest-transport-card.js"


def _card_url():
    """Change the browser URL whenever the bundled JavaScript changes."""
    digest = hashlib.sha256(CARD_FILE.read_bytes()).hexdigest()[:16]
    return f"{CARD_URL}?v={digest}"


def _is_card(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return not parts.scheme and not parts.netloc and parts.path == CARD_URL


async def async_register_card(hass):
    """Create or migrate one module resource, preserving unrelated resources."""
    lovelace = hass.data[LOVELACE_DATA]
    if lovelace.resource_mode != MODE_STORAGE:
        _LOGGER.info(
            "Dashboard resources are managed in YAML; register %s as a module once. "
            "The file is updated with the integration",
            CARD_URL,
        )
        return
    try:
        url = await hass.async_add_executor_job(_card_url)
        resources = lovelace.resources
        # The public info call ensures persisted resources are loaded before matching.
        await resources.async_get_info()
        matches = [r for r in resources.async_items() if _is_card(r["url"])]
        if matches:
            first = matches[0]
            if first["url"] != url or first["type"] != "module":
                await resources.async_update_item(first["id"], {"url": url, "res_type": "module"})
            # Multiple module URLs would execute the custom-element definitions twice.
            for duplicate in matches[1:]:
                await resources.async_delete_item(duplicate["id"])
        else:
            await resources.async_create_item({"url": url, "res_type": "module"})
    except Exception:  # Optional UI setup must not disable transport entities or actions.
        _LOGGER.exception(
            "Could not register the dashboard card automatically. Add %s as a "
            "JavaScript module in dashboard Resources",
            CARD_URL,
        )
