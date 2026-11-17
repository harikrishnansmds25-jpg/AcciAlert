"""Responsive, data-driven HTML dashboard around the interactive Folium map."""

from __future__ import annotations

from pathlib import Path

from .validation import ProjectData


def create_dashboard(data: ProjectData, path: Path) -> None:
    accidents = data.layers["validated_accidents"]
    hotspots = data.layers["hotspot_points"].sort_values("Hotspot_ID")
    clusters = data.layers["dbscan_clustered_accidents"]
    cluster_count = len(set(int(value) for value in clusters["Cluster_ID"].dropna() if int(value) > 0))

    html_content = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AcciAlert | Know the Risk Before You Reach</title>
<style>
:root{font-family:Inter,"Segoe UI",Arial,sans-serif;color:#1f2937;background:#f2f5f8}
*{box-sizing:border-box}body{margin:0}.header{background:#173a5e;color:white;padding:22px clamp(16px,4vw,48px)}
.header h1{margin:0 0 5px;font-size:clamp(22px,3vw,32px)}.header p{margin:0;color:#dce7f1}
main{max-width:1440px;margin:auto;padding:20px clamp(12px,3vw,36px) 36px}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:16px 0}
.card,.panel{background:white;border:1px solid #e1e7ed;border-radius:9px;box-shadow:0 2px 8px #152b3b0a}
.card{padding:16px}.card .label{font-size:12px;color:#607080}.card .value{font-size:28px;font-weight:700;color:#173a5e;margin-top:6px}
.map-panel{overflow:hidden}.panel-title{padding:15px 18px 10px;margin:0;font-size:17px}
.map-frame{display:block;width:100%;height:min(68vh,680px);min-height:420px;border:0}
@media(max-width:720px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}
.map-frame{height:62vh;min-height:390px}.card .value{font-size:24px}}
@media(max-width:380px){.cards{gap:8px}.card{padding:12px}.card .value{font-size:21px}}
</style></head>
<body><header class="header"><h1>AcciAlert</h1>
<p>Know the Risk Before You Reach</p></header>
<main>
<section class="cards">
<div class="card"><div class="label">Total synthetic accident records</div><div class="value">__ACCIDENTS__</div></div>
<div class="card"><div class="label">KDE density zones</div><div class="value">__KDE_ZONES__</div></div>
<div class="card"><div class="label">DBSCAN clusters</div><div class="value">__CLUSTERS__</div></div>
<div class="card"><div class="label">Candidate hotspots</div><div class="value">__HOTSPOTS__</div></div>
</section>
<section class="panel map-panel"><h2 class="panel-title">Interactive OpenStreetMap and analysis layers</h2>
<iframe class="map-frame" title="Interactive traffic hotspot map" src="hotspots_on_osm.html" allow="autoplay"></iframe></section>
</main>
</body></html>"""
    substitutions = {
        "__ACCIDENTS__": str(len(accidents)),
        "__KDE_ZONES__": str(data.kde_zone_count),
        "__CLUSTERS__": str(cluster_count),
        "__HOTSPOTS__": str(len(hotspots)),
    }
    for key, replacement in substitutions.items():
        html_content = html_content.replace(key, replacement)
    path.write_text(html_content, encoding="utf-8")
