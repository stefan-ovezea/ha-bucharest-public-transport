import {LitElement, html, css, unsafeCSS, nothing} from "lit";
import type * as Leaflet from "leaflet";
import leafletCSS from "leaflet/dist/leaflet.css";

type Unsubscribe = () => void;
interface Hass {
  language: string;
  locale?: {language?: string; time_zone?: string};
  config?: {time_zone: string};
  connection: {subscribeMessage<T>(callback: (value: T) => void, message: object): Promise<Unsubscribe>};
  callWS<T>(message: object): Promise<T>;
}
interface Config {
  type: string; title?: string; board_id?: string; stop_id?: string;
  route_ids?: string[]; show_map?: boolean; max_arrivals?: number;
  horizon_minutes?: number; tile_url?: string; language?: string;
  estimate_missing_times?: boolean; direction_id?: number;
}
interface Route {type: number; route_id: string; short_name: string; agency: string; long_name: string}
interface Stop {stop_id: string; name: string; platform: string; latitude: number; longitude: number; routes: {route_id: string; short_name: string; headsign: string; agency: string}[]}
interface Arrival {trip_id: string; route_id: string; route_name: string; headsign: string; agency: string; source: string; time: string; scheduled_time: string | null; predicted_time: string | null; observed_at?: string; age_seconds: number | null; event_type: string; color: string; stop_id: string}
interface Alert {id: string; header: Record<string, string>; description: Record<string, string>}
interface Board {stop: Stop; generated_at: string; arrivals: Arrival[]; alerts: Alert[]; status: string; realtime_status: string; alerts_status: string; error?: string}
interface Vehicle {key: string; label: string; route_name: string; route_id: string; headsign: string; agency: string; latitude: number; longitude: number; observed_at: string; stop_status: string; stop_name: string | null; color: string}
interface Metadata {routes: Route[]; boards: {board_id: string; title: string; stop_id: string; route_ids: string[]; direction_id?: number; estimate_missing_times?: boolean}[]; tracked_route_ids: string[]}

const TEXT: Record<string, Record<string, string>> = {
  en: {
    title: "Bucharest transport", search: "Search a station", hint: "Name, partial name, or spelling variant",
    lines: "Line", all: "All selected lines", live: "Live", timetable: "Timetable", estimated: "Estimated", stale: "Stale",
    no_service: "No service in the selected time window.", times_unavailable: "Published times are unavailable for this station.",
    loading: "Loading transit data…", unavailable: "Transit data is unavailable. Retrying…", choose: "Choose a station to see arrivals.",
    no_results: "No matching stations.", destination: "Destination", arrival: "Arrival", departure: "Departure",
    alerts: "Service alerts", stale_notice: "Live predictions are stale; published timetable times are shown where available.",
    realtime_missing: "Live predictions are unavailable for these arrivals.",
    estimate_field: "Estimate missing metro times", estimate_notice: "Estimated times divide the interval between published stops evenly. Allow a margin for unequal station travel times and delays; these are not live train predictions.",
    stopped_at: "At", incoming_at: "Arriving at", in_transit_to: "Travelling to", unknown: "Stop status unknown",
    observed: "Observed", map_error: "The map could not load. Arrivals remain available.",
    tiles_error: "Map tiles are unavailable. Vehicle markers remain visible.", no_vehicles: "No fresh vehicles on the selected lines.",
    minute: "min", now: "Due", title_field: "Title", board_field: "Saved station board", stop_field: "Stop ID (optional)",
    map_field: "Show map", limit_field: "Number of arrivals", horizon_field: "Minutes ahead", language: "Language",
    tiles: "Keyless map tile URL", automatic: "Automatic", alerts_unavailable: "Service alerts are currently unavailable.",
  },
  ro: {
    title: "Transport București", search: "Caută o stație", hint: "Nume complet, parțial sau cu mici greșeli",
    lines: "Linia", all: "Toate liniile selectate", live: "În timp real", timetable: "Orar", estimated: "Estimat", stale: "Date vechi",
    no_service: "Nu sunt curse în intervalul ales.", times_unavailable: "Nu sunt publicate ore pentru această stație.",
    loading: "Se încarcă datele de transport…", unavailable: "Datele sunt indisponibile. Se reîncearcă…", choose: "Alege o stație pentru a vedea sosirile.",
    no_results: "Nu s-au găsit stații.", destination: "Destinație", arrival: "Sosire", departure: "Plecare",
    alerts: "Alerte de transport", stale_notice: "Predicțiile în timp real sunt vechi; se afișează orarul publicat, unde este disponibil.",
    realtime_missing: "Predicțiile în timp real nu sunt disponibile pentru aceste sosiri.",
    estimate_field: "Estimează orele lipsă pentru metrou", estimate_notice: "Orele estimate împart uniform intervalul dintre stațiile cu ore publicate. Luați în calcul timpi diferiți între stații și întârzieri; acestea nu sunt predicții în timp real.",
    stopped_at: "În stația", incoming_at: "Sosește la", in_transit_to: "Spre stația", unknown: "Poziția față de stație este necunoscută",
    observed: "Observat la", map_error: "Harta nu s-a încărcat. Sosirile sunt disponibile.",
    tiles_error: "Fundalul hărții nu este disponibil. Pozițiile vehiculelor sunt vizibile.", no_vehicles: "Nu sunt vehicule cu date recente pe liniile selectate.",
    minute: "min", now: "Acum", title_field: "Titlu", board_field: "Panou de stație salvat", stop_field: "ID stație (opțional)",
    map_field: "Afișează harta", limit_field: "Număr de sosiri", horizon_field: "Minute în avans", language: "Limbă",
    tiles: "URL hartă fără cheie API", automatic: "Automat", alerts_unavailable: "Alertele de transport sunt momentan indisponibile.",
  },
};
const OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const color = (value: string) => /^[0-9a-f]{6}$/i.test(value || "") ? `#${value}` : "#176d8c";
const safeTileURL = (value: string) => {if (!value.startsWith("https://") && !value.startsWith("/")) throw new Error("Map tiles must use HTTPS or a local URL"); return value;};

