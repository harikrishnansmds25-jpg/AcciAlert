"""
visualization.py - draws the analytical maps (PNG) and an optional interactive map (HTML).

Beginner explanation
--------------------
The maps are drawn in UTM metres, so the scale is true and one axis unit = one metre.
If the road geometry is only a reference line the maps say so in the legend and subtitle.
"""
import matplotlib

matplotlib.use("Agg")   # draw to files; no window needed (works on any machine)
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

import config

SEV_STYLE = {"Minor": dict(marker="o", color="#6baed6", s=14), "Major": dict(marker="s", color="#e6550d", s=20),
             "Fatal": dict(marker="^", color="#000000", s=34)}
DISCLAIMER = "SYNTHETIC accident data - for methodology demonstration, not real accident statistics"


def _road_label(is_reference):
    return "Road geometry (REFERENCE line, not surveyed)" if is_reference else "Road geometry (supplied)"


def _base(ax, study_area, roads_proj, is_reference, title):
    minx, miny, maxx, maxy = study_area.bounds
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("equal")
    ax.set_xlabel("Easting (m, EPSG:32643)")
    ax.set_ylabel("Northing (m, EPSG:32643)")
    ax.set_title(title + "\n" + DISCLAIMER, fontsize=10)
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.tick_params(labelsize=7)
    if config.USE_BASEMAP:
        try:
            import contextily as ctx
            ctx.add_basemap(ax, crs=config.CRS_PROJECTED, source=ctx.providers.OpenStreetMap.Mapnik, alpha=0.6)
        except Exception as exc:   # no internet / provider issue: continue without basemap
            print(f"  (basemap skipped: {exc})")
    roads_proj.plot(ax=ax, color="#222222", linewidth=1.6, zorder=3)
    return Line2D([0], [0], color="#222222", lw=1.6, label=_road_label(is_reference))


def _kde_image(ax, kde, alpha=1.0, cmap="YlOrRd"):
    t = kde["transform"]
    d = np.ma.masked_equal(kde["density"], -9999.0)
    ext = (t.c, t.c + t.a * d.shape[1], t.f + t.e * d.shape[0], t.f)
    im = ax.imshow(d, extent=ext, origin="upper", cmap=cmap, alpha=alpha, zorder=1)
    return im


def _points(ax, acc):
    handles = []
    for sev, st in SEV_STYLE.items():
        sub = acc[acc["Severity"] == sev]
        ax.scatter(sub.geometry.x, sub.geometry.y, marker=st["marker"], c=st["color"], s=st["s"],
                   edgecolors="white", linewidths=0.4, zorder=5)
        handles.append(Line2D([0], [0], marker=st["marker"], color="w", markerfacecolor=st["color"],
                              markersize=7, label=f"Accident - {sev} ({len(sub)})"))
    return handles


