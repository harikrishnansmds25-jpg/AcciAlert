"""Build a separate synthetic analysis whose NH 66 points follow mapped OSM geometry."""

from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path

from visualization import configure_proj_data

configure_proj_data()

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import substring

import config


def _unit_tangent(line: LineString, measure: float) -> tuple[float, float]:
    step = min(1.0, line.length / 100)
    before = max(0.0, measure - step)
    after = min(line.length, measure + step)
    first, last = line.interpolate(before), line.interpolate(after)
    dx, dy = last.x - first.x, last.y - first.y
    length = float(np.hypot(dx, dy))
    if length == 0:
        raise ValueError(f"Could not determine road direction at chainage {measure:.2f} m.")
    return dx / length, dy / length


def _study_corridors(
    original_reference: gpd.GeoDataFrame,
    mapped_road: gpd.GeoDataFrame,
    mapped_duk_road: gpd.GeoDataFrame,
) -> tuple[LineString, LineString, LineString, LineString]:
    old_nh66 = original_reference.loc[
        original_reference["road_class"].astype(str).str.strip() == "NH 66"
    ].geometry.union_all()
    old_duk = original_reference.loc[
        original_reference["road_class"].astype(str).str.strip() == "DUK Access Road"
    ].geometry.union_all()
    osm_line = mapped_road.geometry.union_all()
    duk_source_line = mapped_duk_road.geometry.union_all()
    if any(line.geom_type != "LineString" for line in (old_nh66, old_duk, osm_line, duk_source_line)):
        raise ValueError("Expected LineString geometries for both reference and mapped study roads.")

    start = Point(76.8486295, 8.6242459)
    access_anchor = Point(76.8501150, 8.6186208)
    to_projected = Transformer.from_crs(config.CRS_GEOGRAPHIC, config.CRS_PROJECTED, always_xy=True)
    start = Point(*to_projected.transform(start.x, start.y))
    access_anchor = Point(*to_projected.transform(access_anchor.x, access_anchor.y))
    start_measure = osm_line.project(start)
    junction_measure = osm_line.project(access_anchor)
    if abs(start_measure - junction_measure) < 100:
        raise ValueError("Mapped NH 66 study endpoints are too close or incorrectly georeferenced.")
    new_nh66 = substring(osm_line, min(start_measure, junction_measure), max(start_measure, junction_measure))
    if start_measure > junction_measure:
        new_nh66 = LineString(list(new_nh66.coords)[::-1])
    if new_nh66.geom_type != "LineString" or new_nh66.length < 100:
        raise ValueError("Mapped NH 66 segment from Mangalapuram to the DUK junction is invalid.")

    access_line = duk_source_line
    if Point(access_line.coords[-1]).distance(access_anchor) < Point(access_line.coords[0]).distance(access_anchor):
        access_line = LineString(list(access_line.coords)[::-1])
    if Point(access_line.coords[0]).distance(access_anchor) > 25:
        raise ValueError("Mapped DUK access road does not begin near its mapped NH 66 junction.")
    junction = new_nh66.interpolate(new_nh66.project(Point(access_line.coords[0])))
    new_duk = LineString([junction, *access_line.coords])
    if new_duk.length < 100:
        raise ValueError("Mapped DUK Access Road is too short or invalid.")
    return old_nh66, new_nh66, old_duk, new_duk


