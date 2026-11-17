"""Folium map layers and georeferenced KDE raster overlay."""

from __future__ import annotations

import base64
import html
import json
import math
from io import BytesIO
from pathlib import Path

import folium
import matplotlib
import numpy as np
from branca.element import Element
from folium.plugins import Fullscreen, MeasureControl
from PIL import Image

from .validation import ProjectData, reproject_raster_to_wgs84

HOTSPOT_COLOURS = {"RED": "#e31a1c", "ORANGE": "#ff8c00", "GREEN": "#2ca25f"}
ACCIDENT_COLOURS = {"Minor": "#2563eb", "Major": "#2563eb", "Fatal": "#e11d48"}
WARNING_BUFFER_METERS = {"GREEN": 50, "ORANGE": 100, "RED": 150}


def _distance_m(first: list[float], second: list[float]) -> float:
    radius = 6_371_008.8
    lat1, lat2 = math.radians(first[1]), math.radians(second[1])
    dlat = lat2 - lat1
    dlon = math.radians(second[0] - first[0])
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _simulation_route(roads_osm, roads_duk) -> list[list[float]]:
    nh = [[float(x), float(y)] for x, y in list(roads_osm.geometry.iloc[0].coords)]
    duk = [[float(x), float(y)] for x, y in list(roads_duk.geometry.iloc[0].coords)]
    options = [
        (nh, duk), (nh, list(reversed(duk))),
        (list(reversed(nh)), duk), (list(reversed(nh)), list(reversed(duk))),
    ]
    main, access = min(options, key=lambda pair: _distance_m(pair[0][-1], pair[1][0]))
    if _distance_m(main[-1], access[0]) > 25:
        raise ValueError("Mapped NH 66 and DUK lines do not connect for the dashboard vehicle demo.")
    return main + access[1:]


