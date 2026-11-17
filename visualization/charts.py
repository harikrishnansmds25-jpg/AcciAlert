"""Static maps and charts rendered from the existing project analytical outputs."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, MaxNLocator
from rasterio.plot import plotting_extent

from .validation import ProjectData

SYNTHETIC_NOTE = "Synthetic accident data only - not real-world statistics or risk assessments"
CLASS_COLOURS = {"GREEN": "#2ca25f", "ORANGE": "#ff8c00", "RED": "#e31a1c"}
SEVERITY_COLOURS = {"Minor": "#2ca25f", "Major": "#ff8c00", "Fatal": "#e31a1c"}


def _project(layer: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return layer.to_crs("EPSG:32643")


def _map_frame(
    ax,
    roads_osm: gpd.GeoDataFrame,
    roads_duk: gpd.GeoDataFrame,
    roads_reference: gpd.GeoDataFrame,
    accidents: gpd.GeoDataFrame,
    title: str,
) -> None:
    if not roads_reference.empty:
        roads_reference.plot(ax=ax, color="#72777d", linewidth=1.5, linestyle="--", zorder=3)
    roads_osm.plot(ax=ax, color="#0759a5", linewidth=2.5, zorder=4)
    roads_duk.plot(ax=ax, color="#6a3d9a", linewidth=2.5, zorder=4)
    extent_layers = [roads_osm.total_bounds, roads_duk.total_bounds, accidents.total_bounds]
    if not roads_reference.empty:
        extent_layers.append(roads_reference.total_bounds)
    bounds = np.vstack(extent_layers)
    minx, miny = bounds[:, 0].min(), bounds[:, 1].min()
    maxx, maxy = bounds[:, 2].max(), bounds[:, 3].max()
    dx, dy = max(maxx - minx, 1), max(maxy - miny, 1)
    ax.set_xlim(minx - dx * 0.06, maxx + dx * 0.06)
    ax.set_ylim(miny - dy * 0.06, maxy + dy * 0.06)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(f"{title}\n{SYNTHETIC_NOTE}", fontsize=11, weight="bold", pad=10)
    ax.set_xlabel("Easting (km, EPSG:32643)")
    ax.set_ylabel("Northing (km, EPSG:32643)")
    ax.xaxis.set_major_locator(MaxNLocator(5))
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1000:.2f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1000:.2f}"))
    ax.tick_params(labelsize=8)
    ax.grid(color="#dce3e8", linewidth=0.5, alpha=0.7)
    ax.annotate("N\n\u25b2", xy=(0.95, 0.95), xycoords="axes fraction", ha="center", va="top",
                fontsize=12, weight="bold", color="#263746")
    width = dx * 0.22
    scale = max(50, round(width / 50) * 50)
    ax.add_line(Line2D([0.06, 0.06 + scale / dx], [0.07, 0.07], transform=ax.transAxes,
                       color="#263746", linewidth=3))
    ax.text(0.06, 0.085, f"{scale:g} m", transform=ax.transAxes, ha="left", fontsize=8)
    comparison = " Dashed grey: old simplified reference." if not roads_reference.empty else ""
    ax.text(0.01, -0.16,
            "Solid blue: OSM-mapped NH 66; purple: OSM-mapped DUK access road." + comparison,
            transform=ax.transAxes, fontsize=8, color="#5b6670")


def create_static_maps(data: ProjectData) -> None:
    out = data.visualization_outputs
    out.mkdir(parents=True, exist_ok=True)
    accidents = _project(data.layers["validated_accidents"])
    clustered = _project(data.layers["dbscan_clustered_accidents"])
    cluster_polygons = _project(data.layers["dbscan_clusters"])
    roads = _project(data.roads_osm)
    roads_duk = _project(data.roads_duk)
    roads_reference = _project(data.roads_reference)
    if data.expect_road_aligned:
        roads_reference = roads_reference.iloc[0:0].copy()
    hotspots = _project(data.layers["hotspot_polygons"])
    hotspot_points = _project(data.layers["hotspot_points"])

    fig, ax = plt.subplots(figsize=(11, 8))
    with rasterio.open(data.raster_path) as raster:
        density = raster.read(1, masked=True)
        image = ax.imshow(density, extent=plotting_extent(raster), origin="upper", cmap="YlOrRd",
                          interpolation="nearest", alpha=0.85, zorder=1)
        colorbar = fig.colorbar(image, ax=ax, shrink=0.72)
        colorbar.set_label("KDE density (events per km²)")
    for severity, color in SEVERITY_COLOURS.items():
        subset = accidents[accidents["Severity"] == severity]
        ax.scatter(subset.geometry.x, subset.geometry.y, s=12, color=color, edgecolors="white",
                   linewidths=0.3, label=f"{severity} synthetic locations", zorder=5)
    _map_frame(ax, roads, roads_duk, roads_reference, accidents, "Kernel density of synthetic accident records")
    legend_handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=SEVERITY_COLOURS[name],
               markersize=7, label=f"{name} synthetic locations")
        for name in SEVERITY_COLOURS
    ] + [
        Line2D([0], [0], color="#0759a5", lw=2, label="OSM-mapped NH 66"),
        Line2D([0], [0], color="#6a3d9a", lw=2, label="OSM-mapped DUK access road"),
    ]
    if not roads_reference.empty:
        legend_handles.append(
            Line2D([0], [0], color="#72777d", lw=1.5, ls="--", label="Old synthetic reference")
        )
    fig.legend(handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, 0.045),
               ncol=4, fontsize=8, framealpha=0.95)
    fig.tight_layout(rect=(0, 0.12, 1, 1))
    fig.savefig(out / "kde_density_visualization.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 8))
    palette = plt.get_cmap("tab10")
    cluster_ids = sorted(int(value) for value in clustered["Cluster_ID"].dropna().unique() if int(value) > 0)
    legend = []
    for cid in cluster_ids:
        color = palette((cid - 1) % 10)
        polygon = cluster_polygons[cluster_polygons["Cluster_ID"] == cid]
        if not polygon.empty:
            polygon.plot(ax=ax, facecolor=color, edgecolor=color, alpha=0.18, linewidth=1.1, zorder=2)
        points = clustered[clustered["Cluster_ID"] == cid]
        ax.scatter(points.geometry.x, points.geometry.y, s=16, color=color, edgecolors="white",
                   linewidths=0.3, zorder=5)
        legend.append(Line2D([0], [0], marker="o", color="none", markerfacecolor=color,
                             markersize=7, label=f"Cluster {cid} ({len(points)} events)"))
    noise = clustered[clustered["Cluster_ID"] == -1]
    ax.scatter(noise.geometry.x, noise.geometry.y, marker="x", s=32, color="#777777",
               label=f"Noise / isolated ({len(noise)})", zorder=6)
    _map_frame(ax, roads, roads_duk, roads_reference, accidents, "DBSCAN clusters of synthetic accident records")
    fig.legend(handles=legend + [
        Line2D([0], [0], color="#0759a5", lw=2, label="OSM-mapped NH 66"),
        Line2D([0], [0], color="#72777d", lw=1.5, ls="--", label="Old synthetic reference"),
        Line2D([0], [0], marker="x", color="#777777", linestyle="",
                                           label=f"Noise / isolated ({len(noise)})")],
               loc="lower center", bbox_to_anchor=(0.5, 0.035), ncol=4, fontsize=7, framealpha=0.95)
    fig.tight_layout(rect=(0, 0.17, 1, 1))
    fig.savefig(out / "dbscan_visualization.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 8))
    for severity, color in SEVERITY_COLOURS.items():
        subset = accidents[accidents["Severity"] == severity]
        ax.scatter(subset.geometry.x, subset.geometry.y, s=12, color=color, alpha=0.45,
                   edgecolors="none", label=f"{severity} event")
    for _, row in hotspots.iterrows():
        color = CLASS_COLOURS.get(str(row.get("Severity_Class", "")).upper(), "#666666")
        row_gdf = gpd.GeoDataFrame([row.drop(labels=[hotspots.geometry.name])],
                                   geometry=[row.geometry], crs=hotspots.crs)
        row_gdf.plot(ax=ax, facecolor=color, edgecolor=color, alpha=0.30, linewidth=2, zorder=3)
        ax.annotate(str(row.get("Hotspot_ID", "Hotspot")),
                    (row.geometry.centroid.x, row.geometry.centroid.y), xytext=(5, 5),
                    textcoords="offset points", fontsize=8, weight="bold", zorder=7)
    for _, point in hotspot_points.iterrows():
        color = CLASS_COLOURS.get(str(point.get("Severity_Class", "")).upper(), "#666666")
        ax.scatter(point.geometry.x, point.geometry.y, marker="D", s=70,
                   color=color, edgecolors="black", linewidths=1, zorder=8)
    _map_frame(ax, roads, roads_duk, roads_reference, accidents, "Combined candidate hotspot classification")
    legend_handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=SEVERITY_COLOURS["Minor"],
               markersize=7, label="Minor event"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=SEVERITY_COLOURS["Major"],
               markersize=7, label="Major event"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=SEVERITY_COLOURS["Fatal"],
               markersize=7, label="Fatal event"),
        Patch(facecolor=CLASS_COLOURS["GREEN"], alpha=0.3, label="GREEN: lower relative candidate score"),
        Patch(facecolor=CLASS_COLOURS["ORANGE"], alpha=0.3, label="ORANGE: intermediate relative score"),
        Patch(facecolor=CLASS_COLOURS["RED"], alpha=0.3, label="RED: higher relative candidate score"),
        Line2D([0], [0], color="#0759a5", lw=2, label="OSM-mapped NH 66"),
        Line2D([0], [0], color="#72777d", lw=1.5, ls="--", label="Old synthetic reference"),
    ]
    fig.legend(handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, 0.035),
               ncol=4, fontsize=7, framealpha=0.95)
    fig.tight_layout(rect=(0, 0.17, 1, 1))
    fig.savefig(out / "final_hotspot_visualization.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def create_charts(data: ProjectData) -> None:
    out = data.visualization_outputs
    accidents = data.layers["validated_accidents"]
    hotspots = data.layers["hotspot_points"].sort_values("Hotspot_ID")
    severity_order = ["Minor", "Major", "Fatal"]
    severity_counts = accidents["Severity"].value_counts().reindex(severity_order, fill_value=0)

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(severity_counts.index, severity_counts.values,
                  color=[SEVERITY_COLOURS[name] for name in severity_counts.index], width=0.62)
    ax.bar_label(bars, padding=4)
    ax.set_title("Synthetic accident records by severity", weight="bold")
    ax.set_ylabel("Record count")
    ax.text(0.5, -0.20, SYNTHETIC_NOTE, transform=ax.transAxes, ha="center", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "severity_distribution.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    ordered = hotspots.sort_values("Accident_Count")
    colors = [CLASS_COLOURS.get(str(value).upper(), "#708090") for value in ordered["Severity_Class"]]
    bars = ax.barh(ordered["Hotspot_ID"].astype(str), ordered["Accident_Count"], color=colors)
    ax.bar_label(bars, padding=4)
    ax.set_title("Synthetic event count by candidate hotspot", weight="bold")
    ax.set_xlabel("Accident records in source hotspot output")
    ax.text(0.5, -0.20, SYNTHETIC_NOTE, transform=ax.transAxes, ha="center", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "hotspot_accident_counts.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    colors = [CLASS_COLOURS.get(str(value).upper(), "#708090") for value in hotspots["Severity_Class"]]
    bars = ax.bar(hotspots["Hotspot_ID"].astype(str), hotspots["Risk_Score"], color=colors)
    ax.bar_label(bars, fmt="%.1f", padding=4)
    ax.set_ylim(0, max(100, float(hotspots["Risk_Score"].max()) * 1.15) if len(hotspots) else 100)
    ax.set_title("Relative project risk scores (not validated real-world risk)", weight="bold")
    ax.set_ylabel("Project Risk_Score")
    ax.text(0.5, -0.20, SYNTHETIC_NOTE, transform=ax.transAxes, ha="center", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "hotspot_risk_comparison.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    palette = {"GREEN": "#2ca25f", "ORANGE": "#ff8c00", "RED": "#e31a1c"}
    for _, row in hotspots.iterrows():
        classification = str(row.get("Severity_Class", "")).upper()
        ax.scatter(row["Accident_Count"], row["KDE_Density"], s=100,
                   color=palette.get(classification, "#708090"), edgecolor="white", linewidth=0.8)
        cluster = row.get("DBSCAN_Cluster_ID", "n/a")
        ax.annotate(f"{row.get('Hotspot_ID', '')} / C{cluster}",
                    (row["Accident_Count"], row["KDE_Density"]), xytext=(6, 6),
                    textcoords="offset points", fontsize=8)
    ax.set_title("KDE density and DBSCAN-supported hotspot event counts", weight="bold")
    ax.set_xlabel("Source accident count per final hotspot")
    ax.set_ylabel("Mean KDE density (events per km²)")
    ax.grid(alpha=0.2)
    ax.text(0.5, -0.20, "KDE describes smoothed density; DBSCAN assigns points to spatial clusters.\n" + SYNTHETIC_NOTE,
            transform=ax.transAxes, ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "kde_dbscan_comparison.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
