"use strict";

(() => {
  const status = document.getElementById("map-status");
  const mapElement = document.getElementById("map");

  function showStatus(message) {
    if (!status) return;
    status.textContent = message;
    status.hidden = false;
    status.classList.add("is-error");
  }

  function hideStatus() {
    if (!status) return;
    status.hidden = true;
    status.classList.remove("is-error");
  }

  if (!mapElement) {
    showStatus("Map container was not found.");
    return;
  }

  if (typeof L === "undefined") {
    showStatus("The map library could not load. Check your internet connection and refresh.");
    return;
  }

  const map = L.map(mapElement, {
    center: [8.620, 76.850],
    zoom: 15,
    maxZoom: 19,
    zoomControl: true,
    scrollWheelZoom: true,
  });

  const basemap = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
  });

  let loadedTiles = 0;
  basemap.on("tileload", () => {
    loadedTiles += 1;
    if (geoServerErrors.size === 0) hideStatus();
  });
  basemap.on("tileerror", () => {
    if (loadedTiles === 0) {
      showStatus("Map tiles could not load. Check your internet connection and try again.");
    }
  });
  basemap.addTo(map);

  const geoServerBase = String(window.GEOSERVER_BASE || "").replace(/\/$/, "");
  const geoServerUrl = geoServerBase ? `${geoServerBase}/traffic/wms` : "";
  const geoServerWfsUrl = geoServerBase ? `${geoServerBase}/traffic/ows` : "";
  const geoServerOptions = {
    service: "WMS",
    version: "1.1.1",
    format: "image/png",
    transparent: true,
    crs: L.CRS.EPSG3857,
    maxZoom: 19,
    opacity: 1,
    attribution: "GeoServer traffic layers",
  };

  // The workspace WMS advertises local layer names in its capabilities
  // document; SLD_BODY uses those local names for GeoServer library styling.
  function inlineStyle(layerName, catalogStyle, symbolizer) {
    return `<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld">`
      + `<NamedLayer><Name>${layerName}</Name><UserStyle><Name>${catalogStyle}</Name>`
      + `<FeatureTypeStyle><Rule>${symbolizer}</Rule></FeatureTypeStyle>`
      + `</UserStyle></NamedLayer></StyledLayerDescriptor>`;
  }

  map.createPane("roadPane").style.zIndex = "410";
  map.createPane("polygonPane").style.zIndex = "420";
  map.createPane("accidentPane").style.zIndex = "430";
  map.createPane("hotspotPane").style.zIndex = "440";
  map.createPane("gpsPane").style.zIndex = "650";

  const vehicleSimulation = window.TrafficVehicleSimulation
    ? window.TrafficVehicleSimulation.initialize({ map, wfsUrl: geoServerWfsUrl, geometry: window.TrafficHotspotGeometry })
    : null;

  const layerDefinitions = [
    {
      name: "Synthetic accident points",
      layer: "traffic:accident_points",
      pane: "accidentPane",
      localData: "TrafficAccidentPoints",
      style: inlineStyle("accident_points", "point", `<PointSymbolizer><Graphic><Mark>`
        + `<WellKnownName>circle</WellKnownName><Fill><CssParameter name="fill">#e53935</CssParameter></Fill>`
        + `<Stroke><CssParameter name="stroke">#ffffff</CssParameter><CssParameter name="stroke-width">1.5</CssParameter></Stroke>`
        + `</Mark><Size>10</Size></Graphic></PointSymbolizer>`),
      popupTitle: "Accident",
      popupFields: [
        { label: "Accident ID", field: "Accident_ID" },
        { label: "Latitude", field: "Latitude" },
        { label: "Longitude", field: "Longitude" },
        { label: "Date", field: "Date" },
        { label: "Time", field: "Time" },
        { label: "Severity", field: "Severity" },
        { label: "Deaths", field: "Deaths" },
        { label: "Injuries", field: "Injuries" },
        { label: "Road name", field: "Road_Name" },
        { label: "Road type", field: "Road_Type" },
      ],
      visible: true,
    },
    {
      name: "Hotspot centre points",
      layer: "traffic:hotspot_points",
      pane: "hotspotPane",
      localData: "TrafficHotspotPoints",
      wfsPoints: true,
      popupTitle: "Hotspot",
      popupFields: [
        { label: "Hotspot ID", field: "Hotspot_ID" },
        { label: "Risk level", field: "Risk_Level" },
        { label: "Severity class", field: "Sev_Class" },
        { label: "Accident count", field: "Acc_Count" },
        { label: "Deaths", field: "Deaths_T" },
        { label: "Injuries", field: "Injur_T" },
        { label: "Risk score", field: "Risk_Score" },
      ],
      visible: true,
    },
    {
      name: "NH 66 and DUK road corridor",
      layer: "traffic:road_corridor",
      pane: "roadPane",
      localData: "TrafficRoadCorridor",
      style: inlineStyle("road_corridor", "line", `<LineSymbolizer><Stroke>`
        + `<CssParameter name="stroke">#ffffff</CssParameter><CssParameter name="stroke-width">7</CssParameter>`
        + `</Stroke></LineSymbolizer><LineSymbolizer><Stroke>`
        + `<CssParameter name="stroke">#007f9e</CssParameter><CssParameter name="stroke-width">4</CssParameter>`
        + `</Stroke></LineSymbolizer>`),
      popupTitle: "Road",
      popupFields: [
        { label: "Road name", field: "name" },
        { label: "Road class", field: "road_class" },
        { label: "Highway type", field: "highway" },
      ],
      visible: true,
    },
  ];
  const overlayLayers = {};
  const geoServerErrors = new Set();

  layerDefinitions.forEach((definition) => {
    const { name, layer, visible, pane, style } = definition;
    const localData = definition.localData && window[definition.localData];
    if (localData && Array.isArray(localData.features)) {
      const isAccidentLayer = name === "Synthetic accident points";
      const isHotspotLayer = name === "Hotspot centre points";
      const hotspotColor = (feature) => {
        const properties = feature.properties || {};
        const risk = String(properties.Sev_Class || properties.Severity_Class || "").trim().toUpperCase();
        return ({ RED: "#e11d2e", ORANGE: "#f28c00", GREEN: "#2ca25f" })[risk] || "#2563eb";
      };
      const localOptions = {
        pane,
        style: name === "NH 66 and DUK road corridor"
          ? { color: "#007f9e", weight: 5, opacity: 1, lineCap: "round", lineJoin: "round" }
          : undefined,
        pointToLayer: (feature, latlng) => {
          if (isHotspotLayer) {
            return L.circleMarker(latlng, {
              radius: 10, color: "#ffffff", weight: 3,
              fillColor: hotspotColor(feature), fillOpacity: 1,
              bubblingMouseEvents: false,
            });
          }
          const severity = String((feature.properties || {}).Severity || "").trim().toLowerCase();
          const color = severity === "fatal" ? "#e11d48" : "#2563eb";
          return L.circleMarker(latlng, {
            radius: severity === "fatal" ? 8 : 7,
            color: "#ffffff",
            weight: 2,
            fillColor: color,
            fillOpacity: 0.95,
            bubblingMouseEvents: false,
          });
        },
        onEachFeature: (feature, marker) => {
          const properties = feature.properties || {};
          if (isAccidentLayer) {
            const summary = `<strong>${escapeHtml(getProperty(properties, "Accident_ID"))}</strong>`
              + `<br>Severity: ${escapeHtml(getProperty(properties, "Severity"))}`
              + `<br>${escapeHtml(getProperty(properties, "Date"))} · ${escapeHtml(getProperty(properties, "Road_Name"))}`;
            marker.bindTooltip(summary, { sticky: true, direction: "top" });
          } else if (isHotspotLayer) {
            const risk = ({ RED: "High", ORANGE: "Medium", GREEN: "Low" })[
              String(properties.Sev_Class || properties.Severity_Class || "").trim().toUpperCase()
            ] || "Unclassified";
            marker.bindTooltip(`${escapeHtml(getProperty(properties, "Hotspot_ID"))} · ${risk} risk`, {
              direction: "top", sticky: true,
            });
          } else if (name === "NH 66 and DUK road corridor") {
            marker.bindTooltip(escapeHtml(getProperty(properties, "name")), { sticky: true, direction: "top" });
          }
          marker.bindPopup(popupSection(definition, feature), { maxWidth: 360, maxHeight: 420 });
          if (isAccidentLayer) {
            marker.on("mouseover", () => marker.setStyle({ radius: 11, weight: 2.5 }));
            marker.on("mouseout", () => {
            const severity = String(properties.Severity || "").trim().toLowerCase();
            marker.setStyle({ radius: severity === "fatal" ? 8 : 7, weight: 2 });
            });
          }
        },
      };
      const isRoadLayer = name === "NH 66 and DUK road corridor";
      const localLayer = isRoadLayer
        ? L.layerGroup([
          L.geoJSON(localData, {
            pane,
            interactive: false,
            style: { color: "#ffffff", weight: 8, opacity: 1, lineCap: "round", lineJoin: "round" },
          }),
          L.geoJSON(localData, localOptions),
        ])
        : L.geoJSON(localData, localOptions);
      overlayLayers[name] = localLayer;
      definition.localLayer = localLayer;
      if (visible) localLayer.addTo(map);

      if (isAccidentLayer) {
        const legend = L.control({ position: "bottomright" });
        legend.onAdd = () => {
          const container = L.DomUtil.create("div", "accident-severity-legend");
          container.style.cssText = "background:#fff;padding:8px 10px;border:1px solid #cbd5e1;border-radius:6px;line-height:1.6;font:12px Arial,sans-serif;box-shadow:0 1px 4px #0002";
          container.innerHTML = `<strong>Accident severity</strong><br>`
            + `<span style="color:#e11d48">●</span> Fatal<br>`
            + `<span style="color:#2563eb">●</span> Non-fatal`;
          L.DomEvent.disableClickPropagation(container);
          return container;
        };
        legend.addTo(map);
      }
      return;
    }

    if (definition.wfsPoints) {
      const centerLayer = L.layerGroup();
      overlayLayers[name] = centerLayer;
      definition.wmsLayer = null;
      if (visible) centerLayer.addTo(map);
      if (!geoServerWfsUrl) {
        showStatus(`No local data or GeoServer fallback is configured for ${name}.`);
        return;
      }
      const params = new URLSearchParams({
        SERVICE: "WFS",
        VERSION: "2.0.0",
        REQUEST: "GetFeature",
        TYPENAMES: layer,
        OUTPUTFORMAT: "application/json",
        SRSNAME: "EPSG:4326",
        COUNT: "10000",
      });
      fetch(`${geoServerWfsUrl}?${params.toString()}`)
        .then((response) => {
          if (!response.ok) throw new Error(`Hotspot centre WFS returned ${response.status}`);
          return response.json();
        })
        .then((data) => {
          const features = Array.isArray(data.features) ? data.features.filter((feature) => (
            feature.geometry && feature.geometry.type === "Point"
          )) : [];
          if (!features.length) throw new Error("GeoServer returned no hotspot centre points.");
          const centers = L.geoJSON({ type: "FeatureCollection", features }, {
            pane,
            pointToLayer: (feature, latlng) => {
              const properties = feature.properties || {};
              const severity = String(properties.Sev_Class || properties.Severity_Class || "")
                .trim().toUpperCase();
              const color = ({ RED: "#e11d2e", ORANGE: "#f28c00", GREEN: "#2ca25f" })[severity] || "#2563eb";
              return L.circleMarker(latlng, {
                radius: 10,
                color: "#ffffff",
                weight: 3,
                fillColor: color,
                fillOpacity: 1,
                bubblingMouseEvents: false,
              });
            },
            onEachFeature: (feature, marker) => {
              const properties = feature.properties || {};
              const severity = String(properties.Sev_Class || properties.Severity_Class || "")
                .trim().toUpperCase();
              const risk = ({ RED: "High", ORANGE: "Medium", GREEN: "Low" })[severity] || "Unclassified";
              marker.bindTooltip(`${escapeHtml(getProperty(properties, "Hotspot_ID"))} · ${risk} risk`, {
                direction: "top", sticky: true,
              });
              marker.bindPopup(popupSection(definition, feature), { maxWidth: 360, maxHeight: 420 });
            },
          });
          centers.addTo(centerLayer);
        })
        .catch((error) => {
          showStatus(`Hotspot centre points could not be loaded. ${error.message}`);
          console.error("Hotspot centre WFS request failed:", error);
        });
      return;
    }

    if (!geoServerUrl) {
      overlayLayers[name] = L.layerGroup();
      definition.wmsLayer = null;
      if (visible) overlayLayers[name].addTo(map);
      showStatus(`No local data or GeoServer fallback is configured for ${name}.`);
      return;
    }

    const wmsLayer = L.tileLayer.wms(geoServerUrl, {
      ...geoServerOptions,
      layers: layer,
      styles: layer === "traffic:road_corridor" ? "line" : "point",
      sld_body: style,
      pane,
    });

    wmsLayer.on("tileerror", () => {
      geoServerErrors.add(name);
      showStatus(`GeoServer layer unavailable: ${name}. Check that GeoServer is running and the layer is published.`);
    });
    wmsLayer.on("tileload", () => {
      geoServerErrors.delete(name);
      if (geoServerErrors.size === 0 && loadedTiles > 0) hideStatus();
    });

    overlayLayers[name] = wmsLayer;
    definition.wmsLayer = wmsLayer;
    if (visible) wmsLayer.addTo(map);
  });

  const clusterData = window.TrafficDBSCANClusters;
  if (clusterData && Array.isArray(clusterData.features)) {
    const riskColor = (feature) => {
      const status = String((feature.properties || {}).Hotspot_Status || "").toUpperCase();
      const risk = status.match(/\((GREEN|ORANGE|RED)\)/)?.[1];
      return ({ RED: "#e11d2e", ORANGE: "#f28c00", GREEN: "#2ca25f" })[risk] || "#2563eb";
    };
    overlayLayers["DBSCAN clusters"] = L.geoJSON(clusterData, {
      pane: "polygonPane",
      style: (feature) => ({ color: riskColor(feature), weight: 2.5, fillColor: riskColor(feature), fillOpacity: 0.12 }),
      onEachFeature: (feature, layer) => {
        const properties = feature.properties || {};
        layer.bindPopup(`<strong>DBSCAN cluster ${escapeHtml(properties.Cluster_ID ?? "—")}</strong>`
          + `<br>Accident records: ${escapeHtml(properties.Accident_Count ?? "—")}`
          + `<br>Status: ${escapeHtml(properties.Hotspot_Status ?? "Cluster")}`);
      },
    }).addTo(map);
  }

  L.control.layers(
    { "OpenStreetMap": basemap },
    overlayLayers,
    { collapsed: window.matchMedia("(max-width: 600px)").matches, position: "topright" },
  ).addTo(map);

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);
  }

  function getProperty(properties, field) {
    if (!properties) return "—";
    const aliases = {
      "accident_id": ["acc_id", "accident_id"],
      "acc_id": ["acc_id", "accident_id"],
      "hotspot_id": ["hotspot_id", "hotspot_st", "dbscan_id"],
      "risk_level": ["risk_level", "sev_class"],
      "deaths": ["deaths", "deaths_t", "fat_count"],
      "injuries": ["injuries", "injur_t", "maj_count"],
    };
    const target = field.toLowerCase();
    const searchKeys = aliases[target] || [target];
    const key = Object.keys(properties).find((candidate) => {
      const candLower = candidate.toLowerCase();
      return searchKeys.includes(candLower)
        || candidate === field
        || candidate.endsWith(`:${field}`)
        || candLower === target;
    });
    const value = key ? properties[key] : null;
    return value === null || value === undefined || value === "" ? "—" : value;
  }

  function featureInfoUrl(definition, clickEvent) {
    const size = map.getSize();
    const bounds = map.getBounds();
    const southWest = map.options.crs.project(bounds.getSouthWest());
    const northEast = map.options.crs.project(bounds.getNorthEast());
    const point = clickEvent.containerPoint;
    const params = new URLSearchParams({
      SERVICE: "WMS",
      VERSION: "1.1.1",
      REQUEST: "GetFeatureInfo",
      SRS: "EPSG:3857",
      BBOX: `${southWest.x},${southWest.y},${northEast.x},${northEast.y}`,
      WIDTH: String(size.x),
      HEIGHT: String(size.y),
      X: String(Math.max(0, Math.min(size.x - 1, Math.round(point.x)))),
      Y: String(Math.max(0, Math.min(size.y - 1, Math.round(point.y)))),
      LAYERS: definition.layer,
      QUERY_LAYERS: definition.layer,
      STYLES: definition.layer === "traffic:road_corridor" ? "line" : "point",
      SLD_BODY: definition.style,
      FORMAT: "image/png",
      TRANSPARENT: "true",
      INFO_FORMAT: "application/json",
      FEATURE_COUNT: "10",
      FI_POINT_TOLERANCE: "8",
      FI_LINE_TOLERANCE: "8",
      FI_POLYGON_TOLERANCE: "4",
    });
    return `${geoServerUrl}?${params.toString()}`;
  }

  function popupSection(definition, feature) {
    const properties = feature.properties || {};
    const rows = definition.popupFields.map(({ label, field }) => (
      `<div class="feature-popup__row"><dt>${escapeHtml(label)}</dt>`
      + `<dd>${escapeHtml(getProperty(properties, field))}</dd></div>`
    )).join("");
    return `<section class="feature-popup__section"><h3>${escapeHtml(definition.popupTitle)}</h3>`
      + `<dl>${rows}</dl></section>`;
  }

  let activeFeatureInfoRequest;
  map.on("click", async (event) => {
    const visibleDefinitions = layerDefinitions.filter(({ wmsLayer }) => wmsLayer && map.hasLayer(wmsLayer));
    if (visibleDefinitions.length === 0) return;

    if (activeFeatureInfoRequest) activeFeatureInfoRequest.abort();
    activeFeatureInfoRequest = new AbortController();
    const { signal } = activeFeatureInfoRequest;
    const popup = L.popup({ maxWidth: 320, maxHeight: 300 })
      .setLatLng(event.latlng)
      .setContent("<p class=\"feature-popup__loading\">Loading feature details…</p>")
      .openOn(map);

    const results = await Promise.all(visibleDefinitions.map(async (definition) => {
      try {
        const response = await fetch(featureInfoUrl(definition, event), { signal });
        if (!response.ok) throw new Error(`GetFeatureInfo returned ${response.status}`);
        const data = await response.json();
        return { definition, features: Array.isArray(data.features) ? data.features : [] };
      } catch (error) {
        if (error.name === "AbortError") return null;
        console.error(`GetFeatureInfo failed for ${definition.layer}:`, error);
        return { definition, features: [], error };
      }
    }));

    if (signal.aborted || !map.hasLayer(popup)) return;

    const sections = results.filter(Boolean).flatMap(({ definition, features }) => (
      features.map((feature) => popupSection(definition, feature))
    ));
    const failures = results.filter((result) => result && result.error);
    if (sections.length > 0) {
      popup.setContent(`<div class="feature-popup">${sections.join("")}</div>`);
    } else if (failures.length > 0) {
      popup.setContent("<p class=\"feature-popup__empty\">Feature details could not be loaded.</p>");
    } else {
      popup.setContent("<p class=\"feature-popup__empty\">No feature found at this location.</p>");
    }
  });

  if (window.TrafficGpsTracking) {
    window.TrafficGpsTracking.initialize({ map, wfsUrl: geoServerWfsUrl, geometry: window.TrafficHotspotGeometry, vehicleSimulation });
  }
})();
