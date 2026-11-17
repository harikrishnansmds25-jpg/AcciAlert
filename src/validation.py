"""
validation.py - checks the accident CSV and inspects the road GeoJSON.

Beginner explanation
--------------------
Before any analysis we ask: "Is the data usable, and is it what we think it is?"
Two things are checked:

1. The accident table (columns, missing values, duplicates, valid dates, severity
   rules, distance of every point from its road).
2. The road GeoJSON. A real road centreline has many vertices (it bends). A file
   with 2 vertices per road is just a straight line between two reference points.
   We detect that and say so, instead of pretending the points are "on the road".

Nothing is moved or corrected here. Problems are written into flag columns.
"""
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point

import config
from src.data_preparation import InputError, road_class_column


# ----------------------------------------------------------------------------
# Road geometry inspection
# ----------------------------------------------------------------------------
def _lines_of(geom):
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    return []


def inspect_road_geometry(roads_4326, roads_proj):
    """Decide whether the GeoJSON is a detailed centreline or a simplified reference line."""
    col = road_class_column(roads_proj)
    rows, reasons = [], []
    all_simple = True
    for i, geom in enumerate(roads_proj.geometry):
        lines = _lines_of(geom)
        vertices = sum(len(l.coords) for l in lines)
        length = sum(l.length for l in lines)
        chord = 0.0
        if len(lines) == 1:
            c = lines[0].coords
            chord = Point(c[0]).distance(Point(c[-1]))
        sinuosity = (length / chord) if chord > 0 else float("nan")
        props = roads_proj.drop(columns="geometry").iloc[i].to_dict()
        note = str(props.get("note", ""))
        rows.append({"index": i, "class": props.get(col, "?") if col else "?",
                     "geometry_type": geom.geom_type, "vertices": vertices,
                     "length_m": round(length, 1), "sinuosity": round(sinuosity, 4) if sinuosity == sinuosity else None,
                     "note": note})
        if vertices > config.SIMPLIFIED_MAX_VERTICES:
            all_simple = False
        if any(w in note.lower() for w in ("reference line", "straight line", "simplified")):
            reasons.append(f"feature {i} note says: '{note}'")
    if all_simple:
        reasons.append(f"every road feature has <= {config.SIMPLIFIED_MAX_VERTICES} vertices "
                       "(a real road centreline normally has many)")

    is_reference = all_simple or any("note says" in r for r in reasons)

    # connectivity between the road classes (gap between nearest endpoints)
    gap = None
    classes = {}
    if col:
        for name, grp in roads_proj.groupby(col):
            classes[name] = [l for g in grp.geometry for l in _lines_of(g)]
    if len(classes) >= 2:
        names = list(classes)
        ends = {n: [Point(l.coords[0]) for l in classes[n]] + [Point(l.coords[-1]) for l in classes[n]]
                for n in names}
        gap = min(a.distance(b) for a in ends[names[0]] for b in ends[names[1]])

    return {
        "n_features": len(roads_proj),
        "geometry_types": sorted(set(roads_proj.geom_type)),
        "property_columns": [c for c in roads_proj.columns if c != "geometry"],
        "features": rows,
        "is_reference_only": is_reference,
        "reasons": reasons,
        "endpoint_gap_m": gap,
        "classes": sorted(classes),
        "bounds_4326": tuple(round(v, 6) for v in roads_4326.total_bounds),
        "total_length_m": round(sum(r["length_m"] for r in rows), 1),
    }


# ----------------------------------------------------------------------------
# Accident table validation
# ----------------------------------------------------------------------------
def _in_range(value, lo, hi):
    return value >= lo and (hi is None or value <= hi)


