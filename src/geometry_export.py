"""
geometry_export.py - turns the scored hotspots into point/polygon layers and writes GeoJSON.

Beginner explanation
--------------------
* Hotspot point   = the average position (centroid) of the accident points in the hotspot.
* Hotspot polygon = the boundary found by the analysis (DBSCAN hull + buffer, or the KDE
                    high-density zone) - not an arbitrary circle.
* All GeoJSON files are written in EPSG:4326 (lat/lon) so QGIS and Leaflet read them directly.
  All geometry processing happens before that in metres (EPSG:32643).
"""
import json

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import make_valid
from shapely.geometry import MultiPolygon, Point, Polygon

import config

POINT_FIELDS = ["Hotspot_ID", "Latitude", "Longitude", "Severity_Class", "Accident_Count", "Minor_Count",
                "Major_Count", "Fatal_Count", "Total_Deaths", "Total_Injuries", "KDE_Density",
                "DBSCAN_Cluster_ID", "Risk_Score", "Risk_Level", "Detection_Method", "Primary_Road"]


def _polygonal(geom):
    """Make a geometry valid and keep only its polygon parts."""
    g = make_valid(geom) if not geom.is_valid else geom
    if isinstance(g, (Polygon, MultiPolygon)):
        return g
    parts = [p for p in getattr(g, "geoms", []) if isinstance(p, (Polygon, MultiPolygon))]
    if not parts:
        return g.buffer(config.CLUSTER_POLYGON_BUFFER_M)
    out = parts[0]
    for p in parts[1:]:
        out = out.union(p)
    return out


def build_hotspot_layers(hotspots, acc_proj):
    """Return (polygons GeoDataFrame, points GeoDataFrame), both in the projected CRS."""
    if len(hotspots) == 0:
        empty = gpd.GeoDataFrame(columns=POINT_FIELDS + ["geometry"], geometry="geometry",
                                 crs=config.CRS_PROJECTED)
        return empty, empty.copy()
    from pyproj import Transformer
    back = Transformer.from_crs(config.CRS_PROJECTED, config.CRS_GEOGRAPHIC, always_xy=True)
    recs_poly, recs_pt, pts = [], [], []
    for _, h in hotspots.iterrows():
        sub = acc_proj.loc[h["point_index"]]
        cx, cy = sub.geometry.x.mean(), sub.geometry.y.mean()
        lon, lat = back.transform(cx, cy)
        rec = {
            "Hotspot_ID": h["Hotspot_ID"], "Latitude": round(lat, 6), "Longitude": round(lon, 6),
            "Severity_Class": h["Severity_Class"], "Accident_Count": int(h["Accident_Count"]),
            "Minor_Count": int(h["Minor_Count"]), "Major_Count": int(h["Major_Count"]),
            "Fatal_Count": int(h["Fatal_Count"]), "Total_Deaths": int(h["Total_Deaths"]),
            "Total_Injuries": int(h["Total_Injuries"]), "KDE_Density": float(h["KDE_Density"]),
            "DBSCAN_Cluster_ID": int(h["DBSCAN_Cluster_ID"]), "Risk_Score": float(h["Risk_Score"]),
            "Risk_Level": h["Risk_Level"],
            "Detection_Method": h["Method"], "Primary_Road": h["Primary_Road"],
        }
        recs_poly.append({**rec, "geometry": _polygonal(h["geometry"])})
        recs_pt.append(rec)
        pts.append(Point(cx, cy))
    polys = gpd.GeoDataFrame(recs_poly, geometry="geometry", crs=config.CRS_PROJECTED)
    points = gpd.GeoDataFrame(recs_pt, geometry=pts, crs=config.CRS_PROJECTED)
    return polys, points


def warning_buffers(points_proj):
    """Optional buffers around hotspot points (radii from config.WARNING_BUFFER_M)."""
    g = points_proj.copy()
    g["Buffer_m"] = g["Severity_Class"].map(config.WARNING_BUFFER_M)
    g["geometry"] = [p.buffer(r) for p, r in zip(g.geometry, g["Buffer_m"])]
    return g


def validated_accidents_gdf(df):
    """All CSV rows with their ORIGINAL 10 values (numbers typed) + validation columns, in EPSG:4326."""
    out = pd.DataFrame({c: df[c] for c in config.REQUIRED_COLUMNS})
    for col, num in (("Latitude", "lat_num"), ("Longitude", "lon_num"),
                     ("Deaths", "deaths_num"), ("Injuries", "injuries_num")):
        out[col] = df[num].where(df[num].notna(), df[col])
        if col in ("Deaths", "Injuries"):
            out[col] = out[col].apply(lambda v: int(v) if isinstance(v, (int, float, np.integer, np.floating)) and v == v else v)
    for col in ("Road_Distance_m", "Within_Road_Tolerance", "Inside_Study_Area", "Issue_Flags", "Use_In_Analysis"):
        out[col] = df[col]
    geoms = [Point(lo, la) if (la == la and lo == lo) else None for la, lo in zip(df["lat_num"], df["lon_num"])]
    return gpd.GeoDataFrame(out, geometry=geoms, crs=config.CRS_GEOGRAPHIC)


def write_geojson(gdf, path):
    """Write any GeoDataFrame as GeoJSON in EPSG:4326 (converts from the projected CRS if needed)."""
    g = gdf.copy()
    if g.crs is None:
        g = g.set_crs(config.CRS_GEOGRAPHIC)
    g = g.to_crs(config.CRS_GEOGRAPHIC)
    for col in g.columns:                     # GeoJSON cannot store lists / numpy objects
        if col != "geometry" and g[col].apply(lambda v: isinstance(v, (list, dict, set, tuple))).any():
            g = g.drop(columns=col)
    if path.exists():
        path.unlink()
    g.to_file(path, driver="GeoJSON")
    json.loads(path.read_text(encoding="utf-8"))   # sanity check: file is valid JSON
    return path
