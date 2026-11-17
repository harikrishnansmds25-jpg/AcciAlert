"""Input inventory, GIS loading, and output validation for visualization generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import CRS, Transformer
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.geometry import Point

import config


REQUIRED_INPUTS = (
    "validated_accidents.geojson",
    "dbscan_clustered_accidents.geojson",
    "dbscan_clusters.geojson",
    "hotspots.geojson",
    "hotspot_points.geojson",
    "hotspot_polygons.geojson",
    "kde_density.tif",
    "data_validation_report.txt",
    "kde_analysis_report.txt",
    "dbscan_analysis_report.txt",
    "hotspot_analysis_report.txt",
)

VISUALIZATION_OUTPUTS = (
    "hotspots_on_osm.html",
    "traffic_hotspot_dashboard.html",
    "visualization_inventory.txt",
    "kde_density_visualization.png",
    "dbscan_visualization.png",
    "final_hotspot_visualization.png",
    "severity_distribution.png",
    "hotspot_accident_counts.png",
    "hotspot_risk_comparison.png",
    "kde_dbscan_comparison.png",
)


@dataclass
class ProjectData:
    root: Path
    outputs: Path
    visualization_outputs: Path
    layers: dict[str, gpd.GeoDataFrame]
    raster_path: Path
    reports: dict[str, str]
    kde_zone_count: int
    roads_reference: gpd.GeoDataFrame
    roads_osm: gpd.GeoDataFrame
    roads_duk: gpd.GeoDataFrame
    road_offset_m: dict[str, float | int]
    duk_offset_m: dict[str, float | int]
    expect_road_aligned: bool


def load_project_data(
    root: Path,
    outputs_path: Path | None = None,
    visualization_outputs_path: Path | None = None,
    expect_road_aligned: bool = False,
) -> ProjectData:
    default_outputs = (
        config.OUTPUT_DIR if root.resolve() == config.BASE_DIR else root / "outputs"
    )
    outputs = outputs_path or default_outputs
    visualization_outputs = visualization_outputs_path or outputs / "visualization"
    missing = [name for name in REQUIRED_INPUTS if not (outputs / name).is_file()]
    if missing:
        raise FileNotFoundError("Required analytical inputs are missing from outputs/: " + ", ".join(missing))

    geojson_names = [name for name in REQUIRED_INPUTS if name.endswith(".geojson")]
    layers = {Path(name).stem: gpd.read_file(outputs / name) for name in geojson_names}
    for name, layer in layers.items():
        if layer.crs is None:
            raise ValueError(f"{name}.geojson has no CRS; refusing to guess its coordinate reference system.")
        if layer.geometry.isna().any() or layer.geometry.is_empty.any():
            raise ValueError(f"{name}.geojson contains null or empty geometries.")
    reference_path = root / "data" / "nh66_reference_corridor.geojson"
    osm_road_path = root / "data" / "nh66_osm_mapped_reference.geojson"
    duk_road_path = root / "data" / "duk_osm_mapped_reference.geojson"
    missing_roads = [str(path) for path in (reference_path, osm_road_path, duk_road_path) if not path.is_file()]
    if missing_roads:
        raise FileNotFoundError("Required road reference file(s) are missing: " + ", ".join(missing_roads))
    roads_reference = gpd.read_file(reference_path)
    if expect_road_aligned:
        alternative_roads_path = outputs.parent / "data" / "nh66_road_aligned_corridor.geojson"
        if not alternative_roads_path.is_file():
            raise FileNotFoundError(f"Road-aligned corridor is missing: {alternative_roads_path}")
        current_roads_path = alternative_roads_path
    else:
        current_roads_path = (
            config.ROAD_GEOJSON
            if root.resolve() == config.BASE_DIR
            else root / "data" / "nh66_road_aligned_corridor.geojson"
        )
    if not current_roads_path.is_file():
        raise FileNotFoundError(f"The configured analysis road file is missing: {current_roads_path}")
    current_roads = gpd.read_file(current_roads_path)
    if "road_class" not in current_roads.columns:
        raise ValueError(f"The configured analysis roads have no road_class column: {current_roads_path}")
    roads_osm = current_roads[
        current_roads["road_class"].astype(str).str.strip() == "NH 66"
    ].copy()
    roads_duk = current_roads[
        current_roads["road_class"].astype(str).str.strip() == "DUK Access Road"
    ].copy()
    for roads in (roads_osm, roads_duk):
        if "retrieved" in roads.columns:
            roads["retrieved"] = roads["retrieved"].astype(str)
    for label, roads in (
        ("original reference", roads_reference),
        ("OSM mapped NH 66", roads_osm),
        ("OSM mapped DUK Access Road", roads_duk),
    ):
        if roads.crs is None or roads.empty or roads.geometry.isna().any() or roads.geometry.is_empty.any():
            raise ValueError(f"The {label} road file has no CRS or usable geometries.")
    if not roads_osm["ref"].astype(str).str.replace(" ", "").str.upper().eq("NH66").any():
        raise ValueError("The mapped OSM road file does not identify a feature with ref NH66.")

    nh66 = layers["validated_accidents"]
    if "Road_Name" in nh66:
        nh66 = nh66[nh66["Road_Name"].astype(str).str.strip().str.upper() == "NH 66"]
    if nh66.empty:
        raise ValueError("No NH 66 synthetic accident records are available for alignment checks.")
    mapped_line = roads_osm.to_crs("EPSG:32643").geometry.union_all()
    point_distances = nh66.to_crs("EPSG:32643").geometry.distance(mapped_line)
    road_offset_m = {
        "records": int(len(point_distances)),
        "median": float(point_distances.median()),
        "p90": float(point_distances.quantile(0.9)),
        "within_5m": int((point_distances <= 5).sum()),
    }
    duk_points = layers["validated_accidents"]
    if "Road_Name" in duk_points:
        duk_points = duk_points[duk_points["Road_Name"].astype(str).str.strip() == "DUK Access Road"]
    if duk_points.empty:
        raise ValueError("No DUK Access Road synthetic accident records are available for alignment checks.")
    mapped_duk_line = roads_duk.to_crs("EPSG:32643").geometry.union_all()
    duk_distances = duk_points.to_crs("EPSG:32643").geometry.distance(mapped_duk_line)
    duk_offset_m = {
        "records": int(len(duk_distances)),
        "median": float(duk_distances.median()),
        "p90": float(duk_distances.quantile(0.9)),
        "within_5m": int((duk_distances <= 5).sum()),
    }

    reports = {
        Path(name).stem: (outputs / name).read_text(encoding="utf-8")
        for name in REQUIRED_INPUTS
        if name.endswith(".txt")
    }
    match = re.search(r"High-density zones.*?:\s*(\d+)", reports["kde_analysis_report"])
    if not match:
        raise ValueError("Could not determine the KDE density-zone count from kde_analysis_report.txt.")

    raster_path = outputs / "kde_density.tif"
    with rasterio.open(raster_path) as raster:
        if raster.crs is None:
            raise ValueError("kde_density.tif has no CRS.")
        if raster.count != 1 or raster.width <= 0 or raster.height <= 0:
            raise ValueError("kde_density.tif must contain one non-empty raster band.")
        if not np.isfinite(raster.transform.a) or not np.isfinite(raster.transform.e):
            raise ValueError("kde_density.tif has an invalid affine transform.")
    return ProjectData(
        root=root,
        outputs=outputs,
        visualization_outputs=visualization_outputs,
        layers=layers,
        raster_path=raster_path,
        reports=reports,
        kde_zone_count=int(match.group(1)),
        roads_reference=roads_reference,
        roads_osm=roads_osm,
        roads_duk=roads_duk,
        road_offset_m=road_offset_m,
        duk_offset_m=duk_offset_m,
        expect_road_aligned=expect_road_aligned,
    )


def inventory_text(data: ProjectData) -> str:
    lines = [
        "TRAFFIC HOTSPOT VISUALIZATION - INPUT INVENTORY",
        "=" * 68,
        "All accident records are synthetic; results are not real-world safety assessments.",
        "The original supplied road geometry is simplified reference geometry, not a verified road centreline.",
        "",
        "Required analytical inputs:",
    ]
    for name in REQUIRED_INPUTS:
        path = data.outputs / name
        status = "AVAILABLE" if path.is_file() else "MISSING"
        lines.append(f"- {name}: {status}" + (f" ({path.stat().st_size:,} bytes)" if path.is_file() else ""))
    lines.extend(["", "Source data:"])
    source_files = (
        data.root / "data" / "nh66_reference_based_synthetic_accidents.csv",
        data.root / "data" / "nh66_reference_corridor.geojson",
        data.root / "data" / "nh66_osm_mapped_reference.geojson",
        data.root / "data" / "duk_osm_mapped_reference.geojson",
    )
    for path in source_files:
        lines.append(f"- data/{path.name}: {'AVAILABLE' if path.is_file() else 'MISSING'}")
    csv_path, road_path, osm_road_path, duk_road_path = source_files
    if csv_path.is_file():
        lines.append(f"  Accident CSV records: {len(pd.read_csv(csv_path))} (synthetic)")
    if road_path.is_file():
        road = gpd.read_file(road_path)
        lines.append(
            f"  Road geometry: {len(road)} features | CRS {road.crs} | "
            f"geometry {', '.join(f'{key}={value}' for key, value in road.geom_type.value_counts().items())}"
        )
        lines.append(f"  road attributes={', '.join(column for column in road.columns if column != road.geometry.name)}")
    if osm_road_path.is_file():
        osm_roads = gpd.read_file(osm_road_path)
        properties = osm_roads.iloc[0].drop(labels=[osm_roads.geometry.name]).to_dict()
        lines.extend(
            [
                f"  OSM road geometry: {len(osm_roads)} feature(s) | CRS {osm_roads.crs} | "
                f"feature={properties.get('name')} | ref={properties.get('ref')} | "
                f"way={properties.get('osm_way_id')}",
                f"  OSM attribution: {properties.get('attribution')}",
                f"  Synthetic NH 66 records: {data.road_offset_m['records']} | "
                f"distance to mapped OSM road: median={data.road_offset_m['median']:.1f} m, "
                f"p90={data.road_offset_m['p90']:.1f} m, "
                f"within 5 m={data.road_offset_m['within_5m']}",
            ]
        )
    if duk_road_path.is_file():
        duk_roads = gpd.read_file(duk_road_path)
        properties = duk_roads.iloc[0].drop(labels=[duk_roads.geometry.name]).to_dict()
        lines.extend(
            [
                f"  OSM DUK access geometry: {len(duk_roads)} feature(s) | "
                f"way={properties.get('osm_way_id')} | junction way={properties.get('junction_way_id')}",
                f"  Synthetic DUK Access Road records: {data.duk_offset_m['records']} | "
                f"distance to mapped OSM road: median={data.duk_offset_m['median']:.1f} m, "
                f"p90={data.duk_offset_m['p90']:.1f} m, "
                f"within 5 m={data.duk_offset_m['within_5m']}",
            ]
        )
    lines.extend(["", "GeoJSON schemas:"])
    for name, layer in data.layers.items():
        properties = [column for column in layer.columns if column != layer.geometry.name]
        lines.append(
            f"- {name}.geojson: {len(layer)} features | CRS {layer.crs} | "
            f"geometry {', '.join(f'{key}={value}' for key, value in layer.geom_type.value_counts().items()) or 'none'}"
        )
        lines.append(f"  bounds={tuple(round(float(v), 7) for v in layer.total_bounds)}")
        lines.append(f"  attributes={', '.join(properties) if properties else '(none)'}")
    with rasterio.open(data.raster_path) as raster:
        lines.extend(
            [
                "",
                "KDE raster:",
                f"- {data.raster_path.name}: {raster.width} x {raster.height}, {raster.count} band(s), "
                f"CRS {raster.crs}, dtype {raster.dtypes[0]}, nodata {raster.nodata}",
                f"  bounds={tuple(round(float(v), 3) for v in raster.bounds)}",
                f"  affine transform={raster.transform}",
                "",
                "Visualization suitability:",
                "- validated_accidents: clickable individual synthetic event layer.",
                "- dbscan_clustered_accidents: per-cluster points and separate noise points.",
                "- dbscan_clusters: clickable cluster polygon layer with reported counts.",
                "- hotspot_polygons / hotspots: candidate hotspot boundaries and their attributes.",
                "- hotspot_points: the three pre-junction NH 66 risk-tier centroids.",
                "- kde_density: georeferenced raster; reprojected to EPSG:4326 for Leaflet.",
                "- nh66_osm_mapped_reference.geojson: NH 66 segment from Mangalapuram to the DUK junction (OSM way 44634862).",
                "- duk_osm_mapped_reference.geojson: mapped DUK access road to the campus-side road endpoint (OSM way 802362018).",
                "- nh66_reference_corridor.geojson: original simplified line, kept as a separate optional layer.",
                "",
                "Existing analytical outputs:",
            ]
        )
    for path in sorted(data.outputs.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file():
            lines.append(f"- {path.name} ({path.stat().st_size:,} bytes)")
    return "\n".join(lines) + "\n"


def reproject_raster_to_wgs84(source_path: Path) -> tuple[np.ndarray, object, tuple[float, float, float, float], float]:
    with rasterio.open(source_path) as source:
        if source.crs is None:
            raise ValueError(f"Cannot reproject raster without a CRS: {source_path}")
        transform, width, height = calculate_default_transform(
            source.crs, CRS.from_epsg(4326), source.width, source.height, *source.bounds
        )
        destination = np.full((height, width), source.nodata if source.nodata is not None else np.nan, dtype="float32")
        reproject(
            source=rasterio.band(source, 1),
            destination=destination,
            src_transform=source.transform,
            src_crs=source.crs,
            dst_transform=transform,
            dst_crs=CRS.from_epsg(4326),
            src_nodata=source.nodata,
            dst_nodata=source.nodata if source.nodata is not None else np.nan,
            resampling=Resampling.bilinear,
        )
        west = transform.c
        north = transform.f
        east = west + transform.a * width
        south = north + transform.e * height
        bounds = (south, west, north, east)
        nodata = source.nodata if source.nodata is not None else np.nan
    if not np.isfinite(destination).any() or np.all(destination == nodata):
        raise ValueError("Raster reprojection produced no valid KDE pixels.")
    return destination, transform, bounds, float(nodata)


def verify_outputs(data: ProjectData) -> list[str]:
    problems = []
    for name in VISUALIZATION_OUTPUTS:
        path = data.visualization_outputs / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append(f"Missing or empty output: {name}")

    if not problems:
        map_html = (data.visualization_outputs / "hotspots_on_osm.html").read_text(encoding="utf-8")
        for label in (
            "OpenStreetMap",
            "Mapped roads",
            "KDE density overlay",
            "DBSCAN clusters",
            "Synthetic accident locations",
            "Hotspot centre points",
        ):
            if label not in map_html:
                problems.append(f"Interactive map is missing expected layer/text: {label}")
        if "clusterMessages" not in map_html or "function inGeometry" not in map_html:
            problems.append("Vehicle demo is missing its DBSCAN-cluster entry alert logic.")
        if (
            "approachDistance=10" not in map_html
            or "Careful! Accident hotspot ahead. Please slow down." not in map_html
            or "distanceToGeometryMeters" not in map_html
        ):
            problems.append("Vehicle demo is missing the 10 m pre-entry warning outside DBSCAN polygons.")
        for color in ("#e11d48", "#2563eb"):
            if color not in map_html:
                problems.append(f"Interactive accident map is missing severity marker color {color}.")
        if "Is_DUK_Demo" in map_html:
            problems.append("Interactive hotspot details still include the removed DUK demo field.")
        for removed_label in (
            "Mapped NH 66 (OpenStreetMap)",
            "Mapped DUK Access Road (OpenStreetMap)",
            "Original simplified synthetic reference line",
            "DBSCAN noise points",
            "Final candidate hotspot polygons",
        ):
            if removed_label in map_html:
                problems.append(f"Interactive map still includes removed layer: {removed_label}")
        dashboard = (data.visualization_outputs / "traffic_hotspot_dashboard.html").read_text(encoding="utf-8")
        dashboard_labels = ["Total synthetic accident records", "KDE density zones",
                            "DBSCAN clusters", "Candidate hotspots",
                            str(len(data.layers["validated_accidents"]))]
        for label in dashboard_labels:
            if label not in dashboard:
                problems.append(f"Dashboard is missing expected summary: {label}")

    accident_asset = data.root / "web" / "js" / "accident-points.js"
    if not accident_asset.is_file():
        problems.append("The web map's generated 50-record accident point asset is missing.")
    else:
        try:
            payload = accident_asset.read_text(encoding="utf-8").strip()
            prefix = "window.TrafficAccidentPoints = "
            if not payload.startswith(prefix) or not payload.endswith(";"):
                raise ValueError("Unexpected JavaScript assignment format.")
            accident_geojson = json.loads(payload[len(prefix):-1])
            features = accident_geojson.get("features", [])
            severities = {str(feature.get("properties", {}).get("Severity", "")).title() for feature in features}
            if len(features) != 50:
                problems.append(f"The web accident point asset should contain 50 records, found {len(features)}.")
            if not {"Fatal", "Major", "Minor"}.issubset(severities):
                problems.append(f"The web accident point asset has unexpected severity categories: {sorted(severities)}.")
        except (ValueError, json.JSONDecodeError) as exc:
            problems.append(f"Could not read the generated accident point asset: {exc}")
    app_script = data.root / "web" / "js" / "app.js"
    if app_script.is_file():
        app_text = app_script.read_text(encoding="utf-8")
        if "bindTooltip(summary" not in app_text or "bindPopup(popupSection" not in app_text:
            problems.append("The web map accident points are missing hover summaries or click details.")
        if "#e11d48" not in app_text or "#2563eb" not in app_text:
            problems.append("The web map is missing fatal/non-fatal accident marker colors.")

    accidents = data.layers["validated_accidents"]
    clustered = data.layers["dbscan_clustered_accidents"]
    hotspots = data.layers["hotspot_points"]
    clusters = data.layers["dbscan_clusters"]
    if len(accidents) != 50:
        problems.append(f"Expected 50 accident records, found {len(accidents)}.")
    if len(hotspots) != 3:
        problems.append(f"Expected three displayed hotspots, found {len(hotspots)}.")
    if len(clusters) != 3:
        problems.append(f"Expected exactly three DBSCAN alert areas, found {len(clusters)}.")
    if len(hotspots) and "Risk_Level" in hotspots.columns:
        levels = set(hotspots["Risk_Level"].astype(str))
        if levels != {"Low", "Medium", "High"}:
            problems.append(f"Expected one low, medium and high hotspot; found {sorted(levels)}.")
    if len(accidents) != len(clustered):
        problems.append(f"Accident layer counts differ ({len(accidents)} vs {len(clustered)}).")
    if len(hotspots) and "Primary_Road" in hotspots.columns and not hotspots["Primary_Road"].astype(str).str.strip().eq("NH 66").all():
        problems.append("All three alert hotspots must be on NH 66 before the DUK junction.")
    if len(hotspots) == 3 and len(clusters) == 3:
        hotspot_cluster_ids = set(hotspots["DBSCAN_Cluster_ID"].astype(int))
        dbscan_cluster_ids = set(clusters["Cluster_ID"].astype(int))
        if hotspot_cluster_ids != dbscan_cluster_ids:
            problems.append("The three alert hotspots do not map one-to-one to the three DBSCAN areas.")
        nh66_line = data.roads_osm.to_crs("EPSG:32643").geometry.iloc[0]
        hotspot_points = hotspots.to_crs("EPSG:32643")
        if any(nh66_line.project(point) >= nh66_line.length for point in hotspot_points.geometry):
            problems.append("A hotspot is at or beyond the NH 66 / DUK junction.")
        projected_clusters = clusters.to_crs("EPSG:32643").set_index("Cluster_ID")
        for _, hotspot in hotspot_points.iterrows():
            cluster_id = int(hotspot["DBSCAN_Cluster_ID"])
            boundary_distance = hotspot.geometry.distance(projected_clusters.loc[cluster_id].geometry.boundary)
            if abs(boundary_distance - config.CLUSTER_POLYGON_BUFFER_M) > 0.1:
                problems.append(
                    f"{hotspot['Risk_Level']} hotspot is {boundary_distance:.2f} m from its DBSCAN boundary; "
                    f"expected {config.CLUSTER_POLYGON_BUFFER_M:g} m."
                )
    if data.roads_osm.iloc[0].get("osm_way_id") != "44634862":
        problems.append("Mapped NH 66 OSM way id is missing or unexpected.")
    if data.roads_duk.iloc[0].get("osm_way_id") != "802362018":
        problems.append("Mapped DUK access OSM way id is missing or unexpected.")
    if data.expect_road_aligned and data.road_offset_m["within_5m"] != data.road_offset_m["records"]:
        problems.append("The alternative road-aligned dataset has NH 66 points outside the 5 m tolerance.")
    if data.expect_road_aligned and data.duk_offset_m["within_5m"] != data.duk_offset_m["records"]:
        problems.append("The alternative road-aligned dataset has DUK points outside the 5 m tolerance.")
    if data.expect_road_aligned:
        nh66_line = data.roads_osm.to_crs("EPSG:32643").geometry.iloc[0]
        duk_line = data.roads_duk.to_crs("EPSG:32643").geometry.iloc[0]
        if nh66_line.distance(Point(duk_line.coords[0])) > 0.1:
            problems.append("The mapped DUK access road does not connect to the NH 66 centreline.")

    try:
        transformer = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
        longitude, latitude = transformer.transform(703500, 953500)
        if not (76 <= longitude <= 78 and 8 <= latitude <= 10):
            problems.append(f"EPSG:32643 transformation is outside expected study area: {longitude}, {latitude}.")
    except Exception as exc:
        problems.append(f"EPSG CRS transformation failed: {exc}")

    try:
        with rasterio.open(data.raster_path) as raster:
            calculate_default_transform(raster.crs, "EPSG:4326", raster.width, raster.height, *raster.bounds)
    except Exception as exc:
        problems.append(f"Raster reprojection verification failed: {exc}")
    return problems


def test_report(data: ProjectData, problems: list[str]) -> str:
    layers = data.layers
    cluster_ids = (
        set(int(value) for value in layers["dbscan_clustered_accidents"]["Cluster_ID"].dropna() if int(value) > 0)
        if "Cluster_ID" in layers["dbscan_clustered_accidents"]
        else set()
    )
    noise = (
        int((layers["dbscan_clustered_accidents"]["Cluster_ID"] == -1).sum())
        if "Cluster_ID" in layers["dbscan_clustered_accidents"]
        else 0
    )
    cluster_boundaries = layers["dbscan_clusters"].to_crs("EPSG:32643").set_index("Cluster_ID")
    hotspot_buffer_distances = []
    for _, hotspot in layers["hotspot_points"].to_crs("EPSG:32643").iterrows():
        cluster_id = int(hotspot["DBSCAN_Cluster_ID"])
        if cluster_id in cluster_boundaries.index:
            hotspot_buffer_distances.append(
                hotspot.geometry.distance(cluster_boundaries.loc[cluster_id].geometry.boundary)
            )
    lines = [
        "VISUALIZATION OUTPUT TEST REPORT",
        "=" * 68,
        "Environment PROJ database is selected from Rasterio's bundled proj_data before GIS imports.",
        f"EPSG:4326 initializes: {'PASS' if CRS.from_epsg(4326) else 'FAIL'}",
        f"EPSG:32643 initializes: {'PASS' if CRS.from_epsg(32643) else 'FAIL'}",
        f"GeoJSON layers readable and assigned CRS: PASS ({len(layers)} layers)",
        f"Accident points: {len(layers['validated_accidents'])}",
        f"DBSCAN clusters represented: {len(cluster_ids)}; noise points: {noise}",
        f"KDE density zones in analysis report: {data.kde_zone_count}",
        f"Candidate hotspots: {len(layers['hotspot_points'])}",
        f"Three hotspots map one-to-one to DBSCAN alert areas: "
        f"{len(layers['hotspot_points']) == 3 and len(layers['dbscan_clusters']) == 3 and set(layers['hotspot_points'].get('DBSCAN_Cluster_ID', [])) == set(layers['dbscan_clusters'].get('Cluster_ID', []))}",
        "All three hotspots are on NH 66 before the DUK junction: "
        f"{len(layers['hotspot_points']) == 3 and layers['hotspot_points'].get('Primary_Road', pd.Series(dtype=str)).astype(str).str.strip().eq('NH 66').all()}",
        "Hotspot-to-DBSCAN boundary distances: "
        + ", ".join(f"{distance:.1f} m" for distance in hotspot_buffer_distances),
        f"Mapped NH 66 line: OSM way {data.roads_osm.iloc[0].get('osm_way_id')} "
        f"({len(data.roads_osm.to_crs('EPSG:32643').geometry.iloc[0].coords)} vertices)",
        f"NH 66 synthetic records offset from mapped line: median={data.road_offset_m['median']:.1f} m, "
        f"p90={data.road_offset_m['p90']:.1f} m, within 5 m={data.road_offset_m['within_5m']}/"
        f"{data.road_offset_m['records']}",
        f"NH 66 points meet 5 m road-alignment tolerance: "
        f"{data.road_offset_m['within_5m'] == data.road_offset_m['records']}",
        f"DUK Access Road OSM way: {data.roads_duk.iloc[0].get('osm_way_id')}",
        f"DUK synthetic records offset from mapped road: median={data.duk_offset_m['median']:.1f} m, "
        f"p90={data.duk_offset_m['p90']:.1f} m, within 5 m={data.duk_offset_m['within_5m']}/"
        f"{data.duk_offset_m['records']}",
        f"DUK points meet 5 m road-alignment tolerance: "
        f"{data.duk_offset_m['within_5m'] == data.duk_offset_m['records']}",
        "KDE raster CRS, affine transform, and EPSG:4326 reprojection: PASS" if not any(
            "raster" in problem.lower() for problem in problems
        ) else "KDE raster CRS/reprojection: FAIL",
        "Leaflet HTML expected layers and dashboard summary values: PASS" if not any(
            "map" in problem.lower() or "dashboard" in problem.lower() for problem in problems
        ) else "Leaflet/dashboard content validation: FAIL",
        "",
        "Result: " + ("PASS" if not problems else "FAIL"),
    ]
    if problems:
        lines.extend(["", "Failures:"] + [f"- {problem}" for problem in problems])
    lines.extend(
        [
            "",
            "Limitations: accident records are synthetic; hotspot classes are relative project scores, not real-world risk.",
        "The solid road line is OSM-mapped geometry, not a surveyed or authoritative centreline.",
        "The study corridor begins at Mangalapuram and follows the mapped DUK access road to the campus-side road endpoint.",
        "Mapped roads are OSM geometry, not surveyed or authoritative centrelines; the simplified reference line is omitted from the interactive map.",
        (
            "The road-aligned synthetic set contains 50 records and is analyzed against mapped road geometry."
            if data.expect_road_aligned
            else "Original synthetic event coordinates and analytical outputs were not moved or rerun."
        ),
    ]
    )
    return "\n".join(lines) + "\n"