def validate_accidents(df_raw, roads_by_class, study_area):
    """Return (annotated DataFrame, results dict). Original columns are kept unchanged."""
    missing_cols = [c for c in config.REQUIRED_COLUMNS if c not in df_raw.columns]
    if missing_cols:
        raise InputError(f"CSV is missing required columns: {missing_cols}. "
                         f"Expected: {config.REQUIRED_COLUMNS}")
    extra_cols = [c for c in df_raw.columns if c not in config.REQUIRED_COLUMNS]

    df = df_raw.copy()
    n = len(df)
    flags = [[] for _ in range(n)]

    def flag(mask, text):
        for i in np.flatnonzero(mask.to_numpy() if hasattr(mask, "to_numpy") else mask):
            flags[i].append(text)

    # 1. missing values -------------------------------------------------------
    missing = df[config.REQUIRED_COLUMNS].isna()
    flag(missing.any(axis=1), "missing_value")

    # 2. coordinates ----------------------------------------------------------
    df["lat_num"] = pd.to_numeric(df["Latitude"], errors="coerce")
    df["lon_num"] = pd.to_numeric(df["Longitude"], errors="coerce")
    bad_coord = (df["lat_num"].isna() | df["lon_num"].isna()
                 | ~df["lat_num"].between(-90, 90) | ~df["lon_num"].between(-180, 180))
    flag(bad_coord, "invalid_coordinate")

    # 3. duplicates -----------------------------------------------------------
    dup_id = df["Accident_ID"].duplicated(keep=False) & df["Accident_ID"].notna()
    flag(dup_id, "duplicate_id")
    dup_xy = df.duplicated(subset=["Latitude", "Longitude"], keep=False) & ~bad_coord
    flag(dup_xy, "duplicate_coordinates")

    # 4. date / time ----------------------------------------------------------
    d = pd.to_datetime(df["Date"], format=config.DATE_FORMAT, errors="coerce")
    t = pd.to_datetime(df["Time"], format=config.TIME_FORMAT, errors="coerce")
    flag(d.isna(), "invalid_date")
    flag(t.isna(), "invalid_time")

    # 5. severity + casualties ------------------------------------------------
    bad_sev = ~df["Severity"].isin(config.SEVERITY_CATEGORIES)
    flag(bad_sev, "invalid_severity")
    df["deaths_num"] = pd.to_numeric(df["Deaths"], errors="coerce")
    df["injuries_num"] = pd.to_numeric(df["Injuries"], errors="coerce")
    bad_cas_num = df["deaths_num"].isna() | df["injuries_num"].isna()
    flag(bad_cas_num, "invalid_casualty_number")
    inconsistent = pd.Series(False, index=df.index)
    for i, row in df.iterrows():
        rule = config.SEVERITY_RULES.get(row["Severity"])
        if rule is None or bad_cas_num[i]:
            continue
        ok = (_in_range(row["deaths_num"], *rule["deaths"])
              and _in_range(row["injuries_num"], *rule["injuries"]))
        inconsistent[i] = not ok
    flag(inconsistent, "severity_casualty_inconsistent")

    # 6. road name / type / distance to the matching road -----------------------
    dist = np.full(n, np.nan)
    inside = np.zeros(n, dtype=bool)
    from pyproj import Transformer
    tr = Transformer.from_crs(config.CRS_GEOGRAPHIC, config.CRS_PROJECTED, always_xy=True)
    for i in range(n):
        if bad_coord[i]:
            continue
        x, y = tr.transform(df.at[i, "lon_num"], df.at[i, "lat_num"])
        p = Point(x, y)
        inside[i] = study_area.contains(p)
        name = df.at[i, "Road_Name"]
        cls = config.ROAD_NAME_MAP.get(name, (name, None))[0]
        geom = roads_by_class.get(cls)
        if geom is None:
            flags[i].append("road_name_has_no_matching_geometry")
            geom = roads_by_class.get("ALL") or min(roads_by_class.values(), key=lambda g: g.distance(p))
        dist[i] = p.distance(geom)
        expected_type = config.ROAD_NAME_MAP.get(name, (None, None))[1]
        if expected_type and df.at[i, "Road_Type"] != expected_type:
            flags[i].append("road_type_mismatch")
    df["Road_Distance_m"] = np.round(dist, 3)
    df["Within_Road_Tolerance"] = df["Road_Distance_m"] <= config.ROAD_TOLERANCE_M
    df["Inside_Study_Area"] = inside
    flag(~df["Within_Road_Tolerance"] & ~bad_coord, "outside_road_tolerance")
    flag(~df["Inside_Study_Area"] & ~bad_coord, "outside_study_area")

    df["Issue_Flags"] = [";".join(f) for f in flags]
    # Records unusable for spatial analysis (cannot be located or classified)
    unusable = bad_coord | bad_sev | bad_cas_num
    df["Use_In_Analysis"] = ~unusable

    # Build check table ---------------------------------------------------------
    def status(count, hard=True):
        return "PASS" if count == 0 else ("FAIL" if hard else "WARN")

    checks = [
        ("Required columns present", "PASS", f"{len(config.REQUIRED_COLUMNS)} of {len(config.REQUIRED_COLUMNS)}"
         + (f" (extra columns ignored: {extra_cols})" if extra_cols else "")),
        ("Missing values", status(int(missing.any(axis=1).sum())), f"{int(missing.any(axis=1).sum())} rows"),
        ("Valid latitude/longitude", status(int(bad_coord.sum())), f"{int(bad_coord.sum())} invalid"),
        ("Duplicate Accident_ID", status(int(dup_id.sum())), f"{int(dup_id.sum())} rows"),
        ("Duplicate coordinates", status(int(dup_xy.sum()), hard=False), f"{int(dup_xy.sum())} rows"),
        ("Date format YYYY-MM-DD", status(int(d.isna().sum())), f"{int(d.isna().sum())} invalid"),
        ("Time format HH:MM", status(int(t.isna().sum())), f"{int(t.isna().sum())} invalid"),
        ("Severity categories", status(int(bad_sev.sum())), f"{int(bad_sev.sum())} invalid"),
        ("Severity / deaths / injuries consistent", status(int(inconsistent.sum())), f"{int(inconsistent.sum())} rows"),
        (f"Within {config.ROAD_TOLERANCE_M:g} m of matching road geometry",
         status(int((~df['Within_Road_Tolerance'] & ~bad_coord).sum()), hard=False),
         f"{int((~df['Within_Road_Tolerance'] & ~bad_coord).sum())} outside"),
        ("Inside study-area boundary", status(int((~df['Inside_Study_Area'] & ~bad_coord).sum()), hard=False),
         f"{int((~df['Inside_Study_Area'] & ~bad_coord).sum())} outside"),
    ]
    valid_dist = df["Road_Distance_m"].dropna()
    results = {
        "n_records": n,
        "n_usable": int(df["Use_In_Analysis"].sum()),
        "n_no_flags": int((df["Issue_Flags"] == "").sum()),
        "checks": checks,
        "dist_max": float(valid_dist.max()) if len(valid_dist) else float("nan"),
        "dist_mean": float(valid_dist.mean()) if len(valid_dist) else float("nan"),
        "dist_by_road": df.groupby("Road_Name")["Road_Distance_m"].agg(["count", "max", "mean"]).round(3),
        "date_min": d.min(), "date_max": d.max(),
        "severity_counts": df["Severity"].value_counts().to_dict(),
        "road_counts": df["Road_Name"].value_counts().to_dict(),
        "flagged_ids": df.loc[df["Issue_Flags"] != "", ["Accident_ID", "Issue_Flags"]],
        "has_hard_failure": any(c[1] == "FAIL" for c in checks),
    }
    return df, results


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def write_validation_report(path, geom_info, results):
    L = []
    add = L.append
    add("DATA VALIDATION REPORT")
    add("=" * 70)
    add("The accident records are SYNTHETIC (generated for methodology demonstration).")
    add("They are not real recorded road accidents.")
    add("")
    add("1. ROAD GEOMETRY INSPECTION")
    add("-" * 70)
    add(f"Features: {geom_info['n_features']} | geometry types: {', '.join(geom_info['geometry_types'])}")
    add(f"Property columns: {', '.join(geom_info['property_columns'])}")
    add(f"Road classes: {', '.join(geom_info['classes'])} | total length: {geom_info['total_length_m']} m")
    add(f"Bounding box (lon/lat): {geom_info['bounds_4326']}")
    if geom_info["endpoint_gap_m"] is not None:
        add(f"Gap between nearest endpoints of the two road classes: {geom_info['endpoint_gap_m']:.2f} m")
    for r in geom_info["features"]:
        add(f"  - [{r['class']}] {r['geometry_type']}, {r['vertices']} vertices, {r['length_m']} m, "
            f"sinuosity {r['sinuosity']}")
    add("")
    if geom_info["is_reference_only"]:
        add("VERDICT: SIMPLIFIED REFERENCE LINE - NOT a verified road centreline.")
        for r in geom_info["reasons"]:
            add(f"  * {r}")
        add("Consequence: distances reported below are distances to this REFERENCE LINE. They show that")
        add("the points follow the reference line, NOT that they lie on the physical NH 66 or access road.")
        add("To obtain a road-accurate analysis, replace the GeoJSON with an OSM/surveyed centreline and")
        add("regenerate or re-validate the accident points against it.")
    else:
        add("VERDICT: the geometry has enough vertices to be a detailed centreline. It has NOT been")
        add("independently verified against a map or survey by this program.")
    add("")
    add("2. ACCIDENT TABLE VALIDATION")
    add("-" * 70)
    add(f"Records read: {results['n_records']} | usable for analysis: {results['n_usable']} | "
        f"with no flags at all: {results['n_no_flags']}")
    for name, st, detail in results["checks"]:
        add(f"  [{st}] {name}: {detail}")
    add("")
    add(f"Date range: {results['date_min'].date() if pd.notna(results['date_min']) else 'n/a'} to "
        f"{results['date_max'].date() if pd.notna(results['date_max']) else 'n/a'}")
    add(f"Severity counts: {results['severity_counts']}")
    add(f"Road counts: {results['road_counts']}")
    add("")
    add("3. DISTANCE TO MATCHING ROAD GEOMETRY (metres, EPSG:32643)")
    add("-" * 70)
    add(f"Maximum: {results['dist_max']:.3f} | Mean: {results['dist_mean']:.3f} | "
        f"Tolerance: {config.ROAD_TOLERANCE_M:g}")
    add(results["dist_by_road"].to_string())
    add("")
    fl = results["flagged_ids"]
    add(f"Records with any flag: {len(fl)}")
    if len(fl):
        add(fl.head(50).to_string(index=False))
        if len(fl) > 50:
            add(f"... {len(fl) - 50} more (see Issue_Flags in validated_accidents.geojson)")
    add("")
    add("4. HANDLING POLICY")
    add("-" * 70)
    add("No coordinate was moved, snapped or edited. Records with unusable coordinates, severity or")
    add("casualty numbers are excluded from analysis; all other flagged records are kept and flagged.")
    add("Original CSV values are preserved unchanged in validated_accidents.geojson.")
    path.write_text("\n".join(L), encoding="utf-8")
