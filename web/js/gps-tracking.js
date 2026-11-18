"use strict";

(function exposeGpsTracking(root) {
  let isTracking = false;
  const APPROACH_METERS = 10;
  const EXIT_HYSTERESIS_METERS = 5;
  const MAX_ALERT_ACCURACY_METERS = 30;
  const MIN_SPEECH_GAP_MS = 1200;
  const RISK_RANK = Object.freeze({ GREEN: 1, ORANGE: 2, RED: 3 });
  const RISK_LABEL = Object.freeze({ GREEN: "Low", ORANGE: "Medium", RED: "High" });
  const ALERT_TEXT = Object.freeze({
    APPROACH: "Careful! Accident hotspot ahead. Please slow down.",
    GREEN: "Careful! Accident hotspot ahead. Please slow down.",
    ORANGE: "Attention! Accident hotspot ahead. Drive carefully.",
    RED: "Warning! High-risk accident hotspot ahead. Reduce your speed!",
  });

  function featureRisk(feature) {
    const properties = feature.properties || {};
    const direct = String(properties.Sev_Class || properties.Severity_Class || properties.Risk_Class || "")
      .trim().toUpperCase();
    if (RISK_RANK[direct]) return direct;
    const status = String(properties.Hotspot_Status || "").toUpperCase();
    const match = status.match(/\((GREEN|ORANGE|RED)\)/);
    return match ? match[1] : "GREEN";
  }

  function featureKey(feature, index) {
    const properties = feature.properties || {};
    return String(properties.Hotspot_ID || properties.DBSCAN_ID || properties.Cluster_ID || properties.cluster_id || feature.id || `cluster-${index + 1}`);
  }

  function classifyPosition(features, geometry, longitude, latitude) {
    const measurements = features.map((feature, index) => ({
      feature,
      id: featureKey(feature, index),
      distance: geometry.geometryDistanceMeters(feature.geometry, longitude, latitude),
      inside: geometry.geometryContainsPoint(feature.geometry, longitude, latitude),
      risk: featureRisk(feature),
    }));
    const inside = measurements.filter((item) => item.inside)
      .sort((a, b) => RISK_RANK[b.risk] - RISK_RANK[a.risk]);
    if (inside.length) return { state: "inside", ...inside[0] };
    const approaching = measurements.filter((item) => item.distance <= APPROACH_METERS)
      .sort((a, b) => a.distance - b.distance);
    return approaching.length ? { state: "approaching", ...approaching[0] } : { state: "outside" };
  }

  function initialize({ map, wfsUrl, geometry, vehicleSimulation }) {
    const el = (id) => document.getElementById(id);
    const startButton = el("gps-start");
    const stopButton = el("gps-stop");
    const voiceButton = el("voice-toggle");
    const recenterButton = el("gps-recenter");
    const status = el("gps-status");
    const voiceStatus = el("voice-status");
    const coordinates = el("gps-coordinates");
    const hotspotState = el("gps-hotspot-state");
    const fixTime = el("gps-fix-time");
    if (!map || !geometry || !startButton || !stopButton) return;

    let watchId = null;
    let running = false;
    let voiceEnabled = false;
    let features = null;
    let featurePromise = null;
    let usingBundledClusters = false;
    let marker = null;
    let accuracyCircle = null;
    let lastPosition = null;
    let current = { state: "outside", id: null };
    let lastSpokenAt = 0;
    let speechQueue = [];
    let wakeLock = null;
    let follow = true;
    let hasCentered = false;
    let mapMoveIsProgrammatic = false;

    const setStatus = (message) => { if (status) status.textContent = message; };

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

    function pumpSpeech() {
      if (!running || !voiceEnabled || !speechQueue.length || !root.speechSynthesis) return;
      if (root.speechSynthesis.speaking || root.speechSynthesis.pending) return;
      const wait = Math.max(0, MIN_SPEECH_GAP_MS - (Date.now() - lastSpokenAt));
      if (wait) { window.setTimeout(pumpSpeech, wait); return; }
      const item = speechQueue.shift();
      const utterance = new root.SpeechSynthesisUtterance(item.text);
      utterance.lang = "en-IN";
      utterance.onstart = () => { if (voiceStatus) voiceStatus.textContent = "Voice alert is playing."; };
      utterance.onend = () => {
        lastSpokenAt = Date.now();
        if (voiceStatus && voiceEnabled) voiceStatus.textContent = speechQueue.length ? "Voice alert queued." : "Voice alert finished.";
        pumpSpeech();
      };
      utterance.onerror = (event) => {
        if (event.error !== "canceled" && event.error !== "interrupted" && voiceStatus) {
          voiceStatus.textContent = "The browser could not play the alert. Check sound settings.";
        }
        lastSpokenAt = Date.now();
        pumpSpeech();
      };
      try {
        lastSpokenAt = Date.now();
        root.speechSynthesis.speak(utterance);
      } catch (error) {
        if (voiceStatus) voiceStatus.textContent = "The browser could not start voice alerts.";
      }
    }

    function announce(text) {
      if (voiceStatus) voiceStatus.textContent = text.endsWith(".") ? text : `${text}.`;
      if (!voiceEnabled || !root.speechSynthesis || !root.SpeechSynthesisUtterance) return;
      speechQueue.push({ text });
      pumpSpeech();
    }

    function unlockSpeechFromGesture() {
      if (!root.speechSynthesis || !root.SpeechSynthesisUtterance) return;
      try {
        const unlock = new root.SpeechSynthesisUtterance("");
        unlock.volume = 0;
        root.speechSynthesis.speak(unlock);
        root.speechSynthesis.resume();
      } catch (error) { /* Text alerts remain available when speech is blocked. */ }
    }

    async function acquireWakeLock() {
      if (!running || document.visibilityState !== "visible" || !navigator.wakeLock || wakeLock) return;
      try {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      } catch (error) {
        if (voiceStatus) voiceStatus.textContent = "Screen may lock during tracking; keep the app visible.";
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

    function updateZone(longitude, latitude) {
      if (!features) return;
      const next = classifyPosition(features, geometry, longitude, latitude);
      const previous = current;
      if (previous.id && previous.state !== "outside") {
        const previousFeature = features.find((feature, index) => featureKey(feature, index) === previous.id);
        if (previousFeature) {
          const distance = geometry.geometryDistanceMeters(previousFeature.geometry, longitude, latitude);
          const stillInside = geometry.geometryContainsPoint(previousFeature.geometry, longitude, latitude);
          if (stillInside || distance <= EXIT_HYSTERESIS_METERS) {
            if (next.state === "inside" && next.id !== previous.id) {
              current = next;
            } else {
              current = { state: "inside", id: previous.id, feature: previousFeature, risk: featureRisk(previousFeature) };
              if (hotspotState) hotspotState.textContent = `Hotspot state: Inside ${RISK_LABEL[current.risk]} risk DBSCAN cluster ${current.id}`;
              return;
            }
          } else if (distance <= APPROACH_METERS + EXIT_HYSTERESIS_METERS && next.state === "outside") {
            current = { state: "approaching", id: previous.id, feature: previousFeature, risk: featureRisk(previousFeature) };
            if (hotspotState) hotspotState.textContent = `Hotspot state: Leaving ${RISK_LABEL[current.risk]} risk DBSCAN cluster ${current.id}`;
            return;
          }
        }
      }
      if (next.state === "outside") {
        current = { state: "outside", id: null };
        if (hotspotState) hotspotState.textContent = "Hotspot state: Outside DBSCAN clusters";
        return;
      }
      if (next.state === "approaching") {
        if (previous.state === "outside" || previous.id !== next.id) announce(ALERT_TEXT.APPROACH);
        current = next;
        if (hotspotState) hotspotState.textContent = `Hotspot state: Approaching ${RISK_LABEL[next.risk]} risk DBSCAN cluster ${next.id} (within ${APPROACH_METERS} m)`;
        return;
      }
      current = next;
      if (previous.state !== "inside" || previous.id !== next.id) announce(ALERT_TEXT[next.risk]);
      if (hotspotState) hotspotState.textContent = `Hotspot state: Inside ${RISK_LABEL[next.risk]} risk DBSCAN cluster ${next.id}`;
    }

    function onPosition(position) {
      lastPosition = position;
      const { latitude, longitude, accuracy } = position.coords;
      const latlng = L.latLng(latitude, longitude);
      ensureGpsLayers(latlng, accuracy, position.coords.heading);
      setMapFollow(latlng, !hasCentered);
      hasCentered = true;
      if (coordinates) coordinates.textContent = `Position: ${latitude.toFixed(6)}, ${longitude.toFixed(6)} (accuracy ±${Math.round(accuracy)} m)`;
      if (fixTime) fixTime.textContent = `Last fix: ${new Date(position.timestamp || Date.now()).toLocaleTimeString()}`;
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

    function stopWatch() {
      if (watchId !== null && navigator.geolocation) navigator.geolocation.clearWatch(watchId);
      watchId = null;
      running = false;
      isTracking = false;
      speechQueue = [];
      if (root.speechSynthesis) root.speechSynthesis.cancel();
      releaseWakeLock();
      startButton.disabled = false;
      stopButton.disabled = true;
      if (recenterButton) recenterButton.hidden = true;
      current = { state: "outside", id: null };
    }

    function onGpsError(error) {
      if (error.code === 1) {
        stopWatch();
        setStatus("Location permission denied. Allow location access in browser settings, then start GPS again.");
      } else if (error.code === 2) {
        setStatus("GPS location unavailable. Check device location services and move to an open area.");
      } else if (error.code === 3) {
        setStatus("GPS request timed out. Waiting for another fix; check device location services.");
      } else {
        setStatus("GPS could not determine your location. Check device location settings.");
      }
    }

    function stopGps() {
      stopWatch();
      if (marker) map.removeLayer(marker);
      if (accuracyCircle) map.removeLayer(accuracyCircle);
      marker = null;
      accuracyCircle = null;
      lastPosition = null;
      hasCentered = false;
      if (coordinates) coordinates.textContent = "Position: not available";
      if (fixTime) fixTime.textContent = "Last fix: not available";
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
    if (voiceButton) {
      if (!root.speechSynthesis || !root.SpeechSynthesisUtterance) {
        voiceButton.disabled = true;
        voiceButton.textContent = "Voice alerts unavailable";
        if (voiceStatus) voiceStatus.textContent = "This browser does not support speech synthesis.";
      } else {
        voiceButton.addEventListener("click", () => {
          voiceEnabled = !voiceEnabled;
          voiceButton.setAttribute("aria-pressed", String(voiceEnabled));
          voiceButton.textContent = `Voice alerts: ${voiceEnabled ? "On" : "Off"}`;
          if (!voiceEnabled) { speechQueue = []; root.speechSynthesis.cancel(); }
          else { unlockSpeechFromGesture(); if (voiceStatus) voiceStatus.textContent = "Voice alerts enabled."; }
        });
      }
    }
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
