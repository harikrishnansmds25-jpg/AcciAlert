# AcciAlert

AcciAlert is a demonstration web GIS for the Mangalapuram–DUK study area. It
shows 50 synthetic accident records, three DBSCAN hotspot polygons, hotspot
centres, the NH 66 and DUK road corridor, a vehicle simulation, and live device
GPS alerts.

## Local map data

The web page loads its required map data from JavaScript GeoJSON bundles:

- js/accident-points.js: 50 accident records.
- js/dbscan-clusters.js: three risk-classified DBSCAN polygons.
- js/hotspot-points.js: three hotspot centres.
- js/road-corridor.js: NH 66 and DUK access road geometries.

The road and hotspot-centre bundles retain the attributes and record order from
the project's EPSG:4326 shapefiles. GeoServer remains optional for WMS/WFS
fallbacks. The deployed docs/js/config.js leaves that fallback unconfigured,
so the GitHub Pages site does not request localhost or require GeoServer. The
OpenStreetMap basemap still needs an internet connection.

All accident records are synthetic demonstration data. The map and alerts are
not a validated road-safety service.

## Run locally

From the project root, run:

    py web/dev_server.py

Open http://localhost:8000/. The bundled datasets make the map, hotspot alerts,
and vehicle route available without GeoServer. GeoServer requests are only
needed if a local dataset is absent and an optional fallback is configured.

## Live GPS and voice alerts

- Tap Start GPS Tracking and grant location permission. The app uses
  navigator.geolocation.watchPosition and displays the phone's marker,
  accuracy, coordinates, and last-fix time.
- Readings with accuracy worse than 30 m do not trigger hotspot alerts.
- Approach distance and polygon distance are measured in metres. The approach
  warning is issued within 10 m of a DBSCAN polygon; risk-class alerts trigger
  on entry. A 5 m exit hysteresis prevents repeated alerts at polygon edges.
- The voice control is separate from tracking. Starting GPS unlocks speech from
  the user's tap where supported; enable Voice alerts to hear alerts.
- The map follows the device initially. Dragging pauses following; Recenter on
  me resumes it. A screen wake lock is requested where supported.
- Keep the page open and visible. A web page cannot guarantee background
  tracking when closed or when the phone is locked. GPS accuracy and speech
  behavior depend on the device and browser.

## GitHub Pages

The GitHub Pages source is the docs/ folder on main. Its relative asset paths
work under the /AcciAlert/ repository subpath. The published config keeps
GeoServer disabled because all required layers are bundled locally.

For a physical-phone check, open the published HTTPS page, start GPS, grant
permission, confirm the live marker and accuracy, approach and enter a hotspot,
check the risk alert and voice output, exit and re-enter, then stop tracking.
Desktop checks do not verify real GPS or mobile speech behavior.