def plot_kde_map(path, kde, zones, threshold, study_area, roads_proj, acc, is_reference):
    fig, ax = plt.subplots(figsize=(10, 8))
    road_h = _base(ax, study_area, roads_proj, is_reference, "Kernel density of accident events")
    im = _kde_image(ax, kde)
    for z in zones:
        ax.plot(*z.exterior.xy, color="#08306b", lw=1.2, ls="--", zorder=4) if z.geom_type == "Polygon" else None
    handles = _points(ax, acc) + [road_h,
                                  Line2D([0], [0], color="#08306b", ls="--", label="High-density zone outline")]
    cb = fig.colorbar(im, ax=ax, shrink=0.7)
    cb.set_label(f"KDE (events per km$^2$), bandwidth {kde['bandwidth']:g} m")
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=3, fontsize=7, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_dbscan_map(path, acc, cluster_polys, study_area, roads_proj, is_reference, eps, min_samples):
    fig, ax = plt.subplots(figsize=(10, 8))
    road_h = _base(ax, study_area, roads_proj, is_reference,
                   f"DBSCAN clusters (eps = {eps:g} m, min_samples = {min_samples})")
    cmap = plt.get_cmap("tab10")
    handles = [road_h]
    for cid, poly in cluster_polys.items():
        col = cmap((cid - 1) % 10)
        geoms = [poly] if poly.geom_type == "Polygon" else list(poly.geoms)
        for g in geoms:
            ax.fill(*g.exterior.xy, color=col, alpha=0.25, zorder=2)
            ax.plot(*g.exterior.xy, color=col, lw=1.2, zorder=4)
        sub = acc[acc["Cluster_ID"] == cid]
        ax.scatter(sub.geometry.x, sub.geometry.y, color=col, s=16, edgecolors="white", linewidths=0.3, zorder=5)
        c = poly.centroid
        ax.annotate(f"C{cid} (n={len(sub)})", (c.x, c.y), xytext=(0, 14), textcoords="offset points",
                    fontsize=7, ha="center", zorder=6, bbox=dict(boxstyle="round,pad=0.15", fc="white", alpha=0.8))
        handles.append(Patch(facecolor=col, alpha=0.5, label=f"Cluster {cid} ({len(sub)} events)"))
    noise = acc[acc["Cluster_ID"] == -1]
    ax.scatter(noise.geometry.x, noise.geometry.y, marker="x", color="#777777", s=22, zorder=5)
    handles.append(Line2D([0], [0], marker="x", color="#777777", ls="", label=f"Noise / isolated ({len(noise)})"))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=3, fontsize=7, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_final_map(path, kde, zones, cluster_polys, hotspot_polys, hotspot_points, acc, study_area,
                   roads_proj, is_reference):
    fig, ax = plt.subplots(figsize=(11, 9))
    road_h = _base(ax, study_area, roads_proj, is_reference,
                   "Candidate accident hotspots - NH 66 (Mangalapuram to DUK)")
    _kde_image(ax, kde, alpha=0.35, cmap="Greys")
    for cid, poly in cluster_polys.items():
        geoms = [poly] if poly.geom_type == "Polygon" else list(poly.geoms)
        for g in geoms:
            ax.plot(*g.exterior.xy, color="#3182bd", lw=0.9, ls=":", zorder=3)
    pt_handles = _points(ax, acc)
    for _, h in hotspot_polys.iterrows():
        col = config.SEVERITY_COLOURS[h["Severity_Class"]]
        geoms = [h.geometry] if h.geometry.geom_type == "Polygon" else list(h.geometry.geoms)
        for g in geoms:
            ax.fill(*g.exterior.xy, color=col, alpha=0.30, zorder=2)
            ax.plot(*g.exterior.xy, color=col, lw=2, zorder=4)
    for _, h in hotspot_points.iterrows():
        col = config.SEVERITY_COLOURS[h["Severity_Class"]]
        ax.scatter(h.geometry.x, h.geometry.y, marker="D", s=80, color=col,
                   edgecolors="black", linewidths=1.0, zorder=7)
        label = f"{h['Hotspot_ID']} {h['Severity_Class']} ({h['Risk_Score']:.0f})"
        ax.annotate(label, (h.geometry.x, h.geometry.y), xytext=(10, 10), textcoords="offset points", fontsize=7.5,
                    fontweight="bold", zorder=8, bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.85))
    handles = pt_handles + [
        road_h,
        Patch(facecolor=config.SEVERITY_COLOURS["RED"], alpha=0.5, label="RED hotspot (high)"),
        Patch(facecolor=config.SEVERITY_COLOURS["ORANGE"], alpha=0.5, label="ORANGE hotspot (medium)"),
        Patch(facecolor=config.SEVERITY_COLOURS["GREEN"], alpha=0.5, label="GREEN hotspot (low)"),
        Line2D([0], [0], marker="*", color="w", markerfacecolor="white", markeredgecolor="black", markersize=13,
               label="DUK demonstration hotspot"),
        Line2D([0], [0], color="#3182bd", ls=":", label="DBSCAN cluster outline"),
        Patch(facecolor="#888888", alpha=0.4, label="KDE density (darker = denser)"),
    ]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=3, fontsize=7, framealpha=0.92)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_road_profile(path, profile, peaks):
    if profile is None or profile.empty:
        return
    roads = profile["Road"].unique()
    fig, axes = plt.subplots(len(roads), 1, figsize=(10, 3 * len(roads)), squeeze=False)
    for ax, road in zip(axes[:, 0], roads):
        p = profile[profile["Road"] == road]
        ax.plot(p["Chainage_m"], p["Density_per_km"], color="#b2182b")
        ax.fill_between(p["Chainage_m"], p["Density_per_km"], color="#b2182b", alpha=0.2)
        if peaks is not None and not peaks.empty:
            pk = peaks[peaks["Road"] == road]
            ax.scatter(pk["Chainage_m"], pk["Density_per_km"], color="black", zorder=3)
        ax.set_title(f"{road}: accident density along the road (1-D KDE)", fontsize=9)
        ax.set_xlabel("Distance along road (m)")
        ax.set_ylabel("events per km")
    fig.suptitle(DISCLAIMER, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def make_folium_map(path, roads_4326, acc_4326, hotspot_polys_4326, hotspot_points_4326, is_reference):
    import folium
    c = roads_4326.geometry.union_all().centroid
    m = folium.Map(location=[c.y, c.x], zoom_start=16, control_scale=True, tiles="OpenStreetMap")
    folium.GeoJson(roads_4326[["geometry"]], name=_road_label(is_reference),
                   style_function=lambda f: {"color": "#222", "weight": 3}).add_to(m)
    fg = folium.FeatureGroup(name="Synthetic accidents", show=True)
    col = {"Minor": "#6baed6", "Major": "#e6550d", "Fatal": "#000000"}
    for _, r in acc_4326.dropna(subset=["geometry"]).iterrows():
        folium.CircleMarker([r.geometry.y, r.geometry.x], radius=3, color=col.get(r["Severity"], "#888"),
                            fill=True, fill_opacity=0.8,
                            tooltip=f"{r['Accident_ID']} {r['Severity']} {r['Date']} {r['Time']}").add_to(fg)
    fg.add_to(m)
    if len(hotspot_polys_4326):
        folium.GeoJson(hotspot_polys_4326, name="Hotspot polygons",
                       style_function=lambda f: {"color": config.SEVERITY_COLOURS[f["properties"]["Severity_Class"]],
                                                 "fillOpacity": 0.3, "weight": 2},
                       tooltip=folium.GeoJsonTooltip(fields=["Hotspot_ID", "Severity_Class", "Risk_Score",
                                                             "Accident_Count"])).add_to(m)
        for _, h in hotspot_points_4326.iterrows():
            folium.Marker([h.geometry.y, h.geometry.x],
                          popup=f"{h['Hotspot_ID']} {h['Severity_Class']} score {h['Risk_Score']} "
                                f"({h['Accident_Count']} synthetic events)",
                          icon=folium.Icon(color={"RED": "red", "ORANGE": "orange", "GREEN": "green"}[h["Severity_Class"]])
                          ).add_to(m)
    folium.LayerControl().add_to(m)
    m.save(path)