export class BucharestTransportCard extends LitElement {
  static properties = {_config: {state: true}, _board: {state: true}, _results: {state: true}, _query: {state: true}, _error: {state: true}, _metadata: {state: true}, _routeIds: {state: true}, _vehicles: {state: true}, _searchDone: {state: true}, _mapError: {state: true}, _tilesError: {state: true}, _estimateMissingTimes: {state: true}};
  private _hass?: Hass;
  private _config!: Config;
  private _board?: Board;
  private _results: Stop[] = [];
  private _query = "";
  private _error = "";
  private _metadata?: Metadata;
  private _routeIds: string[] = [];
  private _vehicles: Vehicle[] = [];
  private _searchDone = false;
  private _stopId?: string;
  private _directionId?: number;
  private _estimateMissingTimes = false;
  private _ready = false;
  private _selectionInitialized = false;
  private _starting = false;
  private _mapError = false;
  private _tilesError = false;
  private _boardUnsub?: Unsubscribe;
  private _vehicleUnsub?: Unsubscribe;
  private _boardSeq = 0;
  private _vehicleSeq = 0;
  private _searchSeq = 0;
  private _searchTimer?: ReturnType<typeof setTimeout>;
  private _retryTimer?: ReturnType<typeof setTimeout>;
  private _clock?: ReturnType<typeof setInterval>;
  private _map?: Leaflet.Map;
  private _leaflet?: typeof Leaflet;
  private _markers = new Map<string, Leaflet.Marker>();
  private _stopMarker?: Leaflet.CircleMarker;
  private _mapStarting = false;
  private _fitNeeded = true;

  static async getConfigElement() {return document.createElement("bucharest-transport-card-editor");}
  static getStubConfig() {return {type: "custom:bucharest-transport-card", show_map: true};}
  getCardSize() {return this._config?.show_map === false ? 5 : 9;}
  getGridOptions() {return {columns: 12, min_columns: 6};}

