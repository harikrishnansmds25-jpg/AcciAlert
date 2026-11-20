"use strict";

(function exposeGpsTracking(root) {
  let isTracking = false;
  const APPROACH_METERS = 10;
  const EXIT_HYSTERESIS_METERS = 5;
  const MAX_ALERT_ACCURACY_METERS = 30;
  const RISK_LABEL = Object.freeze({ GREEN: "Low", ORANGE: "Medium", RED: "High" });
  const speedAlerts = root.TrafficSpeedAlerts;

  // Stateless snapshot (kept for external callers); live alerts use geometry.createAlertTracker.
  function classifyPosition(features, geometry, longitude, latitude) {
    const { primary } = geometry.createAlertTracker({ approachMeters: APPROACH_METERS })
      .update(features, longitude, latitude, { preview: true });
    if (primary.state !== "outside") return primary;
    const nearest = (features || []).map((feature, index) => ({
      feature, id: geometry.hotspotKey(feature, index), risk: geometry.featureRisk(feature),
      distance: geometry.geometryDistanceMeters(feature.geometry, longitude, latitude),
    })).filter((item) => item.distance <= APPROACH_METERS).sort((a, b) => a.distance - b.distance)[0];
    return nearest ? { state: "approaching", ...nearest } : { state: "outside" };
  }

  function initialize({ map, wfsUrl, geometry, vehicleSimulation }) {
    const el = (id) => document.getElementById(id);
    const startButton = el("gps-start");
    const stopButton = el("gps-stop");
    const recenterButton = el("gps-recenter");
    const status = el("gps-status");
    const alertText = el("gps-alert");
    const coordinates = el("gps-coordinates");
    const hotspotState = el("gps-hotspot-state");
    const fixTime = el("gps-fix-time");
    const speedElements = {
      speed: el("gps-speed"), id: el("gps-hotspot-detail"), risk: el("gps-risk-detail"),
      recommendation: el("gps-recommended-speed"), status: el("gps-speed-status"),
    };
    if (!map || !geometry || !startButton || !stopButton) return;
    const tracker = geometry.createAlertTracker({ approachMeters: APPROACH_METERS, hysteresisMeters: EXIT_HYSTERESIS_METERS });

    let watchId = null;
    let running = false;
    let features = null;
    let featurePromise = null;
    let usingBundledClusters = false;
    let marker = null;
    let accuracyCircle = null;
    let lastPosition = null;
    let currentSpeedKmh = null;
    let current = { state: "outside", id: null };
    let wakeLock = null;
    let follow = true;
    let hasCentered = false;
    let mapMoveIsProgrammatic = false;

    const setStatus = (message) => { if (status) status.textContent = message; };

    function renderSpeed(feature) {
      const evaluation = feature && speedAlerts ? speedAlerts.evaluate(feature, currentSpeedKmh) : null;
      if (speedElements.speed) speedElements.speed.textContent = `Current speed: ${currentSpeedKmh === null ? "unavailable" : `${Math.round(currentSpeedKmh)} km/h`}`;
      if (speedElements.id) speedElements.id.textContent = `Approaching hotspot: ${evaluation ? evaluation.id : "—"}`;
      if (speedElements.risk) speedElements.risk.textContent = `Risk level: ${evaluation ? evaluation.risk : "—"}`;
      if (speedElements.recommendation) speedElements.recommendation.textContent = `Recommended speed: ${evaluation && evaluation.recommendation ? `${evaluation.recommendation} km/h` : "—"}`;
      if (speedElements.status) {
        speedElements.status.textContent = `Speed status: ${evaluation && evaluation.recommendation ? evaluation.status : "—"}`;
        speedElements.status.classList.toggle("is-over", Boolean(evaluation && evaluation.above));
      }
      if (root.TrafficSpeedometer && running) {
        const state = currentSpeedKmh === null ? "Unavailable" : currentSpeedKmh > 0 ? "Moving" : "Stopped";
        root.TrafficSpeedometer.update("GPS", currentSpeedKmh, state);
      }
      return evaluation;
    }

    function loadClusters() {
      if (features) return Promise.resolve(features);
      if (featurePromise) return featurePromise;
      const bundled = root.TrafficDBSCANClusters;
      const localPolygons = bundled && Array.isArray(bundled.features)
        ? bundled.features.filter((feature) => (
          feature.geometry && ["Polygon", "MultiPolygon"].includes(feature.geometry.type)
        )) : [];
      if (localPolygons.length) {
        features = localPolygons;
        usingBundledClusters = true;
        return Promise.resolve(features);
      }
      const fetchLayer = (typeName) => {
        const params = new URLSearchParams({
          SERVICE: "WFS", VERSION: "2.0.0", REQUEST: "GetFeature",
          TYPENAMES: typeName, OUTPUTFORMAT: "application/json",
          SRSNAME: "EPSG:4326", COUNT: "10000",
        });
        return fetch(`${wfsUrl}?${params}`)
          .then((response) => {
            if (!response.ok) throw new Error(`${typeName} WFS returned ${response.status}`);
            return response.json();
          })
          .then((data) => {
            const polygons = Array.isArray(data.features) ? data.features.filter((feature) => (
              feature.geometry && ["Polygon", "MultiPolygon"].includes(feature.geometry.type)
            )) : [];
            if (!polygons.length) throw new Error(`GeoServer returned no polygon geometries for ${typeName}.`);
            return polygons;
          });
      };

      featurePromise = fetchLayer("traffic:hotspot_polygons")
        .catch(() => fetchLayer("traffic:dbscan_clusters"))
        .then((polygons) => {
          features = polygons;
          return features;
        })
        .catch((error) => {
          featurePromise = null;
          throw error;
        });
      return featurePromise;
    }

    // Alerts are shown as text and spoken through the shared voice queue (voice-alerts.js),
    // which honours the Voice alerts On/Off buttons and recovers from stuck browser speech.
    function announce(text) {
      if (alertText) alertText.textContent = `Last alert: ${text}`;
      if (root.TrafficVoice) root.TrafficVoice.say(text);
    }

    function unlockSpeechFromGesture() {
      if (root.TrafficVoice) root.TrafficVoice.unlock();
    }

    async function acquireWakeLock() {
      if (!running || document.visibilityState !== "visible" || !navigator.wakeLock || wakeLock) return;
      try {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      } catch (error) {
        console.info("Screen wake lock unavailable; keep the app visible during tracking.", error);
      }
    }

    async function releaseWakeLock() {
      const lock = wakeLock;
      wakeLock = null;
      if (lock) { try { await lock.release(); } catch (error) { /* already released */ } }
    }

    function setMapFollow(latlng, firstFix) {
      if (!follow) return;
      mapMoveIsProgrammatic = true;
      if (firstFix) map.setView(latlng, Math.max(map.getZoom(), 16), { animate: false });
      else map.panTo(latlng, { animate: false });
      window.setTimeout(() => { mapMoveIsProgrammatic = false; }, 0);
    }

    const vehicleIconHtml = '<div class="gps-vehicle-marker"><div class="gps-vehicle-radar"></div><svg class="gps-vehicle-svg" viewBox="0 0 36 36" width="36" height="36" xmlns="http://www.w3.org/2000/svg"><defs><filter id="v-shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#000" flood-opacity="0.4"/></filter></defs><rect x="5" y="7" width="4" height="7" rx="1.5" fill="#1e293b"/><rect x="27" y="7" width="4" height="7" rx="1.5" fill="#1e293b"/><rect x="5" y="22" width="4" height="7" rx="1.5" fill="#1e293b"/><rect x="27" y="22" width="4" height="7" rx="1.5" fill="#1e293b"/><path d="M10 5 C10 3, 26 3, 26 5 L29 14 C30 18, 30 25, 28 30 C26 33, 10 33, 8 30 C6 25, 6 18, 7 14 Z" fill="#0284c7" stroke="#ffffff" stroke-width="2" filter="url(#v-shadow)"/><path d="M11 10 L25 10 L26 15 L10 15 Z" fill="#e0f2fe"/><rect x="11" y="16" width="14" height="7" rx="2" fill="#0369a1"/><circle cx="18" cy="19.5" r="1.8" fill="#38bdf8"/><path d="M12 24 L24 24 L25 27 L11 27 Z" fill="#bae6fd"/><ellipse cx="11" cy="5" rx="2" ry="1.2" fill="#fef08a"/><ellipse cx="25" cy="5" rx="2" ry="1.2" fill="#fef08a"/><rect x="9.5" y="31" width="3.5" height="1.5" rx="0.5" fill="#ef4444"/><rect x="23" y="31" width="3.5" height="1.5" rx="0.5" fill="#ef4444"/></svg></div>';

    function ensureGpsLayers(latlng, accuracy, heading) {
      if (!accuracyCircle) {
        accuracyCircle = L.circle(latlng, {
          pane: "gpsPane", radius: accuracy, color: "#1267d6", weight: 1,
          fillColor: "#1267d6", fillOpacity: 0.12, interactive: false,
        }).addTo(map);
      } else { accuracyCircle.setLatLng(latlng); accuracyCircle.setRadius(accuracy); }
      if (!marker) {
        marker = L.marker(latlng, {
          pane: "gpsPane", keyboard: false, interactive: false,
          icon: L.divIcon({ className: "gps-position-marker", html: vehicleIconHtml, iconSize: [36, 36], iconAnchor: [18, 18] }),
        }).addTo(map);
        marker.bindTooltip("Live vehicle tracking");
      } else {
        marker.setLatLng(latlng);
      }
      if (marker && heading !== null && heading !== undefined && !isNaN(heading)) {
        const el = marker.getElement();
        const vEl = el && el.querySelector(".gps-vehicle-marker");
        if (vEl) vEl.style.transform = `rotate(${heading}deg)`;
      }
    }

    // Stage 1: within APPROACH_METERS of a hotspot boundary -> one approach alert.
    // Stage 2: inside the hotspot polygon -> one risk / speed alert.
    function updateZone(longitude, latitude) {
      if (!features) return;
      const { events, primary } = tracker.update(features, longitude, latitude);
      current = primary;
      renderSpeed(primary.feature);
      events.forEach((event) => {
        if (event.type === "approach") {
          announce(speedAlerts ? speedAlerts.approachMessage : "Careful! Accident hotspot ahead. Please slow down.");
        } else if (event.type === "enter") {
          announce(speedAlerts ? speedAlerts.evaluate(event.feature, currentSpeedKmh).message
            : `You are entering a ${RISK_LABEL[event.risk].toLowerCase()}-risk accident hotspot.`);
        }
      });
      if (!hotspotState) return;
      if (primary.state === "inside") {
        hotspotState.textContent = `Hotspot state: Inside ${RISK_LABEL[primary.risk]} risk hotspot ${primary.id}`;
      } else if (primary.state === "approaching") {
        hotspotState.textContent = `Hotspot state: Approaching ${RISK_LABEL[primary.risk]} risk hotspot ${primary.id} (${Math.round(primary.distance)} m from boundary)`;
      } else {
        hotspotState.textContent = "Hotspot state: Outside hotspots";
      }
    }

    function onPosition(position) {
      lastPosition = position;
      const { latitude, longitude, accuracy } = position.coords;
      const rawSpeed = position.coords.speed;
      currentSpeedKmh = accuracy <= MAX_ALERT_ACCURACY_METERS
        && Number.isFinite(rawSpeed) && rawSpeed >= 0 && rawSpeed * 3.6 <= 250
        ? rawSpeed * 3.6 : null;
      const latlng = L.latLng(latitude, longitude);
      ensureGpsLayers(latlng, accuracy, position.coords.heading);
      setMapFollow(latlng, !hasCentered);
      hasCentered = true;
      if (coordinates) coordinates.textContent = `Position: ${latitude.toFixed(6)}, ${longitude.toFixed(6)} (accuracy ±${Math.round(accuracy)} m)`;
      if (fixTime) fixTime.textContent = `Last fix: ${new Date(position.timestamp || Date.now()).toLocaleTimeString()}`;
      renderSpeed(current.id ? current.feature : null);
      if (accuracy > MAX_ALERT_ACCURACY_METERS) {
        setStatus(`GPS active, but accuracy is weak (±${Math.round(accuracy)} m). Hotspot alerts paused until accuracy is ${MAX_ALERT_ACCURACY_METERS} m or better.`);
        if (hotspotState) hotspotState.textContent = "Hotspot state: paused because GPS accuracy is weak";
        return;
      }
      setStatus(features
        ? (usingBundledClusters ? "Live GPS tracking. Alerts use the bundled demo DBSCAN polygons." : "Live GPS tracking. Hotspot alerts are active.")
        : "Live GPS tracking. Loading DBSCAN hotspot polygons…");
      updateZone(longitude, latitude);
    }

    function stopWatch(speedStatus = "Unavailable") {
      if (watchId !== null && navigator.geolocation) navigator.geolocation.clearWatch(watchId);
      watchId = null;
      running = false;
      isTracking = false;
      if (root.TrafficVoice) root.TrafficVoice.stop();
      releaseWakeLock();
      startButton.disabled = false;
      stopButton.disabled = true;
      if (recenterButton) recenterButton.hidden = true;
      current = { state: "outside", id: null };
      tracker.reset();
      currentSpeedKmh = null;
      renderSpeed(null);
      if (root.TrafficSpeedometer) root.TrafficSpeedometer.stop("GPS", speedStatus);
    }

    function onGpsError(error) {
      if (error.code === 1) {
        stopWatch();
        setStatus("Location permission denied. Allow location access in browser settings, then start GPS again.");
      } else if (error.code === 2) {
        currentSpeedKmh = null;
        renderSpeed(current.id ? current.feature : null);
        setStatus("GPS location unavailable. Check device location services and move to an open area.");
      } else if (error.code === 3) {
        currentSpeedKmh = null;
        renderSpeed(current.id ? current.feature : null);
        setStatus("GPS request timed out. Waiting for another fix; check device location services.");
      } else {
        currentSpeedKmh = null;
        renderSpeed(current.id ? current.feature : null);
        setStatus("GPS could not determine your location. Check device location settings.");
      }
    }

    function stopGps() {
      stopWatch("Stopped");
      if (marker) map.removeLayer(marker);
      if (accuracyCircle) map.removeLayer(accuracyCircle);
      marker = null;
      accuracyCircle = null;
      lastPosition = null;
      currentSpeedKmh = null;
      hasCentered = false;
      if (coordinates) coordinates.textContent = "Position: not available";
      if (fixTime) fixTime.textContent = "Last fix: not available";
      if (alertText) alertText.textContent = "Last alert: none yet";
      if (hotspotState) hotspotState.textContent = "Hotspot state: not available";
      setStatus("GPS tracking stopped.");
    }

    function startGps() {
      if (running) return;
      const isLocal = location.hostname === "localhost" || location.hostname === "127.0.0.1";
      if (!root.isSecureContext && !isLocal) {
        const httpsUrl = `https://${location.hostname}:8443${location.pathname}${location.search}`;
        if (status) status.innerHTML = `⚠️ Mobile GPS requires HTTPS.<br><a href="${httpsUrl}" style="color:#1267d6;font-weight:700;text-decoration:underline;">Tap here to open via HTTPS (Port 8443)</a>`;
        return;
      }
      if (!navigator.geolocation) { setStatus("This browser does not support device geolocation."); return; }
      if (!geometry || typeof geometry.geometryDistanceMeters !== "function") { setStatus("Hotspot geometry support did not load. Refresh the page."); return; }
      if (vehicleSimulation && typeof vehicleSimulation.disable === "function") vehicleSimulation.disable();
      unlockSpeechFromGesture();
      running = true;
      isTracking = true;
      currentSpeedKmh = null;
      if (root.TrafficSpeedometer) root.TrafficSpeedometer.activate("GPS", "Unavailable");
      follow = true;
      hasCentered = false;
      startButton.disabled = true;
      stopButton.disabled = false;
      if (coordinates) coordinates.textContent = "Position: waiting for GPS fix";
      setStatus("Requesting device GPS permission and loading published DBSCAN polygons…");
      acquireWakeLock();
      loadClusters().then(() => {
        if (running && lastPosition && lastPosition.coords.accuracy <= MAX_ALERT_ACCURACY_METERS) {
          updateZone(lastPosition.coords.longitude, lastPosition.coords.latitude);
          setStatus(usingBundledClusters
            ? "Live GPS tracking. Alerts use the bundled demo DBSCAN polygons."
            : "Live GPS tracking. Hotspot alerts are active.");
        }
      }).catch((error) => {
        if (running) setStatus(`GPS is on, but hotspot alert data failed to load: ${error.message}`);
      });
      try {
        watchId = navigator.geolocation.watchPosition(onPosition, onGpsError, {
          enableHighAccuracy: true, maximumAge: 1000, timeout: 15000,
        });
      } catch (error) {
        stopWatch();
        setStatus(`GPS could not start: ${error.message}`);
      }
    }

    startButton.addEventListener("click", startGps);
    stopButton.addEventListener("click", stopGps);
    if (recenterButton) recenterButton.addEventListener("click", () => {
      if (!lastPosition) return;
      follow = true;
      setMapFollow(L.latLng(lastPosition.coords.latitude, lastPosition.coords.longitude), false);
      recenterButton.hidden = true;
    });
    map.on("dragstart", () => {
      if (!mapMoveIsProgrammatic && running) {
        follow = false;
        if (recenterButton) recenterButton.hidden = false;
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") acquireWakeLock();
    });
    window.addEventListener("pagehide", stopGps);
  }

  root.TrafficGpsTracking = Object.freeze({ initialize, classifyPosition, isRunning: () => isTracking, APPROACH_METERS, EXIT_HYSTERESIS_METERS });
}(window));
