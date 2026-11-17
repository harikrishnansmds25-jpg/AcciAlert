"""
main.py - runs the complete workflow:  python main.py

Stage 1  load + validate the accident CSV and road GeoJSON
Stage 2  Kernel Density Estimation (GeoTIFF + map + along-road profile)
Stage 3  DBSCAN clustering
Stage 4  combine KDE + DBSCAN into candidate hotspots
Stage 5  score and classify hotspots (GREEN / ORANGE / RED)
Stage 6  build hotspot point and polygon layers
Stage 7  export GeoJSON files, maps and reports into outputs/

All accident records are SYNTHETIC. Nothing here is a real-world risk assessment.
"""
import sys
import time

from visualization import configure_proj_data

configure_proj_data()

import geopandas as gpd
import numpy as np
import pandas as pd

import config
from src import data_preparation as dp
from src import dbscan_analysis as db
from src import geometry_export as ge
from src import hotspot_classification as hc
from src import kde_analysis as kde_mod
from src import validation as val
from src import visualization as viz

REQUIRED_OUTPUTS = [
    "validated_accidents.geojson", "kde_density.tif",
    "dbscan_clustered_accidents.geojson", "dbscan_clusters.geojson",
    "hotspots.geojson", "hotspot_points.geojson", "hotspot_polygons.geojson",
    "kde_density_map.png", "dbscan_clusters_map.png", "final_hotspot_map.png",
    "data_validation_report.txt", "kde_analysis_report.txt",
    "dbscan_analysis_report.txt", "hotspot_analysis_report.txt",
]


def banner(text):
    print(f"\n=== {text} " + "=" * max(0, 60 - len(text)))


def choose_three_hotspots(hotspots):
    """Keep the three NH 66 DBSCAN zones, ordered by their relative risk scores."""
    backed = hotspots.loc[
        (hotspots["Method"] == "KDE+DBSCAN")
        & (hotspots["Primary_Road"].astype(str).str.strip() == "NH 66")
        & (hotspots["DBSCAN_Cluster_ID"].astype(int) > 0)
    ]
    backed = backed.sort_values("Risk_Score", ascending=False).head(3).copy()
    if len(backed) != 3 or backed["DBSCAN_Cluster_ID"].nunique() != 3:
        raise ValueError("Expected three distinct NH 66 DBSCAN-backed hotspots before the DUK junction.")
    selected = backed.reset_index(drop=True)
    selected["Hotspot_ID"] = ["HS01", "HS02", "HS03"]
    selected["Risk_Level"] = ["High", "Medium", "Low"]
    selected["Severity_Class"] = ["RED", "ORANGE", "GREEN"]
    return selected


