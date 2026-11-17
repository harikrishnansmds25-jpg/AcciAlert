"""
hotspot_classification.py - combines KDE and DBSCAN, then scores GREEN/ORANGE/RED.

Beginner explanation
--------------------
KDE and DBSCAN are used together, not as copies of each other:

* A DBSCAN cluster becomes a hotspot only if it has enough events AND most of its
  points sit inside a high-density KDE zone  -> method "KDE+DBSCAN".
* A high-density KDE zone that no accepted cluster covers is kept if it holds
  enough events                               -> method "KDE-only".
* A DBSCAN cluster that is too small or not in a high-density zone is NOT a
  hotspot; it is listed as a "watch cluster" with the reason.
* Noise points are isolated events and are listed separately, never called hotspots.

Risk score (0-100) = 100 * sum(weight_i * value_i / max_i) over three components:
weighted severity (fatal and deaths count most), accident count, and mean KDE density.
Class thresholds come from config.py (quantiles of the hotspots' own scores by default).
Because quantile thresholds are relative, at least one hotspot is always RED if there are
>= 3 hotspots - classes rank hotspots against EACH OTHER, they are not absolute risk levels.
"""
import numpy as np
import pandas as pd
from shapely.ops import unary_union

import config


def _stats_for_points(pts):
    sev = pts["Severity"].value_counts()
    sev_score = (config.W_MINOR * sev.get("Minor", 0) + config.W_MAJOR * sev.get("Major", 0)
                 + config.W_FATAL * sev.get("Fatal", 0)
                 + config.W_DEATH * pts["deaths_num"].sum() + config.W_INJURY * pts["injuries_num"].sum())
    labels = pts.loc[pts["Cluster_ID"] > 0, "Cluster_ID"]
    return {
        "Accident_Count": len(pts),
        "Minor_Count": int(sev.get("Minor", 0)), "Major_Count": int(sev.get("Major", 0)),
        "Fatal_Count": int(sev.get("Fatal", 0)),
        "Total_Deaths": int(pts["deaths_num"].sum()), "Total_Injuries": int(pts["injuries_num"].sum()),
        "KDE_Density": round(float(pts["KDE_Density"].mean()), 2),
        "KDE_Density_Max": round(float(pts["KDE_Density"].max()), 2),
        "DBSCAN_Cluster_ID": int(labels.mode().iat[0]) if len(labels) else -1,
        "Severity_Score_Raw": float(sev_score),
        "Primary_Road": pts["Road_Name"].mode().iat[0],
    }


def identify_candidates(acc, cluster_polys, kde_zones):
    """
    acc: projected GeoDataFrame with Cluster_ID, KDE_Density, severity columns.
    Returns (candidates list, excluded_clusters DataFrame, isolated points GeoDataFrame).
    Each candidate: dict(method, geometry, point_index, stats...).
    """
    zone_union = unary_union(kde_zones) if kde_zones else None
    acc = acc.copy()
    acc["In_KDE_High"] = acc.geometry.within(zone_union) if zone_union is not None else False

    candidates, excluded = [], []
    for cid, poly in cluster_polys.items():
        pts = acc[acc["Cluster_ID"] == cid]
        n, frac = len(pts), float(pts["In_KDE_High"].mean())
        reasons = []
        if n < config.HOTSPOT_MIN_ACCIDENTS:
            reasons.append(f"only {n} events (< {config.HOTSPOT_MIN_ACCIDENTS})")
        if frac < config.CLUSTER_KDE_POINT_FRACTION:
            reasons.append(f"{frac:.0%} of points in high-density KDE zones (< {config.CLUSTER_KDE_POINT_FRACTION:.0%})")
        if reasons:
            excluded.append({"Cluster_ID": cid, "Accident_Count": n, "Share_in_KDE_high": round(frac, 2),
                             "Reason": "; ".join(reasons)})
            continue
        candidates.append({"Method": "KDE+DBSCAN", "geometry": poly, "points": pts,
                           "Share_in_KDE_high": round(frac, 2)})

    accepted_union = unary_union([c["geometry"] for c in candidates]) if candidates else None
    for zone in kde_zones:
        if accepted_union is not None and zone.intersects(accepted_union):
            continue                       # already represented by an accepted cluster
        pts = acc[acc.geometry.within(zone)]
        if len(pts) >= config.KDE_ONLY_MIN_ACCIDENTS:
            candidates.append({"Method": "KDE-only", "geometry": zone, "points": pts,
                               "Share_in_KDE_high": 1.0})
        else:
            excluded.append({"Cluster_ID": -1, "Accident_Count": len(pts), "Share_in_KDE_high": 1.0,
                             "Reason": f"KDE zone with only {len(pts)} events (< {config.KDE_ONLY_MIN_ACCIDENTS}), no accepted cluster"})

    isolated = acc[acc["Cluster_ID"] == -1]
    return candidates, pd.DataFrame(excluded), isolated, acc


