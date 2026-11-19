"use strict";

window.GEOSERVER_BASE = "http://localhost:8080/geoserver";

// Project demonstration recommendations; these are not official speed limits.
window.TrafficSpeedAlerts = Object.freeze((() => {
  const speeds = Object.freeze({ HS01: 40, HS02: 50, HS03: 60 });
  const values = Object.freeze({
    RED: { risk: "High", normal: "Warning! High-risk accident hotspot ahead. Drive carefully." },
    ORANGE: { risk: "Medium", normal: "Attention! Medium-risk accident hotspot ahead. Drive carefully." },
    GREEN: { risk: "Low", normal: "Careful! Low-risk accident hotspot ahead. Please slow down." },
  });
  const riskOf = (feature) => {
    const p = feature && feature.properties || {};
    const severity = String(p.Sev_Class || p.Severity_Class || p.Risk_Class || "").trim().toUpperCase();
    if (values[severity]) return severity;
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
      const above = Boolean(recommendation && reliableSpeed && speedKmh > recommendation);
      const prefix = ({ RED: "Warning!", ORANGE: "Attention!", GREEN: "Careful!" })[risk];
      const riskName = values[risk].risk.toLowerCase();
      return {
        id: id || "—", risk: values[risk].risk, recommendation,
        speedKmh: reliableSpeed ? speedKmh : null, above,
        status: above ? "Above Recommended Speed"
          : reliableSpeed ? "Within Recommended Speed" : "Speed unavailable",
        message: above
          ? `${prefix} You are approaching a ${riskName}-risk accident hotspot above the recommended speed. Reduce your speed to ${recommendation} kilometres per hour.`
          : values[risk].normal,
      };
    },
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
