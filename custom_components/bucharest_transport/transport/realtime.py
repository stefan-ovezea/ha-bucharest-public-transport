"""Decode immutable full-dataset snapshots with independent freshness checks."""

from dataclasses import dataclass
from datetime import datetime

from google.transit import gtfs_realtime_pb2 as gtfs

from ..const import ALERT_MAX_AGE, FUTURE_SKEW, MAX_AGE
from .time import TZ, service_epoch


def optional(message, name: str, default=None):
    return getattr(message, name) if message.HasField(name) else default


def fresh(timestamp: float | None, now: float, maximum: int = MAX_AGE) -> bool:
    return timestamp is not None and -FUTURE_SKEW <= now - timestamp <= maximum


@dataclass(frozen=True)
class Snapshot:
    kind: str
    timestamp: int | None = None
    records: tuple[dict, ...] = ()
    error: str | None = None
    received_at: float | None = None
    unmatched: int = 0
    maximum: int = MAX_AGE

    def fresh(self, now):
        return fresh(self.timestamp, now, self.maximum)

    def usable(self, record, now):
        return self.fresh(now) and fresh(record["timestamp"], now, self.maximum)

    def health(self, now):
        return {
            "timestamp": self.timestamp,
            "received_at": self.received_at,
            "error": self.error,
            "age_seconds": round(now - self.timestamp) if self.timestamp is not None else None,
            "status": "fresh"
            if self.fresh(now)
            else "stale"
            if self.timestamp is not None
            else "unavailable",
            "records": len(self.records),
            "stale_records": sum(not self.usable(r, now) for r in self.records),
            "unmatched_references": self.unmatched,
        }


def _translated(message):
    return {t.language or "ro": t.text for t in message.translation}


def _event(stop, name, call, epoch):
    if not stop.HasField(name):
        return None
    value = getattr(stop, name)
    timestamp = optional(value, "time")
    delay = optional(value, "delay")
    # GTFS propagates a delay to following stops, including a delay implied by an absolute time.
    if delay is None and timestamp is not None and epoch is not None and call.get(name) is not None:
        delay = timestamp - epoch - call[name]
    return {"time": timestamp, "delay": delay}


def _trip(message, trips, routes, feed_time):
    tid = optional(message, "trip_id", "")
    static = trips.get(tid, {})
    rid = optional(message, "route_id", "") or static.get("route_id", "")
    route = routes.get(rid, {})
    return {
        "trip_id": tid,
        "route_id": rid,
        "agency_id": route.get("agency_id", ""),
        "direction_id": optional(message, "direction_id", static.get("direction")),
        "headsign": static.get("headsign", ""),
        "route_name": route.get("short_name", rid),
        "agency": route.get("agency", ""),
        "color": route.get("color", ""),
        "start_date": optional(message, "start_date", ""),
        "start_time": optional(message, "start_time", ""),
        "relationship": optional(message, "schedule_relationship", 0),
    }