def score_and_classify(candidates):
    """Adds Risk_Score, Severity_Class, Hotspot_ID (ranked by score). Returns DataFrame-like list of dicts."""
    if not candidates:
        return [], {"method": "n/a", "thresholds": (None, None), "note": "no candidate hotspots"}
    rows = []
    for c in candidates:
        s = _stats_for_points(c["points"])
        s.update({"Method": c["Method"], "geometry": c["geometry"], "Share_in_KDE_high": c["Share_in_KDE_high"],
                  "point_index": list(c["points"].index)})
        rows.append(s)
    df = pd.DataFrame(rows)

    w = config.SCORE_COMPONENT_WEIGHTS
    comp = {"severity": "Severity_Score_Raw", "count": "Accident_Count", "kde": "KDE_Density"}
    score = np.zeros(len(df))
    for key, col in comp.items():
        mx = df[col].max()
        score += w[key] * (df[col] / mx if mx > 0 else 0)
    df["Risk_Score"] = np.round(100 * score / sum(w.values()), 1)

    if config.THRESHOLD_METHOD == "quantile" and len(df) >= 3:
        t1, t2 = (float(df["Risk_Score"].quantile(q)) for q in config.QUANTILE_THRESHOLDS)
        method = f"quantile {config.QUANTILE_THRESHOLDS} of the hotspots' own scores"
    else:
        t1, t2 = config.FIXED_THRESHOLDS
        method = f"fixed thresholds {config.FIXED_THRESHOLDS}"
    df["Severity_Class"] = np.where(df["Risk_Score"] < t1, "GREEN",
                                    np.where(df["Risk_Score"] < t2, "ORANGE", "RED"))
    df = df.sort_values("Risk_Score", ascending=False).reset_index(drop=True)
    df["Hotspot_ID"] = [f"HS{i:02d}" for i in range(1, len(df) + 1)]
    info = {"method": method, "thresholds": (round(t1, 1), round(t2, 1))}
    return df, info


def write_hotspot_report(path, hotspots, info, excluded, isolated, geometry_is_reference, n_total):
    L = []
    add = L.append
    add("HOTSPOT ANALYSIS REPORT")
    add("=" * 70)
    add("IMPORTANT: all accident records are SYNTHETIC. GREEN/ORANGE/RED classes rank hotspots in")
    add("the synthetic data against each other. They are NOT verified real-world risk assessments.")
    if geometry_is_reference:
        add("The road geometry is a simplified reference line, not a verified road centreline.")
    add("")
    add("METHOD")
    add(f"  1. High-density KDE zones: top {100 - config.KDE_HIGH_PERCENTILE}% of corridor cells (KDE).")
    add(f"  2. DBSCAN clusters: eps={config.DBSCAN_EPS_M:g} m, min_samples={config.DBSCAN_MIN_SAMPLES}.")
    add(f"  3. A cluster is a hotspot if it has >= {config.HOTSPOT_MIN_ACCIDENTS} events and >= "
        f"{config.CLUSTER_KDE_POINT_FRACTION:.0%} of its points in high-density zones (KDE+DBSCAN).")
    add(f"  4. A high-density zone with no accepted cluster is kept if it has >= {config.KDE_ONLY_MIN_ACCIDENTS} "
        "events (KDE-only).")
    add("  5. Isolated (noise) points and rejected clusters are listed below and are not hotspots.")
    add("")
    add("SCORING")
    add(f"  Weighted severity = {config.W_MINOR:g}*Minor + {config.W_MAJOR:g}*Major + {config.W_FATAL:g}*Fatal"
        f" + {config.W_DEATH:g}*Deaths + {config.W_INJURY:g}*Injuries")
    add(f"  Risk_Score (0-100) = 100 * ({config.SCORE_COMPONENT_WEIGHTS['severity']:g}*severity/max"
        f" + {config.SCORE_COMPONENT_WEIGHTS['count']:g}*count/max + {config.SCORE_COMPONENT_WEIGHTS['kde']:g}*mean KDE/max),")
    add("  where 'max' is the largest value among the identified hotspots.")
    add(f"  Thresholds: {info['method']} -> GREEN < {info['thresholds'][0]} <= ORANGE < {info['thresholds'][1]} <= RED")
    add("  Quantile thresholds are relative: they guarantee a spread of classes rather than measure absolute risk.")
    add("")
    add("HOTSPOTS")
    if len(hotspots) == 0:
        add("  None identified with the current settings.")
    else:
        cols = ["Hotspot_ID", "Risk_Level", "Severity_Class", "Risk_Score", "Method", "Accident_Count", "Minor_Count",
                "Major_Count", "Fatal_Count", "Total_Deaths", "Total_Injuries", "KDE_Density",
                "DBSCAN_Cluster_ID", "Primary_Road"]
        add(hotspots[cols].to_string(index=False))
        add("")
        add(f"  Events inside hotspots: {int(hotspots['Accident_Count'].sum())} of {n_total} "
            "(a point in two overlapping hotspots would be counted twice)")
        add("  Class counts: " + ", ".join(f"{k}={int((hotspots['Severity_Class'] == k).sum())}"
                                           for k in ("GREEN", "ORANGE", "RED")))
    add("")
    add("DBSCAN CLUSTERS / KDE ZONES NOT ACCEPTED AS HOTSPOTS")
    if excluded is None or len(excluded) == 0:
        add("  None.")
    else:
        add(excluded.to_string(index=False))
    add("")
    add(f"ISOLATED POINTS (DBSCAN noise, not hotspots): {len(isolated)}")
    add("")
    add("LIMITATIONS")
    add("  * Synthetic events: hotspots reflect how the generator placed clusters, not real risk.")
    add("  * Hotspot positions are centroids of event points; they are not snapped to any road.")
    add("  * Weights and thresholds are modelling choices; change them in config.py and compare.")
    path.write_text("\n".join(L), encoding="utf-8")
