# Public interfaces

All commands use Home Assistant's authenticated WebSocket connection. There is one regional integration entry.

## Search

```json
{"id": 1, "type": "bucharest_transport/search_stops", "query": "Pta Uniri"}
```

Returns at most 20 separate stop objects with `stop_id`, `name`, `latitude`, `longitude`, `parent`, `location_type`, `platform`, and `routes`. Route summaries include `route_id`, `short_name`, `agency`, `direction_id`, and `headsign`. Stop/route IDs are strings. Queries must contain 2–120 characters.

## Lines and saved boards

```json
{"id": 2, "type": "bucharest_transport/get_routes"}
```

Returns `routes`, `boards`, and `tracked_route_ids`. Route records include operator, display name, route type, and colour. Saved boards include `board_id`, title, stop, and filters.

## Station subscription

```json
{"id": 3, "type": "bucharest_transport/subscribe_board", "stop_id": "15100", "limit": 10, "horizon_minutes": 60}
```

Supply `stop_id` or `board_id`. Optional filters are `route_ids` (string array), `direction_id` (0 or 1), and `estimate_missing_times` (boolean, default false). A saved board's configured stop and filters take precedence over request filters. Limits are 1–50 calls and 1–1440 minutes.

After an acknowledgement, an initial event is sent immediately and updates follow shared feed refreshes. Events have the same shape as the `bucharest_transport.get_arrivals` action response:

- `stop`: selected physical stop or parent station.
- `generated_at`: UTC ISO timestamp.
- `arrivals`: sorted calls, keyed by service date, trip, instance start time, and stop sequence. Fields include `trip_id`, `service_date`, `start_time`, `stop_sequence`, `stop_id`, `route_id`, `route_name`, `agency`, `agency_id`, `direction_id`, `headsign`, `color`, `scheduled_time`, `predicted_time`, `time`, `event_type`, `source`, `observed_at`, `timestamp_basis`, `age_seconds`, and `delay_seconds`.
- `status`: `live`, `timetable`, `estimated`, `no_service`, or `times_unavailable`.
- `realtime_status` / `alerts_status`: `fresh`, `stale`, or `unavailable`. Prediction availability is evaluated for the board's lines and direction, so fresh surface feeds do not imply Metrorex coverage.
- `timetable_valid`: whether the cached schedule covers the current local date.
- `alerts`: currently relevant, fresh alerts with translated `header`, `description`, `url`, active periods, and selectors.
- `cancelled_calls`: calls suppressed by fresh trip cancellations within evaluated candidates.

Missing times are JSON `null`. `source` is `live` only when a usable prediction exists. `timestamp_basis` distinguishes record and feed timestamps. `event_type` distinguishes arrivals and departures. With `estimate_missing_times: true`, missing subway calls bounded by published times are interpolated evenly by actual stop order. These have `source: estimated`, `estimated_time`, `estimate_method: stop_order_interpolation`, and `estimate_anchor_sequences`. Their published `scheduled_time` remains null. No extrapolation is performed, and realtime delays are not applied to estimated bases. Explicit live events, cancellations and skips take precedence. Saved board settings override the request option. Estimates do not indicate observed train movement and have no known error bound.

## Vehicle subscription

```json
{"id": 4, "type": "bucharest_transport/subscribe_vehicles", "route_ids": ["041"]}
```

Returns events with `vehicles` and `feed`. Empty filters use configured watched lines; if no lines are configured, no vehicles are returned. Filtered card exploration does not create tracker entities.

Each vehicle includes a stable agency/vehicle `key`, source vehicle ID, label, route and trip references, operator, destination, coordinates, optional bearing and occupancy, `observed_at`, freshness, and published stop status. Stop status is `stopped_at`, `incoming_at`, `in_transit_to`, or `unknown`; the stop reference is withheld if inconsistent with the static trip. Only fresh positions are included.

Use the standard Home Assistant command `{"id":5,"type":"unsubscribe_events","subscription":3}` to cancel a subscription. Disconnect and integration unload also remove listeners. Unload emits an `error: integration_unloaded` event; clients should retry after the integration loads again.