def _relocate_road_records(
    csv_path: Path,
    output_path: Path,
    road_mappings: dict[str, tuple[LineString, LineString]],
) -> dict[str, dict[str, float | int]]:
    source = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    required = set(config.REQUIRED_COLUMNS)
    if not required.issubset(source.columns):
        raise ValueError(f"Source CSV is missing required columns: {sorted(required - set(source.columns))}")

    to_projected = Transformer.from_crs(config.CRS_GEOGRAPHIC, config.CRS_PROJECTED, always_xy=True)
    to_geographic = Transformer.from_crs(config.CRS_PROJECTED, config.CRS_GEOGRAPHIC, always_xy=True)
    labels = source["Road_Name"].str.strip()
    unknown_roads = sorted(set(labels) - set(road_mappings))
    if unknown_roads:
        raise ValueError(f"The source CSV contains unsupported road labels: {unknown_roads}")

    stats = {}
    for road_name, (old_line, new_line) in road_mappings.items():
        distances = []
        indexes = source.index[labels == road_name]
        if len(indexes) == 0:
            raise ValueError(f"The source CSV contains no {road_name} records.")
        for index in indexes:
            row = source.loc[index]
            longitude, latitude = float(row["Longitude"]), float(row["Latitude"])
            x, y = to_projected.transform(longitude, latitude)
            event = Point(x, y)

            old_measure = old_line.project(event)
            old_center = old_line.interpolate(old_measure)
            fraction = min(1.0, max(0.0, old_measure / old_line.length))
            new_measure = fraction * new_line.length
            new_center = new_line.interpolate(new_measure)

            old_tx, old_ty = _unit_tangent(old_line, old_measure)
            new_tx, new_ty = _unit_tangent(new_line, new_measure)
            offset_x, offset_y = event.x - old_center.x, event.y - old_center.y
            along_offset = offset_x * old_tx + offset_y * old_ty
            cross_offset = offset_x * -old_ty + offset_y * old_tx
            aligned_x = new_center.x + along_offset * new_tx - cross_offset * new_ty
            aligned_y = new_center.y + along_offset * new_ty + cross_offset * new_tx
            distances.append(Point(aligned_x, aligned_y).distance(new_line))

            new_lon, new_lat = to_geographic.transform(aligned_x, aligned_y)
            source.at[index, "Latitude"] = f"{new_lat:.6f}"
            source.at[index, "Longitude"] = f"{new_lon:.6f}"

        values = np.asarray(distances)
        if values.max() > config.ROAD_TOLERANCE_M:
            raise ValueError(
                f"Repositioned {road_name} points exceed the {config.ROAD_TOLERANCE_M:g} m tolerance "
                f"(max {values.max():.2f} m)."
            )
        stats[road_name] = {
            "records": int(len(values)),
            "max_m": float(values.max()),
            "mean_m": float(values.mean()),
            "within_5m": int((values <= config.ROAD_TOLERANCE_M).sum()),
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    source.to_csv(output_path, index=False, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    return stats


def _arrange_three_pre_junction_zones(
    csv_path: Path,
    nh66: LineString,
    duk: LineString,
) -> dict[str, dict[str, float | int]]:
    """Place three severity-tiered DBSCAN groups on NH 66 before the DUK turn."""
    source = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    severity = source["Severity"].str.strip().str.title()
    fatal = list(source.index[severity == "Fatal"])
    major = list(source.index[severity == "Major"])
    minor = list(source.index[severity == "Minor"])
    if (len(source), len(fatal), len(major), len(minor)) != (50, 5, 15, 30):
        raise ValueError("Expected 50 records with 5 fatal, 15 major, and 30 minor events.")
    minor_nh, minor_duk = minor[:20], minor[20:]
    nh_indexes = fatal + major + minor_nh
    duk_indexes = minor_duk
    source.loc[nh_indexes, "Road_Name"] = "NH 66"
    source.loc[nh_indexes, "Road_Type"] = "National Highway"
    source.loc[duk_indexes, "Road_Name"] = "DUK Access Road"
    source.loc[duk_indexes, "Road_Type"] = "Access Road"

    assignments = [
        (fatal + major[:5], nh66, 100.0, np.linspace(-9, 9, 10)),
        (major[5:15], nh66, 300.0, np.linspace(-9, 9, 10)),
        (minor_nh[:10], nh66, 500.0, np.linspace(-9, 9, 10)),
    ]
    to_geographic = Transformer.from_crs(config.CRS_PROJECTED, config.CRS_GEOGRAPHIC, always_xy=True)
    distances = {"NH 66": [], "DUK Access Road": []}
    placed_points = {"NH 66": [], "DUK Access Road": []}
    for indexes, line, center, offsets in assignments:
        if len(indexes) != len(offsets):
            raise ValueError("Accident counts do not match the requested three-zone road layout.")
        measures = center + offsets if center is not None else offsets
        if np.min(measures) < 0 or np.max(measures) > line.length:
            raise ValueError("A planned accident position falls outside its mapped road segment.")
        for index, measure in zip(indexes, measures):
            point = line.interpolate(float(measure))
            road_name = "NH 66" if line is nh66 else "DUK Access Road"
            distances[road_name].append(float(point.distance(line)))
            placed_points[road_name].append(point)
            longitude, latitude = to_geographic.transform(point.x, point.y)
            source.at[index, "Longitude"] = f"{longitude:.7f}"
            source.at[index, "Latitude"] = f"{latitude:.7f}"

    rng = np.random.default_rng(config.RANDOM_SEED)

    def irregular_chainages(low: float, high: float, count: int, minimum_gap: float) -> list[float]:
        """Sample non-uniform positions while keeping records beyond DBSCAN eps."""
        slack = high - low - minimum_gap * max(0, count - 1)
        if slack < 0:
            raise ValueError("The mapped road segment is too short for isolated accident records.")
        gaps = rng.dirichlet(np.ones(count + 1)) * slack
        measures, current = [], low + gaps[0]
        for offset in range(count):
            measures.append(float(current))
            current += minimum_gap + gaps[offset + 1]
        return measures

    # Keep NH 66 scatter outside the 60 m cluster buffers, with extra clearance.
    noise_indexes = minor_nh[10:20]
    centers = (100.0, 300.0, 500.0)
    clearance = config.CLUSTER_POLYGON_BUFFER_M + 21.0
    ranges = [(12.0, centers[0] - clearance),
              (centers[0] + clearance, centers[1] - clearance),
              (centers[1] + clearance, centers[2] - clearance),
              (centers[2] + clearance, nh66.length - 12.0)]
    group_sizes = (1, 3, 2, 4)
    cursor = 0
    for (low, high), count in zip(ranges, group_sizes):
        span = 10.0 if count > 1 else 0.0
        margin = min(span + 2.0, (high - low) / 2 - 0.5)
        center = float(rng.uniform(low + margin, high - margin))
        offsets = np.sort(rng.uniform(-span, span, count)) if span else np.zeros(1)
        group_points = []
        for index, measure in zip(noise_indexes[cursor:cursor + count], center + offsets):
            point = nh66.interpolate(float(measure))
            if min(point.distance(existing) for existing in placed_points["NH 66"]) <= 30.0:
                raise ValueError("A scattered NH 66 record would touch a hotspot DBSCAN cluster.")
            group_points.append((index, point))
        for index, point in group_points:
            placed_points["NH 66"].append(point)
            distances["NH 66"].append(float(point.distance(nh66)))
            longitude, latitude = to_geographic.transform(point.x, point.y)
            source.at[index, "Longitude"] = f"{longitude:.7f}"
            source.at[index, "Latitude"] = f"{latitude:.7f}"
        cursor += count

    # Small, irregular groups (never more than four points) keep the other
    # road events visibly uneven without creating extra DBSCAN clusters.
    duk_group_sizes = (4, 1, 3, 2)
    duk_centers = irregular_chainages(45.0, duk.length - 30.0, len(duk_group_sizes), 52.0)
    duk_order = list(rng.permutation(duk_indexes))
    cursor = 0
    for center, count in zip(duk_centers, duk_group_sizes):
        span = 10.0 if count > 2 else (7.0 if count == 2 else 0.0)
        offsets = np.sort(rng.uniform(-span, span, count)) if span else np.zeros(1)
        group_points = []
        for index, measure in zip(duk_order[cursor:cursor + count], center + offsets):
            point = duk.interpolate(float(measure))
            external_points = placed_points["NH 66"] + placed_points["DUK Access Road"]
            if any(point.distance(existing) <= 30.0 for existing in external_points):
                raise ValueError("A DUK accident group would join another DBSCAN alert area.")
            group_points.append((index, point))
        for index, point in group_points:
            placed_points["DUK Access Road"].append(point)
            distances["DUK Access Road"].append(float(point.distance(duk)))
            longitude, latitude = to_geographic.transform(point.x, point.y)
            source.at[index, "Longitude"] = f"{longitude:.7f}"
            source.at[index, "Latitude"] = f"{latitude:.7f}"
        cursor += count
    source.to_csv(csv_path, index=False, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    return {
        road_name: {
            "records": len(values),
            "max_m": max(values, default=0.0),
            "mean_m": float(np.mean(values)) if values else 0.0,
            "within_5m": sum(value <= 5.0 for value in values),
        }
        for road_name, values in distances.items()
    }


def _write_alternative_roads(
    root: Path,
    output_path: Path,
    new_nh66: LineString,
    new_duk: LineString,
) -> None:
    mapped = gpd.read_file(root / "data" / "nh66_osm_mapped_reference.geojson")
    mapped_duk = gpd.read_file(root / "data" / "duk_osm_mapped_reference.geojson")
    nh66_properties = mapped.iloc[0]
    duk_properties = mapped_duk.iloc[0]

    def geographic(line: LineString) -> LineString:
        return gpd.GeoSeries([line], crs=config.CRS_PROJECTED).to_crs(config.CRS_GEOGRAPHIC).iloc[0]

    roads = gpd.GeoDataFrame(
        [
            {
                "road_class": "NH 66",
                "name": nh66_properties["name"],
                "note": "Mapped OSM NH 66 study segment from Mangalapuram to the DUK access junction; not surveyed or authoritative.",
                "source": "OpenStreetMap contributors",
                "ref": "NH66",
                "highway": "trunk",
                "osm_way_id": nh66_properties["osm_way_id"],
                "source_url": nh66_properties["source_url"],
                "attribution": nh66_properties["attribution"],
                "geometry": geographic(new_nh66),
            },
            {
                "road_class": "DUK Access Road",
                "name": duk_properties["name"],
                "note": "Mapped OSM access road from NH 66 to the road endpoint beside the Digital University Kerala campus; includes a short tie-in to the NH 66 centreline.",
                "source": "OpenStreetMap contributors",
                "highway": duk_properties["highway"],
                "osm_way_id": duk_properties["osm_way_id"],
                "junction_way_id": duk_properties["junction_way_id"],
                "source_url": duk_properties["source_url"],
                "attribution": duk_properties["attribution"],
                "geometry": geographic(new_duk),
            }
        ],
        geometry="geometry",
        crs=config.CRS_GEOGRAPHIC,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    roads.to_file(output_path, driver="GeoJSON", index=False)


def _write_alignment_report(
    path: Path,
    mapped: gpd.GeoDataFrame,
    mapped_duk: gpd.GeoDataFrame,
    stats: dict[str, dict[str, float | int]],
) -> None:
    nh66 = stats["NH 66"]
    duk = stats["DUK Access Road"]
    nh66_properties = mapped.iloc[0]
    duk_properties = mapped_duk.iloc[0]
    lines = [
        "ALTERNATIVE ROAD-ALIGNED SYNTHETIC DATA REPORT",
        "=" * 68,
        "This road-aligned dataset uses the selected 50-record sample; the reference analysis is regenerated from the same records.",
        f"NH 66 source: OpenStreetMap way {nh66_properties.get('osm_way_id')} ({nh66_properties.get('source_url')})",
        f"DUK access source: OpenStreetMap way {duk_properties.get('osm_way_id')} ({duk_properties.get('source_url')})",
        f"NH 66 junction tie-in: OpenStreetMap way {duk_properties.get('junction_way_id')} ({duk_properties.get('junction_source_url')})",
        "OSM road geometry is mapped, not surveyed or authoritative.",
        "NH 66 is clipped to the mapped Mangalapuram town point and ends at the DUK access junction.",
        "The DUK route follows the mapped residential access road to its endpoint beside the campus.",
        "A short connector joins the DUK OSM road node to the NH 66 centreline; the mapped node is about 13 m from it.",
        "Method: place three 10-record severity-tiered concentrations on NH 66 before the DUK junction;",
        "the remaining 20 events are reproducibly randomized into irregular small groups outside",
        "those alert areas; no scatter group exceeds four points, so they remain DBSCAN noise.",
        f"Alternative KDE: {config.KDE_BANDWIDTH_M:g} m bandwidth, "
        f"{config.KDE_HIGH_PERCENTILE}th-percentile high-density threshold.",
        f"NH 66 records relocated: {nh66['records']}; mean offset={nh66['mean_m']:.2f} m, max={nh66['max_m']:.2f} m, within 5 m={nh66['within_5m']}/{nh66['records']}",
        f"DUK records relocated: {duk['records']}; mean offset={duk['mean_m']:.2f} m, max={duk['max_m']:.2f} m, within 5 m={duk['within_5m']}/{duk['records']}",
        "",
        "KDE, DBSCAN, hotspot scores, classifications, and polygons below are newly recalculated",
        "from the separate road-aligned CSV and mapped road-input GeoJSON.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    root = Path(__file__).resolve().parent
    base = root / "outputs" / "road_aligned_analysis"
    input_dir = base / "data"
    analysis_dir = base / "analysis"
    visualization_dir = base / "visualization"
    source_csv = root / "data" / "nh66_reference_based_synthetic_accidents.csv"
    source_roads = gpd.read_file(root / "data" / "nh66_reference_corridor.geojson").to_crs(config.CRS_PROJECTED)
    mapped_roads = gpd.read_file(root / "data" / "nh66_osm_mapped_reference.geojson")
    mapped_duk = gpd.read_file(root / "data" / "duk_osm_mapped_reference.geojson")
    old_nh66, new_nh66, old_duk, new_duk = _study_corridors(
        source_roads,
        mapped_roads.to_crs(config.CRS_PROJECTED),
        mapped_duk.to_crs(config.CRS_PROJECTED),
    )

    alternative_csv = input_dir / "nh66_road_aligned_synthetic_accidents.csv"
    default_csv = root / "data" / "nh66_road_aligned_synthetic_accidents.csv"
    stats = _relocate_road_records(
        source_csv,
        default_csv,
        {
            "NH 66": (old_nh66, new_nh66),
            "DUK Access Road": (old_duk, new_duk),
        },
    )
    stats = _arrange_three_pre_junction_zones(default_csv, new_nh66, new_duk)
    alternative_roads = input_dir / "nh66_road_aligned_corridor.geojson"
    default_roads = root / "data" / "nh66_road_aligned_corridor.geojson"
    _write_alternative_roads(root, default_roads, new_nh66, new_duk)
    alternative_csv.parent.mkdir(parents=True, exist_ok=True)
    alternative_roads.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(default_csv, alternative_csv)
    shutil.copy2(default_roads, alternative_roads)
    config.ACCIDENT_CSV = default_csv
    config.ROAD_GEOJSON = default_roads
    config.OUTPUT_DIR = analysis_dir
    config.KDE_BANDWIDTH_M = 20.0
    config.KDE_HIGH_PERCENTILE = 80
    _write_alignment_report(base / "road_alignment_report.txt", mapped_roads, mapped_duk, stats)
    print(f"Separate synthetic CSV: {alternative_csv}")
    for road_name, road_stats in stats.items():
        print(
            f"Mapped-road tolerance: max {road_stats['max_m']:.2f} m for "
            f"{road_stats['within_5m']}/{road_stats['records']} {road_name} events"
        )

    from main import run as run_analysis

    result = run_analysis()
    if result != 0:
        raise RuntimeError(f"Road-aligned analysis pipeline returned status {result}.")

    from create_visualizations import main as generate_visualizations

    result = generate_visualizations(root, analysis_dir, visualization_dir, expect_road_aligned=True)
    if result != 0:
        raise RuntimeError(f"Road-aligned visualization pipeline returned status {result}.")
    print(f"Alternative analysis and dashboard are under: {base}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ROAD-ALIGNED ANALYSIS ERROR: {exc}", file=sys.stderr)
        raise
