"""
kde_analysis.py - Kernel Density Estimation of the accident points.

Beginner explanation
--------------------
KDE puts a small smooth "hill" (a Gaussian bump) on every accident point and adds
all the hills together. Where many accidents are close, the hills pile up into a
tall peak = high density. Two settings matter:

* bandwidth - how wide each hill is (here: sigma in metres). Too small -> every
  point is its own island. Too large -> the whole corridor becomes one blob.
* cell size - the pixel size of the output raster.

All calculations use metres (EPSG:32643), never raw degrees.

Two versions are provided:
1. Planar (2-D) KDE on a raster  -> kde_density.tif  (what QGIS heat-maps show).
2. Road-network (1-D) KDE        -> density measured ALONG the road only. This is
   more honest for accidents that happen on a line (see limitation in the report).
"""
import numpy as np
import pandas as pd
import rasterio
from rasterio import features
from rasterio.transform import from_origin
from scipy.signal import find_peaks
from shapely.geometry import LineString, MultiLineString, Point, shape
from shapely.ops import linemerge, unary_union

import config

NODATA = -9999.0


def _gaussian_sum(eval_x, eval_y, px, py, h):
    """Sum of 2-D Gaussians (sigma=h) centred on each point, evaluated at (eval_x, eval_y) (1-D arrays)."""
    out = np.zeros(len(eval_x))
    two_h2 = 2.0 * h * h
    chunk = 2000
    for s in range(0, len(eval_x), chunk):
        dx = eval_x[s:s + chunk, None] - px[None, :]
        dy = eval_y[s:s + chunk, None] - py[None, :]
        out[s:s + chunk] = np.exp(-(dx * dx + dy * dy) / two_h2).sum(axis=1)
    return out / (2.0 * np.pi * h * h)           # events per square metre


def kde_at_points(points_xy, h=config.KDE_BANDWIDTH_M):
    """Exact KDE value at each accident location (events per km^2)."""
    px, py = points_xy[:, 0], points_xy[:, 1]
    return _gaussian_sum(px, py, px, py, h) * 1e6


def run_planar_kde(points_xy, study_area, h=config.KDE_BANDWIDTH_M, cell=config.KDE_CELL_SIZE_M):
    """Return dict with the density grid (events per km^2), transform, mask and sample coordinates."""
    # extend the grid by 2 bandwidths so the density tails are not cut, then clip to the study area
    minx, miny, maxx, maxy = study_area.bounds
    ncols = int(np.ceil((maxx - minx) / cell))
    nrows = int(np.ceil((maxy - miny) / cell))
    xs = minx + cell * (np.arange(ncols) + 0.5)
    ys = maxy - cell * (np.arange(nrows) + 0.5)
    gx, gy = np.meshgrid(xs, ys)
    dens = _gaussian_sum(gx.ravel(), gy.ravel(), points_xy[:, 0], points_xy[:, 1], h)
    dens = (dens * 1e6).reshape(nrows, ncols)

    transform = from_origin(minx, maxy, cell, cell)
    inside = features.geometry_mask([study_area], out_shape=(nrows, ncols), transform=transform, invert=True)
    dens = np.where(inside, dens, NODATA).astype("float32")
    return {"density": dens, "transform": transform, "mask": inside, "xs": xs, "ys": ys,
            "bandwidth": h, "cell": cell}


def save_geotiff(kde, path):
    d = kde["density"]
    with rasterio.open(path, "w", driver="GTiff", height=d.shape[0], width=d.shape[1], count=1,
                       dtype="float32", crs=config.CRS_PROJECTED, transform=kde["transform"],
                       nodata=NODATA, compress="deflate") as dst:
        dst.write(d, 1)
        dst.update_tags(1, units="events per km2", bandwidth_m=str(kde["bandwidth"]),
                        cell_m=str(kde["cell"]), note="SYNTHETIC accident events")


def high_density_zones(kde, percentile=config.KDE_HIGH_PERCENTILE, min_area=config.KDE_MIN_ZONE_AREA_M2):
    """Polygons (projected CRS) of cells at or above the chosen percentile of corridor density."""
    d = kde["density"]
    vals = d[kde["mask"]]
    threshold = float(np.percentile(vals, percentile))
    binary = (d >= threshold) & kde["mask"]
    polys = []
    for geom, val in features.shapes(binary.astype("uint8"), mask=binary, transform=kde["transform"]):
        p = shape(geom)
        if p.area >= min_area:
            polys.append(p.buffer(0))
    return polys, threshold


# ----------------------------------------------------------------------------
# Optional: road-network (1-D) KDE
# ----------------------------------------------------------------------------
def _merge_lines(geom):
    merged = linemerge(geom) if isinstance(geom, MultiLineString) else geom
    return list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]


