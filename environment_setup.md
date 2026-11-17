# Visualization environment setup

The visualization pipeline reads the existing KDE, DBSCAN, and hotspot outputs; it
does not rerun or overwrite the analysis.

## Windows PowerShell

From the project directory:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python create_visualizations.py
```

If no virtual environment exists, create one first with `python -m venv .venv`.
The checked-in project environment used during development is Python 3.14.2 with
GeoPandas 1.2.0, Rasterio 1.5.2, PyProj 3.8.0, Folium 0.20.0, Matplotlib 3.11.2,
Pandas 3.1.1, and NumPy 2.5.4. No dependency upgrade is required for the existing
environment.

## PROJ / PostgreSQL compatibility

This machine has a `PROJ_LIB` environment variable pointing to PostgreSQL/PostGIS's
older `proj.db`. Rasterio fails EPSG lookups and raster reprojection with that
database (`DATABASE.LAYOUT.VERSION.MINOR = 2`). The visualization package selects
the installed Rasterio wheel's bundled `proj_data` directory and assigns both
`PROJ_LIB` and `PROJ_DATA` before importing GeoPandas or Rasterio. This process-local
override leaves PostgreSQL/PostGIS untouched. The pipeline verifies EPSG:4326,
EPSG:32643, and reprojection of the actual KDE raster in
`outputs\visualization\visualization_test_report.txt`.

## NH 66 map alignment

The original `data\nh66_reference_corridor.geojson` is retained unchanged; it
contains a simplified two-point line used when the synthetic records were created.
The map now draws a separate mapped NH 66 geometry from OSM way
[44634862](https://www.openstreetmap.org/way/44634862) in solid blue and leaves the
original approximation as an optional dashed layer. The OSM geometry is mapped
data, not a surveyed or authoritative centreline. OSM attribution is included in
the feature metadata and map.

The 240 synthetic records labelled NH 66 remain at their original coordinates.
They follow the old simplified line: their median distance from the mapped OSM road
is approximately 90 m, and only 12 are within 5 m. The pipeline does not move those
points or rerun KDE, DBSCAN, or hotspot classification; the dashboard and test
report state this offset explicitly.

If a road-aligned alternative is needed, run
`python create_road_aligned_analysis.py`. This creates a separate copy of the CSV
with NH 66 points mapped along the OSM line, reruns the full analysis and generates
a dashboard under `outputs\road_aligned_analysis\`. The original dataset and output
files are preserved. DUK Access Road coordinates remain unchanged because no mapped
DUK access-road geometry has been verified.

If the bundle lacks `proj.db`, generation stops with an explicit error instead of
falling back to the incompatible system database.

## Outputs

Run `python create_visualizations.py` to generate the interactive map, dashboard,
static maps, charts, inventory, summary, and test report under
`outputs\visualization\`. Open `traffic_hotspot_dashboard.html` in a browser. The
interactive tiles and Chart.js charts load from their public CDNs, so internet
access is required for those browser features.
