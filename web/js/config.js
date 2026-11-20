"use strict";

window.GEOSERVER_BASE = "http://localhost:8080/geoserver";

// Project demonstration recommendations; these are not official speed limits.
window.TrafficSpeedAlerts = Object.freeze((() => {
  const speeds = Object.freeze({ HS01: 40, HS02: 50, HS03: 60 });
  // Stage 1: spoken once when the vehicle comes within 10 m of a hotspot boundary.
  const approachMessage = "Careful! Accident hotspot ahead. Please slow down.";
  // Stage 2: spoken once when the vehicle enters the hotspot polygon.
  const values = Object.freeze({
    RED: { risk: "High", normal: "Warning! You are entering a high-risk accident hotspot. Reduce your speed and drive carefully." },
    ORANGE: { risk: "Medium", normal: "Attention! You are entering a medium-risk accident hotspot. Drive carefully." },
    GREEN: { risk: "Low", normal: "Careful! You are entering a low-risk accident hotspot. Please slow down." },
  });
  const riskOf = (feature) => {
    const p = feature && feature.properties || {};
    const severity = String(p.Sev_Class || p.Severity_Class || p.Risk_Class || "").trim().toUpperCase();
    if (values[severity]) return severity;
    // DBSCAN polygons store the class only in Hotspot_Status, e.g. "Hotspot HS01 (RED)".
    const status = String(p.Hotspot_Status || "").toUpperCase().match(/\b(GREEN|ORANGE|RED)\b/);
    if (status) return status[1];
    const risk = String(p.Risk_Level || "").trim().toUpperCase();
    return ({ HIGH: "RED", MEDIUM: "ORANGE", LOW: "GREEN" })[risk] || "GREEN";
  };
  const idOf = (feature) => {
    const p = feature && feature.properties || {};
    const status = String(p.Hotspot_Status || "").match(/HS\d+/i);
    return String(p.Hotspot_ID || (status && status[0]) || "");
  };
  return {
    recommendations: speeds,
    evaluate(feature, speedKmh) {
      const id = idOf(feature);
      const risk = riskOf(feature);
      const recommendation = speeds[id] || null;
      const reliableSpeed = Number.isFinite(speedKmh) && speedKmh >= 0 && speedKmh <= 250;
      // Compare the rounded speed the driver sees on the speedometer.
      const above = Boolean(recommendation && reliableSpeed && Math.round(speedKmh) > recommendation);
      const prefix = ({ RED: "Warning!", ORANGE: "Attention!", GREEN: "Careful!" })[risk];
      const riskName = values[risk].risk.toLowerCase();
      return {
        id: id || "—", risk: values[risk].risk, recommendation,
        speedKmh: reliableSpeed ? speedKmh : null, above,
        status: above ? "Above Recommended Speed"
          : reliableSpeed ? "Within Recommended Speed" : "Speed unavailable",
        message: above
          ? `${prefix} You are entering a ${riskName}-risk accident hotspot above the recommended speed. Reduce your speed to ${recommendation} kilometres per hour.`
          : values[risk].normal,
      };
    },
    approachMessage,
  };
})());

window.TrafficSpeedometer = Object.freeze((() => {
  let activeMode = null;
  const elements = () => ({
    mode: document.getElementById("speedometer-mode"),
    value: document.getElementById("speedometer-value"),
    status: document.getElementById("speedometer-status"),
    root: document.getElementById("speedometer"),
  });
  function render(mode, speedKmh, status) {
    const ui = elements();
    if (!ui.root) return;
    ui.mode.textContent = mode || "No active mode";
    ui.value.textContent = Number.isFinite(speedKmh) ? String(Math.round(speedKmh)) : "--";
    ui.status.textContent = status;
    ui.root.dataset.status = String(status).toLowerCase();
  }
  return {
    activate(mode, status = "Unavailable") {
      activeMode = mode;
      render(mode, null, status);
    },
    update(mode, speedKmh, status) {
      if (activeMode !== mode) return;
      render(mode, speedKmh, status);
    },
    stop(mode, status = "Stopped") {
      if (activeMode !== mode) return;
      render(mode, null, status);
      activeMode = null;
    },
  };
})());