def run_road_kde(acc_gdf, roads_by_class, h=config.KDE_BANDWIDTH_M, step=config.ROAD_KDE_STEP_M):
    """Density along each road (events per km of road) using chainage = distance along the line."""
    from pyproj import Transformer
    back = Transformer.from_crs(config.CRS_PROJECTED, config.CRS_GEOGRAPHIC, always_xy=True)
    rows, peaks = [], []
    for road, geom in roads_by_class.items():
        pts = acc_gdf[acc_gdf["Road_Name"] == road]
        if pts.empty:
            continue
        for part_id, line in enumerate(_merge_lines(geom), start=1):
            # assign every point of this road to its nearest part, then measure distance along that part
            near = [p for p in pts.geometry if line.distance(p) <= min(l.distance(p) for l in _merge_lines(geom)) + 1e-9]
            if not near:
                continue
            ch = np.array([line.project(p) for p in near])
            grid = np.arange(0, line.length + step, step)
            dens = np.exp(-((grid[:, None] - ch[None, :]) ** 2) / (2 * h * h)).sum(axis=1)
            dens = dens / (h * np.sqrt(2 * np.pi)) * 1000.0           # events per km of road
            for s, dv in zip(grid, dens):
                q = line.interpolate(min(s, line.length))
                lon, lat = back.transform(q.x, q.y)
                rows.append({"Road": road, "Part": part_id, "Chainage_m": round(float(s), 1),
                             "Density_per_km": round(float(dv), 3), "Latitude": round(lat, 6),
                             "Longitude": round(lon, 6)})
            idx, _ = find_peaks(dens, prominence=max(dens.max() * config.ROAD_KDE_PEAK_PROMINENCE, 1e-9))
            for k in idx:
                q = line.interpolate(min(grid[k], line.length))
                lon, lat = back.transform(q.x, q.y)
                peaks.append({"Road": road, "Chainage_m": round(float(grid[k]), 1),
                              "Density_per_km": round(float(dens[k]), 1),
                              "Latitude": round(lat, 6), "Longitude": round(lon, 6)})
    return pd.DataFrame(rows), pd.DataFrame(peaks)


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def write_kde_report(path, kde, threshold, zones, n_points, geometry_is_reference, peaks_df, density_at_points):
    d = kde["density"][kde["mask"]]
    h, cell = kde["bandwidth"], kde["cell"]
    L = []
    add = L.append
    add("KDE ANALYSIS REPORT")
    add("=" * 70)
    add("Input: SYNTHETIC accident events. Density shows where the synthetic generator placed events,")
    add("not where real accidents are likely to occur.")
    if geometry_is_reference:
        add("The road geometry is a simplified reference line, so density is relative to that line only.")
    add("")
    add("PARAMETERS")
    add(f"  CRS: {config.CRS_PROJECTED} (metres) | points used: {n_points}")
    add(f"  Kernel: isotropic Gaussian, bandwidth (sigma) = {h:g} m, evaluated exactly at every cell centre")
    add(f"  Cell size: {cell:g} m | grid: {kde['density'].shape[1]} x {kde['density'].shape[0]} cells")
    add(f"  Clipped to: road geometry buffered by {config.CORRIDOR_BUFFER_M:g} m")
    add("  Units: events per km^2 (sum of Gaussians, not divided by the number of points)")
    add("")
    add("WHY THESE VALUES FOR THIS CORRIDOR")
    add(f"  * The corridor is narrow (about {2 * config.CORRIDOR_BUFFER_M:g} m wide) and spans a short urban route.")
    add(f"  * A bandwidth of {h:g} m is wide enough to join neighbouring points of one concentration but much")
    add("    smaller than the spacing between concentrations (a few hundred metres), so separate")
    add("    concentrations stay separate peaks. Several hundred metres would blur them into one ridge.")
    add(f"  * A cell size of {cell:g} m is about 1/8 of the bandwidth. A common rule is cell <= bandwidth/4,")
    add("    which keeps the surface smooth without needless file size.")
    add("  * Change KDE_BANDWIDTH_M / KDE_CELL_SIZE_M in config.py and compare. A short sensitivity")
    add("    test (30, 40, 60, 100 m) is the usual way to justify a final value in a report.")
    add("")
    add("RESULTS")
    add(f"  Density min / mean / max over corridor: {d.min():.1f} / {d.mean():.1f} / {d.max():.1f} events per km^2")
    add(f"  High-density threshold: {config.KDE_HIGH_PERCENTILE}th percentile of corridor cells = {threshold:.1f} events per km^2")
    add(f"  High-density zones (area >= {config.KDE_MIN_ZONE_AREA_M2:g} m^2): {len(zones)}")
    for i, z in enumerate(zones, 1):
        add(f"    zone {i}: area {z.area:.0f} m^2")
    add(f"  KDE value at accident locations: min {density_at_points.min():.1f}, mean {density_at_points.mean():.1f}, max {density_at_points.max():.1f}")
    add("")
    add("ROAD-NETWORK (1-D) KDE - PEAKS ALONG THE ROAD")
    if peaks_df is None or peaks_df.empty:
        add("  No prominent peaks found.")
    else:
        add(peaks_df.to_string(index=False))
    add("  Full profile: outputs/road_network_kde_profile.csv (events per km of road vs. chainage).")
    add("")
    add("LIMITATIONS")
    add("  * Planar KDE spreads density in all directions, including to ground that is not road.")
    add("    Accidents happen on a line, so planar density slightly overstates the area affected and can")
    add("    blend points of the NH 66 and the access road where they come within one bandwidth of each")
    add("    other (near their junction).")
    add("  * The 1-D road-network KDE measures density along the road only and avoids that problem, but")
    add("    it ignores how the two roads connect (no density is shared across the junction) and has no")
    add("    boundary correction at the road ends, so peaks within one bandwidth of an end are under-estimated.")
    add("  * Bandwidth choice changes the result; there is no single 'correct' value.")
    path.write_text("\n".join(L), encoding="utf-8")
