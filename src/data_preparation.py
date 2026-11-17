"""
data_preparation.py - reads the two input files and converts them to the
projected CRS.

Beginner explanation
--------------------
Latitude/longitude are angles (degrees). One degree of longitude is not the same
length as one degree of latitude, so distances computed on raw degrees are wrong.
We therefore convert everything to UTM zone 43N (EPSG:32643) where one unit = one
metre, do all measurements there, and convert back to lat/lon only when exporting.

This module NEVER edits the original CSV on disk and never moves any point.
"""
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from shapely.ops import unary_union

import config


class InputError(Exception):
    """Raised when an input file is missing or unusable (message says how to fix it)."""


def load_accidents_csv(path=config.ACCIDENT_CSV):
    """Read the CSV as text so that nothing is silently converted or 'fixed'."""
    path = Path(path)
    if not path.exists():
        raise InputError(f"Accident CSV not found: {path}\n"
                         "Put the file in the data/ folder or edit ACCIDENT_CSV in config.py.")
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception as exc:  # unreadable / malformed CSV
        raise InputError(f"Could not read {path}: {exc}") from exc
    df.columns = [c.strip() for c in df.columns]
    return df


def load_roads_geojson(path=config.ROAD_GEOJSON):
    """Read the road GeoJSON; return it in EPSG:4326 (as supplied) with empty geometries removed."""
    path = Path(path)
    if not path.exists():
        raise InputError(f"Road GeoJSON not found: {path}\n"
                         "Put the file in the data/ folder or edit ROAD_GEOJSON in config.py.")
    try:
        roads = gpd.read_file(path)
    except Exception as exc:
        raise InputError(f"Could not read {path}: {exc}") from exc
    if roads.crs is None:
        roads = roads.set_crs(config.CRS_GEOGRAPHIC)   # GeoJSON standard is WGS84
    roads = roads.to_crs(config.CRS_GEOGRAPHIC)
    roads = roads[roads.geometry.notna() & ~roads.geometry.is_empty].copy()
    if roads.empty:
        raise InputError("The road GeoJSON contains no usable geometries.")
    return roads.reset_index(drop=True)


def road_class_column(roads):
    """Return the GeoJSON property that names each road (road_class, Road_Name or name)."""
    for col in ("road_class", "Road_Name", "name"):
        if col in roads.columns:
            return col
    return None


def roads_by_class(roads_proj):
    """Dict {road class: merged projected geometry}. Used to measure distance per road."""
    col = road_class_column(roads_proj)
    if col is None:
        return {"ALL": unary_union(roads_proj.geometry.values)}
    out = {}
    for name, grp in roads_proj.groupby(col):
        out[str(name)] = unary_union(grp.geometry.values)
    return out


def study_area_polygon(roads_proj, buffer_m=config.CORRIDOR_BUFFER_M):
    """Study-area boundary = all road geometry buffered by `buffer_m` metres."""
    return unary_union(roads_proj.geometry.values).buffer(buffer_m)


def accidents_to_gdf(df, crs_projected=config.CRS_PROJECTED):
    """Turn rows that have numeric lat/lon (columns lat_num / lon_num) into a projected GeoDataFrame."""
    gdf = gpd.GeoDataFrame(
        df.copy(),
        geometry=[Point(lon, lat) for lon, lat in zip(df["lon_num"], df["lat_num"])],
        crs=config.CRS_GEOGRAPHIC,
    )
    return gdf.to_crs(crs_projected)
