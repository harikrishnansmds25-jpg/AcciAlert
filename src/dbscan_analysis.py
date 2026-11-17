"""
dbscan_analysis.py - groups nearby accident points with DBSCAN.

Beginner explanation
--------------------
DBSCAN looks at every point and asks: "do at least `min_samples` points (itself
included) lie within `eps` metres of me?" If yes the point is a "core" point and
it, together with everything reachable from it, forms a cluster. Points that
belong to no cluster are "noise" (isolated events).

* larger eps         -> clusters grow and merge; fewer clusters, less noise
* smaller eps        -> clusters split; more clusters, more noise
* larger min_samples -> a group must be denser to count; more noise

KDE answers "where is density high?"; DBSCAN answers "which points belong together?".
They are complementary, so the next module uses both.
"""
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import MultiPoint
from sklearn.cluster import DBSCAN

import config


def run_dbscan(points_xy, eps=config.DBSCAN_EPS_M, min_samples=config.DBSCAN_MIN_SAMPLES):
    """Cluster IDs start at 1; noise = -1. Input coordinates must be in metres."""
    labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(points_xy)
    return np.where(labels >= 0, labels + 1, -1)


def sensitivity_table(points_xy, eps_list=config.DBSCAN_SENSITIVITY_EPS,
                      min_list=config.DBSCAN_SENSITIVITY_MIN_SAMPLES):
    rows = []
    for eps in eps_list:
        for ms in min_list:
            lab = run_dbscan(points_xy, eps, ms)
            sizes = pd.Series(lab[lab > 0]).value_counts()
            rows.append({"eps_m": eps, "min_samples": ms, "clusters": int((lab > 0).any() and lab.max()),
                         "noise_points": int((lab == -1).sum()),
                         "largest_cluster": int(sizes.max()) if len(sizes) else 0})
    return pd.DataFrame(rows)


def cluster_statistics(acc_gdf):
    """One row per cluster (needs columns Cluster_ID, Severity, deaths_num, injuries_num, Road_Name)."""
    back = Transformer.from_crs(config.CRS_PROJECTED, config.CRS_GEOGRAPHIC, always_xy=True)
    rows = []
    for cid, grp in acc_gdf[acc_gdf["Cluster_ID"] > 0].groupby("Cluster_ID"):
        cx, cy = grp.geometry.x.mean(), grp.geometry.y.mean()
        lon, lat = back.transform(cx, cy)
        sev = grp["Severity"].value_counts()
        rows.append({
            "Cluster_ID": int(cid), "Accident_Count": len(grp),
            "Fatal_Count": int(sev.get("Fatal", 0)), "Major_Count": int(sev.get("Major", 0)),
            "Minor_Count": int(sev.get("Minor", 0)),
            "Total_Deaths": int(grp["deaths_num"].sum()), "Total_Injuries": int(grp["injuries_num"].sum()),
            "Centroid_X_m": round(cx, 2), "Centroid_Y_m": round(cy, 2),
            "Centroid_Lat": round(lat, 6), "Centroid_Lon": round(lon, 6),
            "Primary_Road": grp["Road_Name"].mode().iat[0],
        })
    return pd.DataFrame(rows)


def cluster_polygons(acc_gdf, buffer_m=config.CLUSTER_POLYGON_BUFFER_M):
    """
    Boundary of each cluster = convex hull of its points, widened by `buffer_m`.
    Why: accident points lie along a road, so their hull is nearly a line with no area;
    the buffer gives it a width of a typical road + shoulder. It is not an arbitrary circle -
    the shape follows the cluster's own extent.
    """
    polys = {}
    for cid, grp in acc_gdf[acc_gdf["Cluster_ID"] > 0].groupby("Cluster_ID"):
        hull = MultiPoint(list(grp.geometry)).convex_hull
        polys[int(cid)] = hull.buffer(buffer_m)
    return polys


def write_dbscan_report(path, eps, min_samples, labels, stats, sens, geometry_is_reference):
    n_clusters = int((labels > 0).any() and labels.max())
    L = []
    add = L.append
    add("DBSCAN ANALYSIS REPORT")
    add("=" * 70)
    add("Input: SYNTHETIC accident events. Clusters describe the synthetic data only.")
    if geometry_is_reference:
        add("The road geometry is a simplified reference line (see data_validation_report.txt).")
    add("")
    add("PARAMETERS")
    add(f"  eps = {eps:g} m | min_samples = {min_samples} | coordinates: {config.CRS_PROJECTED} (metres)")
    add("  The prototype suggestion eps=100 m, min_samples=4 is included in the sensitivity table below;")
    add("  the default here is smaller because the corridor is short and concentrations are a few hundred")
    add("  metres apart - a large eps merges neighbouring concentrations into one cluster.")
    add("")
    add("RESULT")
    add(f"  Records clustered: {len(labels)} | clusters: {n_clusters} | noise points: {int((labels == -1).sum())}")
    add("")
    add(stats.drop(columns=["Centroid_X_m", "Centroid_Y_m"]).to_string(index=False))
    add("")
    add("SENSITIVITY OF THE RESULT TO eps AND min_samples")
    add(sens.to_string(index=False))
    add("")
    add("HOW TO READ THE TABLE")
    add("  * Increasing eps at fixed min_samples merges clusters (cluster count falls, noise falls).")
    add("  * Increasing min_samples at fixed eps removes small groups into noise (noise rises).")
    add("  * Pick the smallest eps that keeps each visible concentration whole, and check that")
    add("    the cluster count is stable for neighbouring values - an unstable count means the")
    add("    result depends too much on the exact setting.")
    add("  * A k-distance plot (distance to the k-th nearest neighbour, sorted) is the standard way")
    add("    to choose eps; the 'knee' of the curve is a good starting value.")
    add("")
    add("LIMITATION")
    add("  DBSCAN uses straight-line (Euclidean) distance, not distance along the road. Points on")
    add("  different roads that are close in a straight line may be clustered together.")
    path.write_text("\n".join(L), encoding="utf-8")
