"use strict";

(function exposeVehicleSimulation(root) {
  // Same two-stage rule as live GPS tracking (see hotspot-geometry.js createAlertTracker):
  // one approach alert within 10 m of a hotspot boundary, one risk alert on entry.
  const APPROACH_DISTANCE_METERS = 10;
  const EXIT_HYSTERESIS_METERS = 5;
  const RISK_LABEL = Object.freeze({ GREEN: "Low", ORANGE: "Medium", RED: "High" });
  const EARTH_RADIUS_METERS = 6371008.8;
  const speedAlerts = root.TrafficSpeedAlerts;

  function initialize({ map, wfsUrl, geometry }) {
    const elements = {
      toggle: document.getElementById("simulation-toggle"),
      start: document.getElementById("simulation-start"),
      pause: document.getElementById("simulation-pause"),
      reset: document.getElementById("simulation-reset"),
      state: document.getElementById("simulation-state"),
      position: document.getElementById("vehicle-position"),
      hotspot: document.getElementById("vehicle-hotspot-status"),
      alert: document.getElementById("vehicle-alert"),
      speed: document.getElementById("vehicle-speed"),
      hotspotDetail: document.getElementById("vehicle-hotspot-detail"),
      riskDetail: document.getElementById("vehicle-risk-detail"),
      recommendedSpeed: document.getElementById("vehicle-recommended-speed"),
      speedStatus: document.getElementById("vehicle-speed-status"),
    };
    if (!map || !geometry || Object.values(elements).some((element) => !element)) return;

    let enabled = false;
    let running = false;
    let autoDrive = false;
    let frameId = null;
    let lastFrameTime = 0;
    let routeCoordinates = null;
    let routeSegments = [];
    let routeLength = 0;
    let routePromise = null;
    let clusterFeatures = null;
    let clusterPromise = null;
    let vehicleMarker = null;
    let routeProgress = 0;
    let lateralOffset = 0;
    let currentSpeedKmh = 0;
    const tracker = geometry.createAlertTracker({
      approachMeters: APPROACH_DISTANCE_METERS, hysteresisMeters: EXIT_HYSTERESIS_METERS,
    });
    let currentZone = { state: "outside", feature: null };
    let suppressAlertsOnce = false;
    let startupAlertPending = false;
    const pressedKeys = new Set();

    function setState(message) {
      elements.state.textContent = message;
    }

    function renderSpeed(feature, statusOverride = null) {
      const result = feature && speedAlerts ? speedAlerts.evaluate(feature, currentSpeedKmh) : null;
      elements.speed.textContent = `Current speed: ${Math.round(currentSpeedKmh)} km/h`;
      elements.hotspotDetail.textContent = `Approaching hotspot: ${result ? result.id : "—"}`;
      elements.riskDetail.textContent = `Risk level: ${result ? result.risk : "—"}`;
      elements.recommendedSpeed.textContent = `Recommended speed: ${result && result.recommendation ? `${result.recommendation} km/h` : "—"}`;
      elements.speedStatus.textContent = `Speed status: ${result && result.recommendation ? result.status : "—"}`;
      elements.speedStatus.classList.toggle("is-over", Boolean(result && result.above));
      if (root.TrafficSpeedometer) {
        const state = statusOverride || (currentSpeedKmh > 0 ? "Moving" : "Stopped");
        root.TrafficSpeedometer.update("Demo", currentSpeedKmh, state);
      }
      return result;
    }

    function haversineMeters(first, second) {
      const latitude1 = first[1] * Math.PI / 180;
      const latitude2 = second[1] * Math.PI / 180;
      const deltaLatitude = latitude2 - latitude1;
      const deltaLongitude = (second[0] - first[0]) * Math.PI / 180;
      const a = Math.sin(deltaLatitude / 2) ** 2
        + Math.cos(latitude1) * Math.cos(latitude2) * Math.sin(deltaLongitude / 2) ** 2;
      return 2 * EARTH_RADIUS_METERS * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function loadLayer(typeName) {
      const params = new URLSearchParams({
        SERVICE: "WFS",
        VERSION: "2.0.0",
        REQUEST: "GetFeature",
        TYPENAMES: typeName,
        OUTPUTFORMAT: "application/json",
        SRSNAME: "EPSG:4326",
        COUNT: "10000",
      });
      return fetch(`${wfsUrl}?${params.toString()}`).then((response) => {
        if (!response.ok) throw new Error(`${typeName} returned ${response.status}`);
        return response.json();
      }).then((data) => Array.isArray(data.features) ? data.features : []);
    }

    // Hotspot alert zones are the DBSCAN hotspot polygons (bundled locally, WFS as fallback).
    function loadClusters() {
      if (clusterFeatures) return Promise.resolve(clusterFeatures);
      const localClusters = root.TrafficDBSCANClusters;
      const polygonsOf = (features) => features.filter((feature) => (
        feature.geometry && ["Polygon", "MultiPolygon"].includes(feature.geometry.type)
      ));
      if (localClusters && Array.isArray(localClusters.features) && polygonsOf(localClusters.features).length) {
        clusterFeatures = polygonsOf(localClusters.features);
        return Promise.resolve(clusterFeatures);
      }
      if (!clusterPromise) {
        clusterPromise = loadLayer("traffic:hotspot_polygons")
          .catch(() => loadLayer("traffic:dbscan_clusters"))
          .then((features) => {
            const polygons = polygonsOf(features);
            if (!polygons.length) throw new Error("No hotspot polygons were returned.");
            clusterFeatures = polygons;
            return clusterFeatures;
          }).catch((error) => {
            clusterPromise = null;
            throw error;
          });
      }
      return clusterPromise;
    }

    function loadRoute() {
      if (routeCoordinates) return Promise.resolve(routeCoordinates);
      if (!routePromise) {
        const localRoads = root.TrafficRoadCorridor;
        const roadFeatures = localRoads && Array.isArray(localRoads.features)
          ? localRoads.features : null;
        const source = roadFeatures && roadFeatures.length
          ? Promise.resolve(roadFeatures) : loadLayer("traffic:road_corridor");
        routePromise = source.then((features) => {
          const roadKey = (feature) => {
            const properties = feature.properties || {};
            return `${properties.road_class || ""} ${properties.ref || ""} ${properties.name || ""}`
              .toLowerCase().replace(/[^a-z0-9]+/g, "");
          };
          const nh66 = features.find((feature) => (
            roadKey(feature).includes("nh66") || roadKey(feature).includes("nationalhighway66")
          ));
          const duk = features.find((feature) => (
            roadKey(feature).includes("dukaccessroad")
            || (roadKey(feature).includes("digitaluniversitykerala") && roadKey(feature).includes("access"))
          ));
          if (!nh66 || !duk) {
            const available = features.map((feature) => (feature.properties || {}).road_class || "unnamed road").join(", ");
            throw new Error(`Could not identify both mapped roads. GeoServer returned: ${available || "no road features"}.`);
          }

          function lineCoordinates(feature, roadName) {
            const geometryType = feature.geometry && feature.geometry.type;
            let parts = geometryType === "LineString" ? [feature.geometry.coordinates]
              : geometryType === "MultiLineString" ? feature.geometry.coordinates : [];
            parts = parts.filter((line) => Array.isArray(line) && line.length >= 2)
              .map((line) => line.map((point) => [...point]));
            if (!parts.length) throw new Error(`${roadName} has no line geometry in GeoServer.`);

            let merged = parts.shift();
            while (parts.length) {
              let best = null;
              parts.forEach((part, index) => {
                const reversed = [...part].reverse();
                const options = [
                  { gap: haversineMeters(merged[merged.length - 1], part[0]), index, points: part.slice(1), prepend: false },
                  { gap: haversineMeters(merged[merged.length - 1], part[part.length - 1]), index, points: reversed.slice(1), prepend: false },
                  { gap: haversineMeters(merged[0], part[part.length - 1]), index, points: part.slice(0, -1), prepend: true },
                  { gap: haversineMeters(merged[0], part[0]), index, points: reversed.slice(0, -1), prepend: true },
                ];
                options.forEach((option) => { if (!best || option.gap < best.gap) best = option; });
              });
              if (!best || best.gap > 25) {
                throw new Error(`${roadName} has disconnected MultiLineString sections (${Math.round(best ? best.gap : Infinity)} m gap).`);
              }
              const [part] = parts.splice(best.index, 1);
              if (best.prepend) merged = [...best.points, ...merged];
              else merged = [...merged, ...best.points];
            }
            return merged;
          }

          const first = lineCoordinates(nh66, "NH 66");
          const second = lineCoordinates(duk, "DUK access road");
          const options = [
            [first, second], [first, [...second].reverse()],
            [[...first].reverse(), second], [[...first].reverse(), [...second].reverse()],
          ];
          options.sort((a, b) => (
            haversineMeters(a[0][a[0].length - 1], a[1][0])
            - haversineMeters(b[0][b[0].length - 1], b[1][0])
          ));
          const [mainRoad, accessRoad] = options[0];
          if (haversineMeters(mainRoad[mainRoad.length - 1], accessRoad[0]) > 25) {
            throw new Error("The mapped study roads do not connect to form a simulation route.");
          }
          routeCoordinates = [...mainRoad, ...accessRoad.slice(1)];
          let cumulative = 0;
          routeSegments = [];
          for (let index = 0; index < routeCoordinates.length - 1; index += 1) {
            const start = routeCoordinates[index];
            const end = routeCoordinates[index + 1];
            const length = haversineMeters(start, end);
            routeSegments.push({ start, end, length, cumulative });
            cumulative += length;
          }
          routeLength = cumulative;
          if (routeLength <= 0) throw new Error("The mapped route has no usable length.");
          return routeCoordinates;
        }).catch((error) => {
          routePromise = null;
          throw error;
        });
      }
      return routePromise;
    }

    function pointAtProgress(distance, offset = 0) {
      const progress = Math.max(0, Math.min(routeLength, distance));
      const segment = routeSegments.find((item) => progress <= item.cumulative + item.length)
        || routeSegments[routeSegments.length - 1];
      const fraction = segment.length === 0 ? 0
        : Math.max(0, Math.min(1, (progress - segment.cumulative) / segment.length));
      const longitude = segment.start[0] + (segment.end[0] - segment.start[0]) * fraction;
      const latitude = segment.start[1] + (segment.end[1] - segment.start[1]) * fraction;
      const latitudeScale = EARTH_RADIUS_METERS * Math.PI / 180;
      const longitudeScale = latitudeScale * Math.cos(latitude * Math.PI / 180);
      const east = (segment.end[0] - segment.start[0]) * longitudeScale;
      const north = (segment.end[1] - segment.start[1]) * latitudeScale;
      const segmentLength = Math.hypot(east, north) || 1;
      const shiftedLongitude = longitude + (-north / segmentLength * offset) / longitudeScale;
      const shiftedLatitude = latitude + (east / segmentLength * offset) / latitudeScale;
      return {
        latlng: L.latLng(shiftedLatitude, shiftedLongitude),
        bearing: Math.atan2(east, north) * 180 / Math.PI,
      };
    }

    function closestRoutePosition(latlng) {
      const target = map.latLngToLayerPoint(latlng);
      let closest = null;
      routeSegments.forEach((segment) => {
        const start = map.latLngToLayerPoint([segment.start[1], segment.start[0]]);
        const end = map.latLngToLayerPoint([segment.end[1], segment.end[0]]);
        const dx = end.x - start.x;
        const dy = end.y - start.y;
        const lengthSquared = dx * dx + dy * dy;
        const fraction = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1,
          ((target.x - start.x) * dx + (target.y - start.y) * dy) / lengthSquared));
        const x = start.x + fraction * dx;
        const y = start.y + fraction * dy;
        const distanceSquared = (target.x - x) ** 2 + (target.y - y) ** 2;
        if (!closest || distanceSquared < closest.distanceSquared) {
          closest = {
            distanceSquared,
            progress: segment.cumulative + segment.length * fraction,
          };
        }
      });
      return closest ? closest.progress : 0;
    }

    function announce(message) {
      if (!message) return;
      elements.alert.textContent = message;
      if (!root.speechSynthesis || !root.SpeechSynthesisUtterance) {
        setState("Simulation active. Voice playback is not supported in this browser.");
        return;
      }
      const utterance = new root.SpeechSynthesisUtterance(message);
      utterance.lang = "en-IN";
      utterance.onstart = () => {
        if (enabled) setState("Simulation active. Voice alert is playing.");
      };
      utterance.onerror = (event) => {
        if (!enabled) return;
        const detail = event.error === "canceled" || event.error === "interrupted"
          ? "Voice alert was cancelled." : "The browser could not play the voice alert.";
        setState(`Simulation active. ${detail}`);
      };
      try {
        root.speechSynthesis.speak(utterance);
      } catch (error) {
        setState("Simulation active. The browser could not start the voice alert.");
        console.error("Demo vehicle speech synthesis failed:", error);
      }
    }

    // Stage 1: within 10 m of a hotspot boundary -> one approach alert.
    // Stage 2: inside the hotspot polygon -> one risk / speed alert.
    // The initial, stationary placement is previewed silently and does not
    // seed alert history, so the first movement after Start can still alert.
    function updateHotspots(latlng) {
      const silent = suppressAlertsOnce;
      suppressAlertsOnce = false;
      const { events, primary } = tracker.update(clusterFeatures, latlng.lng, latlng.lat, { preview: silent });
      currentZone = primary;
      elements.hotspot.textContent = primary.state === "inside"
        ? `Inside ${RISK_LABEL[primary.risk]} risk hotspot ${primary.id}`
        : primary.state === "approaching"
          ? `Approaching ${RISK_LABEL[primary.risk]} risk hotspot ${primary.id} (${Math.round(primary.distance)} m from boundary)`
          : "Outside hotspot";
      renderSpeed(primary.feature);
      events.forEach((event) => {
        if (event.type === "approach") {
          announce(speedAlerts ? speedAlerts.approachMessage : "Careful! Accident hotspot ahead. Please slow down.");
        } else if (event.type === "enter") {
          announce(speedAlerts ? speedAlerts.evaluate(event.feature, currentSpeedKmh).message
            : `You are entering a ${RISK_LABEL[event.risk].toLowerCase()}-risk accident hotspot.`);
        }
      });
    }

    function updateVehicle(distance = routeProgress, offset = lateralOffset) {
      routeProgress = Math.max(0, Math.min(routeLength, distance));
      lateralOffset = Math.max(-6, Math.min(6, offset));
      const position = pointAtProgress(routeProgress, lateralOffset);
      if (vehicleMarker) {
        vehicleMarker.setLatLng(position.latlng);
        const icon = vehicleMarker.getElement()?.querySelector(".demo-vehicle-icon");
        if (icon) icon.style.transform = `rotate(${position.bearing + 180}deg)`;
      }
      elements.position.textContent = `Vehicle position: ${position.latlng.lat.toFixed(6)}, ${position.latlng.lng.toFixed(6)}`;
      updateHotspots(position.latlng);
    }

    function createVehicle() {
      if (vehicleMarker) return;
      const firstPosition = pointAtProgress(0);
      map.panTo(firstPosition.latlng);
      vehicleMarker = L.marker(firstPosition.latlng, {
        pane: "gpsPane",
        draggable: true,
        autoPan: true,
        icon: L.divIcon({
          className: "demo-vehicle-marker",
          html: '<span class="demo-vehicle-icon" aria-hidden="true">🚗</span>',
          iconSize: [30, 30],
          iconAnchor: [15, 15],
        }),
        title: "Demo vehicle. Drag to reposition on the road.",
      }).addTo(map);
      vehicleMarker.bindTooltip("Demo vehicle");
      vehicleMarker.on("dragstart", () => pause());
      vehicleMarker.on("drag", (event) => {
        routeProgress = closestRoutePosition(event.target.getLatLng());
        lateralOffset = 0;
        updateVehicle(routeProgress, lateralOffset);
      });
      vehicleMarker.on("dragend", () => updateVehicle());
      suppressAlertsOnce = true;
      updateVehicle(0, 0);
    }

    function setButtonsReady(ready) {
      elements.start.disabled = !ready;
      elements.pause.disabled = !ready;
      elements.reset.disabled = !ready;
    }

    function enable() {
      if (enabled) return;
      if (root.TrafficGpsTracking && root.TrafficGpsTracking.isRunning()) {
        setState("Stop live GPS tracking before starting the demo simulation.");
        return;
      }
      enabled = true;
      if (root.TrafficSpeedometer) root.TrafficSpeedometer.activate("Demo", "Unavailable");
      elements.toggle.setAttribute("aria-pressed", "true");
      elements.toggle.textContent = "Demo Simulation: On";
      setButtonsReady(false);
      setState("Loading the mapped road route and hotspots…");
      Promise.all([loadRoute(), loadClusters()]).then(() => {
        if (!enabled) return;
        routeProgress = 0;
        lateralOffset = 0;
        tracker.reset();
        // The initial vehicle position is rendered before the user starts the
        // demo. Do not consume hotspot entry alerts until the first movement,
        // when speech playback is still directly connected to the Start action.
        suppressAlertsOnce = true;
        createVehicle();
        // createVehicle() consumes its one-time suppression while drawing the
        // stationary marker. Keep a separate pending flag for the Start click.
        startupAlertPending = true;
        setButtonsReady(true);
        setState("Simulation ready. Press Start Simulation or use the keyboard.");
      }).catch((error) => {
        if (!enabled) return;
        disable();
        setState(`Simulation could not load its route or hotspots. ${error.message}`);
        console.error("Demo vehicle data could not be loaded:", error);
      });
    }

    function disable() {
      pause();
      enabled = false;
      elements.toggle.setAttribute("aria-pressed", "false");
      elements.toggle.textContent = "Demo Simulation: Off";
      setButtonsReady(false);
      pressedKeys.clear();
      tracker.reset();
      currentZone = { state: "outside", feature: null };
      startupAlertPending = false;
      if (vehicleMarker) map.removeLayer(vehicleMarker);
      vehicleMarker = null;
      elements.position.textContent = "Vehicle position: not available";
      elements.hotspot.textContent = "Outside hotspot";
      currentSpeedKmh = 0;
      renderSpeed(null);
      if (root.TrafficSpeedometer) root.TrafficSpeedometer.stop("Demo", "Stopped");
      elements.alert.textContent = "No demo alert yet.";
      setState("Simulation is off.");
    }

    function pause() {
      running = false;
      autoDrive = false;
      currentSpeedKmh = 0;
      renderSpeed(currentZone.feature || null, "Paused");
      pressedKeys.clear();
      if (frameId !== null) cancelAnimationFrame(frameId);
      frameId = null;
      if (enabled && routeCoordinates) setState("Simulation paused.");
    }

    function frame(timestamp) {
      if (!running) return;
      const deltaSeconds = Math.min((timestamp - lastFrameTime) / 1000, 0.1);
      lastFrameTime = timestamp;
      let forward = autoDrive ? 1 : 0;
      let sideways = 0;
      if (!autoDrive) {
        if (pressedKeys.has("forward")) forward += 1;
        if (pressedKeys.has("backward")) forward -= 1;
        if (pressedKeys.has("left")) sideways += 1;
        if (pressedKeys.has("right")) sideways -= 1;
      }
      const nextProgress = Math.max(0, Math.min(routeLength, routeProgress + forward * 14 * deltaSeconds));
      const nextOffset = Math.max(-6, Math.min(6, lateralOffset + sideways * 3 * deltaSeconds));
      const traveledMeters = Math.hypot(nextProgress - routeProgress, nextOffset - lateralOffset);
      currentSpeedKmh = deltaSeconds > 0 ? (traveledMeters / deltaSeconds) * 3.6 : 0;
      updateVehicle(nextProgress, nextOffset);

      if (autoDrive && routeProgress >= routeLength) {
        pause();
        setState("Route end reached. Reset the vehicle to run the demo again.");
        return;
      }
      frameId = requestAnimationFrame(frame);
    }

    function run(auto = false) {
      if (!enabled || !routeCoordinates || running) return;
      if (auto && routeProgress >= routeLength) {
        setState("Route end reached. Reset the vehicle to run the demo again.");
        return;
      }
      running = true;
      autoDrive = auto;
      if (auto) pressedKeys.clear();
      setState(auto ? "Simulation running. Use the keyboard to steer." : "Vehicle moving by keyboard.");
      // Speak the first applicable warning from this click/key gesture. The
      // initial map render is intentionally silent, and a delayed animation
      // frame may no longer have browser permission to start speech.
      if (startupAlertPending) {
        startupAlertPending = false;
        updateVehicle(routeProgress, lateralOffset);
      }
      renderSpeed(currentZone.feature || null, "Stopped");
      lastFrameTime = performance.now();
      frameId = requestAnimationFrame(frame);
    }

    function reset() {
      if (!enabled || !routeCoordinates) return;
      pause();
      tracker.reset();
      elements.alert.textContent = "No demo alert yet.";
      routeProgress = 0;
      lateralOffset = 0;
      suppressAlertsOnce = true;
      updateVehicle(0, 0);
      startupAlertPending = true;
      setState("Vehicle reset to the start of the mapped route.");
    }

    function keyAction(event) {
      const target = event.target;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(target.tagName))) return null;
      const key = ({
        ArrowUp: "forward", KeyW: "forward",
        ArrowDown: "backward", KeyS: "backward",
        ArrowLeft: "left", KeyA: "left",
        ArrowRight: "right", KeyD: "right",
      })[event.code];
      return key || null;
    }

    document.addEventListener("keydown", (event) => {
      if (!enabled || !routeCoordinates) return;
      const action = keyAction(event);
      if (!action) return;
      event.preventDefault();
      pressedKeys.add(action);
      if (!running) run(false);
      else if (autoDrive) {
        autoDrive = false;
        setState("Simulation running by keyboard.");
      }
    });

    document.addEventListener("keyup", (event) => {
      const action = keyAction(event);
      if (!action || !pressedKeys.has(action)) return;
      pressedKeys.delete(action);
      if (pressedKeys.size === 0 && running && !autoDrive) pause();
    });

    window.addEventListener("blur", () => {
      if (running && !autoDrive) pause();
    });

    elements.toggle.addEventListener("click", () => {
      if (enabled) disable();
      else enable();
    });
    elements.start.addEventListener("click", () => run(true));
    elements.pause.addEventListener("click", pause);
    elements.reset.addEventListener("click", reset);
    return Object.freeze({ disable, isEnabled: () => enabled });
  }

  root.TrafficVehicleSimulation = Object.freeze({ initialize });
}(window));
