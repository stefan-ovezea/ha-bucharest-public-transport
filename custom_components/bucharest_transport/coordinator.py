"""Shared feed polling, offline cache, and deduplicated board queries."""

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import aiohttp
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import ALERT_SECONDS, DOMAIN, FEED_URLS, POLL_SECONDS, STATIC_SECONDS, STATIC_URL
from .transport.engine import TransportEngine
from .transport.realtime import decode
from .transport.schedule import Schedule, import_feed
from .transport.search import search

_LOGGER = logging.getLogger(__name__)


@dataclass
class PollState:
    due: float = 0
    failures: int = 0
    etag: str | None = None
    modified: str | None = None
    error: str | None = None


class FeedError(Exception):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


class TransportCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass, entry):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_SECONDS),
            always_update=True,
        )
        self.entry = entry
        self.cache_path = Path(hass.config.path(".storage", DOMAIN, "schedule.sqlite"))
        self.engine: TransportEngine | None = None
        self.polls = {k: PollState() for k in ["static", *FEED_URLS]}
        self._board_tasks: dict[tuple, asyncio.Future] = {}
        self.generation = 0
        self.subscriptions = set()
        self._vehicles = {}
        entry.async_on_unload(self._cleanup_subscriptions)

    def _cleanup_subscriptions(self):
        for unload in tuple(self.subscriptions):
            unload()
        self.subscriptions.clear()

    async def _async_setup(self):
        schedule = await self.hass.async_add_executor_job(Schedule, self.cache_path)
        self.engine = TransportEngine(schedule)
        if schedule.ready:
            self.polls["static"].etag = schedule.meta.get("etag")
            self.polls["static"].modified = schedule.meta.get("last_modified")
        await self._poll("static", STATIC_URL, STATIC_SECONDS)
        if not self.engine.schedule.ready:
            raise ConfigEntryNotReady(
                "Could not download a usable TPBI timetable; will retry automatically"
            )

    async def _fetch(self, url, state, maximum):
        headers = {
            "User-Agent": "BucharestPublicTransport/0.2.3 (+https://github.com/stefan-ovezea/ha-bucharest-public-transport)"
        }
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.modified:
            headers["If-Modified-Since"] = state.modified
        session = async_get_clientsession(self.hass)
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60 if maximum > 16 * 1024 * 1024 else 20),
        ) as response:
            if response.status == 304:
                return None, dict(response.headers)
            if response.status != 200:
                retry = response.headers.get("Retry-After", "0")
                try:
                    retry = float(retry)
                except ValueError:
                    try:
                        retry = max(0, parsedate_to_datetime(retry).timestamp() - time.time())
                    except ValueError, TypeError:
                        retry = 0
                raise FeedError(f"HTTP {response.status}", retry)
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > maximum:
                    raise FeedError("Feed exceeds download size limit")
            return bytes(body), dict(response.headers)

    async def _poll(self, kind, url, interval):
        state = self.polls[kind]
        if time.time() < state.due:
            return
        try:
            payload, headers = await self._fetch(
                url, state, 32 * 1024 * 1024 if kind == "static" else 16 * 1024 * 1024
            )
            if payload is not None:
                if kind == "static":
                    changed = await self.hass.async_add_executor_job(
                        import_feed, payload, self.cache_path, headers
                    )
                    if changed or not self.engine.schedule.ready:
                        self.engine.schedule = await self.hass.async_add_executor_job(
                            Schedule, self.cache_path
                        )
                else:
                    snapshot = await self.hass.async_add_executor_job(
                        decode, payload, kind, self.engine.schedule, time.time()
                    )
                    self.engine.feeds[kind] = snapshot
            elif kind != "static":
                self.engine.feeds[kind] = replace(self.engine.feeds[kind], error=None)
            state.etag = headers.get("ETag", state.etag)
            state.modified = headers.get("Last-Modified", state.modified)
            if state.error:
                _LOGGER.info("TPBI %s recovered", kind)
            state.error = None
            state.failures = 0
            state.due = time.time() + interval
        except (
            Exception
        ) as err:  # Feed failures are independent; retain their last valid snapshots.
            message = str(err) or type(err).__name__
            if message != state.error:
                _LOGGER.warning("TPBI %s update failed: %s", kind, message)
            state.error = message
            state.failures += 1
            state.due = time.time() + max(
                min(300, POLL_SECONDS * 2 ** min(state.failures - 1, 4)),
                getattr(err, "retry_after", 0),
            )
            if kind != "static":
                self.engine.feeds[kind] = replace(self.engine.feeds[kind], error=message)

    async def _async_update_data(self):
        # Refreshing the static cache is uncommon; keep ordinary network polls concurrent.
        await asyncio.gather(
            *(
                self._poll(k, u, ALERT_SECONDS if k == "serviceAlerts" else POLL_SECONDS)
                for k, u in FEED_URLS.items()
            )
        )
        if time.time() >= self.polls["static"].due:
            await self._poll("static", STATIC_URL, STATIC_SECONDS)
        self.generation += 1
        self._board_tasks.clear()
        self._vehicles = {v["key"]: v for v in self.engine.vehicles(time.time())}
        boards = {}
        for board_id, board in self.entry.subentries.items():
            if board.subentry_type != "board":
                continue
            try:
                boards[board_id] = await self.async_board(**dict(board.data))
            except ValueError:
                boards[board_id] = None  # A removed GTFS stop must not break other boards.
        return {"boards": boards, "generation": self.generation}

    async def async_search(self, query):
        stops = [s for s in self.engine.schedule.stops.values() if s["location_type"] in (0, 1)]
        return await self.hass.async_add_executor_job(search, stops, query)

    async def async_board(
        self,
        stop_id,
        route_ids=None,
        direction_id=None,
        limit=10,
        horizon_minutes=60,
        estimate_missing_times=False,
    ):
        key = (
            self.generation,
            stop_id,
            tuple(sorted(route_ids or [])),
            direction_id,
            limit,
            horizon_minutes,
            estimate_missing_times,
        )
        if key not in self._board_tasks:
            self._board_tasks[key] = self.hass.async_add_executor_job(
                self.engine.board,
                stop_id,
                time.time(),
                route_ids,
                direction_id,
                limit,
                horizon_minutes,
                estimate_missing_times,
            )
        try:
            return await asyncio.shield(self._board_tasks[key])
        except Exception:
            self._board_tasks.pop(key, None)
            raise

    def vehicles(self, route_ids=None):
        now = time.time()
        feed = self.engine.feeds["vehiclePositions"]
        return [
            v
            for v in self._vehicles.values()
            if feed.usable(v, now) and (not route_ids or v["route_id"] in route_ids)
        ]

    def vehicle(self, key):
        vehicle = self._vehicles.get(key)
        routes = self.entry.options.get("tracked_route_ids", [])
        direction = self.entry.options.get("tracked_direction", "both")
        if (
            vehicle
            and vehicle["route_id"] in routes
            and (direction == "both" or vehicle["direction_id"] == int(direction))
            and self.engine.feeds["vehiclePositions"].usable(vehicle, time.time())
        ):
            return vehicle
        return None

    def diagnostics(self):
        now = time.time()
        return {
            "timetable": {
                **self.engine.schedule.meta,
                "ready": self.engine.schedule.ready,
                "error": self.polls["static"].error,
            },
            "feeds": {k: s.health(now) for k, s in self.engine.feeds.items()},
        }