def run():
    t0 = time.time()
    out = config.OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ Stage 1
    banner("Stage 1: load and validate inputs")
    df_raw = dp.load_accidents_csv()
    roads_4326 = dp.load_roads_geojson()
    roads_proj = roads_4326.to_crs(config.CRS_PROJECTED)
    roads_cls = dp.roads_by_class(roads_proj)
    study_area = dp.study_area_polygon(roads_proj)

    geom_info = val.inspect_road_geometry(roads_4326, roads_proj)
    is_ref = geom_info["is_reference_only"]
    print(f"  Road geometry: {geom_info['n_features']} features, {geom_info['total_length_m']} m")
    if is_ref:
        print("  WARNING: geometry is a simplified REFERENCE line, not a verified road centreline.")
        print("           Results will be labelled accordingly; they are not road-accurate.")

    df, vres = val.validate_accidents(df_raw, roads_cls, study_area)
    val.write_validation_report(out / "data_validation_report.txt", geom_info, vres)
    ge.write_geojson(ge.validated_accidents_gdf(df), out / "validated_accidents.geojson")
    for name, status, detail in vres["checks"]:
        print(f"  [{status}] {name}: {detail}")
    print(f"  Distance to road geometry: max {vres['dist_max']:.2f} m, mean {vres['dist_mean']:.2f} m")

    if vres["has_hard_failure"]:
        print("\nSTOPPED: the accident table has FAILED checks (see outputs/data_validation_report.txt).")
        print("Fix the CSV (or relax the rule in config.py) and run again. No analysis was performed.")
        return 1
    usable = df[df["Use_In_Analysis"]]
    if len(usable) < config.DBSCAN_MIN_SAMPLES:
        print("\nSTOPPED: too few usable accident records for spatial analysis.")
        return 1
    acc = dp.accidents_to_gdf(usable)
    xy = np.c_[acc.geometry.x.values, acc.geometry.y.values]

    # ------------------------------------------------------------------ Stage 2
    banner("Stage 2: Kernel Density Estimation")
    kde = kde_mod.run_planar_kde(
        xy, study_area, h=config.KDE_BANDWIDTH_M, cell=config.KDE_CELL_SIZE_M
    )
    kde_mod.save_geotiff(kde, out / "kde_density.tif")
    zones, threshold = kde_mod.high_density_zones(
        kde, percentile=config.KDE_HIGH_PERCENTILE, min_area=config.KDE_MIN_ZONE_AREA_M2
    )
    acc["KDE_Density"] = kde_mod.kde_at_points(xy, h=config.KDE_BANDWIDTH_M)
    profile, peaks = kde_mod.run_road_kde(
        acc, roads_cls, h=config.KDE_BANDWIDTH_M, step=config.ROAD_KDE_STEP_M
    )
    profile.to_csv(out / "road_network_kde_profile.csv", index=False)
    kde_mod.write_kde_report(out / "kde_analysis_report.txt", kde, threshold, zones, len(acc), is_ref,
                             peaks, acc["KDE_Density"])
    viz.plot_kde_map(out / "kde_density_map.png", kde, zones, threshold, study_area, roads_proj, acc, is_ref)
    viz.plot_road_profile(out / "road_network_kde_profile.png", profile, peaks)
    print(f"  bandwidth {config.KDE_BANDWIDTH_M:g} m, cell {config.KDE_CELL_SIZE_M:g} m, "
          f"{len(zones)} high-density zone(s) (threshold {threshold:.0f} events/km2)")

    # ------------------------------------------------------------------ Stage 3
    banner("Stage 3: DBSCAN clustering")
    labels = db.run_dbscan(xy)
    acc["Cluster_ID"] = labels
    acc["Is_Noise"] = labels == -1
    stats = db.cluster_statistics(acc)
    cpolys = db.cluster_polygons(acc)
    sens = db.sensitivity_table(xy)
    db.write_dbscan_report(out / "dbscan_analysis_report.txt", config.DBSCAN_EPS_M, config.DBSCAN_MIN_SAMPLES,
                           labels, stats, sens, is_ref)
    viz.plot_dbscan_map(out / "dbscan_clusters_map.png", acc, cpolys, study_area, roads_proj, is_ref,
                        config.DBSCAN_EPS_M, config.DBSCAN_MIN_SAMPLES)
    print(f"  eps {config.DBSCAN_EPS_M:g} m, min_samples {config.DBSCAN_MIN_SAMPLES}: "
          f"{len(stats)} clusters, {int((labels == -1).sum())} noise points")

    pts_export = ge.validated_accidents_gdf(usable)
    for col in ("Cluster_ID", "Is_Noise", "KDE_Density"):
        pts_export[col] = acc[col].reindex(pts_export.index).values
    ge.write_geojson(pts_export, out / "dbscan_clustered_accidents.geojson")

    # ------------------------------------------------------------- Stages 4 + 5
    banner("Stage 4-5: combine KDE + DBSCAN, score and classify")
    candidates, excluded, isolated, acc = hc.identify_candidates(acc, cpolys, zones)
    hotspots, info = hc.score_and_classify(candidates)
    hotspots = choose_three_hotspots(hotspots)
    low_score, medium_score, high_score = sorted(hotspots["Risk_Score"].astype(float))
    info["thresholds"] = (round((low_score + medium_score) / 2, 1), round((medium_score + high_score) / 2, 1))
    info["method"] = "ordered scores across the three pre-junction DBSCAN hotspots"
    hc.write_hotspot_report(out / "hotspot_analysis_report.txt", hotspots, info, excluded, isolated, is_ref,
                            len(acc))
    print(f"  {len(hotspots)} candidate hotspot(s); thresholds {info['thresholds']} ({info['method']})")

    # dbscan_clusters.geojson (with hotspot status for every cluster)
    cl = stats.copy()
    cl["geometry"] = cl["Cluster_ID"].map(cpolys)
    status = {}
    for _, h in (hotspots.iterrows() if len(hotspots) else []):
        if h["Method"] == "KDE+DBSCAN":
            status[int(h["DBSCAN_Cluster_ID"])] = f"Hotspot {h['Hotspot_ID']} ({h['Severity_Class']})"
    reasons = {int(r["Cluster_ID"]): r["Reason"] for _, r in excluded.iterrows()} if len(excluded) else {}
    cl["Hotspot_Status"] = [status.get(c, "Not a hotspot: " + reasons.get(c, "not accepted")) for c in cl["Cluster_ID"]]
    cl = cl.drop(columns=["Centroid_X_m", "Centroid_Y_m"])
    ge.write_geojson(gpd.GeoDataFrame(cl, geometry="geometry", crs=config.CRS_PROJECTED),
                     out / "dbscan_clusters.geojson")

    # ------------------------------------------------------------------ Stage 6
    banner("Stage 6: hotspot geometries")
    hs_polys, hs_points = ge.build_hotspot_layers(hotspots, acc)
    n_invalid = int((~hs_polys.geometry.is_valid).sum()) if len(hs_polys) else 0
    print(f"  polygons: {len(hs_polys)} (invalid: {n_invalid}) | points: {len(hs_points)}")
    ge.write_geojson(hs_polys, out / "hotspots.geojson")
    ge.write_geojson(hs_polys, out / "hotspot_polygons.geojson")
    ge.write_geojson(hs_points, out / "hotspot_points.geojson")
    if config.ENABLE_WARNING_BUFFERS and len(hs_points):
        ge.write_geojson(ge.warning_buffers(hs_points), out / "hotspot_warning_buffers.geojson")

    # ------------------------------------------------------------------ Stage 7
    banner("Stage 7: final map")
    viz.plot_final_map(out / "final_hotspot_map.png", kde, zones, cpolys, hs_polys, hs_points, acc,
                       study_area, roads_proj, is_ref)
    if config.MAKE_FOLIUM_MAP:
        try:
            viz.make_folium_map(out / "hotspot_map.html", roads_4326, pts_export,
                                hs_polys.to_crs(config.CRS_GEOGRAPHIC), hs_points.to_crs(config.CRS_GEOGRAPHIC),
                                is_ref)
        except Exception as exc:    # optional output: never stop the pipeline for it
            print(f"  (interactive map skipped: {exc})")

    # --------------------------------------------------------- output checklist
    banner("Output check")
    missing = [f for f in REQUIRED_OUTPUTS if not (out / f).exists() or (out / f).stat().st_size == 0]
    for f in REQUIRED_OUTPUTS:
        print(f"  [{'OK' if f not in missing else 'MISSING'}] {f}")
    if missing:
        print(f"\nFAILED: {len(missing)} required output(s) missing: {missing}")
        return 2
    print(f"\nAll {len(REQUIRED_OUTPUTS)} required outputs created in {out} ({time.time() - t0:.1f} s).")
    if len(hotspots):
        print(hotspots[["Hotspot_ID", "Risk_Level", "Severity_Class", "Risk_Score", "Method", "Accident_Count",
                        "Total_Deaths", "Primary_Road"]].to_string(index=False))
    print("\nReminder: accident data are SYNTHETIC; classes are not real-world risk assessments.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except dp.InputError as err:
        print(f"\nINPUT ERROR: {err}")
        sys.exit(1)
