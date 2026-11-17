# Project Status Report

## Project overview

This folder contains the desktop Python GIS analysis portion of **Traffic Accident Hotspot Analysis and Real-Time Voice Alert System**. It prepares data, runs KDE and DBSCAN, classifies candidate hotspots, and exports maps and GIS layers. The folder does not contain a deployed web/mobile application, GPS tracking service, voice alert component, GeoServer configuration, or a live data feed.

All accident records are **synthetic**. The hotspot classes and scores describe those generated records only. The original road reference is a simplified straight-line approximation, and the OSM road geometry is mapped data, not survey-grade or an authoritative centreline.

## Folder structure and important files

- `data/`: Two-road reference GeoJSON, a 50-row synthetic CSV, OSM road references, and the matching road-aligned CSV and clipped road GeoJSON.
- `src/`: Analysis modules: input conversion, validation, planar and road-network KDE, DBSCAN, hotspot scoring/classification, geometry export, and static/Folium mapping.
- `visualization/`: Inventory and validation, OSM map, charts, and dashboard generation modules.
- `outputs/`: Historical results from the original reference-line run, retained for comparison.
- `outputs/road_aligned_analysis/`: Default analysis and visualization outputs, plus reproducible copies of the aligned synthetic CSV and road input.
- `main.py`: Runs the complete analysis pipeline and checks the expected outputs.
- `config.py`: Input/output paths, EPSG codes, validation limits, KDE and DBSCAN parameters, and hotspot scoring thresholds.
- `create_road_aligned_analysis.py`: Builds and analyzes the separate road-aligned synthetic alternative.
- `create_visualizations.py`: Builds visualizations from existing analysis outputs; it does not run the analysis itself.
- `requirements.txt`: Python package minimum versions.
- `README.md`, `environment_setup.md`: Workflow, limitations, settings, environment notes, and output descriptions.
- `.venv/`, `__pycache__/`: Local Python environment and cached bytecode; these are not source modules.

## Datasets and coordinate reference systems

### Original inputs

`data/nh66_reference_based_synthetic_accidents.csv` contains a deterministic 50-record sample selected from the former 300 records. It includes 40 NH 66 and 10 DUK Access Road events, dated 2021-01-12 through 2026-09-23; severity totals are 30 Minor, 15 Major, and 5 Fatal.

`data/nh66_reference_corridor.geojson` contains two LineString features (`road_class`, `note`): NH 66 and DUK Access Road. It has no explicit CRS member; GeoJSON coordinates are treated as WGS84 longitude/latitude (EPSG:4326/CRS84). Each line has only two vertices and the notes explicitly describe straight reference segments, not surveyed roads. The original pipeline projects to EPSG:32643 (UTM zone 43N, metres) for distance and density work.

`data/nh66_osm_mapped_reference.geojson` and `data/duk_osm_mapped_reference.geojson` record the OSM road sources. The default analysis uses the matching 50-record `data/nh66_road_aligned_synthetic_accidents.csv` and `data/nh66_road_aligned_corridor.geojson`. The aligned CSV preserves the selected non-coordinate fields and updates synthetic coordinates to the clipped OSM routes. The road file contains the Mangalapuram-to-junction NH 66 segment (OSM way 44634862) and the connected DUK route (way 802362018; junction way 805241725).

### Default road-aligned dataset and analysis

`data/nh66_road_aligned_synthetic_accidents.csv` is the default 50-record synthetic input; an identical copy and its road input are retained under `outputs/road_aligned_analysis/data/`. The alignment report records all 40 NH 66 and all 10 DUK points within 5 m of their corresponding mapped line (maximum offsets about 3 m). The road source remains OSM-derived and not surveyed.

### Generated spatial data

GeoJSON exports are written in EPSG:4326 for interchange and web maps. The historical original-reference raster at `outputs/kde_density.tif` is a single-band Float32 raster in EPSG:32643, 168 x 253 cells, with 5 m cells and -9999 nodata. The default aligned raster is at `outputs/road_aligned_analysis/analysis/kde_density.tif` (101 x 202 cells). Existing generated GeoJSON layers include validated accidents, DBSCAN-labelled points, DBSCAN clusters, candidate hotspots, hotspot points and hotspot polygons.

## Analysis methods and existing results

The default pipeline in `main.py` loads the aligned CSV and clipped road routes, validates inputs, projects distances, runs planar KDE and a separate along-road KDE profile, runs DBSCAN, combines the results into candidate hotspots, scores/classifies them, exports spatial layers, and produces maps and reports under `outputs/road_aligned_analysis/analysis/`.

- **Validation:** Checks required fields, missing/invalid values, duplicates, date/time and casualty consistency, road distance and study-area membership. It preserves coordinates and flags/excludes according to configured rules; it does not snap points.
- **Planar KDE:** Both 50-record runs use a Gaussian 20 m bandwidth, 5 m cells, 60 m road buffer and 80th-percentile density threshold. The generated report records six high-density zones.
- **Road-network KDE:** Also calculates a 1-D density profile by road chainage. This avoids spreading density sideways but treats the two roads separately and has no end correction, as noted in its report.
- **DBSCAN:** The current run uses 30 m `eps` and `min_samples=5`, producing exactly three NH 66 alert clusters before the DUK junction. Each cluster maps to one Low, Medium, or High hotspot; the other 20 records are randomized along the roads outside those zones with irregular gaps and no extra DBSCAN clusters.
- **Alert-zone buffer:** each cluster boundary is 60 m from its hotspot point at the nearest edge. The vehicle gives a generic warning 10 m before polygon entry and a risk-tier warning on entry.
- **Hotspot classification:** Combines high KDE with DBSCAN clusters, applies minimum counts and density-share rules, computes a relative 0–100 score from severity, count and mean KDE, and assigns one displayed candidate to each low, medium and high risk level (GREEN/ORANGE/RED). The low-risk candidate is on the DUK access road. These are synthetic-data classes, not real-world risk levels.

