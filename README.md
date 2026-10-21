# Bucharest Public Transport

A Home Assistant custom integration and dashboard card for public transport in Bucharest and Ilfov. Uses TPBI's public GTFS and GTFS-Realtime feeds directly. **No account, API key, paid service, or separate server is required.**

Requires **Home Assistant 2026.10 or newer**. English and Romanian are supported.

## What it provides

- Station boards with live arrival predictions and clearly labelled timetable fallback.
- Permissive station search: `Piata Unirii`, `Pta Uniri`, missing diacritics, partial names, and spelling variations.
- GPS tracker entities for vehicles on lines you select, including published current/next-stop status.
- A dashboard card with station search, line filters, alerts, and an interactive map.
- Diagnostics for feed freshness, stale records, missing references, and timetable validity.

| Operator | Timetable | Live positions and predictions |
| --- | --- | --- |
| STB | Yes | Where supplied by TPBI |
| STV | Yes | Where supplied by TPBI |
| STCM | Yes | Where supplied by TPBI |
| Regio Serv Transport | Yes | Where supplied by TPBI |
| Metrorex | Partial | Not currently supplied in the inspected feeds |

**Metrorex:** many intermediate stations have no published stop times. Those stations display “Times unavailable.” The integration does not interpolate missing times or simulate train locations. Compatible live metro records will be used if TPBI publishes them. [InfoTB's FAQ](https://info.stb.ro/ro/faq) explains the absence of live metro locations.

## Install with HACS

1. In HACS, open **Custom repositories** and add `https://github.com/stefan-ovezea/ha-bucharest-public-transport` with category **Integration**.
2. Download **Bucharest Public Transport**, then restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → Bucharest Public Transport**.
4. Wait for the initial timetable import. The current regional feed contains about 1.45 million stop-time rows; allow a minute or more on slower hardware.

For manual installation, copy `custom_components/bucharest_transport` into your configuration's `custom_components` directory and restart.

### Save station boards

On the integration, select **Add station board**. Search for a station, choose the correct platform, and optionally filter lines and direction. Each board creates:

- A next-arrival timestamp sensor, with up to 10 upcoming calls in its attributes.
- A service-alert problem sensor. It becomes unavailable when the alert feed is stale.

Identically named physical stops remain separate. A parent metro station includes its explicitly linked platforms.

Use the integration's **Configure** options to select lines for GPS vehicle entities. Both directions are selected by default. New vehicles on watched lines are discovered automatically; their identities persist across restarts. Unselected or stale vehicles become unavailable. Without selected lines, no vehicle entities are created.

### Add the dashboard card

1. Enable Advanced Mode in your Home Assistant profile if the Resources menu is hidden.
2. Open **Settings → Dashboards → menu → Resources → Add resource**.
3. Set URL to `/bucharest_transport/bucharest-transport-card.js?v=0.1.0` and type to **JavaScript module**.
4. Add the **Bucharest Public Transport** card using the card picker. Use its visual editor to select a saved board or leave it open for station searches.

Example:

```yaml
type: custom:bucharest-transport-card
title: My commute
show_map: true
max_arrivals: 10
horizon_minutes: 60
```

| Card option | Default | Meaning |
| --- | --- | --- |
| `board_id` | unset | A saved station board; selected in the visual editor |
| `stop_id` | unset | A specific GTFS stop, overriding the saved board's stop |
| `route_ids` | saved board filters, otherwise all | GTFS line IDs, as strings |
| `show_map` | `true` | Show the vehicle map |
| `max_arrivals` | `10` | 1–50 calls |
| `horizon_minutes` | `60` | 1–1440 minutes ahead |
| `language` | Home Assistant language | `en` or `ro` |
| `tile_url` | OpenStreetMap | HTTPS or local keyless tile URL with `{z}`, `{x}`, `{y}` |

The map initially shows the configured or watched lines. Selecting a line in the card explores that line without creating additional Home Assistant entities. Map tiles are fetched by your browser; transit feeds are fetched only by Home Assistant. No frontend libraries load from a CDN.

After an upgrade, update the resource's `?v=` suffix if your browser retains an old card.

## Using arrivals in automations

The response-returning `bucharest_transport.get_arrivals` action accepts a string `stop_id`, optional `route_ids` and `direction_id` (`0` or `1`), `limit` (default 10, maximum 50), and `horizon_minutes` (default 60, maximum 1440).

```yaml
action: bucharest_transport.get_arrivals
data:
  stop_id: "15100"
  limit: 5
  horizon_minutes: 30
response_variable: transport
```

Use station search to find a suitable ID. Metro stations can return no times; the example ID is not a guarantee of arrivals.

The response includes `stop`, `arrivals`, `alerts`, `status`, `realtime_status`, `alerts_status`, and `timetable_valid`. Each call includes line, operator, direction, destination, stop sequence, service date, `scheduled_time`, `predicted_time`, effective `time`, `source`, freshness, and `event_type`. Times use ISO 8601 UTC. A departure-only update is explicitly labelled `departure`.

Authenticated WebSocket commands are documented in [docs/API.md](docs/API.md).

## Freshness, outages, and caching

Positions and trip updates are polled every 30 seconds; alerts every five minutes. All cards and entities share those requests. Failed feeds back off independently and respect `Retry-After`.

- **Live:** both feed and record timestamps are at most 90 seconds old, allowing 60 seconds of future clock skew. If a record has no timestamp, its feed timestamp is used and recorded as the timestamp basis.
- **Timetable:** a published time, without a fresh prediction. The timetable must cover the current date.
- **Stale:** a previously received feed has expired. Available timetable times remain labelled as timetable data.
- **No service:** no published calls in the chosen time window.
- **Times unavailable:** neither a usable prediction nor published timetable times are available.

The timetable is checked every six hours using HTTP cache validators and a SHA-256 checksum. It is streamed into SQLite under `.storage/bucharest_transport/`. Imports happen outside the event loop; a validated replacement atomically replaces the cache. Failed updates retain the previous valid cache. A timetable build date several weeks old is normal when its service calendar remains valid.

Public feeds have no availability guarantee. A feed failure does not disable other usable feeds. Diagnostic entities and integration diagnostics expose the source status. If a saved stop disappears from a new timetable, reconfigure that board using station search.

## Data and attribution

- [TPBI regional GTFS](https://gtfs.tpbi.ro/regional/) — stops, lines, trips, service calendars, and schedules.
- [Vehicle positions](https://gtfs.tpbi.ro/api/gtfs-rt/vehiclePositions), [trip updates](https://gtfs.tpbi.ro/api/gtfs-rt/tripUpdates), and [service alerts](https://gtfs.tpbi.ro/api/gtfs-rt/serviceAlerts).
- Data authority: **ADTPBI**. Feed producer: **FlashWeb IT**. Operators retain attribution for their published data.
- [Mo-bi's public API and source](https://github.com/BucharestOpenData/Endpoint_OpenData) informed the source comparison; this integration does not depend on that API or copy its implementation.
- Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright). Default tiles follow the [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/); there is no bulk download or prefetch feature.
- Leaflet and Lit are distributed with their license notices inside the integration.

Original integration and card code are [MIT licensed](LICENSE). This license does not relicense upstream transit or map data.

## Development

```sh
python3.14 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm ci
npm run typecheck
npm run build
.venv/bin/pytest -q
.venv/bin/ruff check .
npx playwright install chromium
npm test
```

The compiled card is committed under the integration so HACS users need no build tools. Rebuild it after changing `frontend/`. Browser tests use offline fixtures and mock map tiles; ordinary tests do not contact TPBI.

Run `.venv/bin/python scripts/live_smoke.py` for an explicit live-feed import and arrival check. It downloads the full feed and uses `.cache/live.sqlite`; keep it out of routine automated test runs.

CI runs Python tests, frontend type checks and browser tests, reproducible bundle checks, hassfest, and HACS validation. Installing through a custom HACS repository does not imply inclusion in HACS's default catalogue.
