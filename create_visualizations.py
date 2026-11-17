"""Generate interactive and static visualizations from existing analysis outputs."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from visualization import configure_proj_data

# Configure the bundled PROJ database before importing GeoPandas/Rasterio.
configure_proj_data()

from pyproj import CRS

from visualization.charts import create_charts, create_static_maps
from visualization.dashboard import create_dashboard
from visualization.osm_map import create_osm_map
from visualization.validation import (
    VISUALIZATION_OUTPUTS,
    inventory_text,
    load_project_data,
    test_report,
    verify_outputs,
)
import config


def main(
    root: Path | None = None,
    analysis_outputs: Path | None = None,
    visualization_outputs: Path | None = None,
    expect_road_aligned: bool = False,
) -> int:
    started = time.time()
    root = root or Path(__file__).resolve().parent
    is_workspace_root = root.resolve() == config.BASE_DIR
    if is_workspace_root and config.ROAD_GEOJSON == root / "data" / "nh66_road_aligned_corridor.geojson":
        expect_road_aligned = True
    analysis_outputs = analysis_outputs or (
        config.OUTPUT_DIR if is_workspace_root else root / "outputs"
    )
    visualization_outputs = visualization_outputs or (
        analysis_outputs.parent / "visualization"
        if analysis_outputs.name == "analysis"
        else analysis_outputs / "visualization"
    )
    data = load_project_data(root, analysis_outputs, visualization_outputs, expect_road_aligned)
    data.visualization_outputs.mkdir(parents=True, exist_ok=True)
    accident_layer = data.layers["validated_accidents"].to_crs("EPSG:4326").copy()
    for column in accident_layer.columns:
        if column != accident_layer.geometry.name:
            accident_layer[column] = accident_layer[column].map(
                lambda value: value.isoformat() if hasattr(value, "isoformat") else value
            )
    accident_features = json.loads(accident_layer.to_json())
    accident_script = root / "web" / "js" / "accident-points.js"
    accident_script.write_text(
        "window.TrafficAccidentPoints = "
        + json.dumps(accident_features, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    if expect_road_aligned:
        cluster_features = json.loads(
            data.layers["dbscan_clusters"].to_crs("EPSG:4326").to_json()
        )
        cluster_script = root / "web" / "js" / "dbscan-clusters.js"
        cluster_script.write_text(
            "window.TrafficDBSCANClusters = "
            + json.dumps(cluster_features, separators=(",", ":"))
            + ";\n",
            encoding="utf-8",
        )
    (data.visualization_outputs / "visualization_inventory.txt").write_text(
        inventory_text(data), encoding="utf-8"
    )
    print("Loaded and checked existing GeoJSON, reports, and KDE raster.")
    print(f"Records: {len(data.layers['validated_accidents'])} | "
          f"DBSCAN cluster polygons: {len(data.layers['dbscan_clusters'])} | "
          f"KDE zones: {data.kde_zone_count} | "
          f"candidate hotspots: {len(data.layers['hotspot_points'])}")
    print(f"PROJ database: {Path(__import__('os').environ['PROJ_DATA']) / 'proj.db'}")
    print(f"CRS checks: {CRS.from_epsg(4326).name}; {CRS.from_epsg(32643).name}")

    create_osm_map(data, data.visualization_outputs / "hotspots_on_osm.html")
    create_static_maps(data)
    create_charts(data)
    create_dashboard(data, data.visualization_outputs / "traffic_hotspot_dashboard.html")

    problems = verify_outputs(data)
    report = test_report(data, problems)
    (data.visualization_outputs / "visualization_test_report.txt").write_text(report, encoding="utf-8")
    summary = [
        "VISUALIZATION GENERATION SUMMARY",
        "=" * 68,
        f"Result: {'PASS' if not problems else 'FAIL'}",
        f"Input accidents: {len(data.layers['validated_accidents'])} synthetic records",
        f"KDE high-density zones (source report): {data.kde_zone_count}",
        f"DBSCAN cluster geometries: {len(data.layers['dbscan_clusters'])}",
        f"Candidate hotspots: {len(data.layers['hotspot_points'])}",
        f"Pre-junction NH 66 hotspots: {int(data.layers['hotspot_points'].get('Primary_Road', []).astype(str).str.strip().eq('NH 66').sum())}",
        f"NH 66 synthetic points within 5 m of mapped road: "
        f"{data.road_offset_m['within_5m']}/{data.road_offset_m['records']}",
        f"DUK synthetic points within 5 m of mapped road: "
        f"{data.duk_offset_m['within_5m']}/{data.duk_offset_m['records']}",
        f"Elapsed: {time.time() - started:.1f} seconds",
        f"Outputs: {data.visualization_outputs}",
        "",
        "All accident records are synthetic; output classes are not real-world risk assessments.",
        "Mapped roads combines the OSM-mapped NH 66 and DUK access road.",
        (
            "This is a separate road-aligned synthetic copy starting at Mangalapuram; original input data and analysis outputs were preserved."
            if data.expect_road_aligned
            else "Original synthetic event coordinates and analytical outputs were not moved or rerun."
        ),
    ]
    if problems:
        summary.extend(["", "Validation failures:"] + [f"- {problem}" for problem in problems])
    (data.visualization_outputs / "visualization_summary.txt").write_text(
        "\n".join(summary) + "\n", encoding="utf-8"
    )
    for name in ("visualization_test_report.txt", "visualization_summary.txt"):
        output = data.visualization_outputs / name
        if not output.is_file() or output.stat().st_size == 0:
            problems.append(f"Missing or empty output: {name}")
    for name in VISUALIZATION_OUTPUTS:
        output = data.visualization_outputs / name
        print(f"[{'OK' if output.is_file() and output.stat().st_size else 'MISSING'}] {output}")
    print(report)
    return 0 if not problems else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"VISUALIZATION ERROR: {exc}", file=sys.stderr)
        raise
