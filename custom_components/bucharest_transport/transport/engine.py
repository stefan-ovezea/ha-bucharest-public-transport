"""Merge explicit realtime observations with published timetable calls."""

from collections import Counter
from datetime import datetime

from .realtime import Snapshot
from .time import TZ, iso, service_epoch


class TransportEngine:
    def __init__(self, schedule):
        self.schedule = schedule
        self.feeds = {k: Snapshot(k) for k in ("vehiclePositions", "tripUpdates", "serviceAlerts")}

    def vehicles(self, now: float, route_ids: list[str] | None = None) -> list[dict]:
        feed = self.feeds["vehiclePositions"]
        return [
            {**r, "observed_at": iso(r["timestamp"]), "age_seconds": round(now - r["timestamp"])}
            for r in feed.records
            if feed.usable(r, now) and (not route_ids or r["route_id"] in route_ids)
        ]

    def alerts(self, now, stop_ids, route_ids, direction_id=None, trip_ids=None):
        feed = self.feeds["serviceAlerts"]
        routes = [self.schedule.routes[r] for r in route_ids if r in self.schedule.routes]

        def matches(s):
            return (
                (s["agency_id"] is None or any(r["agency_id"] == s["agency_id"] for r in routes))
                and (s["route_id"] is None or s["route_id"] in route_ids)
                and (s["route_type"] is None or any(r["type"] == s["route_type"] for r in routes))
                and (s["stop_id"] is None or s["stop_id"] in stop_ids)
                and (
                    s["direction_id"] is None
                    or direction_id is None
                    or s["direction_id"] == direction_id
                )
                and (s["trip_id"] is None or s["trip_id"] in (trip_ids or set()))
            )

        return [
            r
            for r in feed.records
            if feed.usable(r, now)
            and (
                not r["periods"]
                or any(
                    (p["start"] is None or p["start"] <= now)
                    and (p["end"] is None or now < p["end"])
                    for p in r["periods"]
                )
            )
            and (not r["selectors"] or any(matches(s) for s in r["selectors"]))
        ]

    def board(
        self,
        stop_id: str,
        now: float,
        route_ids=None,
        direction_id=None,
        limit=10,
        horizon_minutes=60,
        estimate_missing_times=False,
    ):
        schedule = self.schedule
        route_ids = route_ids or []
        if any(r not in schedule.routes for r in route_ids):
            raise ValueError("Unknown route_id")
        feed = self.feeds["tripUpdates"]
        live = [r for r in feed.records if feed.usable(r, now)]
        scheduled, live_rows, has_times = schedule.board_rows(
            stop_id, now, horizon_minutes, {r["trip_id"] for r in live}, estimate_missing_times
        )
        valid_day = datetime.fromtimestamp(now, TZ).strftime("%Y%m%d")
        valid_schedule = schedule.meta["start_date"] <= valid_day <= schedule.meta["end_date"]
        candidates = {}

        def key(row, day, start=""):
            return row["trip_id"], day, start, row["sequence"]

        for row in scheduled if valid_schedule else []:
            candidates[key(row, row["service_date"])] = (row, row["epoch"], None, "")
        by_trip = {}
        for row in live_rows:
            by_trip.setdefault(row["trip_id"], []).append(row)
        repeated = Counter((row["trip_id"], row["stop_id"]) for row in live_rows)
        for update in live:
            day = update["start_date"] or datetime.fromtimestamp(feed.timestamp, TZ).strftime(
                "%Y%m%d"
            )
            try:
                epoch = service_epoch(datetime.strptime(day, "%Y%m%d").date())
            except ValueError:
                continue
            # Replacement keys include the instance start time when the producer supplies one.
            for row in by_trip.get(update["trip_id"], []):
                candidates.pop(key(row, day), None)
                candidates[key(row, day, update["start_time"])] = (
                    row,
                    epoch,
                    update,
                    update["start_time"],
                )
        arrivals = []
        cancelled = 0
        for (tid, day, start, seq), (row, epoch, update, _) in candidates.items():
            if (
                row["pickup"] == 1
                or (route_ids and row["route_id"] not in route_ids)
                or (direction_id is not None and row["direction"] != direction_id)
            ):
                continue
            if update and update["relationship"] in (3, 7):  # CANCELED, DELETED
                cancelled += 1
                continue
            event_type = (
                "departure"
                if row["arrival"] is None and row["departure"] is not None
                else "arrival"
            )
            scheduled_seconds = row[event_type]
            scheduled_time = epoch + scheduled_seconds if scheduled_seconds is not None else None
            predicted = None
            if update:
                applicable = [
                    s
                    for s in update["stops"]
                    if (
                        s["sequence"] == seq
                        if s["sequence"] is not None
                        else s["stop_id"] == row["stop_id"] and repeated[(tid, row["stop_id"])] == 1
                    )
                ]
                explicit = applicable[0] if applicable else None
                if explicit and explicit["stop_id"] and explicit["stop_id"] != row["stop_id"]:
                    explicit = None
                if explicit and explicit["relationship"] == 1:  # SKIPPED
                    continue
                if explicit and explicit["relationship"] != 2:  # NO_DATA
                    if explicit["arrival"] is not None:
                        event_type = "arrival"
                    elif explicit["departure"] is not None:
                        event_type = "departure"
                    event = explicit[event_type]
                    base = row[event_type]
                    scheduled_time = epoch + base if base is not None else None
                    if event:
                        predicted = event["time"]
                        if (
                            predicted is None
                            and event["delay"] is not None
                            and scheduled_time is not None
                        ):
                            predicted = scheduled_time + event["delay"]
                # Propagate a reported delay only until another stop update resets it.
                if explicit is None and scheduled_time is not None:
                    delay = update["delay"]
                    for s in update["stops"]:
                        if s["sequence"] is None or s["sequence"] >= seq:
                            continue
                        if s["relationship"] == 2:
                            delay = None
                        elif s["relationship"] != 1:
                            event = s["departure"] or s["arrival"]
                            if event:
                                delay = event["delay"]
                    if delay is not None:
                        predicted = scheduled_time + delay
            estimated = (
                epoch + row["estimated"]
                if estimate_missing_times and row["estimated"] is not None and valid_schedule
                else None
            )
            effective = (
                predicted
                if predicted is not None
                else (scheduled_time if scheduled_time is not None else estimated)
                if valid_schedule
                else None
            )
            if effective is None or not now <= effective <= now + horizon_minutes * 60:
                continue
            route = schedule.routes[row["route_id"]]
            arrivals.append(
                {
                    "trip_id": tid,
                    "service_date": day,
                    "start_time": start,
                    "stop_sequence": seq,
                    "stop_id": row["stop_id"],
                    "route_id": row["route_id"],
                    "route_name": route["short_name"],
                    "agency": route["agency"],
                    "agency_id": route["agency_id"],
                    "direction_id": row["direction"],
                    "headsign": row["headsign"] or row["trip_headsign"],
                    "color": route["color"],
                    "scheduled_time": iso(scheduled_time),
                    "predicted_time": iso(predicted),
                    "estimated_time": iso(estimated),
                    "estimate_method": "stop_order_interpolation"
                    if estimated is not None
                    else None,
                    "estimate_anchor_sequences": [row["before_sequence"], row["after_sequence"]]
                    if estimated is not None
                    else None,
                    "time": iso(effective),
                    "event_type": event_type,
                    "source": "live"
                    if predicted is not None
                    else "estimated"
                    if estimated is not None
                    else "timetable",
                    "observed_at": iso(update["timestamp"])
                    if update and predicted is not None
                    else None,
                    "timestamp_basis": update["timestamp_basis"]
                    if update and predicted is not None
                    else None,
                    "age_seconds": round(now - update["timestamp"])
                    if update and predicted is not None
                    else None,
                    "delay_seconds": round(predicted - scheduled_time)
                    if predicted is not None and scheduled_time is not None
                    else None,
                }
            )
        arrivals.sort(key=lambda r: (r["time"], r["route_name"], r["trip_id"], r["stop_sequence"]))
        arrivals = arrivals[:limit]
        ids = schedule.stop_ids(stop_id)
        relevant_routes = route_ids or sorted(
            {r["route_id"] for s in ids for r in schedule.stops[s]["routes"]}
        )
        relevant_updates = [
            r
            for r in feed.records
            if r["route_id"] in relevant_routes
            and (direction_id is None or r["direction_id"] == direction_id)
        ]
        realtime_status = (
            "fresh"
            if any(feed.usable(r, now) for r in relevant_updates)
            else "stale"
            if relevant_updates
            else "unavailable"
        )
        return {
            "stop": schedule.public_stop(stop_id),
            "generated_at": iso(now),
            "arrivals": arrivals,
            "status": "live"
            if any(r["source"] == "live" for r in arrivals)
            else "estimated"
            if any(r["source"] == "estimated" for r in arrivals)
            else "timetable"
            if arrivals
            else "times_unavailable"
            if not has_times or not valid_schedule
            else "no_service",
            "realtime_status": realtime_status,
            "timetable_valid": valid_schedule,
            "alerts": self.alerts(
                now,
                [stop_id, *ids],
                relevant_routes,
                direction_id,
                {r["trip_id"] for r in arrivals},
            ),
            "alerts_status": self.feeds["serviceAlerts"].health(now)["status"],
            "cancelled_calls": cancelled,
        }
