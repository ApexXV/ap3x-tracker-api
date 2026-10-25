(function () {
  "use strict";

  const CONFIG = Object.freeze({
    schemaVersion: "LOC-MAP-1",
    fallbackCenter: [42.33, -75.2],
    fallbackZoom: 9,
    mapContract: "/local-ops-data/current.json",
    basemapUrl: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    refreshMs: 30_000,
    cameraRefreshMs: 5_000,
    fetchTimeoutMs: 5_000,
    maxFeatures: 75,
    selectionHoldMs: 120_000,
    opsBriefMaxAgeSeconds: 180,
    mapContractMaxAgeSeconds: 180,
    weatherMaxAgeSeconds: 15 * 60,
    opsFeedMaxAgeSeconds: 180,
    workloadsMaxAgeSeconds: 180,
    cameraStatusMaxAgeSeconds: 600,
    cameraFrameMaxAgeSeconds: 45,
    radarFrameMaxAgeSeconds: 20 * 60,
  });

  const params = new URLSearchParams(window.location.search);
  const fixtureName = params.get("fixture");
  const offlineMode = params.get("offline") === "1";
  const forceDayMode = params.get("day") === "1";
  const fixtureUrl = fixtureName
    ? `/fixtures/loc001-${encodeURIComponent(fixtureName)}.json`
    : null;

  const runtime = {
    map: null,
    basemap: null,
    incidentLayer: null,
    radarLayer: null,
    radarUrl: null,
    radarPendingUrl: null,
    basemapLoaded: false,
    selectedFeatureId: null,
    selectedAt: 0,
    latestData: null,
    lastMapContract: null,
    cameraTimer: null,
    cameraFrames: {},
  };

  const severityRank = Object.freeze({
    CRITICAL: 5,
    EXTREME: 5,
    HIGH: 4,
    SEVERE: 4,
    WATCH: 3,
    MODERATE: 3,
    INFO: 2,
    MINOR: 2,
    RECOVERY: 1,
    UNKNOWN: 0,
  });

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    const element = byId(id);
    if (element) element.textContent = value == null ? "—" : String(value);
  }

  function numeric(value, fallback = null) {
    const result = Number(value);
    return Number.isFinite(result) ? result : fallback;
  }

  function finitePair(value) {
    return (
      Array.isArray(value) &&
      value.length === 2 &&
      Number.isFinite(Number(value[0])) &&
      Number.isFinite(Number(value[1]))
    );
  }

  function parseTimestamp(value) {
    if (!value) return null;
    const parsed = Date.parse(String(value));
    return Number.isFinite(parsed) ? parsed : null;
  }

  function ageSeconds(value) {
    const parsed = parseTimestamp(value);
    return parsed == null ? Infinity : Math.max(0, (Date.now() - parsed) / 1000);
  }

  function formatClock(value) {
    const parsed = parseTimestamp(value);
    if (parsed == null) return "TIME UNKNOWN";
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(parsed));
  }

  function formatAge(value) {
    const seconds = ageSeconds(value);
    if (!Number.isFinite(seconds)) return "AGE UNKNOWN";
    if (seconds < 60) return `${Math.max(0, Math.round(seconds))} SEC AGO`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} MIN AGO`;
    return `${Math.round(seconds / 3600)} HR AGO`;
  }

  function titleCase(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/(^|[_\s-])([a-z])/g, (_, separator, letter) =>
        `${separator === "_" ? " " : separator}${letter.toUpperCase()}`,
      );
  }

  function upper(value, fallback = "UNKNOWN") {
    const text = String(value || "").trim();
    return text ? text.toUpperCase() : fallback;
  }

  function abbreviate(text, maxLength = 64) {
    const value = String(text || "").replace(/\s+/g, " ").trim();
    if (value.length <= maxLength) return value;
    return `${value.slice(0, Math.max(0, maxLength - 1)).trim()}…`;
  }

  function formatPercent(value) {
    const number = numeric(value);
    return number == null ? "—%" : `${Math.round(number)}%`;
  }

  function formatTemperature(value, suffix = "°F") {
    const number = numeric(value);
    return number == null ? `—${suffix}` : `${Math.round(number)}${suffix}`;
  }

  function formatMiles(value) {
    const number = numeric(value);
    if (number == null) return "DISTANCE NOT REPORTED";
    return `${number < 10 ? number.toFixed(1) : Math.round(number)} MI AWAY`;
  }

  function sourceState(value) {
    const state = upper(value, "UNKNOWN");
    if (["LIVE", "HEALTHY", "ONLINE", "CURRENT"].includes(state)) return "LIVE";
    if (["RETAINED", "CACHED"].includes(state)) return "RETAINED";
    if (["PARTIAL", "DEGRADED"].includes(state)) return "PARTIAL";
    if (["STALE", "OFFLINE", "FAILED", "ERROR"].includes(state)) return state;
    if (["NOT_CONFIGURED", "PENDING", "PENDING_KEY", "KEY_REQUIRED"].includes(state)) {
      return "PENDING";
    }
    return "PENDING";
  }

  function setSourceChip(id, label, stateValue) {
    const element = byId(id);
    if (!element) return;
    const state = sourceState(stateValue);
    element.className = `source-chip source-${state.toLowerCase()}`;
    element.textContent = `${label} · ${state}`;
  }

  function setInlineState(id, text, tone) {
    const element = byId(id);
    if (!element) return;
    element.className = `inline-state state-${tone}`;
    element.textContent = text;
  }

  function setPosture(text, tone) {
    const element = byId("overall-posture");
    if (!element) return;
    element.className = `posture posture-${tone}`;
    element.replaceChildren();
    const dot = document.createElement("i");
    dot.setAttribute("aria-hidden", "true");
    element.append(dot, document.createTextNode(text));
  }

  async function fetchJson(url) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), CONFIG.fetchTimeoutMs);

    try {
      const response = await fetch(url, {
        cache: "no-store",
        headers: { Accept: "application/json, application/geo+json" },
        signal: controller.signal,
      });
      const contentType = response.headers.get("content-type") || "";
      if (!response.ok) throw new Error(`${url} returned ${response.status}`);
      if (!/(?:application|text)\/(?:[^;]+\+)?json\b|geo\+json/i.test(contentType)) {
        throw new Error(`${url} returned non-JSON content (${contentType || "unknown"})`);
      }
      const value = await response.json();
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new Error(`${url} returned an invalid JSON contract`);
      }
      return value;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function safeFetch(url, fallback = null) {
    try {
      return await fetchJson(url);
    } catch (error) {
      console.warn("LOC001 read-only fetch failed", url, error);
      return fallback;
    }
  }

  async function fetchMapContract() {
    try {
      const contract = await fetchJson(CONFIG.mapContract);
      runtime.lastMapContract = contract;
      return contract;
    } catch (error) {
      console.warn("LOC002 map contract retained after fetch failure", error);
      return runtime.lastMapContract || emptyMapContract();
    }
  }

  async function loadRuntimeData() {
    if (fixtureUrl) {
      const fixture = applyFixtureClock(await fetchJson(fixtureUrl));
      return {
        opsBrief: fixture.opsBrief || null,
        opsFeed: fixture.opsFeed || null,
        workloads: fixture.workloads || null,
        status: fixture.status || null,
        games: fixture.games || null,
        mapContract: fixture.mapContract || emptyMapContract(),
      };
    }

    const [opsBrief, opsFeed, workloads, status, games, mapContract] =
      await Promise.all([
        safeFetch("/api/ops/brief"),
        safeFetch("/api/ops/feed"),
        safeFetch("/api/ops/workloads"),
        safeFetch("/api/status"),
        safeFetch("/api/games"),
        fetchMapContract(),
      ]);

    return { opsBrief, opsFeed, workloads, status, games, mapContract };
  }

  function applyFixtureClock(fixture) {
    const now = Date.now();
    const stale = fixtureName === "stale" || fixtureName === "frozen";
    const generated = new Date(now - (stale ? 3 * 60 * 60_000 : 20_000)).toISOString();
    const observed = new Date(now - (stale ? 3 * 60 * 60_000 : 90_000)).toISOString();
    const expires = new Date(now + 2 * 60 * 60_000).toISOString();

    if (fixture.opsBrief) {
      fixture.opsBrief.generated_at = generated;
      if (fixture.opsBrief.weather) fixture.opsBrief.weather.updated_at = observed;
      if (fixture.opsBrief.camera_alpha) {
        fixture.opsBrief.camera_alpha.updated_at =
          fixtureName === "camera-stale" ? new Date(now - 60 * 24 * 60 * 60_000).toISOString() : observed;
      }
    }
    if (fixture.opsFeed) {
      fixture.opsFeed.generated_at = generated;
      for (const item of fixture.opsFeed.items || []) item.timestamp = observed;
    }
    if (fixture.workloads) fixture.workloads.generated_at = generated;
    if (fixture.mapContract) {
      if (fixtureName !== "map-undated") fixture.mapContract.generated_at = generated;
      if (fixture.mapContract.radar) fixture.mapContract.radar.frame_at = observed;
      for (const feature of fixture.mapContract.features || []) {
        feature.properties = feature.properties || {};
        feature.properties.updated_at = observed;
        if (feature.properties.expires_at) feature.properties.expires_at = expires;
        if (feature.properties.planned_end_at) feature.properties.planned_end_at = expires;
      }
    }
    return fixture;
  }

  function emptyMapContract() {
    return {
      type: "FeatureCollection",
      schema_version: CONFIG.schemaVersion,
      generated_at: null,
      center: { lat: CONFIG.fallbackCenter[0], lon: CONFIG.fallbackCenter[1], zoom: 9 },
      health: { state: "PARTIAL", summary: "Local map contract unavailable" },
      sources: {
        nws_alerts: { state: "OFFLINE" },
        ny511: { state: "NOT_CONFIGURED", reason: "KEY_REQUIRED" },
        nyseg: { state: "NOT_CONFIGURED", reason: "CONTRACT_PENDING" },
      },
      radar: { state: "OFFLINE", url: null, bounds: null },
      features: [],
    };
  }

  function initMap() {
    if (!window.L) {
      byId("map-fallback").hidden = false;
      setSourceChip("source-basemap", "BASEMAP", "OFFLINE");
      return;
    }

    const map = window.L.map("ops-map", {
      center: CONFIG.fallbackCenter,
      zoom: CONFIG.fallbackZoom,
      minZoom: CONFIG.fallbackZoom,
      maxZoom: CONFIG.fallbackZoom,
      zoomControl: false,
      attributionControl: true,
      preferCanvas: true,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      touchZoom: false,
      fadeAnimation: false,
      zoomAnimation: false,
      markerZoomAnimation: false,
    });

    map.createPane("radarPane");
    map.getPane("radarPane").classList.add("leaflet-radar-pane");
    map.getPane("radarPane").style.zIndex = "260";
    map.createPane("warningPane");
    map.getPane("warningPane").style.zIndex = "410";
    map.createPane("incidentPane");
    map.getPane("incidentPane").style.zIndex = "440";

    runtime.map = map;

    if (offlineMode) {
      setSourceChip("source-basemap", "BASEMAP", "PENDING");
      return;
    }

    const basemap = window.L.tileLayer(CONFIG.basemapUrl, {
      minZoom: CONFIG.fallbackZoom,
      maxZoom: CONFIG.fallbackZoom,
      updateWhenIdle: true,
      keepBuffer: 1,
      detectRetina: false,
      crossOrigin: true,
      attribution: "&copy; OpenStreetMap contributors",
    });

    let tileErrors = 0;
    basemap.on("load", () => {
      runtime.basemapLoaded = true;
      setSourceChip("source-basemap", "BASEMAP", tileErrors ? "PARTIAL" : "LIVE");
      updateMapFallback();
    });
    basemap.on("tileerror", () => {
      tileErrors += 1;
      setSourceChip("source-basemap", "BASEMAP", runtime.basemapLoaded ? "PARTIAL" : "OFFLINE");
      updateMapFallback();
    });
    basemap.addTo(map);
    runtime.basemap = basemap;
  }

  function updateMapFallback() {
    const fallback = byId("map-fallback");
    if (!fallback) return;
    const hasMapEngine = Boolean(runtime.map);
    const hasIncidentData = Boolean(runtime.latestData?.mapContract?.features?.length);
    fallback.hidden = hasMapEngine && (offlineMode || runtime.basemapLoaded || hasIncidentData);
  }

  function validateMapContract(contract) {
    if (!contract || contract.type !== "FeatureCollection") return emptyMapContract();
    if (contract.schema_version !== CONFIG.schemaVersion) return emptyMapContract();
    const normalized = {
      ...contract,
      sources: contract.sources && typeof contract.sources === "object" ? contract.sources : {},
      radar: contract.radar && typeof contract.radar === "object" ? contract.radar : {},
      features: Array.isArray(contract.features) ? contract.features : [],
    };
    const generatedAt = parseTimestamp(normalized.generated_at);
    if (generatedAt != null && ageSeconds(normalized.generated_at) <= CONFIG.mapContractMaxAgeSeconds) {
      return normalized;
    }
    const staleSource = (source) => {
      const value = source && typeof source === "object" ? source : {};
      return upper(value.state) === "NOT_CONFIGURED" ? value : { ...value, state: "STALE" };
    };
    return {
      ...normalized,
      health: {
        ...(normalized.health || {}),
        state: "STALE",
        summary: generatedAt == null
          ? "Local map contract has no valid generation time."
          : "Local map contract is not refreshing.",
      },
      sources: Object.fromEntries(
        Object.entries(normalized.sources).map(([name, source]) => [name, staleSource(source)]),
      ),
      radar: { ...normalized.radar, state: "STALE" },
      features: normalized.features.map((feature) => ({
        ...feature,
        properties: {
          ...(feature?.properties || {}),
          freshness_state: "STALE",
          promotable: false,
        },
      })),
    };
  }

  function featureProperties(feature) {
    return feature && feature.properties && typeof feature.properties === "object"
      ? feature.properties
      : {};
  }

  function featureId(feature) {
    const props = featureProperties(feature);
    return String(feature.id || props.id || `${props.source || "source"}:${props.source_id || "unknown"}`);
  }

  function featureSeverity(feature) {
    const props = featureProperties(feature);
    const severity = upper(props.severity || props.priority || "UNKNOWN");
    return { label: severity, rank: severityRank[severity] ?? 0 };
  }

  function featureKind(feature) {
    const props = featureProperties(feature);
    const value = upper(props.kind || props.category || props.event_type || "LOCAL");
    if (value.includes("ROAD") || value.includes("CLOSURE") || value.includes("TRAFFIC")) return "ROAD";
    if (value.includes("POWER") || value.includes("OUTAGE")) return "POWER";
    if (value.includes("FLOOD") || value.includes("WATER")) return "FLOOD";
    if (value.includes("WEATHER") || value.includes("WARNING") || value.includes("ALERT")) return "WEATHER";
    return "LOCAL";
  }

  function featureVisible(feature) {
    const props = featureProperties(feature);
    const status = upper(props.status || "ACTIVE");
    if (["EXPIRED", "RECOVERED", "CLOSED", "INACTIVE", "SCHEDULED", "FUTURE"].includes(status)) return false;
    const expiresAt = parseTimestamp(props.expires_at || props.planned_end_at);
    return expiresAt == null || expiresAt > Date.now();
  }

  function activeAreaAlertCount(contract) {
    const source = contract?.sources?.nws_alerts;
    if (!source || typeof source !== "object") return null;

    const state = sourceState(source.state);
    if (!["LIVE", "RETAINED"].includes(state)) return null;

    const features = Array.isArray(contract.features) ? contract.features : [];
    return features.filter((feature) => {
      const props = featureProperties(feature);
      return upper(props.source, "") === "NWS" && featureVisible(feature);
    }).length;
  }

  function featurePromotable(feature) {
    if (!featureVisible(feature)) return false;
    const props = featureProperties(feature);
    const freshness = upper(props.freshness_state || "LIVE");
    return props.promotable !== false && !["STALE", "OFFLINE", "FAILED", "ERROR"].includes(freshness);
  }

  function featureSort(a, b) {
    const severityDelta = featureSeverity(b).rank - featureSeverity(a).rank;
    if (severityDelta) return severityDelta;
    const watchedDelta = Number(Boolean(featureProperties(b).watched_route)) - Number(Boolean(featureProperties(a).watched_route));
    if (watchedDelta) return watchedDelta;
    const distanceA = numeric(featureProperties(a).distance_miles, Infinity);
    const distanceB = numeric(featureProperties(b).distance_miles, Infinity);
    if (distanceA !== distanceB) return distanceA - distanceB;
    return featureId(a).localeCompare(featureId(b));
  }

  function selectPriority(features) {
    const candidates = features.filter(featurePromotable).sort(featureSort);
    if (!candidates.length) {
      runtime.selectedFeatureId = null;
      runtime.selectedAt = 0;
      return null;
    }

    const strongest = candidates[0];
    const previous = candidates.find((feature) => featureId(feature) === runtime.selectedFeatureId);
    const holdActive = Date.now() - runtime.selectedAt < CONFIG.selectionHoldMs;

    if (
      previous &&
      holdActive &&
      featureSeverity(previous).rank >= featureSeverity(strongest).rank
    ) {
      return previous;
    }

    const nextId = featureId(strongest);
    if (nextId !== runtime.selectedFeatureId) {
      runtime.selectedFeatureId = nextId;
      runtime.selectedAt = Date.now();
    }
    return strongest;
  }

  function markerClass(feature, selected) {
    const kind = featureKind(feature).toLowerCase();
    const critical = featureSeverity(feature).rank >= severityRank.HIGH;
    return [
      "ops-marker-shell",
      `ops-marker-${kind === "local" ? "road" : kind}`,
      critical ? "ops-marker-critical" : "",
      selected ? "ops-marker-selected" : "",
    ]
      .filter(Boolean)
      .join(" ");
  }

  function markerGlyph(feature) {
    const kind = featureKind(feature);
    if (kind === "POWER") return "P";
    if (kind === "FLOOD") return "F";
    if (kind === "WEATHER") return "!";
    return "!";
  }

  function geometryStyle(feature, selected) {
    const kind = featureKind(feature);
    const severity = featureSeverity(feature).rank;
    const high = severity >= severityRank.HIGH;
    const colors = {
      ROAD: high ? "#ff6174" : "#ffc75f",
      POWER: "#b98cff",
      FLOOD: "#39e9ff",
      WEATHER: high ? "#ff9d32" : "#ffc75f",
      LOCAL: "#ffc75f",
    };
    const color = colors[kind] || colors.LOCAL;
    return {
      pane: kind === "WEATHER" ? "warningPane" : "incidentPane",
      color,
      weight: selected ? 6 : kind === "ROAD" ? 4 : 2,
      opacity: selected ? 1 : 0.84,
      fillColor: color,
      fillOpacity: kind === "WEATHER" ? (selected ? 0.24 : 0.14) : 0.12,
      dashArray: featureProperties(feature).location_precision === "ZONE_LEVEL_AREA" ? "8 7" : undefined,
    };
  }

  function tooltipNode(feature) {
    const props = featureProperties(feature);
    const node = document.createElement("div");
    node.textContent = abbreviate(props.headline || props.title || props.roadway || "Local incident", 80);
    return node;
  }

  function renderMapFeatures(contract, priority) {
    if (!runtime.map || !window.L) return;
    if (runtime.incidentLayer) runtime.incidentLayer.remove();

    const selectedId = priority ? featureId(priority) : null;
    const activeFeatures = contract.features
      .filter(featureVisible)
      .sort(featureSort)
      .slice(0, CONFIG.maxFeatures);

    runtime.incidentLayer = window.L.geoJSON(activeFeatures, {
      pane: "incidentPane",
      style: (feature) => geometryStyle(feature, featureId(feature) === selectedId),
      pointToLayer: (feature, latlng) => {
        const selected = featureId(feature) === selectedId;
        const icon = window.L.divIcon({
          className: "",
          html: `<div class="${markerClass(feature, selected)}"><span class="ops-marker">${markerGlyph(feature)}</span></div>`,
          iconSize: selected ? [32, 32] : [26, 26],
          iconAnchor: selected ? [16, 16] : [13, 13],
        });
        return window.L.marker(latlng, { icon, pane: "incidentPane", keyboard: false });
      },
      onEachFeature: (feature, layer) => {
        layer.bindTooltip(tooltipNode(feature), {
          className: "ops-tooltip",
          direction: "top",
          opacity: 0.96,
        });
      },
    }).addTo(runtime.map);
  }

  function renderRadar(radar) {
    if (!runtime.map || !window.L) return;
    let state = sourceState(radar.state);
    if (state === "LIVE" && ageSeconds(radar.frame_at) > CONFIG.radarFrameMaxAgeSeconds) {
      state = "STALE";
    }

    if (offlineMode) {
      setSourceChip("source-radar", "RADAR", state);
      return;
    }

    if (!radar.url || !Array.isArray(radar.bounds) || radar.bounds.length !== 2) {
      setSourceChip("source-radar", "RADAR", runtime.radarLayer ? "RETAINED" : "OFFLINE");
      return;
    }

    const southWest = radar.bounds[0];
    const northEast = radar.bounds[1];
    if (!finitePair(southWest) || !finitePair(northEast)) {
      setSourceChip("source-radar", "RADAR", runtime.radarLayer ? "RETAINED" : "OFFLINE");
      return;
    }

    // Contract state is authoritative while the replacement overlay loads.
    // This prevents a healthy radar feed from appearing offline at page start.
    setSourceChip("source-radar", "RADAR", state);

    const url = `${radar.url}${radar.url.includes("?") ? "&" : "?"}v=${encodeURIComponent(radar.frame_at || Date.now())}`;
    if (runtime.radarUrl === url) {
      runtime.radarLayer?.setOpacity(state === "STALE" ? 0.3 : 0.56);
      setSourceChip("source-radar", "RADAR", state);
      return;
    }
    if (runtime.radarPendingUrl === url) return;

    const previous = runtime.radarLayer;
    const layer = window.L.imageOverlay(url, [southWest, northEast], {
      pane: "radarPane",
      opacity: state === "STALE" ? 0.3 : 0.56,
      interactive: false,
      crossOrigin: false,
    });
    runtime.radarPendingUrl = url;
    layer.on("load", () => {
      if (previous && previous !== layer) previous.remove();
      runtime.radarLayer = layer;
      runtime.radarUrl = url;
      runtime.radarPendingUrl = null;
      setSourceChip("source-radar", "RADAR", state);
    });
    layer.on("error", () => {
      layer.remove();
      runtime.radarPendingUrl = null;
      setSourceChip("source-radar", "RADAR", previous ? "RETAINED" : "OFFLINE");
    });
    layer.addTo(runtime.map);
  }

  function renderMapContract(rawContract, priority) {
    const contract = validateMapContract(rawContract);
    const center = contract.center || {};
    const lat = numeric(center.lat);
    const lon = numeric(center.lon);
    const zoom = numeric(center.zoom, CONFIG.fallbackZoom);
    if (runtime.map && lat != null && lon != null) runtime.map.setView([lat, lon], zoom, { animate: false });

    const healthState = sourceState(contract.health?.state || "PARTIAL");
    const nwsState = contract.sources?.nws_alerts?.state || "OFFLINE";
    const roadState = contract.sources?.ny511?.state || "NOT_CONFIGURED";
    setSourceChip("source-alerts", "WARNINGS", nwsState);
    if (
      upper(roadState) === "NOT_CONFIGURED" &&
      upper(contract.sources?.ny511?.reason) === "KEY_REQUIRED"
    ) {
      const roadChip = byId("source-roads");
      if (roadChip) {
        roadChip.className = "source-chip source-pending";
        roadChip.textContent = "511NY · KEY REQUIRED";
      }
    } else {
      setSourceChip("source-roads", "511NY", roadState);
    }
    setText(
      "map-updated",
      contract.generated_at
        ? `UPDATED ${formatClock(contract.generated_at)} · ${healthState}`
        : "LOCAL CONTRACT UNAVAILABLE",
    );

    renderRadar(contract.radar || {});
    renderMapFeatures(contract, priority);
    updateMapFallback();
    return contract;
  }

  function renderWeather(opsBrief, mapContract) {
    const areaAlertCount = activeAreaAlertCount(mapContract);
    setText("weather-alerts", areaAlertCount ?? "—");

    const weather = opsBrief?.weather;
    if (!weather || typeof weather !== "object") {
      setText("weather-state", "WEATHER LINK OFFLINE");
      setText("weather-temp", "—°F");
      setText("weather-feels", "—°");
      setText("weather-wind", "—");
      setText("weather-gust", "—");
      setText("weather-rain", "—%");
      return;
    }
    const briefFresh = ageSeconds(opsBrief?.generated_at) <= CONFIG.opsBriefMaxAgeSeconds;
    const observationFresh =
      ageSeconds(weather.updated_at || opsBrief?.generated_at) <= CONFIG.weatherMaxAgeSeconds;
    const live =
      briefFresh &&
      observationFresh &&
      weather.usable === true &&
      weather.stale !== true;
    const posture = upper(weather.posture, live ? "LIVE" : "PARTIAL");
    const state = live
      ? "LIVE"
      : !briefFresh || !observationFresh
        ? "STALE"
        : weather.stale === true && posture === "LIVE"
          ? "STALE"
          : posture;
    setText("weather-state", `WEATHER ${state}`);
    setText("weather-temp", formatTemperature(weather.temperature));
    setText("weather-feels", formatTemperature(weather.feels_like, "°"));
    setText("weather-wind", weather.wind_mph == null ? "—" : `${numeric(weather.wind_mph, 0).toFixed(1)}`);
    setText("weather-gust", weather.gust_mph == null ? "—" : `${numeric(weather.gust_mph, 0).toFixed(1)}`);
    setText("weather-rain", weather.rain_chance == null ? "—%" : `${Math.round(numeric(weather.rain_chance, 0))}%`);
  }

  function metricTone(value, warning, critical, fresh) {
    if (!fresh || value == null) return "pending";
    if (value >= critical) return "critical";
    if (value >= warning) return "watch";
    return "good";
  }

  function setMetricTone(id, tone) {
    const row = byId(id)?.closest("div");
    if (!row) return;
    row.classList.remove("metric-good", "metric-watch", "metric-critical", "metric-pending");
    row.classList.add(`metric-${tone}`);
  }

  function renderFabric(data) {
    const brief = data.opsBrief || {};
    const status = data.status || {};
    const services = brief.services?.counts || {};
    const metrics = brief.resources?.metrics || {};
    const external = brief.external || {};
    const serviceOnline = numeric(services.online, null) ?? Object.values(status.services || {}).filter((value) => upper(value) === "ONLINE").length;
    const serviceTotal = numeric(services.total, null) ?? Object.keys(status.services || {}).length;
    const externalValues = [external.dashboard, external.status, external.tracker].filter(Boolean);
    const externalOnline = externalValues.filter((value) => upper(value.state) === "ONLINE").length;
    const resource = (name, fallback) => numeric(metrics[name]?.value, numeric(status.system?.[fallback]));
    const briefFresh = brief.generated_at
      ? ageSeconds(brief.generated_at) <= CONFIG.opsBriefMaxAgeSeconds
      : Boolean(status && Object.keys(status).length);
    const servicePosture = upper(brief.services?.posture || brief.state?.overall_state || "UNKNOWN");
    const resourcePosture = upper(brief.resources?.posture || "UNKNOWN");
    const cpu = resource("cpu", "cpu");
    const memory = resource("memory", "memory");
    const rootDisk = resource("root_disk", "root_disk");
    const mediaDisk = resource("media_disk", "media_disk");
    const metricTones = {
      cpu: metricTone(cpu, 75, 92, briefFresh),
      memory: metricTone(memory, 80, 94, briefFresh),
      root: metricTone(rootDisk, 82, 94, briefFresh),
      media: metricTone(mediaDisk, 82, 94, briefFresh),
    };
    const worstMetric = Object.values(metricTones).includes("critical")
      ? "critical"
      : Object.values(metricTones).includes("watch")
        ? "watch"
        : Object.values(metricTones).includes("pending")
          ? "pending"
          : "good";
    const postureGood = ["STABLE", "GREEN", "ONLINE"].includes(servicePosture) &&
      ["STABLE", "GREEN", "ONLINE", "UNKNOWN"].includes(resourcePosture);
    const tone = !briefFresh ? "pending" : !postureGood ? "watch" : worstMetric;
    const posture = !briefFresh
      ? "RETAINED"
      : tone === "critical"
        ? "RESOURCE ALERT"
        : tone === "watch"
          ? "WATCH"
          : servicePosture;

    setInlineState("fabric-state", posture, tone);
    setInlineState("fabric-state-compact", posture, tone);
    setText("fabric-services", `${serviceOnline ?? "—"} / ${serviceTotal ?? "—"}`);
    setText("fabric-cpu", formatPercent(cpu));
    setText("fabric-memory", formatPercent(memory));
    setText("fabric-root", formatPercent(rootDisk));
    setText("fabric-media", formatPercent(mediaDisk));
    setText("fabric-external", `${externalOnline} / ${externalValues.length || "—"}`);
    setText("compact-services", `${serviceOnline ?? "—"} / ${serviceTotal ?? "—"}`);
    setText("compact-cpu", formatPercent(cpu));
    setText("compact-memory", formatPercent(memory));
    setText("compact-root", formatPercent(rootDisk));
    setText("compact-external", `${externalOnline} / ${externalValues.length || "—"}`);
    setMetricTone("fabric-cpu", metricTones.cpu);
    setMetricTone("fabric-memory", metricTones.memory);
    setMetricTone("fabric-root", metricTones.root);
    setMetricTone("fabric-media", metricTones.media);
    setMetricTone("compact-cpu", metricTones.cpu);
    setMetricTone("compact-memory", metricTones.memory);
    setMetricTone("compact-root", metricTones.root);
  }

  function renderRecent(opsFeed, featureCount, mapContract) {
    if (!opsFeed || typeof opsFeed !== "object") {
      setText("incident-count", "LINK OFFLINE");
      setText("recent-primary", "OPERATIONS FEED UNAVAILABLE");
      setText("recent-secondary", "NO CLEAR-STATE CLAIM · CHECK DATA LINKS");
      return;
    }
    const generatedAt = parseTimestamp(opsFeed.generated_at);
    if (generatedAt == null) {
      setText("incident-count", "LINK PARTIAL");
      setText("recent-primary", "OPERATIONS FEED FRESHNESS UNKNOWN");
      setText("recent-secondary", "NO CURRENT INCIDENT CLAIM · CHECK FEED CONTRACT");
      return;
    }
    if (ageSeconds(opsFeed.generated_at) > CONFIG.opsFeedMaxAgeSeconds) {
      setText("incident-count", "FEED STALE");
      setText("recent-primary", "OPERATIONS FEED STALE");
      setText("recent-secondary", `NO CURRENT INCIDENT CLAIM · UPDATED ${formatAge(opsFeed.generated_at)}`);
      return;
    }
    const items = Array.isArray(opsFeed?.items) ? opsFeed.items : [];
    const reportedActive = numeric(opsFeed?.counts?.active, null);
    if (reportedActive == null) {
      setText("incident-count", "COUNT UNKNOWN");
      setText("recent-primary", featureCount ? `${featureCount} MAP IMPACT${featureCount === 1 ? "" : "S"}` : "OPERATIONS COUNT UNAVAILABLE");
      setText("recent-secondary", "NO CLEAR-STATE CLAIM · CHECK FEED CONTRACT");
      return;
    }
    const mapState = sourceState(mapContract?.health?.state || "PARTIAL");
    const mapCurrent = mapState === "LIVE";
    const activeCount = reportedActive + (mapCurrent ? featureCount : 0);
    setText(
      "incident-count",
      mapCurrent ? `${activeCount} ACTIVE` : `${reportedActive} FEED ACTIVE · MAP ${mapState}`,
    );

    if (!activeCount) {
      setText(
        "recent-primary",
        mapCurrent ? "NO ACTIVE INCIDENTS" : `NO FEED INCIDENTS · MAP ${mapState}`,
      );
    } else {
      const firstActive = items.find((item) => upper(item.status) !== "RECOVERED");
      setText("recent-primary", firstActive ? abbreviate(firstActive.title, 58).toUpperCase() : `${activeCount} LOCAL IMPACT${activeCount === 1 ? "" : "S"}`);
    }

    const latest = items[0];
    setText(
      "recent-secondary",
      latest
        ? `${formatClock(latest.timestamp)} · ${abbreviate(latest.title || latest.summary, 72).toUpperCase()}`
        : "OPERATIONS FEED CURRENT · NO RECENT EVENTS",
    );
  }

  function renderGames(games) {
    if (!games) {
      setText("games-title", "GAMES LINK PARTIAL");
      setText("games-detail", "CONTRACT UNAVAILABLE");
      return;
    }
    const online = Object.entries(games)
      .filter(([key, value]) => !key.startsWith("_") && upper(value) === "ONLINE")
      .map(([key]) => key.replace("terraria_calamity", "terraria").replace("minecraft_modded", "modded"));
    setText("games-title", online.length ? "GAMES RUNNING" : "GAMES QUIET");
    setText("games-detail", online.length ? `${online.length} · ${online.map((item) => upper(item)).join(" · ")}` : "NO SERVERS ONLINE");
  }

  function renderWorkloads(workloads) {
    if (!workloads) {
      setText("workloads-title", "MEDIA PARTIAL");
      setText("workloads-detail", "CONTRACT UNAVAILABLE");
      return;
    }
    const generatedAt = parseTimestamp(workloads.generated_at);
    if (generatedAt == null) {
      setText("workloads-title", "MEDIA PARTIAL");
      setText("workloads-detail", "FRESHNESS UNKNOWN");
      return;
    }
    if (ageSeconds(workloads.generated_at) > CONFIG.workloadsMaxAgeSeconds) {
      setText("workloads-title", "MEDIA STALE");
      setText("workloads-detail", `LAST SNAPSHOT ${formatAge(workloads.generated_at)}`);
      return;
    }

    if (workloads.contract === "MA001" && workloads.display?.title && workloads.display?.detail) {
      setText("workloads-title", abbreviate(workloads.display.title, 24).toUpperCase());
      setText("workloads-detail", abbreviate(workloads.display.detail, 54).toUpperCase());
      return;
    }

    const count = numeric(workloads.counts?.active, 0);
    setText("workloads-title", workloads.active ? "WORKLOADS ACTIVE" : "WORKLOADS STABLE");
    setText("workloads-detail", workloads.active ? `${count} ACTIVE · ${abbreviate(workloads.summary, 40).toUpperCase()}` : "NONE ACTIVE");
  }

  function incidentTone(priority) {
    const rank = featureSeverity(priority).rank;
    return rank >= severityRank.CRITICAL ? "critical" : rank >= severityRank.HIGH ? "watch" : "partial";
  }

  function renderPriority(priority) {
    const hasPriority = Boolean(priority && featureSeverity(priority).rank >= severityRank.WATCH);
    document.body.classList.toggle("incident-active", hasPriority);
    byId("priority-card").hidden = !hasPriority;
    byId("incident-support").hidden = !hasPriority;

    if (!hasPriority) return;
    const props = featureProperties(priority);
    const kind = featureKind(priority);
    const severity = featureSeverity(priority).label;
    const source = upper(props.source, "LOCAL OPS");
    const title = props.roadway || props.headline || props.title || "LOCAL INCIDENT";
    const impact = props.impact || props.lane_impact || props.description || `${kind} IMPACT`;
    const location = props.distance_miles != null
      ? formatMiles(props.distance_miles)
      : upper(props.location || props.area || props.county || "HOME AREA");
    const end = props.planned_end_at || props.expires_at;
    const precision = upper(props.location_precision, "SOURCE POINT").replaceAll("_", " ");
    const freshness = upper(props.freshness_state || "LIVE");
    const retained = freshness === "RETAINED";

    setText("priority-label", `${severity} PRIORITY${retained ? " · RETAINED" : ""}`);
    setText("priority-kind", `${kind} IMPACT`);
    setText("priority-title", abbreviate(title, 38).toUpperCase());
    setText("priority-impact", abbreviate(impact, 66).toUpperCase());
    setText("priority-location", location);
    setText("priority-timing", end ? `EXPECTED THROUGH ${formatClock(end)}` : "END TIME NOT REPORTED");
    setText(
      "priority-source",
      `SOURCE ${source} · ${precision} · ${retained ? "DATA RETAINED · " : ""}UPDATED ${formatAge(props.updated_at)}`,
    );
  }

  function renderDataLinks(contract) {
    const sources = contract.sources || {};
    const road = sourceState(sources.ny511?.state || "NOT_CONFIGURED");
    const nws = sourceState(sources.nws_alerts?.state || "OFFLINE");
    const power = sourceState(sources.nyseg?.state || "NOT_CONFIGURED");
    setText("data-links-detail", `511NY ${road} · NWS ${nws} · NYSEG ${power}`);
  }

  function renderOverall(data, contract, priority) {
    if (priority && featureSeverity(priority).rank >= severityRank.WATCH) {
      const kind = featureKind(priority);
      setPosture(`OPS PRIORITY · ${kind} IMPACT`, incidentTone(priority));
      return;
    }

    const briefFresh = data.opsBrief && ageSeconds(data.opsBrief.generated_at) <= CONFIG.opsBriefMaxAgeSeconds;
    const mapState = sourceState(contract.health?.state || "PARTIAL");
    const operational = upper(data.opsBrief?.state?.overall_state || "UNKNOWN");
    const threat = upper(data.opsBrief?.state?.threat_level || "UNKNOWN");
    const goodThreat = ["GREEN", "LOW", "NONE", "CLEAR"].includes(threat);
    const criticalState =
      briefFresh &&
      (["RED", "CRITICAL", "SEVERE", "EXTREME"].includes(threat) ||
        ["RED", "CRITICAL", "FAILED", "OFFLINE"].includes(operational));
    const good =
      briefFresh &&
      ["STABLE", "GREEN"].includes(operational) &&
      goodThreat &&
      mapState === "LIVE";

    if (good) {
      setPosture(`${threat} · ${operational}`, "good");
    } else if (criticalState) {
      setPosture(`${threat} · ${operational}`, "critical");
    } else if (!briefFresh || ["STALE", "OFFLINE", "FAILED", "ERROR", "PARTIAL", "RETAINED"].includes(mapState)) {
      setPosture("PARTIAL · CHECK DATA LINKS", "partial");
    } else {
      setPosture(`${threat} · ${operational}`, "watch");
    }
  }

  function updateCameraStatus(imageLoaded) {
    const camera = runtime.latestData?.opsBrief?.camera_alpha || {};
    const statusFresh = ageSeconds(camera.updated_at) <= CONFIG.cameraStatusMaxAgeSeconds;
    let text = "UNAVAILABLE";
    let tone = "critical";

    if (imageLoaded && statusFresh && camera.online === true) {
      text = "ONLINE";
      tone = "good";
    } else if (imageLoaded) {
      text = "FRAME LIVE · STATUS RETAINED";
      tone = "watch";
    } else if (camera.online === true && statusFresh) {
      text = "STATUS LIVE · FRAME WAIT";
      tone = "watch";
    } else if (camera.online === true) {
      text = "STATUS RETAINED";
      tone = "pending";
    }

    setInlineState("camera-state", text, tone);
    setInlineState("camera-state-compact", text.includes("FRAME LIVE") ? "FRAME LIVE" : text, tone);
  }

  function hasRecentCameraFrame() {
    const newest = Math.max(0, ...Object.values(runtime.cameraFrames).map((value) => numeric(value, 0)));
    return newest > 0 && Date.now() - newest <= CONFIG.cameraFrameMaxAgeSeconds * 1000;
  }

  function refreshCamera() {
    if (fixtureName) {
      updateCameraStatus(false);
      return;
    }
    const stamp = Date.now();
    const url = `/go2rtc/api/frame.jpeg?src=office_cam_wall&v=${stamp}`;
    ["camera-image", "camera-image-compact"].forEach((id) => {
      const image = byId(id);
      if (!image) return;
      image.onload = () => {
        runtime.cameraFrames[id] = Date.now();
        const placeholderId = id === "camera-image" ? "camera-placeholder" : "camera-placeholder-compact";
        const placeholder = byId(placeholderId);
        if (placeholder) placeholder.hidden = true;
        updateCameraStatus(hasRecentCameraFrame());
      };
      image.onerror = () => {
        runtime.cameraFrames[id] = null;
        image.removeAttribute("src");
        const placeholderId = id === "camera-image" ? "camera-placeholder" : "camera-placeholder-compact";
        const placeholder = byId(placeholderId);
        if (placeholder) placeholder.hidden = false;
        updateCameraStatus(hasRecentCameraFrame());
      };
      image.src = url;
    });
  }

  function applyNightMode() {
    if (forceDayMode) {
      document.body.classList.remove("night-mode");
      return;
    }
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hour: "2-digit",
      hour12: false,
    }).formatToParts(new Date());
    const hour = Number(parts.find((part) => part.type === "hour")?.value || 12);
    document.body.classList.toggle("night-mode", hour >= 23 || hour < 8);
  }

  async function refresh() {
    try {
      const data = await loadRuntimeData();
      const contract = validateMapContract(data.mapContract);
      const features = contract.features.filter(featureVisible);
      const priority = selectPriority(features);
      runtime.latestData = { ...data, mapContract: contract };

      renderWeather(data.opsBrief || {}, contract);
      renderFabric(data);
      renderRecent(data.opsFeed, features.length, contract);
      renderGames(data.games);
      renderWorkloads(data.workloads);
      renderPriority(priority);
      renderDataLinks(contract);
      renderOverall(data, contract, priority);
      renderMapContract(contract, priority);
      updateCameraStatus(hasRecentCameraFrame());
      document.body.dataset.state = "ready";
    } catch (error) {
      console.error("LOC001 refresh failed", error);
      setPosture("PARTIAL · LOCAL CONTRACT FAILED", "critical");
      byId("map-fallback").hidden = false;
      document.body.dataset.state = "degraded";
    }
  }

  function start() {
    initMap();
    applyNightMode();
    refreshCamera();
    refresh();
    runtime.cameraTimer = window.setInterval(refreshCamera, CONFIG.cameraRefreshMs);
    window.setInterval(refresh, CONFIG.refreshMs);
    window.setInterval(applyNightMode, 60_000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
