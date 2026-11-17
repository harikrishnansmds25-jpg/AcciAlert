# AcciAlert — Traffic Accident Hotspot Analysis and Web GIS

This project contains the Python GIS analysis pipeline and the AcciAlert web app
in web/. The website is published from docs/ for GitHub Pages.

> **All accident records are SYNTHETIC.** Hotspot classes (GREEN/ORANGE/RED) describe the synthetic data only and
> are not real-world risk assessments.
>
> The default analysis uses a separate synthetic CSV whose coordinates follow mapped OSM geometry, clipped from
> Mangalapuram along NH 66 and the DUK access road. The original CSV and straight-line reference remain unchanged.
> OSM roads are mapped data, not surveyed or authoritative centrelines.

## Web app

The web app displays the bundled accident, DBSCAN, hotspot-centre, and road
corridor data without requiring GeoServer. See web/README.md for local running,
GPS/voice behavior, and physical-phone checks. The GitHub Pages source is docs/.

## Run on Windows (PowerShell, VS Code terminal)

```powershell
cd path\to\traffic_hotspot_analysis
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process Bypass
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```
The default analysis outputs appear in `outputs\road_aligned_analysis\analysis\`.
Open `outputs\road_aligned_analysis\visualization\traffic_hotspot_dashboard.html`
for the dashboard.

## Visualization dashboard
Run `python create_visualizations.py` to generate the interactive OSM map,
responsive dashboard, static visualization maps, charts, inventory, and validation
reports under `outputs\road_aligned_analysis\visualization\`. Open
`outputs\road_aligned_analysis\visualization\traffic_hotspot_dashboard.html` in a browser. OSM tiles and
dashboard charts require an internet connection.

The interactive web map displays the current 50 accident records. Hover over a
marker for its ID, severity, date, and road; click to see all CSV fields. Fatal
markers are red, major markers orange, and minor markers teal/green.

Both CSV inputs now contain the same deterministic 50-record sample, stratified
by road and severity (40 NH 66, 10 DUK Access Road; 30 Minor, 15 Major, 5 Fatal).
The default input pair is `data\nh66_road_aligned_synthetic_accidents.csv` and
`data\nh66_road_aligned_corridor.geojson`. The mapped NH 66 segment from Mangalapuram
to the DUK access junction comes from OSM way
[44634862](https://www.openstreetmap.org/way/44634862). The mapped DUK access road
uses OSM way [802362018](https://www.openstreetmap.org/way/802362018), with its NH 66
junction connection from [way 805241725](https://www.openstreetmap.org/way/805241725).
These are mapped roads, not surveyed centrelines.

To regenerate the default aligned CSV/road pair and rerun its analysis and dashboard,
run `python create_road_aligned_analysis.py`. The workflow also stores copies of the
aligned inputs and report under `outputs\road_aligned_analysis\`. The default analysis
uses a 20 m KDE bandwidth and an 80th-percentile high-density threshold. Both the
reference and road-aligned analyses are regenerated from the 50 selected records.

## Folder guide
| File | What it does (plain language) |
|---|---|
| `config.py` | All settings: file paths, KDE bandwidth/cell, DBSCAN eps/min_samples, scoring weights, thresholds. Edit here only. |
| `main.py` | Runs every stage in order and checks that all 14 required outputs exist. |
| `src/data_preparation.py` | Reads the CSV (as text, so nothing is silently changed) and GeoJSON; converts to metres (EPSG:32643). |
| `src/validation.py` | Checks columns, missing values, duplicates, dates, severity rules, distance to road; decides if the road file is a real centreline or a reference line. Never moves points. |
| `src/kde_analysis.py` | Gaussian KDE raster (GeoTIFF), high-density zones, plus an along-the-road (1-D) KDE. |
| `src/dbscan_analysis.py` | DBSCAN clusters, per-cluster statistics, cluster polygons, eps/min_samples sensitivity table. |
| `src/hotspot_classification.py` | Combines KDE and DBSCAN, scores hotspots, assigns GREEN/ORANGE/RED. |
| `src/geometry_export.py` | Builds hotspot point/polygon layers and writes GeoJSON (EPSG:4326). |
| `src/visualization.py` | PNG maps and the Folium HTML map. |

## Key ideas
* **CRS:** distances use UTM 43N (EPSG:32643, metres); exported GeoJSON uses WGS84 (EPSG:4326).
* **KDE:** a smooth Gaussian "hill" on every point; default aligned analysis uses a 20 m bandwidth, 5 m cells and an 80th-percentile threshold.
* **DBSCAN:** eps 30 m, min_samples 5. The current 50-record run creates exactly three NH 66 clusters before the DUK junction, paired one-to-one with the Low, Medium, and High hotspots. The other 20 records are randomized along the roads outside those alert zones, with irregular gaps and no additional DBSCAN clusters.
* **Alert-zone buffer:** each cluster polygon extends 60 m beyond its DBSCAN event hull; the hotspot point is 60 m from the polygon boundary at the nearest edge. A generic warning triggers 10 m before the vehicle enters a polygon, followed by the Low/Medium/High warning on entry.
  Three hotspot candidates are displayed: one low, one medium and one high risk.
* **Hotspot = KDE and DBSCAN agree** (cluster with enough events and most points in high-density zones). High-density zones
  without a cluster are kept as "KDE-only"; clusters not in high-density zones and noise points are listed, not promoted.
* **Score:** weighted severity (fatal and deaths count most) + accident count + mean KDE, scaled 0-100; class thresholds are
  quantiles of the hotspots' own scores, so classes rank hotspots against each other.

## Default outputs (in `outputs\road_aligned_analysis\analysis\`)
`validated_accidents.geojson`, `kde_density.tif`, `dbscan_clustered_accidents.geojson`, `dbscan_clusters.geojson`,
`hotspots.geojson`, `hotspot_points.geojson`, `hotspot_polygons.geojson`, `kde_density_map.png`, `dbscan_clusters_map.png`,
`final_hotspot_map.png`, four `*_report.txt` files, plus extras `road_network_kde_profile.csv/.png` and `hotspot_map.html`.

## Load into QGIS
Layer > Add Layer > Add Vector Layer for the `.geojson` files; Add Raster Layer for `kde_density.tif`
(set the project CRS to EPSG:32643 or let QGIS reproject on the fly).
