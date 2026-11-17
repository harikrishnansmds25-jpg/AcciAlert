"""
config.py - every setting you may want to change lives here.

Nothing else in the project hard-codes file paths, distances or weights, so you
can experiment by editing only this file and re-running `python main.py`.

IMPORTANT: the accident records are SYNTHETIC. All hotspot classes produced by
this project describe the synthetic data only. They are NOT real-world risk
assessments.
"""
from pathlib import Path

# ---------------------------------------------------------------- paths ------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs" / "road_aligned_analysis" / "analysis"

ACCIDENT_CSV = DATA_DIR / "nh66_road_aligned_synthetic_accidents.csv"
ROAD_GEOJSON = DATA_DIR / "nh66_road_aligned_corridor.geojson"

# ----------------------------------------------------------------- CRS -------
CRS_GEOGRAPHIC = "EPSG:4326"   # used for every exported GeoJSON (lat/lon)
CRS_PROJECTED = "EPSG:32643"   # UTM zone 43N - metres - used for ALL distances

RANDOM_SEED = 42               # DBSCAN/KDE here are deterministic; kept for reproducibility

# ------------------------------------------------------- input validation ----
REQUIRED_COLUMNS = ["Accident_ID", "Latitude", "Longitude", "Date", "Time",
                    "Severity", "Deaths", "Injuries", "Road_Name", "Road_Type"]
SEVERITY_CATEGORIES = ["Minor", "Major", "Fatal"]
DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"

# Allowed deaths / injuries per severity (min, max). None = no upper limit.
SEVERITY_RULES = {
    "Minor": {"deaths": (0, 0), "injuries": (0, 2)},
    "Major": {"deaths": (0, 0), "injuries": (1, 5)},
    "Fatal": {"deaths": (1, None), "injuries": (0, None)},
}

# Road_Name in the CSV -> (value of 'road_class' in the GeoJSON, expected Road_Type)
ROAD_NAME_MAP = {
    "NH 66": ("NH 66", "National Highway"),
    "DUK Access Road": ("DUK Access Road", "Access Road"),
}

ROAD_TOLERANCE_M = 5.0         # max allowed distance from the matching road geometry
CORRIDOR_BUFFER_M = 60.0       # study-area boundary = road geometry buffered by this
# A road feature with this many vertices or fewer is treated as a simplified line
SIMPLIFIED_MAX_VERTICES = 3

# ------------------------------------------------------------------ KDE ------
KDE_BANDWIDTH_M = 20.0         # Gaussian sigma in metres
KDE_CELL_SIZE_M = 5.0          # raster resolution in metres
KDE_HIGH_PERCENTILE = 80       # "high density" = top (100-x)% of corridor cells
KDE_MIN_ZONE_AREA_M2 = 300.0   # ignore tiny high-density specks
ROAD_KDE_STEP_M = 2.0          # step of the optional along-the-road (network) KDE
ROAD_KDE_PEAK_PROMINENCE = 0.15  # fraction of max density a profile peak must stand out by

# --------------------------------------------------------------- DBSCAN ------
# The prompt suggested eps=100 m, min_samples=4 as a prototype. On this ~1.4 km
# corridor that merges almost everything (see the sensitivity table in
# outputs/dbscan_analysis_report.txt), so the default is smaller.
DBSCAN_EPS_M = 30.0
DBSCAN_MIN_SAMPLES = 5
DBSCAN_SENSITIVITY_EPS = [20, 30, 50, 100]
DBSCAN_SENSITIVITY_MIN_SAMPLES = [4, 5, 8]
CLUSTER_POLYGON_BUFFER_M = 60.0  # visible alert-zone distance beyond the cluster points

# ------------------------------------------------ hotspot identification ------
HOTSPOT_MIN_ACCIDENTS = 6        # a DBSCAN cluster needs at least this many events
CLUSTER_KDE_POINT_FRACTION = 0.5  # ...and this share of its points inside high-KDE zones
KDE_ONLY_MIN_ACCIDENTS = 5       # a high-KDE zone with no accepted cluster needs this many events

# ----------------------------------------------------- hotspot scoring --------
# weighted severity score = sum(weight * value)
W_MINOR = 1.0
W_MAJOR = 3.0
W_FATAL = 6.0
W_DEATH = 4.0
W_INJURY = 0.5
# Risk_Score (0-100) = 100 * sum(component_weight * component / max_component_over_hotspots)
SCORE_COMPONENT_WEIGHTS = {"severity": 0.50, "count": 0.25, "kde": 0.25}

# Class thresholds on Risk_Score.
#   "quantile": thresholds are percentiles of the hotspots' own scores
#   "fixed"   : thresholds below are used directly
THRESHOLD_METHOD = "quantile"
QUANTILE_THRESHOLDS = (0.35, 0.70)   # below 1st = GREEN, below 2nd = ORANGE, else RED
FIXED_THRESHOLDS = (40.0, 70.0)      # also used if fewer than 3 hotspots exist

# Optional warning buffers (kept off by default; the prompt says create them only if required)
ENABLE_WARNING_BUFFERS = False
WARNING_BUFFER_M = {"GREEN": 50.0, "ORANGE": 100.0, "RED": 150.0}

# --------------------------------------------------------- visualisation -----
USE_BASEMAP = False            # True = try contextily web tiles (needs internet)
MAKE_FOLIUM_MAP = True         # interactive outputs/hotspot_map.html
SEVERITY_COLOURS = {"GREEN": "#2ca25f", "ORANGE": "#ff8c00", "RED": "#d7191c"}