def _simulation_markup(
    map_name: str, route: list[list[float]], hotspots: list[dict], dbscan_clusters: list[dict]
) -> tuple[str, str]:
    route_json = json.dumps([[lat, lon] for lon, lat in route], separators=(",", ":"))
    hotspots_json = json.dumps(hotspots, separators=(",", ":"))
    panel = """
    <style>
      #vehicle-demo{position:fixed;right:12px;bottom:18px;z-index:10000;width:min(310px,calc(100vw - 24px));
        padding:10px;background:rgba(255,255,255,.97);border:1px solid #cbd5df;border-radius:8px;
        box-shadow:0 2px 9px #0003;color:#263646;font:12px/1.4 Arial,sans-serif}
      #vehicle-demo:not([open]){width:max-content;max-width:calc(100vw - 24px)}
      #vehicle-demo summary{display:flex;align-items:center;justify-content:space-between;gap:20px;
        color:#173a5e;font-size:14px;font-weight:700;cursor:pointer;list-style:none}
      #vehicle-demo summary::-webkit-details-marker{display:none}
      #vehicle-demo summary::after{content:"＋";font-size:16px}
      #vehicle-demo[open] summary{margin-bottom:7px}
      #vehicle-demo[open] summary::after{content:"−"}
      #vehicle-demo .vehicle-demo-buttons{display:flex;flex-wrap:wrap;gap:5px}
      #vehicle-demo button{padding:6px 8px;border:1px solid #9aabba;border-radius:5px;background:#fff;
        color:#173a5e;font:600 12px Arial,sans-serif;cursor:pointer}
      #vehicle-demo button:disabled{opacity:.5;cursor:not-allowed}
      #vehicle-demo p{margin:6px 0 0;overflow-wrap:anywhere}
      #vehicle-demo .vehicle-demo-alert{font-weight:700;color:#173a5e}
      .vehicle-demo-div-icon{border:0;background:transparent}
      .vehicle-demo-icon{display:block;width:36px;height:36px;filter:drop-shadow(0 2px 3px #0009)}
    </style>
    <details id="vehicle-demo" aria-label="Demo vehicle simulation">
      <summary>Demo Vehicle Simulation</summary>
      <div class="vehicle-demo-buttons">
        <button id="vehicle-demo-toggle" type="button" aria-pressed="false">Simulation: Off</button>
        <button id="vehicle-demo-voice" type="button" aria-pressed="true">Voice: On</button>
        <button id="vehicle-demo-test-voice" type="button">Test Voice</button>
        <button id="vehicle-demo-start" type="button" disabled>Start Simulation</button>
        <button id="vehicle-demo-pause" type="button" disabled>Pause Simulation</button>
        <button id="vehicle-demo-reset" type="button" disabled>Reset Vehicle</button>
      </div>
      <p id="vehicle-demo-state" role="status" aria-live="polite">Simulation is off.</p>
      <p id="vehicle-demo-position">Vehicle position: not available</p>
      <p id="vehicle-demo-risk">Outside hotspot</p>
      <p id="vehicle-demo-alert" class="vehicle-demo-alert" role="status" aria-live="polite">No demo alert yet.</p>
      <p id="vehicle-demo-voice-status" role="status" aria-live="polite">Voice alerts are ready.</p>
      <p>Start drives forward. Use ↑/W forward, ↓/S backward, ←/A left, →/D right. Drag the vehicle to move it along the road.</p>
    </details>
    """
    script = r"""
    window.addEventListener("load",()=>{
      const map=__MAP__,route=__ROUTE__,hotspots=__HOTSPOTS__,clusters=__DBSCAN_CLUSTERS__;
      const radii={GREEN:50,ORANGE:100,RED:150},rank={GREEN:1,ORANGE:2,RED:3};
      const messages={GREEN:"Careful! Accident hotspot ahead. Please slow down.",
        ORANGE:"Attention! Accident hotspot ahead. Drive carefully.",
        RED:"Warning! High-risk accident hotspot ahead. Reduce your speed!"};
      const clusterMessages={GREEN:"Caution. You are entering a low-risk accident cluster. Please slow down.",
        ORANGE:"Attention. You are entering a medium-risk accident cluster. Drive carefully.",
        RED:"Warning. You are entering a high-risk accident cluster. Reduce your speed."};
      const approachAlert="Careful! Accident hotspot ahead. Please slow down.",approachDistance=10;
      const $=id=>document.getElementById(id),toggle=$("vehicle-demo-toggle"),start=$("vehicle-demo-start"),
        pause=$("vehicle-demo-pause"),reset=$("vehicle-demo-reset"),voice=$("vehicle-demo-voice"),
        testVoice=$("vehicle-demo-test-voice"),
        voiceStatus=$("vehicle-demo-voice-status"),state=$("vehicle-demo-state"),
        positionText=$("vehicle-demo-position"),riskText=$("vehicle-demo-risk"),alertText=$("vehicle-demo-alert");
      function speechContext(){try{const host=window.parent;if(host!==window&&host.speechSynthesis&&host.SpeechSynthesisUtterance)
          return {api:host.speechSynthesis,Utterance:host.SpeechSynthesisUtterance};}catch(e){}
        return {api:window.speechSynthesis,Utterance:window.SpeechSynthesisUtterance};}
      const speech=speechContext();
      const segments=[];let length=0;
      const meters=(a,b)=>{const r=6371008.8,la=a[0]*Math.PI/180,lb=b[0]*Math.PI/180,
        dlat=lb-la,dlon=(b[1]-a[1])*Math.PI/180;
        const h=Math.sin(dlat/2)**2+Math.cos(la)*Math.cos(lb)*Math.sin(dlon/2)**2;
        return 2*r*Math.atan2(Math.sqrt(h),Math.sqrt(1-h));};
      for(let i=0;i<route.length-1;i++){const a=route[i],b=route[i+1],d=meters(a,b);
        segments.push({a,b,d,at:length});length+=d;}
      let enabled=false,running=false,automatic=false,voiceEnabled=Boolean(speech.api&&speech.Utterance),
        raf=null,last=0,progress=0,side=0,marker=null;
      let active=new Set(),activeClusters=new Set(),approachAlertedClusters=new Set(),keys=new Set(),suppressAlerts=false;
      if(!voiceEnabled){voice.disabled=true;voice.setAttribute("aria-pressed","false");voice.textContent="Voice: Unsupported";voiceStatus.textContent="Speech synthesis is not supported in this browser.";}
      const featureId=f=>String((f.properties||{}).Hotspot_ID||f.id||"");
      const classOf=f=>String((f.properties||{}).Severity_Class||"").toUpperCase();
      function routePoint(m,offset=0){m=Math.max(0,Math.min(length,m));
        const s=segments.find(x=>m<=x.at+x.d)||segments[segments.length-1],t=s.d?Math.max(0,Math.min(1,(m-s.at)/s.d)):0;
        const lat=s.a[0]+(s.b[0]-s.a[0])*t,lon=s.a[1]+(s.b[1]-s.a[1])*t;
        const ys=6371008.8*Math.PI/180,xs=ys*Math.cos(lat*Math.PI/180),east=(s.b[1]-s.a[1])*xs,north=(s.b[0]-s.a[0])*ys,n=Math.hypot(east,north)||1;
        return {latlng:L.latLng(lat+east/n*offset/ys,lon-north/n*offset/xs),bearing:Math.atan2(east,north)*180/Math.PI};}
      function closest(latlng){const p=map.latLngToLayerPoint(latlng);let best=null;
        segments.forEach(s=>{const a=map.latLngToLayerPoint(s.a),b=map.latLngToLayerPoint(s.b),dx=b.x-a.x,dy=b.y-a.y,l=dx*dx+dy*dy,
          t=l?Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/l)):0,x=a.x+t*dx,y=a.y+t*dy,d=(p.x-x)**2+(p.y-y)**2;
          if(!best||d<best.d)best={d,m:s.at+s.d*t};});return best?best.m:0;}
      function say(f,source="hotspot"){const message=source==="approach"?approachAlert:(source==="cluster"?clusterMessages:messages)[classOf(f)];if(!message)return;alertText.textContent=message;
        if(!voiceEnabled||!speech.api||!speech.Utterance){voiceStatus.textContent="Voice is off or unavailable; the alert is shown above.";return;}
        const utterance=new speech.Utterance(message),voices=speech.api.getVoices();
        const english=voices.find(v=>/^en(-|$)/i.test(v.lang));if(english)utterance.voice=english;
        let started=false;const startTimer=window.setTimeout(()=>{if(!started&&enabled&&voiceEnabled)voiceStatus.textContent="The browser has not started speech. Toggle Voice off and on, then try again.";},6000);
        utterance.onstart=()=>{started=true;window.clearTimeout(startTimer);voiceStatus.textContent="Voice alert is playing.";};
        utterance.onend=()=>{window.clearTimeout(startTimer);if(voiceEnabled)voiceStatus.textContent="Voice alert finished.";};
        utterance.onerror=e=>{window.clearTimeout(startTimer);voiceStatus.textContent=e.error==="canceled"||e.error==="interrupted"?"Voice alert was cancelled.":`Browser could not play speech (${e.error||"error"}).`;};
        try{speech.api.resume();speech.api.speak(utterance);voiceStatus.textContent="Voice alert requested; waiting for browser playback.";}
        catch(e){window.clearTimeout(startTimer);voiceStatus.textContent="Browser could not start speech.";console.error("Vehicle demo speech failed:",e);}}
      function inRing(ll,ring){let inside=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){
        const a=ring[i],b=ring[j],cross=((a[1]>ll.lat)!==(b[1]>ll.lat))&&
          (ll.lng<(b[0]-a[0])*(ll.lat-a[1])/(b[1]-a[1])+a[0]);if(cross)inside=!inside;}return inside;}
      function inGeometry(ll,geometry){if(!geometry)return false;const coordinates=geometry.coordinates;
        const inPolygon=polygon=>inRing(ll,polygon[0])&&!polygon.slice(1).some(ring=>inRing(ll,ring));
        return geometry.type==="Polygon"?inPolygon(coordinates):geometry.type==="MultiPolygon"?coordinates.some(inPolygon):false;}
      function segmentDistanceMeters(ll,a,b){const r=6371008.8,rad=Math.PI/180,ys=r*rad,xs=ys*Math.cos(ll.lat*rad),
        ax=(a[0]-ll.lng)*xs,ay=(a[1]-ll.lat)*ys,bx=(b[0]-ll.lng)*xs,by=(b[1]-ll.lat)*ys,dx=bx-ax,dy=by-ay,l=dx*dx+dy*dy,
        t=l?Math.max(0,Math.min(1,-(ax*dx+ay*dy)/l)):0;return Math.hypot(ax+t*dx,ay+t*dy);}
      function distanceToGeometryMeters(ll,geometry){if(inGeometry(ll,geometry))return 0;
        const polygons=geometry.type==="Polygon"?[geometry.coordinates]:geometry.type==="MultiPolygon"?geometry.coordinates:[];let min=Infinity;
        polygons.forEach(poly=>poly.forEach(ring=>{for(let i=0;i<ring.length;i++)min=Math.min(min,segmentDistanceMeters(ll,ring[i],ring[(i+1)%ring.length]));}));return min;}
      function clusterClass(feature){const properties=feature.properties||{},id=Number(properties.Cluster_ID);
        const matching=hotspots.find(h=>Number((h.properties||{}).DBSCAN_Cluster_ID)===id);
        if(matching)return classOf(matching);
        if(Number(properties.Fatal_Count)>0||Number(properties.Total_Deaths)>0)return "RED";
        if(Number(properties.Major_Count)>0)return "ORANGE";return "GREEN";}
      function checkRisk(ll){const now=new Set(),inside=[];
        hotspots.forEach(f=>{const c=f.geometry.coordinates,center=L.latLng(c[1],c[0]),buf=radii[classOf(f)]||0;
          if(ll.distanceTo(center)<=buf){const id=featureId(f);now.add(id);inside.push(f);}});
        const ordered=inside.sort((a,b)=>(rank[classOf(b)]||0)-(rank[classOf(a)]||0));
        riskText.textContent=ordered.length?({GREEN:"Low risk",ORANGE:"Medium risk",RED:"High risk"}[classOf(ordered[0])]||"Outside hotspot"):"Outside hotspot";
        active=now;
        const suppress=suppressAlerts;suppressAlerts=false;
        const clusterNow=new Set(),enteredClusters=[];
        clusters.forEach(f=>{const id=String((f.properties||{}).Cluster_ID||f.id||""),insideCluster=inGeometry(ll,f.geometry),
          distance=distanceToGeometryMeters(ll,f.geometry);
          if(insideCluster){clusterNow.add(id);if(!activeClusters.has(id))enteredClusters.push(f);}
          else if(distance<=approachDistance){if(!approachAlertedClusters.has(id)){approachAlertedClusters.add(id);if(!suppress)say(null,"approach");}}
          else if(distance>approachDistance*2)approachAlertedClusters.delete(id);});
        activeClusters=clusterNow;
        if(enteredClusters.length){const f=enteredClusters[0],risk=clusterClass(f);
          riskText.textContent=`${({GREEN:"Low",ORANGE:"Medium",RED:"High"}[risk]||"Accident")} risk · DBSCAN cluster`;
          if(!suppress)say({properties:{Severity_Class:risk}},"cluster");}}
      function place(){const p=routePoint(progress,side);if(marker){marker.setLatLng(p.latlng);
          const icon=marker.getElement()?.querySelector(".vehicle-demo-icon");if(icon)icon.style.transform=`rotate(${p.bearing+180}deg)`;}
        positionText.textContent=`Vehicle position: ${p.latlng.lat.toFixed(6)}, ${p.latlng.lng.toFixed(6)}`;checkRisk(p.latlng);}
      function nearest(){const ll=marker.getLatLng();progress=closest(ll);side=0;place();}
      function setReady(v){start.disabled=!v;pause.disabled=!v;reset.disabled=!v;}
      function stop(){running=false;automatic=false;keys.clear();if(raf!==null)cancelAnimationFrame(raf);raf=null;}
      function frame(t){if(!running)return;const dt=Math.min((t-last)/1000,.1);last=t;let f=automatic?1:0,s=0;
        if(!automatic){if(keys.has("f"))f++;if(keys.has("b"))f--;if(keys.has("r"))s--;if(keys.has("l"))s++;}
        progress=Math.max(0,Math.min(length,progress+f*14*dt));side=Math.max(-6,Math.min(6,side+s*3*dt));place();
        if(automatic&&progress>=length){stop();state.textContent="Route end reached. Reset the vehicle to run again.";return;}raf=requestAnimationFrame(frame);}
      function run(auto){if(!enabled||running)return;if(auto&&progress>=length){state.textContent="Route end reached. Reset the vehicle to run again.";return;}
        running=true;automatic=auto;if(auto)keys.clear();last=performance.now();state.textContent=auto?"Simulation running. Use the keyboard to steer.":"Vehicle moving by keyboard.";raf=requestAnimationFrame(frame);}
      function enable(){if(enabled)return;enabled=true;toggle.setAttribute("aria-pressed","true");toggle.textContent="Simulation: On";setReady(false);
        state.textContent="Loading mapped route and hotspots…";progress=0;side=0;active.clear();activeClusters.clear();approachAlertedClusters.clear();
        const p=routePoint(0);marker=L.marker(p.latlng,{draggable:true,autoPan:true,icon:L.divIcon({className:"vehicle-demo-div-icon",html:'<svg class="vehicle-demo-icon" viewBox="0 0 36 36" role="img" aria-label="Demo vehicle" xmlns="http://www.w3.org/2000/svg"><path d="M11 3h14l5 8v19H6V11z" fill="#1267d6" stroke="#fff" stroke-width="2"/><path d="M12 7h12l3 6H9z" fill="#dcedfa"/><path d="M10 18h16v7H10z" fill="#173a5e"/><path d="M6 12H3v6h3m27-6h3v6h-3" fill="#263646"/><circle cx="11" cy="29" r="2" fill="#263646"/><circle cx="25" cy="29" r="2" fill="#263646"/></svg>',iconSize:[36,36],iconAnchor:[18,18]})}).addTo(map);
        marker.bindTooltip("Demo vehicle");marker.on("dragstart",()=>{stop();state.textContent="Simulation paused for vehicle repositioning.";});
        marker.on("drag",nearest);marker.on("dragend",nearest);suppressAlerts=true;place();setReady(true);
        state.textContent="Simulation ready. Press Start Simulation or use the keyboard.";}
      function disable(){stop();enabled=false;toggle.setAttribute("aria-pressed","false");toggle.textContent="Simulation: Off";setReady(false);
        if(marker)map.removeLayer(marker);marker=null;active.clear();activeClusters.clear();approachAlertedClusters.clear();positionText.textContent="Vehicle position: not available";
        riskText.textContent="Outside hotspot";alertText.textContent="No demo alert yet.";if(speech.api)speech.api.cancel();state.textContent="Simulation is off.";}
      toggle.addEventListener("click",()=>enabled?disable():enable());
      voice.addEventListener("click",()=>{voiceEnabled=!voiceEnabled;voice.setAttribute("aria-pressed",String(voiceEnabled));voice.textContent=`Voice: ${voiceEnabled?"On":"Off"}`;
        if(voiceEnabled){speech.api.resume();voiceStatus.textContent="Voice alerts are on. A warning plays 10 m before each DBSCAN zone, followed by its risk-level alert on entry.";}
        else{speech.api.cancel();voiceStatus.textContent="Voice alerts are off; on-screen alert messages will still appear.";}});
      testVoice.addEventListener("click",()=>{
        if(!voiceEnabled){voiceStatus.textContent="Turn Voice on to test speech.";return;}
        if(!speech.api||!speech.Utterance){voiceStatus.textContent="Speech synthesis is not supported in this browser.";return;}
        const sample=new speech.Utterance("Voice test. Demo hotspot alerts are enabled.");
        sample.onstart=()=>{voiceStatus.textContent="Voice test is playing.";};
        sample.onend=()=>{voiceStatus.textContent="Voice test finished.";};
        sample.onerror=e=>{voiceStatus.textContent=`Browser could not play speech (${e.error||"error"}).`;};
        try{speech.api.resume();speech.api.speak(sample);voiceStatus.textContent="Voice test requested; waiting for browser playback.";}
        catch(e){voiceStatus.textContent="Browser could not start speech.";console.error("Vehicle demo voice test failed:",e);}
      });
      start.addEventListener("click",()=>{if(voiceEnabled&&speech.api)speech.api.resume();run(true);});
      pause.addEventListener("click",()=>{stop();state.textContent="Simulation paused.";});
      reset.addEventListener("click",()=>{if(!enabled)return;stop();if(window.speechSynthesis)window.speechSynthesis.cancel();progress=0;side=0;active.clear();activeClusters.clear();approachAlertedClusters.clear();alertText.textContent="No demo alert yet.";suppressAlerts=true;place();state.textContent="Vehicle reset to the route start.";});
      const action=e=>({ArrowUp:"f",KeyW:"f",ArrowDown:"b",KeyS:"b",ArrowLeft:"l",KeyA:"l",ArrowRight:"r",KeyD:"r"})[e.code];
      document.addEventListener("keydown",e=>{if(!enabled||e.target.closest("button,input,textarea,select"))return;const k=action(e);if(!k)return;e.preventDefault();keys.add(k);
        if(!running)run(false);else if(automatic){automatic=false;state.textContent="Simulation running by keyboard.";}});
      document.addEventListener("keyup",e=>{const k=action(e);if(!k||!keys.has(k))return;keys.delete(k);if(!keys.size&&running&&!automatic){stop();state.textContent="Simulation paused.";}});
      window.addEventListener("blur",()=>{if(running&&!automatic){stop();state.textContent="Simulation paused.";}});
    });
    """.replace("__MAP__", map_name).replace("__ROUTE__", route_json).replace("__HOTSPOTS__", hotspots_json).replace(
        "__DBSCAN_CLUSTERS__", json.dumps(dbscan_clusters, separators=(",", ":"))
    )
    return panel, script