  setConfig(config: Config) {
    if (config.max_arrivals !== undefined && (!Number.isInteger(config.max_arrivals) || config.max_arrivals < 1 || config.max_arrivals > 50)) throw new Error("max_arrivals must be 1–50");
    if (config.horizon_minutes !== undefined && (!Number.isInteger(config.horizon_minutes) || config.horizon_minutes < 1 || config.horizon_minutes > 1440)) throw new Error("horizon_minutes must be 1–1440");
    safeTileURL(config.tile_url || OSM);
    const oldTiles = this._config?.tile_url;
    this._config = {...config};
    this._selectionInitialized = false;
    this._ready = false;
    if (oldTiles !== config.tile_url && this._map) {this._map.remove(); this._map = undefined; this._markers.clear(); this._stopMarker = undefined;}
    if (this.isConnected && this._hass) void this._start();
  }

  set hass(value: Hass) {
    const changed = this._hass?.connection !== value.connection;
    this._hass = value;
    if (changed) this._ready = false;
    if (this.isConnected && this._config && !this._ready && !this._retryTimer) void this._start();
    this.requestUpdate();
  }
  private get language() {return (this._config?.language || this._hass?.language || "en").startsWith("ro") ? "ro" : "en";}
  private t(key: string) {return TEXT[this.language][key] || key;}
  connectedCallback() {
    super.connectedCallback();
    this._clock = setInterval(() => {this.requestUpdate(); this._syncMap();}, 1000);
    if (this._hass && this._config) void this._start();
  }
  disconnectedCallback() {
    super.disconnectedCallback();
    this._boardSeq++; this._vehicleSeq++; this._searchSeq++;
    this._boardUnsub?.(); this._vehicleUnsub?.();
    this._boardUnsub = this._vehicleUnsub = undefined;
    clearInterval(this._clock); clearTimeout(this._retryTimer); clearTimeout(this._searchTimer);
    this._map?.remove(); this._map = undefined; this._markers.clear(); this._stopMarker = undefined;
    this._ready = false;
  }

