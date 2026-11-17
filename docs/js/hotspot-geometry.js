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

  return Object.freeze({ geometryContainsPoint, geometryDistanceMeters, featureId, hotspotTransitions });
}));