def _safe(value) -> str:
    if value is None:
        return "—"
    try:
        if value is np.nan or bool(np.isnan(value)):
            return "—"
    except (TypeError, ValueError):
        pass
    return html.escape(str(value))


def _popup(properties: dict, fields: list[str]) -> str:
    rows = []
    for field in fields:
        if field in properties and properties[field] is not None:
            rows.append(
                f"<tr><th style='text-align:left;padding:3px 8px'>{html.escape(field)}</th>"
                f"<td style='padding:3px 8px'>{_safe(properties[field])}</td></tr>"
            )
    return "<table style='font-size:12px'>" + "".join(rows) + "</table>"


def _colorize_raster(array: np.ndarray, nodata: float, output_path: Path) -> str:
    valid = np.isfinite(array)
    if np.isfinite(nodata):
        valid &= array != nodata
    if not valid.any():
        raise ValueError("The reprojected KDE raster contains no valid density pixels.")
    low, high = np.percentile(array[valid], [2, 98])
    if high <= low:
        high = low + 1
    normalized = np.clip((array - low) / (high - low), 0, 1)
    rgba = (matplotlib.colormaps["YlOrRd"](normalized) * 255).astype("uint8")
    rgba[..., 3] = np.where(valid, (55 + 200 * normalized).astype("uint8"), 0)
    buffer = BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG", optimize=True)
    content = buffer.getvalue()
    output_path.write_bytes(content)
    return "data:image/png;base64," + base64.b64encode(content).decode("ascii")