DBSCAN remains 30 m / 5. Both original-reference and road-aligned outputs are regenerated from the same 50 selected events; do not combine their layers because their coordinates differ.

## Maps, dashboard and current functionality

Visualizations are under `outputs/road_aligned_analysis/visualization/` and `outputs/visualization/`. The OSM layer control groups both mapped roads under one **Mapped roads** toggle and omits the simplified reference line, DBSCAN noise, and candidate hotspot polygons. It displays three hotspot points: one low, one medium and one high. The vehicle demo alerts on hotspot buffers and when it enters a DBSCAN cluster polygon. The generated dashboards report the 50-record analysis.

The HTML products load Leaflet/Folium assets, OSM tiles, or chart libraries from public CDNs. The README states that internet access is required for the OSM tiles and dashboard charts. The HTML dashboard/map is a project visualization, not evidence of a deployed, hosted or mobile-ready application.

## GeoServer and service availability

No GeoServer project/configuration, workspace/store/layer definitions, service endpoint, credentials, or WMS/WFS URL was found in the project source and configuration files. The available GeoJSON and GeoTIFF files are data that could be published, but their existence does not mean they are published. **GeoServer publication and WMS/WFS availability are not implemented or verifiable from this folder.**

## Work status

### Completed in this folder

- Synthetic sample dataset and two reference road features are present.
- Validation, KDE, DBSCAN, hotspot scoring/classification, export and visualization code is present.
- The default configuration uses the OSM-aligned synthetic CSV and clipped road routes, with matching analysis/dashboard outputs.
- Original synthetic source CSV and historical original-reference outputs are retained for comparison.
- HTML map/dashboard generation and visualization validation/reporting are implemented.

### Partially completed / limitations

- The original reference-based points are not road-aligned: historical outputs report median offsets of 93.2 m for NH 66 and 17.2 m for DUK. The default aligned inputs are within 5 m, but the accidents remain synthetic and road geometry remains unverified against survey data.
- Leaflet visualization exists as generated/static HTML and consumes local generated layers; no live service-backed application or operational data integration is evident.
- KDE/DBSCAN choices and relative hotspot thresholds are modelling choices, with sensitivity documented but no independent calibration to observed accidents.

### Pending / absent

- Real, quality-controlled accident observations and documented provenance.
- Survey/authoritative road centreline verification and operational geographic coverage.
- GeoServer setup, published layers and confirmed WMS/WFS endpoints.
- A deployed application/API and a production data-refresh path.
- Mobile application, permission-aware GPS tracking, background/location lifecycle handling, and user controls.
- Road-aware proximity/matching logic for alerts, alert suppression/cooldowns, and voice/TTS alerts.
- End-to-end integration and operational monitoring for analysis, services, web client and mobile client.

## Compatibility and potential problems

- `requirements.txt` says the project was tested with Python 3.10–3.12 and gives minimum dependency versions, while `environment_setup.md` records a local Python 3.14.2 environment and newer package versions. This mismatch means a clean install on the stated tested range and the recorded local environment are not the same compatibility claim.
- `environment_setup.md` documents a host `PROJ_LIB` conflict with an older PostgreSQL/PostGIS `proj.db`; the visualization package chooses Rasterio's bundled PROJ data before GIS imports. The recorded visualization report says EPSG initialization and reprojection passed in that environment. Other launch contexts may need the same compatibility handling.
- Browser features rely on public CDN and OSM tile availability; without network access, maps/charts may be incomplete.
- The original validation report's road-distance pass only means the points are close to the simplified reference lines, not to the mapped/physical roads.
- DBSCAN is Euclidean in projected space, not network distance along roads; nearby points on different roads may cluster together. KDE bandwidth, cell size, percentile and cluster parameters affect outputs.
- `outputs/` retains historical original-reference results alongside the default `outputs/road_aligned_analysis/` results. Consumers should use a single output branch and its matching source dataset/parameters.
- OSM attribution is present in road metadata and map products; any republishing should preserve applicable attribution and source notes.

## Recommended Next Development Step

Before implementing live alerts, establish one reviewed, versioned analysis input set: decide whether to use surveyed/authoritative roads or retain OSM as a clearly provisional base, keep synthetic records labelled for demonstration, and document the selected data/CRS and analysis parameters. Then expose the existing generated outputs through a local Leaflet client that reads a single, explicit output set (or confirmed WMS/WFS services once GeoServer is configured). This creates a stable map/data interface to build on. After that, implement mobile GPS tracking and voice alerts against a defined proximity rule, with user permission and alert cooldown controls, and validate the full flow using synthetic test routes before any real-world use.
