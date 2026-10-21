"""Authenticated WebSocket subscriptions and a response-returning HA action."""

import time

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

_FILTERS = {
    vol.Optional("route_ids", default=[]): vol.All([str], vol.Length(max=250)),
    vol.Optional("direction_id"): vol.All(vol.Coerce(int), vol.In([0, 1])),
    vol.Optional("limit", default=10): vol.All(cv.positive_int, vol.Range(min=1, max=50)),
    vol.Optional("horizon_minutes", default=60): vol.All(
        cv.positive_int, vol.Range(min=1, max=1440)
    ),
}


def _coordinator(hass):
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED and getattr(entry, "runtime_data", None):
            return entry.runtime_data
    raise HomeAssistantError("Bucharest Public Transport is not loaded")


def _parameters(msg):
    return {
        k: msg[k]
        for k in ("stop_id", "route_ids", "direction_id", "limit", "horizon_minutes")
        if k in msg
    }


@callback
def async_register(hass):
    for command in (search_stops, get_routes, subscribe_board, subscribe_vehicles):
        websocket_api.async_register_command(hass, command)

    async def arrivals(call):
        try:
            return await _coordinator(hass).async_board(**dict(call.data))
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        "get_arrivals",
        arrivals,
        schema=vol.Schema({vol.Required("stop_id"): str, **_FILTERS}),
        supports_response=SupportsResponse.ONLY,
    )


@websocket_api.websocket_command(
    {
        "type": f"{DOMAIN}/search_stops",
        vol.Required("query"): vol.All(str, vol.Length(min=2, max=120)),
    }
)
@websocket_api.async_response
async def search_stops(hass, connection, msg):
    connection.send_result(msg["id"], await _coordinator(hass).async_search(msg["query"]))


@websocket_api.websocket_command({"type": f"{DOMAIN}/get_routes"})
@websocket_api.async_response
async def get_routes(hass, connection, msg):
    coordinator = _coordinator(hass)
    connection.send_result(
        msg["id"],
        {
            "routes": list(coordinator.engine.schedule.routes.values()),
            "boards": [
                {"board_id": s.subentry_id, "title": s.title, **dict(s.data)}
                for s in coordinator.entry.subentries.values()
                if s.subentry_type == "board"
            ],
            "tracked_route_ids": coordinator.entry.options.get("tracked_route_ids", []),
        },
    )


async def _subscribe(hass, connection, msg, coordinator, compute):
    """One listener per subscription; coalesce updates and cancel on disconnect/unload."""
    initial = await compute()
    stopped = False
    pending = False
    task = None

    async def send():
        nonlocal pending
        while not stopped:
            pending = False
            try:
                data = await compute()
            except (ValueError, HomeAssistantError) as err:
                data = {"error": str(err)}
            if not stopped:
                connection.send_event(msg["id"], data)
            if not pending:
                break

    @callback
    def update():
        nonlocal task, pending
        pending = True
        if not stopped and (task is None or task.done()):
            task = hass.async_create_background_task(send(), "TPBI subscription")

    remove = coordinator.async_add_listener(update)

    @callback
    def cleanup():
        nonlocal stopped
        stopped = True
        remove()
        if task and not task.done():
            task.cancel()

    @callback
    def unload():
        if not stopped:
            connection.send_event(msg["id"], {"error": "integration_unloaded"})
        cleanup()

    coordinator.subscriptions.add(unload)

    @callback
    def unsubscribe():
        cleanup()
        coordinator.subscriptions.discard(unload)

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    connection.send_event(msg["id"], initial)


@websocket_api.websocket_command(
    {
        "type": f"{DOMAIN}/subscribe_board",
        vol.Optional("stop_id"): str,
        vol.Optional("board_id"): str,
        **_FILTERS,
    }
)
@websocket_api.async_response
async def subscribe_board(hass, connection, msg):
    coordinator = _coordinator(hass)
    parameters = _parameters(msg)
    if "board_id" in msg:
        board = coordinator.entry.subentries.get(msg["board_id"])
        if board is None or board.subentry_type != "board":
            connection.send_error(msg["id"], "invalid_board", "Unknown station board")
            return
        parameters = {**parameters, **dict(board.data)}
    if "stop_id" not in parameters:
        connection.send_error(msg["id"], "invalid_stop", "stop_id or board_id is required")
        return
    await _subscribe(
        hass, connection, msg, coordinator, lambda: coordinator.async_board(**parameters)
    )


@websocket_api.websocket_command(
    {
        "type": f"{DOMAIN}/subscribe_vehicles",
        vol.Optional("route_ids", default=[]): vol.All([str], vol.Length(max=250)),
    }
)
@websocket_api.async_response
async def subscribe_vehicles(hass, connection, msg):
    coordinator = _coordinator(hass)
    route_ids = msg["route_ids"] or coordinator.entry.options.get("tracked_route_ids", [])
    if any(r not in coordinator.engine.schedule.routes for r in route_ids):
        connection.send_error(msg["id"], "invalid_routes", "Unknown line")
        return

    async def compute():
        return {
            "vehicles": coordinator.vehicles(route_ids) if route_ids else [],
            "feed": coordinator.engine.feeds["vehiclePositions"].health(time.time()),
        }

    await _subscribe(hass, connection, msg, coordinator, compute)