def create_osm_map(data: ProjectData, path: Path) -> None:
    layers = data.layers
    roads_reference = data.roads_reference.to_crs("EPSG:4326")
    roads_osm = data.roads_osm.to_crs("EPSG:4326")
    roads_duk = data.roads_duk.to_crs("EPSG:4326")
    raster, _, bounds, nodata = reproject_raster_to_wgs84(data.raster_path)
    raster_uri = _colorize_raster(raster, nodata, data.visualization_outputs / "kde_density_overlay.png")
    if data.expect_road_aligned:
        alignment_note = (
            f"Alternative synthetic points within 5 m: NH 66 "
            f"{data.road_offset_m['within_5m']}/{data.road_offset_m['records']}; DUK access "
            f"{data.duk_offset_m['within_5m']}/{data.duk_offset_m['records']}."
        )
    else:
        alignment_note = (
            f"Original synthetic NH 66 points follow the old reference: "
            f"median offset {data.road_offset_m['median']:.0f} m from mapped line. "
            f"Original DUK points have median offset {data.duk_offset_m['median']:.0f} m "
            "from the mapped DUK access road."
        )

    extents = [roads_osm.total_bounds, roads_duk.total_bounds,
               layers["validated_accidents"].to_crs("EPSG:4326").total_bounds,
               layers["hotspot_polygons"].to_crs("EPSG:4326").total_bounds]
    if not data.expect_road_aligned:
        extents.append(roads_reference.total_bounds)
    bounds_xy = np.vstack(extents)
    south, west = float(bounds_xy[:, 1].min()), float(bounds_xy[:, 0].min())
    north, east = float(bounds_xy[:, 3].max()), float(bounds_xy[:, 2].max())
    map_view = folium.Map(location=[(south + north) / 2, (west + east) / 2],
                          zoom_start=15, tiles=None, control_scale=True, prefer_canvas=True)
    folium.TileLayer(tiles="OpenStreetMap", name="OpenStreetMap", control=True).add_to(map_view)
    Fullscreen(position="topleft", title="Fullscreen", title_cancel="Exit fullscreen").add_to(map_view)
    MeasureControl(position="topleft", primary_length_unit="meters", secondary_length_unit="kilometers",
                   primary_area_unit="sqmeters").add_to(map_view)

    mapped_roads = folium.FeatureGroup(name="Mapped roads", show=True)
    folium.GeoJson(
        roads_osm.to_json(),
        style_function=lambda feature: {"color": "#0759a5", "weight": 5, "opacity": 0.98},
        tooltip=folium.GeoJsonTooltip(fields=["name", "ref"], aliases=["OSM road name", "Route"]),
        popup=folium.GeoJsonPopup(fields=["name", "ref", "highway", "osm_way_id", "attribution"],
                                  aliases=["Road name", "Route", "OSM road type", "OSM way", "Attribution"]),
    ).add_to(mapped_roads)
    folium.GeoJson(
        roads_duk.to_json(),
        style_function=lambda _: {"color": "#6a3d9a", "weight": 5, "opacity": 0.98},
        tooltip=folium.GeoJsonTooltip(
            fields=["road_class", "osm_way_id"], aliases=["Mapped road", "OSM way"]
        ),
        popup=folium.GeoJsonPopup(
            fields=["road_class", "osm_way_id", "junction_way_id", "attribution"],
            aliases=["Road", "OSM way", "NH 66 connection", "Attribution"],
        ),
    ).add_to(mapped_roads)
    mapped_roads.add_to(map_view)

    density_layer = folium.FeatureGroup(name="KDE density overlay", show=False)
    overlay = folium.raster_layers.ImageOverlay(
        image=raster_uri, bounds=[[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
        opacity=0.68, name="KDE density (events per km²)", interactive=False, cross_origin=False,
        zindex=2,
    )
    overlay.add_to(density_layer)
    density_layer.add_to(map_view)

    clusters = layers["dbscan_clustered_accidents"].to_crs("EPSG:4326")
    cluster_polygons = layers["dbscan_clusters"].to_crs("EPSG:4326")
    cluster_group = folium.FeatureGroup(name="DBSCAN clusters", show=True)
    palette = matplotlib.colormaps["tab10"]
    cluster_colors = {}
    if "Cluster_ID" in clusters:
        cluster_ids = sorted(int(value) for value in clusters["Cluster_ID"].dropna().unique() if int(value) > 0)
    else:
        cluster_ids = []
    for cluster_id in cluster_ids:
        cluster_colors[cluster_id] = matplotlib.colors.to_hex(palette((cluster_id - 1) % 10))
    if not cluster_polygons.empty:
        folium.GeoJson(
            cluster_polygons.to_json(),
            style_function=lambda feature: {
                "color": cluster_colors.get(int(feature["properties"].get("Cluster_ID", -1)), "#666666"),
                "weight": 2, "fillOpacity": 0.12,
            },
            tooltip=folium.GeoJsonTooltip(fields=["Cluster_ID", "Accident_Count", "Hotspot_Status"],
                                          aliases=["Cluster", "Accident records", "Hotspot status"]),
            popup=folium.GeoJsonPopup(fields=[
                field for field in ["Cluster_ID", "Accident_Count", "Fatal_Count", "Major_Count",
                                    "Minor_Count", "Total_Deaths", "Total_Injuries", "Primary_Road",
                                    "Hotspot_Status"] if field in cluster_polygons.columns
            ]),
        ).add_to(cluster_group)
    for _, row in clusters[clusters["Cluster_ID"] > 0].iterrows():
        color = cluster_colors[int(row["Cluster_ID"])]
        fields = ["Accident_ID", "Cluster_ID", "Severity", "Date", "Time", "Road_Name"]
        folium.CircleMarker(
            [row.geometry.y, row.geometry.x], radius=3, color=color, fill=True,
            fill_color=color, fill_opacity=0.82, weight=1,
            popup=folium.Popup(_popup(row, fields), max_width=300),
            tooltip=f"Cluster {int(row['Cluster_ID'])} · {_safe(row.get('Accident_ID'))}",
        ).add_to(cluster_group)
    cluster_group.add_to(map_view)

    accidents = layers["validated_accidents"].to_crs("EPSG:4326")
    accident_group = folium.FeatureGroup(name="Synthetic accident locations", show=True)
    accident_fields = ["Accident_ID", "Latitude", "Longitude", "Date", "Time", "Severity",
                       "Deaths", "Injuries", "Road_Name", "Road_Type"]
    for _, row in accidents.dropna(subset=[accidents.geometry.name]).iterrows():
        severity = str(row.get("Severity", ""))
        color = ACCIDENT_COLOURS.get(severity, "#777777")
        folium.CircleMarker(
            [row.geometry.y, row.geometry.x], radius=7, color="#ffffff", fill=True, fill_color=color,
            fill_opacity=1, weight=2, popup=folium.Popup(_popup(row, accident_fields), max_width=360),
            tooltip=f"{_safe(row.get('Accident_ID'))} · {_safe(severity)} · "
                    f"{_safe(row.get('Date'))} · {_safe(row.get('Road_Name'))}",
        ).add_to(accident_group)
    accident_group.add_to(map_view)
    map_view.get_root().html.add_child(Element(
        "<div style='position:fixed;left:12px;bottom:24px;z-index:9999;background:#fff;"
        "padding:8px 11px;border:1px solid #cbd5e1;border-radius:6px;box-shadow:0 1px 5px #0003;"
        "font:12px Arial,sans-serif'><strong>Accident severity</strong><br>"
        "<span style='color:#e11d48'>●</span> Fatal&nbsp;&nbsp;"
        "<span style='color:#2563eb'>●</span> Non-fatal</div>"
    ))

    points = layers["hotspot_points"].to_crs("EPSG:4326")
    center_group = folium.FeatureGroup(name="Hotspot centre points", show=True)
    for _, row in points.iterrows():
        color = HOTSPOT_COLOURS.get(str(row.get("Severity_Class", "")).upper(), "#666666")
        properties = row.drop(labels=[points.geometry.name]).to_dict()
        popup_fields = list(properties)
        folium.CircleMarker(
            [row.geometry.y, row.geometry.x], radius=12, color="#ffffff", fill=True, fill_color=color,
            fill_opacity=1, weight=3,
            popup=folium.Popup(_popup(properties, popup_fields), max_width=350),
            tooltip=f"{_safe(row.get('Hotspot_ID'))} · {_safe(row.get('Risk_Level'))} risk ({_safe(row.get('Severity_Class'))})",
        ).add_to(center_group)
    center_group.add_to(map_view)

    folium.LayerControl(position="topright", collapsed=False).add_to(map_view)
    route = _simulation_route(roads_osm, roads_duk)
    route_lats = [point[1] for point in route]
    route_lons = [point[0] for point in route]
    map_view.fit_bounds(
        [[min(route_lats), min(route_lons)], [max(route_lats), max(route_lons)]],
        padding=(20, 20),
    )
    simulation_hotspots = [
        {
            "type": "Feature",
            "id": str(row.get("Hotspot_ID", index)),
            "properties": {
                "Hotspot_ID": str(row.get("Hotspot_ID", index)),
                "Severity_Class": str(row.get("Severity_Class", "")),
                "Risk_Level": str(row.get("Risk_Level", "")),
                "DBSCAN_Cluster_ID": int(row.get("DBSCAN_Cluster_ID", -1)),
            },
            "geometry": {
                "type": "Point",
                "coordinates": [float(row.geometry.x), float(row.geometry.y)],
            },
        }
        for index, (_, row) in enumerate(points.iterrows())
    ]
    simulation_panel, simulation_script = _simulation_markup(
        map_view.get_name(), route, simulation_hotspots,
        json.loads(cluster_polygons.to_json()).get("features", []),
    )
    map_view.get_root().html.add_child(Element(simulation_panel))
    map_view.get_root().script.add_child(Element(simulation_script))
    map_view.save(path)
