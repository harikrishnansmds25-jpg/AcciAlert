"use strict";

(function exposeHotspotGeometry(root, factory) {
  const geometry = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = geometry;
  } else {
    root.TrafficHotspotGeometry = geometry;
  }
}(globalThis, () => {
  const BOUNDARY_EPSILON = 1e-12;

  function pointOnSegment(longitude, latitude, start, end) {
    const cross = (longitude - start[0]) * (end[1] - start[1])
      - (latitude - start[1]) * (end[0] - start[0]);
    if (Math.abs(cross) > BOUNDARY_EPSILON) return false;

    return longitude >= Math.min(start[0], end[0]) - BOUNDARY_EPSILON
      && longitude <= Math.max(start[0], end[0]) + BOUNDARY_EPSILON
      && latitude >= Math.min(start[1], end[1]) - BOUNDARY_EPSILON
      && latitude <= Math.max(start[1], end[1]) + BOUNDARY_EPSILON;
  }

  function pointInRing(longitude, latitude, ring) {
    if (!Array.isArray(ring) || ring.length < 3) return false;

    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const start = ring[j];
      const end = ring[i];
      if (pointOnSegment(longitude, latitude, start, end)) return true;

      const crossesLatitude = (end[1] > latitude) !== (start[1] > latitude);
      if (crossesLatitude) {
        const crossingLongitude = ((start[0] - end[0]) * (latitude - end[1]))
          / (start[1] - end[1]) + end[0];
        if (longitude < crossingLongitude) inside = !inside;
      }
    }
    return inside;
  }

  function pointInPolygon(longitude, latitude, rings) {
    if (!Array.isArray(rings) || rings.length === 0
      || !pointInRing(longitude, latitude, rings[0])) return false;

    for (let i = 1; i < rings.length; i += 1) {
      if (pointInRing(longitude, latitude, rings[i])) return false;
    }
    return true;
  }

  function geometryContainsPoint(geometry, longitude, latitude) {
    if (!geometry || !Array.isArray(geometry.coordinates)) return false;
    if (geometry.type === "Polygon") {
      return pointInPolygon(longitude, latitude, geometry.coordinates);
    }
    if (geometry.type === "MultiPolygon") {
      return geometry.coordinates.some((polygon) => pointInPolygon(longitude, latitude, polygon));
    }
    return false;
  }

  function featureId(feature, index) {
    const id = feature && feature.properties && feature.properties.Hotspot_ID;
    if (id !== null && id !== undefined && id !== "") return String(id);
    return feature && feature.id !== undefined ? String(feature.id) : String(index);
  }

  function distanceToSegmentMeters(longitude, latitude, start, end) {
    const earthRadius = 6371008.8;
    const latitudeRadians = latitude * Math.PI / 180;
    const xScale = earthRadius * Math.PI / 180 * Math.cos(latitudeRadians);
    const yScale = earthRadius * Math.PI / 180;
    const startX = (start[0] - longitude) * xScale;
    const startY = (start[1] - latitude) * yScale;
    const endX = (end[0] - longitude) * xScale;
    const endY = (end[1] - latitude) * yScale;
    const segmentX = endX - startX;
    const segmentY = endY - startY;
    const lengthSquared = segmentX * segmentX + segmentY * segmentY;
    const fraction = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1,
      -(startX * segmentX + startY * segmentY) / lengthSquared));
    return Math.hypot(startX + fraction * segmentX, startY + fraction * segmentY);
  }

  function geometryDistanceMeters(geometry, longitude, latitude) {
    if (!geometry || !Array.isArray(geometry.coordinates)) return Infinity;
    if (geometry.type === "Point") {
      return distanceToSegmentMeters(longitude, latitude, geometry.coordinates, geometry.coordinates);
    }
    if (geometryContainsPoint(geometry, longitude, latitude)) return 0;

    const polygons = geometry.type === "Polygon" ? [geometry.coordinates]
      : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
    let minimumDistance = Infinity;
    polygons.forEach((polygon) => polygon.forEach((ring) => {
      for (let i = 0; i < ring.length; i += 1) {
        minimumDistance = Math.min(minimumDistance, distanceToSegmentMeters(
          longitude, latitude, ring[i], ring[(i + 1) % ring.length],
        ));
      }
    }));
    return minimumDistance;
  }

  function hotspotTransitions(features, previousIds, longitude, latitude, warningBufferMeters = {}) {
    const activeIds = new Set();
    const entered = [];

    features.forEach((feature, index) => {
      const properties = feature.properties || {};
      const severityClass = String(properties.Sev_Class || properties.Severity_Class || "")
        .trim().toUpperCase();
      const warningBuffer = Number(warningBufferMeters[severityClass]) || 0;
      if (geometryDistanceMeters(feature.geometry, longitude, latitude) > warningBuffer) return;
      const id = featureId(feature, index);
      activeIds.add(id);
      if (!previousIds.has(id)) entered.push(feature);
    });

    return { activeIds, entered };
  }

  const RISK_RANK = Object.freeze({ GREEN: 1, ORANGE: 2, RED: 3 });

  // Risk class of a hotspot point or a DBSCAN polygon. Polygons carry the class
  // only inside Hotspot_Status, for example "Hotspot HS01 (RED)".
  function featureRisk(feature) {
    const properties = (feature && feature.properties) || {};
    const direct = String(properties.Sev_Class || properties.Severity_Class || properties.Risk_Class || "")
      .trim().toUpperCase();
    if (RISK_RANK[direct]) return direct;
    const status = String(properties.Hotspot_Status || "").toUpperCase().match(/\b(GREEN|ORANGE|RED)\b/);
    if (status) return status[1];
    const level = String(properties.Risk_Level || "").trim().toUpperCase();
    return ({ HIGH: "RED", MEDIUM: "ORANGE", LOW: "GREEN" })[level] || "GREEN";
  }

  // Stable hotspot key (HS01…) for points and polygons alike.
  function hotspotKey(feature, index) {
    const properties = (feature && feature.properties) || {};
    if (properties.Hotspot_ID) return String(properties.Hotspot_ID);
    const status = String(properties.Hotspot_Status || "").match(/HS\d+/i);
    if (status) return status[0].toUpperCase();
    if (properties.Cluster_ID !== undefined && properties.Cluster_ID !== null) return `cluster-${properties.Cluster_ID}`;
    return feature && feature.id !== undefined ? String(feature.id) : `feature-${index}`;
  }

  /*
   * Two-stage hotspot alert tracker shared by GPS tracking and the demo vehicle.
   * Per hotspot polygon:
   *   outside  --(boundary within approachMeters)-->  approaching   emits "approach" (once)
   *   any      --(position inside polygon)-------->  inside        emits "enter"    (once)
   *   inside   --(more than hysteresisMeters out)->  leaving       emits "exit"
   *   approaching/leaving --(beyond approachMeters + hysteresisMeters)--> outside (re-armed)
   * If a position update jumps straight from outside to inside, only "enter" is emitted.
   */
  function createAlertTracker({ approachMeters = 10, hysteresisMeters = 5 } = {}) {
    let states = new Map();

    function update(features, longitude, latitude, { preview = false } = {}) {
      const events = [];
      const measured = [];
      (features || []).forEach((feature, index) => {
        const id = hotspotKey(feature, index);
        const inside = geometryContainsPoint(feature.geometry, longitude, latitude);
        const distance = inside ? 0 : geometryDistanceMeters(feature.geometry, longitude, latitude);
        const risk = featureRisk(feature);
        const previous = preview ? "outside" : states.get(id) || "outside";
        let state = previous;
        let event = null;
        if (inside) {
          if (previous !== "inside") { state = "inside"; event = "enter"; }
        } else if (previous === "inside") {
          if (distance > hysteresisMeters) { state = "leaving"; event = "exit"; }
        } else if (previous === "outside") {
          if (distance <= approachMeters) { state = "approaching"; event = "approach"; }
        } else if (distance > approachMeters + hysteresisMeters) {
          state = "outside";
        }
        if (!preview) {
          states.set(id, state);
          if (event) events.push({ type: event, id, feature, risk, distance });
        }
        measured.push({ id, feature, risk, distance, state });
      });

      const order = { exit: 0, approach: 1, enter: 2 };
      events.sort((a, b) => order[a.type] - order[b.type]
        || RISK_RANK[b.risk] - RISK_RANK[a.risk] || a.distance - b.distance);
      const inside = measured.filter((item) => item.state === "inside")
        .sort((a, b) => RISK_RANK[b.risk] - RISK_RANK[a.risk]);
      const approaching = measured.filter((item) => item.state === "approaching")
        .sort((a, b) => a.distance - b.distance);
      const primary = inside[0] || approaching[0];
      return {
        events,
        primary: primary ? { ...primary } : { state: "outside", id: null, feature: null, risk: null, distance: Infinity },
      };
    }

    return Object.freeze({ update, reset() { states = new Map(); } });
  }

  return Object.freeze({
    geometryContainsPoint, geometryDistanceMeters, featureId, hotspotTransitions,
    featureRisk, hotspotKey, createAlertTracker, RISK_RANK,
  });
}));
