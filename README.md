# Bucharest Public Transport

<img src="custom_components/bucharest_transport/brand/icon.png" alt="A smiling teal bus" width="128" height="128">

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

**Metrorex:** many intermediate stations have no published stop times. Those stations display “Times unavailable” by default. Enable **Estimate missing metro times** on a saved board or card to interpolate missing subway calls between two published times. Estimates are labelled separately and do not simulate train locations. Compatible live metro records will be used if TPBI publishes them. [InfoTB's FAQ](https://info.stb.ro/ro/faq) explains the absence of live metro locations.

## Install with HACS

1. In HACS, open **Custom repositories** and add `https://github.com/stefan-ovezea/ha-bucharest-public-transport` with category **Integration**.
2. Download **Bucharest Public Transport**, then restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → Bucharest Public Transport**.
4. Wait for the initial timetable import. The current regional feed contains about 1.45 million stop-time rows; allow a minute or more on slower hardware.

The integration bundles its own bus icon under `brand/`, supported by Home Assistant 2026.3+. Current HACS versions may still show a placeholder because of [the local-branding display bug](https://github.com/hacs/integration/issues/5402); the maintainers report it fixed in the Marketplace integration planned for Home Assistant 2026.11.

For manual installation, copy `custom_components/bucharest_transport` into your configuration's `custom_components` directory and restart.

### Save station boards

On the integration, select **Add station board**. Search for a station, choose the correct platform, and optionally filter lines and direction. For intermediate metro stations, optionally enable **Estimate missing metro times**. Each board creates:

- A next-arrival timestamp sensor, with up to 10 upcoming calls in its attributes.
- A service-alert problem sensor. It becomes unavailable when the alert feed is stale.

Identically named physical stops remain separate. A parent metro station includes its explicitly linked platforms.

Use the integration's **Configure** options to select lines for GPS vehicle entities. Both directions are selected by default. New vehicles on watched lines are discovered automatically; their identities persist across restarts. Unselected or stale vehicles become unavailable. Without selected lines, no vehicle entities are created.

### Add the dashboard card

The card JavaScript is installed together with the integration. With UI-managed dashboard resources (the default), the integration automatically registers it on startup and updates its resource URL when the bundled file changes. Existing manual entries for this card are migrated and duplicate entries are consolidated.

After installing or upgrading, restart Home Assistant, refresh your browser, and add **Bucharest Public Transport** from the card picker. Its visual editor lets you select a saved board or leave it open for station searches. No separate card download or manual version suffix is required.

If you explicitly manage dashboard **resources in YAML**, add this entry once under `lovelace.resources` in `configuration.yaml`:

```yaml
lovelace:
  resource_mode: yaml
  resources:
    - url: /bucharest_transport/bucharest-transport-card.js
      type: module
```

The integration serves that URL without long-lived cache headers; the JS file is still updated with the integration. It does not edit your YAML. YAML dashboard definitions alone do not prevent automatic registration when resources remain UI-managed.

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
| `estimate_missing_times` | saved board setting, otherwise `false` | Approximate missing subway calls between published anchors |
| `direction_id` | saved board setting, otherwise both | GTFS direction `0` or `1` |
| `show_map` | `true` | Show the vehicle map |
| `max_arrivals` | `10` | 1–50 calls |
| `horizon_minutes` | `60` | 1–1440 minutes ahead |
| `language` | Home Assistant language | `en` or `ro` |
| `tile_url` | OpenStreetMap | HTTPS or local keyless tile URL with `{z}`, `{x}`, `{y}` |

When a searched station serves metro lines, the card shows an **Estimate missing metro times** switch directly above the arrivals. Turn it on to see approximate intermediate-station times such as Academia Militară. This exploratory choice is temporary; use the card editor or saved board settings to make it the default. Searching a different station clears line filters that do not serve that station.

The map initially shows the configured or watched lines. Selecting a line in the card explores that line without creating additional Home Assistant entities. Map tiles are fetched by your browser; transit feeds are fetched only by Home Assistant. No frontend libraries load from a CDN.

After an upgrade, refresh open dashboards so the browser loads the updated JavaScript. If an older browser session shows this card twice in the card picker, perform a full browser refresh after updating; repeated resource loads now keep a single picker registration. UI-managed resource URLs are maintained automatically.

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

The response includes `stop`, `arrivals`, `alerts`, `status`, `realtime_status`, `alerts_status`, and `timetable_valid`. Each call includes line, operator, direction, destination, stop sequence, service date, `scheduled_time`, `predicted_time`, effective `time`, `source`, freshness, and `event_type`. Opt-in interpolated calls have `source: estimated`, `estimated_time`, `estimate_method: stop_order_interpolation`, and `estimate_anchor_sequences`; their `scheduled_time` and `predicted_time` remain null. Times use ISO 8601 UTC. A departure-only update is explicitly labelled `departure`.

### Academia Militară on M5

Save a station board for **Academia militară**, platform **1**, toward **Eroilor**. Enable **Estimate missing metro times**, select M5, and choose direction **1**. This creates a next-arrival timestamp sensor that can be used in automations; its `arrivals` attributes retain the `estimated` source. The board includes trains from both western branches, not just the particular train someone boarded.

To query it directly:

```yaml
action: bucharest_transport.get_arrivals
data:
  stop_id: "9909R"
  route_ids: ["999"]
  direction_id: 1
  estimate_missing_times: true
  horizon_minutes: 60
response_variable: metro
```

For a dashboard card:

```yaml
type: custom:bucharest-transport-card
stop_id: "9909R"
route_ids: ["999"]
direction_id: 1
estimate_missing_times: true
show_map: false
```

`99090` is the parent station (both platforms); `9909R` is platform 1 toward Eroilor, and `9909T` is platform 2 in the opposite direction. These IDs come from the inspected regional feed; station search remains the best way to find them if the publisher changes identifiers.

The calculation distributes time **evenly by actual stop order**, between the preceding published departure and following published arrival. It ignores gaps in stop sequence numbers, respects each trip's branch, calendar and service day, and never extrapolates before the first or after the last timed call. Published stop times are kept intact. Only subway routes (`route_type=1`) are eligible. Fresh explicit predictions, cancellations and skipped stops take precedence; delay-only updates are never applied to an invented schedule base.

For example, on 21 October 2026, trip `MREX_LV_999_1_2127` publishes Valea Ialomiței at 12:37, Romancierilor arrival/departure at 12:39/12:39:30, and Eroilor at 12:49:30. Five blank intermediate stops split that ten-minute interval into six legs, putting Academia Militară at **12:47:50**. This is an approximate reference for planning, not evidence that a train actually reached the station. Inter-station travel times are unequal, so allow a margin; no numerical error bound is implied. Google may use a different timetable or calculation.

A timestamp sensor answers “when is the next train expected here according to the available data?” Estimating a particular person's arrival also requires knowing which train they boarded and allowing for the walk to work.

Authenticated WebSocket commands are documented in [docs/API.md](docs/API.md).

## Freshness, outages, and caching

Positions and trip updates are polled every 30 seconds; alerts every five minutes. All cards and entities share those requests. Failed feeds back off independently and respect `Retry-After`.

- **Live:** both feed and record timestamps are at most 90 seconds old, allowing 60 seconds of future clock skew. If a record has no timestamp, its feed timestamp is used and recorded as the timestamp basis.
- **Timetable:** a published time, without a fresh prediction. The timetable must cover the current date.
- **Estimated:** an optional approximation for missing subway calls between published anchors. Not live; no observed timestamp or known accuracy bound.
- **Stale:** a previously received feed has expired. Available timetable times remain labelled as timetable data.
- **No service:** no published calls in the chosen time window.
- **Times unavailable:** neither a usable prediction, published timetable time, nor enabled bounded estimate is available.

The timetable is checked every six hours using HTTP cache validators and a SHA-256 checksum. It is streamed into SQLite under `.storage/bucharest_transport/`. After upgrading from the first cache format, the integration downloads and rebuilds the timetable automatically. Imports happen outside the event loop; a validated replacement atomically replaces the cache. Failed updates retain the previous valid cache. A timetable build date several weeks old is normal when its service calendar remains valid.

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

### Publishing versions

HACS uses published GitHub release tags for versions; a manifest change or Git tag alone is insufficient. The default branch is hidden in HACS so normal installs use named releases.

For each stable release:

1. Set the same semantic version (for example `0.2.1`) in `custom_components/bucharest_transport/manifest.json`, `package.json`, both root version fields in `package-lock.json`, and `pyproject.toml`. Update the User-Agent version in the coordinator and live smoke script. The dashboard resource uses a content hash and needs no manual version update.
2. Rebuild the card, run the checks above, then commit and push the release changes to `main`.
3. Create and push a matching tag, for example `git tag v0.2.1` then `git push origin v0.2.1`.
4. Wait for the **Release** workflow to validate the versions, build the card, attach the installation ZIP, and publish the GitHub release. If it fails, fix the reported issue before publishing a release.

In HACS, open this integration and select **Update information**, then install the named release and restart Home Assistant. An installation made from a branch may keep its commit ID as the installed version until a release is installed.

Run `.venv/bin/python scripts/live_smoke.py` for an explicit live-feed import and arrival check. It downloads the full feed and uses `.cache/live.sqlite`; keep it out of routine automated test runs.

CI runs Python tests, frontend type checks and browser tests, reproducible bundle checks, hassfest, and HACS validation. Installing through a custom HACS repository does not imply inclusion in HACS's default catalogue.