  private _retry() {
    this._error = this.t("unavailable"); this._ready = false;
    clearTimeout(this._retryTimer);
    this._retryTimer = setTimeout(() => {this._retryTimer = undefined; if (this.isConnected) void this._start();}, 5000);
  }
  private async _start() {
    if (this._starting || !this._hass || !this.isConnected) return;
    this._starting = true;
    try {
      const data = await this._hass.callWS<Metadata>({type: "bucharest_transport/get_routes"});
      if (!this.isConnected) return;
      this._metadata = data;
      if (!this._selectionInitialized) {
        const saved = data.boards.find(b => b.board_id === this._config.board_id);
        this._stopId = this._config.stop_id || saved?.stop_id || this._stopId;
        this._directionId = this._config.direction_id ?? saved?.direction_id;
        this._estimateMissingTimes = this._config.estimate_missing_times ?? saved?.estimate_missing_times ?? false;
        this._routeIds = this._config.route_ids || saved?.route_ids || [];
        this._selectionInitialized = true;
      }
      this._ready = true; this._error = ""; clearTimeout(this._retryTimer); this._retryTimer = undefined;
      if (this._stopId) await this._subscribeBoard();
      await this._subscribeVehicles();
    } catch {this._retry();} finally {this._starting = false;}
  }
  private async _subscribeBoard() {
    if (!this._stopId || !this._hass) return;
    this._boardUnsub?.(); this._boardUnsub = undefined;
    const seq = ++this._boardSeq;
    const msg: Record<string, unknown> = {type: "bucharest_transport/subscribe_board", stop_id: this._stopId, route_ids: this._routeIds, limit: this._config.max_arrivals || 10, horizon_minutes: this._config.horizon_minutes || 60, estimate_missing_times: this._estimateMissingTimes};
    if (this._directionId !== undefined) msg.direction_id = this._directionId;
    const unsub = await this._hass.connection.subscribeMessage<Board>(data => {
      if (seq !== this._boardSeq || !this.isConnected) return;
      if (data.error) {this._retry(); return;}
      this._board = data; this._error = ""; this._syncMap();
    }, msg);
    if (seq !== this._boardSeq || !this.isConnected) unsub(); else this._boardUnsub = unsub;
  }
  private async _subscribeVehicles() {
    if (!this._hass) return;
    this._vehicleUnsub?.(); this._vehicleUnsub = undefined;
    const seq = ++this._vehicleSeq;
    const routes = this._routeIds.length ? this._routeIds : this._metadata?.tracked_route_ids || [];
    const unsub = await this._hass.connection.subscribeMessage<{vehicles: Vehicle[]; error?: string}>(data => {
      if (seq !== this._vehicleSeq || !this.isConnected) return;
      if (data.error) {this._retry(); return;}
      if (!this._vehicles.length && data.vehicles.length) this._fitNeeded = true;
      this._vehicles = data.vehicles; this._syncMap();
    }, {type: "bucharest_transport/subscribe_vehicles", route_ids: routes});
    if (seq !== this._vehicleSeq || !this.isConnected) unsub(); else this._vehicleUnsub = unsub;
  }
  private _search(event: Event) {
    this._query = (event.target as HTMLInputElement).value;
    this._searchDone = false; clearTimeout(this._searchTimer);
    const seq = ++this._searchSeq;
    if (this._query.trim().length < 2) {this._results = []; return;}
    this._searchTimer = setTimeout(async () => {
      try {
        const results = await this._hass!.callWS<Stop[]>({type: "bucharest_transport/search_stops", query: this._query.slice(0, 120)});
        if (seq === this._searchSeq) {this._results = results; this._searchDone = true;}
      } catch {this._retry();}
    }, 250);
  }
  private async _choose(stop: Stop) {
    this._searchSeq++; clearTimeout(this._searchTimer);
    this._stopId = stop.stop_id; this._query = ""; this._results = []; this._searchDone = false;
    this._directionId = undefined; this._board = undefined; this._fitNeeded = true;
    const available = new Set(stop.routes.map(route => route.route_id));
    this._routeIds = this._routeIds.filter(route => available.has(route));
    try {await Promise.all([this._subscribeBoard(), this._subscribeVehicles()]);} catch {this._retry();}
  }
  private async _estimates(event: Event) {
    this._estimateMissingTimes = (event.target as HTMLInputElement).checked;
    try {await this._subscribeBoard();} catch {this._retry();}
  }
  private async _line(event: Event) {
    const value = (event.target as HTMLSelectElement).value;
    this._routeIds = value ? [value] : (this._config.route_ids || []).filter(route => this._board?.stop.routes.some(r => r.route_id === route));
    this._fitNeeded = true;
    try {await Promise.all([this._subscribeBoard(), this._subscribeVehicles()]);} catch {this._retry();}
  }
  private _formatTime(value: string) {return new Date(value).toLocaleTimeString(this.language, {hour: "2-digit", minute: "2-digit", timeZone: this._hass?.config?.time_zone || "Europe/Bucharest"});}
  private _minutes(value: string) {const minutes = Math.ceil((Date.parse(value) - Date.now()) / 60000); return minutes <= 0 ? this.t("now") : `${minutes} ${this.t("minute")}`;}
  private _freshVehicles() {return this._vehicles.filter(v => {const age = Date.now() - Date.parse(v.observed_at); return age >= -60000 && age <= 90000;});}
  private _source(row: Arrival) {return row.source === "live" && row.observed_at && Date.now() - Date.parse(row.observed_at) > 90000 ? "stale" : row.source;}
  protected updated() {if (this._config?.show_map !== false) void this._ensureMap();}
  private async _ensureMap() {
    const element = this.renderRoot.querySelector<HTMLElement>("#map");
    if (!element || this._map || this._mapStarting || !this.isConnected) return;
    this._mapStarting = true;
    try {
      this._leaflet = await import("leaflet");
      if (!this.isConnected) return;
      const L = this._leaflet;
      this._map = L.map(element, {scrollWheelZoom: false}).setView([44.435, 26.102], 12);
      L.tileLayer(safeTileURL(this._config.tile_url || OSM), {maxZoom: 19, referrerPolicy: "strict-origin-when-cross-origin", attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'}).on("tileerror", () => {this._tilesError = true;}).addTo(this._map);
      this._syncMap();
    } catch {this._mapError = true;} finally {this._mapStarting = false;}
  }
  private _syncMap() {
    if (!this._map || !this._leaflet) return;
    const L = this._leaflet;
    const vehicles = this._freshVehicles();
    const keys = new Set(vehicles.map(v => v.key));
    for (const [key, marker] of this._markers) if (!keys.has(key)) {marker.remove(); this._markers.delete(key);}
    for (const vehicle of vehicles) {
      const badge = document.createElement("span"); badge.className = "vehicle-badge";
      badge.style.background = color(vehicle.color); badge.textContent = vehicle.route_name;
      const icon = L.divIcon({html: badge, className: "vehicle-marker", iconSize: [42, 28], iconAnchor: [21, 14]});
      let marker = this._markers.get(vehicle.key);
      if (!marker) {marker = L.marker([vehicle.latitude, vehicle.longitude], {icon, title: `${vehicle.route_name} · ${vehicle.label}`}).addTo(this._map); this._markers.set(vehicle.key, marker);}
      else marker.setLatLng([vehicle.latitude, vehicle.longitude]).setIcon(icon);
      const popup = document.createElement("div");
      const label = document.createElement("strong"); label.textContent = `${vehicle.route_name} → ${vehicle.headsign}`;
      const detail = document.createElement("div"); detail.textContent = `${vehicle.agency} · ${vehicle.label}`;
      const stop = document.createElement("div"); stop.textContent = vehicle.stop_name ? `${this.t(vehicle.stop_status)} ${vehicle.stop_name}` : this.t("unknown");
      const observed = document.createElement("small"); observed.textContent = `${this.t("observed")} ${this._formatTime(vehicle.observed_at)}`;
      popup.append(label, detail, stop, observed); marker.bindPopup(popup);
    }
    this._stopMarker?.remove();
    if (this._board) {
      const stop = this._board.stop;
      const tooltip = document.createElement("span"); tooltip.textContent = stop.name;
      this._stopMarker = L.circleMarker([stop.latitude, stop.longitude], {radius: 8, color: "#176d8c", fillColor: "white", fillOpacity: 1}).addTo(this._map).bindTooltip(tooltip);
    }
    if (this._fitNeeded && (vehicles.length || this._board)) {
      const points: Leaflet.LatLngExpression[] = vehicles.map(v => [v.latitude, v.longitude]);
      if (this._board) points.push([this._board.stop.latitude, this._board.stop.longitude]);
      this._map.fitBounds(L.latLngBounds(points).pad(0.15), {maxZoom: 15}); this._fitNeeded = false;
    }
    this._map.invalidateSize();
  }

  render() {
    if (!this._config) return nothing;
    const board = this._board;
    const metro = board?.stop.routes.some(r => this._metadata?.routes.some(route => route.route_id === r.route_id && route.type === 1));
    const stale = board && (board.realtime_status === "stale" || Date.now() - Date.parse(board.generated_at) > 90000);
    return html`<ha-card><div class="content">
      <header><div class="eyebrow">TPBI · BUCUREȘTI–ILFOV</div><h2>${this._config.title || board?.stop.name || this.t("title")}</h2>
      ${board ? html`<span class="status ${stale ? "stale" : board.status}">${this.t(stale ? "stale" : board.status === "live" ? "live" : board.status === "timetable" ? "timetable" : board.status === "estimated" ? "estimated" : "arrival")}</span>` : nothing}</header>
      <label class="search-label">${this.t("search")}<input type="search" maxlength="120" .value=${this._query} placeholder=${this.t("hint")} @input=${this._search}></label>
      ${this._results.length ? html`<div class="results">${this._results.map(s => html`<button @click=${() => this._choose(s)}><strong>${s.name}</strong><small>${s.platform || s.stop_id} · ${[...new Set(s.routes.map(r => `${r.short_name} ${r.agency}`))].join(", ")}</small></button>`)}</div>` : this._searchDone ? html`<p class="message">${this.t("no_results")}</p>` : nothing}
      <label class="line-label">${this.t("lines")}<select @change=${this._line} .value=${this._routeIds.length === 1 ? this._routeIds[0] : ""}><option value="">${this.t("all")}</option>${(this._metadata?.routes || []).map(r => html`<option value=${r.route_id}>${r.short_name} · ${r.agency}</option>`)}</select></label>
      ${metro ? html`<label class="estimate-toggle"><input type="checkbox" .checked=${this._estimateMissingTimes} @change=${this._estimates}>${this.t("estimate_field")}</label>` : nothing}
      ${this._error ? html`<p role="status" class="notice">${this._error}</p>` : !this._ready ? html`<p role="status" class="message">${this.t("loading")}</p>` : !board ? html`<p class="message">${this.t("choose")}</p>` : nothing}
      ${board && (board.realtime_status !== "fresh" || stale) ? html`<p class="notice">${this.t(stale ? "stale_notice" : "realtime_missing")}</p>` : nothing}
      ${board?.arrivals.some(row => row.source === "estimated") ? html`<p class="notice estimate-notice">${this.t("estimate_notice")}</p>` : nothing}
      ${board?.arrivals.length ? html`<div class="arrivals" role="list">${board.arrivals.map(row => html`<div class="arrival" role="listitem"><span class="route-pill" style="border-color:${color(row.color)};color:${color(row.color)}">${row.route_name}</span><div class="destination"><strong>${row.headsign || row.agency}</strong><small>${row.agency} · <span class="source ${this._source(row)}">${this.t(this._source(row))}</span>${row.event_type === "departure" ? html` · ${this.t("departure")}` : nothing}</small></div><div class="when"><strong>${this._minutes(row.time)}</strong><small>${this._formatTime(row.time)}</small></div></div>`)}</div>` : board ? html`<p class="message empty-state">${this.t(board.status)}</p>` : nothing}
      ${board?.alerts.length ? html`<details class="alerts"><summary>${this.t("alerts")} (${board.alerts.length})</summary>${board.alerts.map(a => html`<article><strong>${a.header[this.language] || a.header.ro || Object.values(a.header)[0]}</strong><p>${a.description[this.language] || a.description.ro || Object.values(a.description)[0]}</p></article>`)}</details>` : nothing}
      ${board && board.alerts_status !== "fresh" ? html`<small class="message">${this.t("alerts_unavailable")}</small>` : nothing}
    </div><div class="map-wrap" ?hidden=${this._config.show_map === false}><div id="map" aria-label=${this.t("title")}></div>${this._mapError || this._tilesError ? html`<p class="map-notice">${this.t(this._mapError ? "map_error" : "tiles_error")}</p>` : nothing}${!this._freshVehicles().length ? html`<div class="map-empty">${this.t("no_vehicles")}</div>` : nothing}</div>
    <footer><a href="https://gtfs.tpbi.ro/regional/" target="_blank" rel="noopener">TPBI · FlashWeb IT</a><span>${this.t("observed")} · Europe/Bucharest</span></footer></ha-card>`;
  }

  static styles = [unsafeCSS(leafletCSS), css`
    :host{display:block;color:var(--primary-text-color,#182c37);font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif)}
    ha-card{display:block;overflow:hidden;background:var(--ha-card-background,var(--card-background-color,#fff));border-radius:var(--ha-card-border-radius,16px);border:1px solid var(--divider-color,#e3e9eb)}
    .content{padding:22px}header{position:relative;padding-right:75px;margin-bottom:20px}.eyebrow{font-size:10px;letter-spacing:.14em;color:var(--secondary-text-color,#637680);font-weight:700}h2{margin:8px 0 0;font-size:23px;line-height:1.2;font-weight:650}
    .status{position:absolute;right:0;top:22px;font-size:11px;padding:5px 8px;border-radius:20px;background:var(--secondary-background-color,#edf1f2)}.status.live{color:#16734c;background:#e5f4ec}
    label{display:block;font-size:12px;font-weight:600}input,select{box-sizing:border-box;width:100%;border:1px solid var(--divider-color,#ccd7dc);border-radius:9px;padding:11px 12px;background:var(--card-background-color,#fff);color:var(--primary-text-color,#182c37);font:inherit;font-size:14px;margin-top:6px;min-height:44px}input:focus,select:focus,button:focus-visible{outline:2px solid var(--primary-color,#176d8c);outline-offset:2px}.line-label{margin:14px 0}.estimate-toggle{display:flex;align-items:center;gap:8px;margin:12px 0}.estimate-toggle input{width:18px;height:18px;min-height:0;margin:0;flex-shrink:0;accent-color:var(--primary-color,#176d8c)}.results{max-height:260px;overflow:auto;border:1px solid var(--divider-color,#e3e9eb);border-radius:8px;margin-top:5px}.results button{display:block;text-align:left;width:100%;border:0;border-bottom:1px solid var(--divider-color,#eee);background:var(--card-background-color,#fff);color:inherit;padding:12px;cursor:pointer}.results button:hover{background:var(--secondary-background-color,#edf3f5)}small{display:block;color:var(--secondary-text-color,#647780);font-size:11px;line-height:1.5}.message{color:var(--secondary-text-color,#647780);font-size:13px;line-height:1.5}.notice{font-size:12px;line-height:1.6;background:var(--secondary-background-color,#f0f4f6);padding:10px 12px;border-radius:8px}.arrival{display:flex;align-items:center;gap:12px;padding:14px 0;border-top:1px solid var(--divider-color,#e7ecee)}.route-pill{display:inline-flex;align-items:center;justify-content:center;min-width:36px;height:28px;border:1px solid;border-radius:6px;font-weight:750;font-size:13px;flex-shrink:0;padding:0 4px}.destination{flex:1;min-width:0}.destination strong{font-size:14px;line-height:1.4;display:block;overflow-wrap:anywhere}.source.live{color:#18764f}.when{text-align:right;white-space:nowrap}.when strong{font-size:17px}.when small{font-size:12px}.alerts{font-size:12px;line-height:1.6;border-top:1px solid var(--divider-color,#eee);padding-top:12px}.alerts summary{cursor:pointer;font-weight:650}.alerts article{padding:10px 0}.alerts p{margin:4px 0}.map-wrap{position:relative;background:#e5eceb}.map-wrap[hidden]{display:none}#map{height:300px;width:100%;z-index:0}.map-empty,.map-notice{position:absolute;z-index:500;background:var(--card-background-color,#fff);padding:7px 10px;border-radius:7px;box-shadow:0 1px 4px #0002;font-size:11px;max-width:80%;left:12px}.map-empty{bottom:28px}.map-notice{top:8px;left:50px}.vehicle-badge{display:flex;align-items:center;justify-content:center;width:42px;height:28px;border-radius:7px;color:white;font-size:12px;font-weight:750;box-shadow:0 1px 5px #0005;border:2px solid white;box-sizing:border-box}.leaflet-container{font-family:inherit}.leaflet-popup-content{font-size:12px;line-height:1.6}footer{display:flex;justify-content:space-between;gap:8px;padding:10px 20px;font-size:9px;color:var(--secondary-text-color,#647780)}footer a{color:inherit;text-decoration:none}@media(max-width:420px){.content{padding:16px}h2{font-size:20px}.arrival{gap:9px}.destination strong{font-size:13px}.when strong{font-size:15px}footer{padding:9px 16px}#map{height:260px}}
  `];
}

export class BucharestTransportEditor extends LitElement {
  static properties = {_config: {state: true}, _metadata: {state: true}};
  private _config!: Config;
  private _hass?: Hass;
  private _metadata?: Metadata;
  private _loading = false;
  setConfig(config: Config) {this._config = {...config};}
  set hass(value: Hass) {this._hass = value; if (!this._metadata && !this._loading) {this._loading = true; value.callWS<Metadata>({type: "bucharest_transport/get_routes"}).then(data => {this._metadata = data;}).catch(() => {}).finally(() => {this._loading = false;});}}
  private t(key: string) {return TEXT[(this._config?.language || this._hass?.language || "en").startsWith("ro") ? "ro" : "en"][key] || key;}
  private change(key: keyof Config, value: unknown) {const config = {...this._config, [key]: value}; if (value === "" || value === undefined) delete config[key]; this._config = config; this.dispatchEvent(new CustomEvent("config-changed", {detail: {config}, bubbles: true, composed: true}));}
  render() {if (!this._config) return nothing; return html`
    <label>${this.t("title_field")}<input .value=${this._config.title || ""} @change=${(e: Event) => this.change("title", (e.target as HTMLInputElement).value)}></label>
    <label>${this.t("board_field")}<select .value=${this._config.board_id || ""} @change=${(e: Event) => this.change("board_id", (e.target as HTMLSelectElement).value)}><option value="">—</option>${this._metadata?.boards.map(b => html`<option value=${b.board_id}>${b.title}</option>`)}</select></label>
    <label>${this.t("stop_field")}<input .value=${this._config.stop_id || ""} @change=${(e: Event) => this.change("stop_id", (e.target as HTMLInputElement).value)}></label>
    <label>${this.t("lines")}<select multiple size="5" @change=${(e: Event) => this.change("route_ids", Array.from((e.target as HTMLSelectElement).selectedOptions).map(o => o.value))}>${this._metadata?.routes.map(r => html`<option value=${r.route_id} ?selected=${this._config.route_ids?.includes(r.route_id)}>${r.short_name} · ${r.agency}</option>`)}</select></label>
    <label class="check"><input type="checkbox" .checked=${(this._config.estimate_missing_times ?? this._metadata?.boards.find(b => b.board_id === this._config.board_id)?.estimate_missing_times ?? false)} @change=${(e: Event) => this.change("estimate_missing_times", (e.target as HTMLInputElement).checked)}>${this.t("estimate_field")}</label>
    <label class="check"><input type="checkbox" .checked=${this._config.show_map !== false} @change=${(e: Event) => this.change("show_map", (e.target as HTMLInputElement).checked)}>${this.t("map_field")}</label>
    <label>${this.t("limit_field")}<input type="number" min="1" max="50" .value=${String(this._config.max_arrivals || 10)} @change=${(e: Event) => this.change("max_arrivals", Number((e.target as HTMLInputElement).value))}></label>
    <label>${this.t("horizon_field")}<input type="number" min="1" max="1440" .value=${String(this._config.horizon_minutes || 60)} @change=${(e: Event) => this.change("horizon_minutes", Number((e.target as HTMLInputElement).value))}></label>
    <label>${this.t("language")}<select .value=${this._config.language || ""} @change=${(e: Event) => this.change("language", (e.target as HTMLSelectElement).value)}><option value="">${this.t("automatic")}</option><option value="en">English</option><option value="ro">Română</option></select></label>
    <label>${this.t("tiles")}<input .value=${this._config.tile_url || ""} placeholder=${OSM} @change=${(e: Event) => this.change("tile_url", (e.target as HTMLInputElement).value)}></label>`;}
  static styles = css`:host{display:grid;gap:12px;font-family:system-ui}label{display:grid;gap:6px;font-size:13px}input,select{font:inherit;padding:10px;border:1px solid var(--divider-color,#ccc);border-radius:8px;color:var(--primary-text-color,#222);background:var(--card-background-color,#fff)}.check{display:flex;align-items:center;gap:8px}`;
}
if (!customElements.get("bucharest-transport-card")) customElements.define("bucharest-transport-card", BucharestTransportCard);
if (!customElements.get("bucharest-transport-card-editor")) customElements.define("bucharest-transport-card-editor", BucharestTransportEditor);
const registry = window as unknown as {customCards: {type?: string; [key: string]: unknown}[]};
// Home Assistant imports this array by reference. Preserve it during deduplication.
registry.customCards = registry.customCards || [];
for (let index = registry.customCards.length - 1; index >= 0; index--) {
  if (registry.customCards[index].type?.replace(/^custom:/, "") === "bucharest-transport-card") registry.customCards.splice(index, 1);
}
registry.customCards.push({type: "bucharest-transport-card", name: "Bucharest Public Transport", description: "TPBI station arrivals and live vehicles", preview: true});