def decode(payload: bytes, kind: str, schedule, now: float) -> Snapshot:
    """Decode and resolve references on a worker, never on the HA event loop."""
    if len(payload) > 16 * 1024 * 1024:
        raise ValueError("Realtime payload exceeds 16 MiB")
    feed = gtfs.FeedMessage()
    feed.ParseFromString(payload)
    if not feed.IsInitialized() or not feed.HasField("header"):
        raise ValueError("Incomplete GTFS-Realtime message")
    if feed.header.incrementality != gtfs.FeedHeader.FULL_DATASET:
        raise ValueError("Differential GTFS-Realtime feeds are not supported")
    timestamp = optional(feed.header, "timestamp")
    field_name = {
        "vehiclePositions": "vehicle",
        "tripUpdates": "trip_update",
        "serviceAlerts": "alert",
    }[kind]
    entities = [
        e for e in feed.entity if e.HasField(field_name) and not optional(e, "is_deleted", False)
    ]
    if feed.entity and not entities:
        raise ValueError(f"No {field_name} records in this feed")
    trip_ids = (
        {getattr(e, field_name).trip.trip_id for e in entities}
        if kind != "serviceAlerts"
        else set()
    )
    trips = schedule.lookup_trips(trip_ids)
    calls = schedule.trip_calls(trip_ids) if kind == "tripUpdates" else {}
    valid_stops = (
        schedule.position_stops(
            [
                (
                    e.vehicle.trip.trip_id,
                    optional(e.vehicle, "current_stop_sequence"),
                    optional(e.vehicle, "stop_id", ""),
                )
                for e in entities
            ]
        )
        if kind == "vehiclePositions"
        else set()
    )
    records = []
    unmatched = 0
    for entity in entities:
        item = getattr(entity, field_name)
        if kind == "serviceAlerts":
            records.append(
                {
                    "id": entity.id,
                    "timestamp": timestamp,
                    "timestamp_basis": "feed",
                    "header": _translated(item.header_text),
                    "description": _translated(item.description_text),
                    "url": _translated(item.url),
                    "cause": item.cause,
                    "effect": item.effect,
                    "periods": [
                        {"start": optional(p, "start"), "end": optional(p, "end")}
                        for p in item.active_period
                    ],
                    "selectors": [
                        {
                            "agency_id": optional(s, "agency_id"),
                            "route_id": optional(s, "route_id"),
                            "route_type": optional(s, "route_type"),
                            "stop_id": optional(s, "stop_id"),
                            "direction_id": optional(s, "direction_id"),
                            "trip_id": s.trip.trip_id or None,
                        }
                        for s in item.informed_entity
                    ],
                }
            )
            continue
        trip = _trip(item.trip, trips, schedule.routes, timestamp)
        record_time = optional(item, "timestamp", timestamp)
        common = {
            **trip,
            "timestamp": record_time,
            "timestamp_basis": "record" if item.HasField("timestamp") else "feed",
        }
        if trip["route_id"] not in schedule.routes or trip["trip_id"] not in trips:
            unmatched += 1
        if kind == "vehiclePositions":
            vehicle_id = optional(item.vehicle, "id", "")
            if not vehicle_id or not trip["agency_id"] or not item.HasField("position"):
                continue
            lat, lon = item.position.latitude, item.position.longitude
            if not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0):
                continue
            sid = optional(item, "stop_id", "")
            seq = optional(item, "current_stop_sequence")
            status = {0: "incoming_at", 1: "stopped_at", 2: "in_transit_to"}.get(
                optional(item, "current_status")
            )
            consistent = (trip["trip_id"], seq, sid) in valid_stops
            records.append(
                {
                    **common,
                    "key": f"{trip['agency_id']}:{vehicle_id}",
                    "vehicle_id": vehicle_id,
                    "label": optional(item.vehicle, "label", "")
                    or optional(item.vehicle, "license_plate", "")
                    or vehicle_id,
                    "latitude": lat,
                    "longitude": lon,
                    "bearing": optional(item.position, "bearing"),
                    "stop_status": status if consistent and status else "unknown",
                    "stop_id": sid if consistent else None,
                    "stop_name": schedule.stops.get(sid, {}).get("name") if consistent else None,
                    "stop_sequence": seq,
                    "occupancy_status": optional(item, "occupancy_status"),
                }
            )
        else:
            updates = []
            epoch = None
            day = trip["start_date"] or (
                datetime.fromtimestamp(timestamp, TZ).strftime("%Y%m%d")
                if timestamp is not None
                else ""
            )
            if day:
                try:
                    epoch = service_epoch(datetime.strptime(day, "%Y%m%d").date())
                except ValueError:
                    pass
            for stop in item.stop_time_update:
                call = calls.get((trip["trip_id"], optional(stop, "stop_sequence")), {})
                if call and stop.stop_id and call["stop_id"] != stop.stop_id:
                    call = {}
                updates.append(
                    {
                        "stop_id": optional(stop, "stop_id", ""),
                        "sequence": optional(stop, "stop_sequence"),
                        "relationship": optional(stop, "schedule_relationship", 0),
                        "arrival": _event(stop, "arrival", call, epoch),
                        "departure": _event(stop, "departure", call, epoch),
                    }
                )
            records.append(
                {
                    **common,
                    "vehicle_id": optional(item.vehicle, "id", ""),
                    "delay": optional(item, "delay"),
                    "stops": sorted(
                        updates, key=lambda s: s["sequence"] if s["sequence"] is not None else -1
                    ),
                }
            )
    return Snapshot(
        kind,
        timestamp,
        tuple(records),
        received_at=now,
        unmatched=unmatched,
        maximum=ALERT_MAX_AGE if kind == "serviceAlerts" else MAX_AGE,
    )
