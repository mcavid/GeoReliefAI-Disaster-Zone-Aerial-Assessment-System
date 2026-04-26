import streamlit as st
import os
import json
import re
import time
import tempfile
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv, find_dotenv
from twelvelabs import TwelveLabs
import requests as _requests

load_dotenv(find_dotenv(usecwd=True))

st.set_page_config(
    page_title="Disaster Zone Aerial Assessment",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stSidebar"] { background: #1a1a2e; }
[data-testid="stSidebar"] * { color: #e0e0e0 !important; }
.stat-card {
    background: #f8f9fa; border-radius: 8px; padding: 16px 20px;
    border-left: 4px solid #e74c3c; margin-bottom: 8px;
}
.severity-complete { border-left-color: #c0392b !important; }
.severity-severe    { border-left-color: #e67e22 !important; }
.severity-moderate  { border-left-color: #f1c40f !important; }
.severity-minor     { border-left-color: #27ae60 !important; }
.metric-row { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
.metric-box {
    flex: 1; min-width: 140px; background: #fff; border: 1px solid #dee2e6;
    border-radius: 8px; padding: 16px; text-align: center;
}
.metric-box .value { font-size: 2rem; font-weight: 700; color: #2c3e50; }
.metric-box .label { font-size: 0.8rem; color: #6c757d; margin-top: 4px; }
</style>
""", unsafe_allow_html=True)


# ── Constants ──────────────────────────────────────────────────────────────────

DISASTER_LABELS = {
    "auto":       "natural disaster",
    "tornado":    "tornado or severe windstorm",
    "hurricane":  "hurricane or tropical storm",
    "wildfire":   "wildfire or forest fire",
    "flood":      "flooding or flash flood",
    "earthquake": "earthquake",
}

UNIT_COSTS = {
    "complete_roof_loss":         (10_000,  18_000,  30_000),
    "partial_roof_damage":        ( 2_500,   6_000,  12_000),
    "structural_collapse":        (80_000, 150_000, 300_000),
    "fire_damage_total_loss":     (90_000, 175_000, 350_000),
    "fire_damage_partial":        (15_000,  50_000, 120_000),
    "flooded_structures":         (20_000,  50_000, 120_000),
    "flooded_road_segments":      ( 5_000,  25_000,  75_000),
    "damaged_bridges":           (100_000, 500_000, 2_000_000),
    "damaged_vehicles":           (15_000,  30_000,  60_000),
    "downed_utility_poles":        (5_000,  12_000,  25_000),
    "debris_cleared_acres_needed": (3_000,   8_000,  20_000),
}


FINANCIAL_PROMPT = """
You are a disaster damage appraiser reviewing aerial drone footage.

Count every instance of each damage type you can observe. Be as specific as possible.
Provide your response as a JSON object with exactly these keys and integer values
(use 0 if none observed, use your best estimate for ranges):

{
  "complete_roof_loss": <count of buildings with total roof missing>,
  "partial_roof_damage": <count of buildings with partial roof damage>,
  "structural_collapse": <count of fully collapsed structures>,
  "fire_damage_total_loss": <count of structures burned to foundation>,
  "fire_damage_partial": <count of structures with significant fire/smoke damage>,
  "flooded_structures": <count of structures with visible flood water inside or surrounding>,
  "flooded_road_segments": <count of distinct road segments underwater or impassable>,
  "damaged_bridges": <count of damaged or collapsed bridges>,
  "damaged_vehicles": <count of vehicles that appear destroyed or heavily damaged>,
  "downed_utility_poles": <count of downed power poles or utility infrastructure>,
  "debris_cleared_acres_needed": <estimated acres of land requiring debris clearing>,
  "assessment_confidence": "low" | "medium" | "high"
}

Only output the JSON object. No commentary.
"""

# ── Metrics & validation ───────────────────────────────────────────────────────

def compute_metrics(geo_items, damage_items, resource_items, access_data,
                    damage_counts, marengo_results, parse_flags):

    def completeness(items, fields):
        if not items:
            return 0.0
        scores = [
            sum(1 for f in fields if item.get(f) and str(item.get(f)).strip().lower()
                not in ("", "n/a", "unknown", "none", "null"))
            / len(fields)
            for item in items if isinstance(item, dict)
        ]
        return round(sum(scores) / len(scores) * 100, 1) if scores else 0.0

    DAMAGE_FIELDS   = ["damage_type", "severity", "structure_type", "location", "description", "timestamp_approx"]
    GEO_FIELDS      = ["type", "identifier", "confidence"]
    RESOURCE_FIELDS = ["resource", "quantity_estimate", "triggered_by", "location", "priority"]
    ACCESS_FIELDS   = ["location_desc", "status", "obstruction_type"]

    access_points = access_data.get("access_points", [])
    fin_nonzero = sum(1 for k, v in damage_counts.items()
                      if k != "assessment_confidence" and isinstance(v, (int, float)) and v > 0)

    quality = {
        "geo":       {"parse_ok": parse_flags["geo"],       "count": len(geo_items),
                      "completeness": completeness(geo_items, GEO_FIELDS),
                      "high_conf": sum(1 for g in geo_items if isinstance(g, dict) and g.get("confidence") == "high")},
        "damage":    {"parse_ok": parse_flags["damage"],    "count": len(damage_items),
                      "completeness": completeness(damage_items, DAMAGE_FIELDS)},
        "resources": {"parse_ok": parse_flags["resources"], "count": len(resource_items),
                      "completeness": completeness(resource_items, RESOURCE_FIELDS)},
        "access":    {"parse_ok": parse_flags["access"],    "count": len(access_points),
                      "staging": len(access_data.get("staging_areas", [])),
                      "completeness": completeness(access_points, ACCESS_FIELDS)},
        "financial": {"parse_ok": parse_flags["financial"], "nonzero_cats": fin_nonzero,
                      "confidence": damage_counts.get("assessment_confidence", "unknown")},
    }

    parse_score       = sum(1 for v in parse_flags.values() if v) / len(parse_flags) * 40
    comp_vals         = [quality["damage"]["completeness"], quality["resources"]["completeness"],
                         quality["access"]["completeness"]]
    completeness_score = (sum(comp_vals) / len(comp_vals)) * 0.40
    financial_score   = min(fin_nonzero / 11 * 20, 20)
    health_score      = round(parse_score + completeness_score + financial_score)

    # ── Cross-section consistency ──────────────────────────────────────────────
    checks = []
    damage_types_found = {item.get("damage_type", "") for item in damage_items if isinstance(item, dict)}
    resource_text = " ".join(r.get("triggered_by", "") + " " + r.get("resource", "")
                             for r in resource_items if isinstance(r, dict)).lower()

    # 1. Damage → Resource coverage
    DAMAGE_KEYWORDS = {
        "missing_roof":        ["roof"],
        "partial_roof_damage": ["roof"],
        "structural_collapse": ["collapse", "structural", "demolition"],
        "fire_damage":         ["fire", "burn"],
        "flood_submersion":    ["flood", "water", "pump", "dewater"],
        "flood_debris":        ["debris", "flood"],
        "road_blocked":        ["road", "route", "debris", "clear"],
        "bridge_damage":       ["bridge"],
        "downed_trees":        ["tree", "debris", "chain"],
        "downed_power_lines":  ["power", "utility", "electric", "line"],
        "vehicle_damage":      ["vehicle", "tow"],
    }
    uncovered = []
    for item in damage_items:
        if not isinstance(item, dict):
            continue
        dtype = item.get("damage_type", "")
        keywords = DAMAGE_KEYWORDS.get(dtype, [dtype.replace("_", " ")])
        if not any(kw in resource_text for kw in keywords):
            uncovered.append(f"{dtype.replace('_', ' ').title()} — {item.get('location', 'unknown location')}")

    if uncovered:
        checks.append({"type": "warning", "section": "Damage → Resources",
                        "message": f"{len(uncovered)} damage item(s) have no matching resource",
                        "details": uncovered})
    else:
        checks.append({"type": "ok", "section": "Damage → Resources",
                        "message": f"All {len(damage_items)} damage items have a corresponding resource requirement",
                        "details": []})

    # 2. Blocked access → Damage corroboration
    OBSTRUCTION_TO_DAMAGE = {
        "flooding":           ["flood_submersion", "flood_debris"],
        "debris":             ["flood_debris", "downed_trees", "structural_collapse", "road_blocked"],
        "structural_collapse":["structural_collapse"],
        "downed_trees":       ["downed_trees", "road_blocked"],
        "downed_power_lines": ["downed_power_lines"],
        "fire":               ["fire_damage"],
        "mud_landslide":      ["road_blocked"],
    }
    blocked = [ap for ap in access_points if ap.get("status") in ("fully_blocked", "partially_blocked")]
    unmatched_blocks = []
    for ap in blocked:
        obs = ap.get("obstruction_type", "")
        expected = OBSTRUCTION_TO_DAMAGE.get(obs, [])
        if expected and not any(dt in damage_types_found for dt in expected):
            unmatched_blocks.append(
                f"{ap.get('location_desc', 'unknown')} — blocked by {obs.replace('_', ' ')}, "
                f"but no matching damage entry found")
    if blocked:
        if unmatched_blocks:
            checks.append({"type": "warning", "section": "Access → Damage",
                            "message": f"{len(unmatched_blocks)} blocked route(s) lack a corroborating damage entry",
                            "details": unmatched_blocks})
        else:
            checks.append({"type": "ok", "section": "Access → Damage",
                            "message": f"All {len(blocked)} blocked route(s) are corroborated by damage entries",
                            "details": []})
    else:
        checks.append({"type": "info", "section": "Access → Damage",
                        "message": "No blocked routes identified — nothing to cross-validate", "details": []})

    # 3. Financial → Damage alignment
    FIN_TO_DAMAGE = {
        "complete_roof_loss":      ["missing_roof"],
        "partial_roof_damage":     ["partial_roof_damage"],
        "structural_collapse":     ["structural_collapse"],
        "fire_damage_total_loss":  ["fire_damage"],
        "fire_damage_partial":     ["fire_damage"],
        "flooded_structures":      ["flood_submersion"],
        "flooded_road_segments":   ["road_blocked", "flood_debris"],
        "damaged_bridges":         ["bridge_damage"],
        "damaged_vehicles":        ["vehicle_damage"],
        "downed_utility_poles":    ["downed_power_lines"],
    }
    unmatched_fin = []
    for fin_key, dtypes in FIN_TO_DAMAGE.items():
        count = damage_counts.get(fin_key, 0)
        if isinstance(count, (int, float)) and count > 0:
            if not any(dt in damage_types_found for dt in dtypes):
                unmatched_fin.append(
                    f"{fin_key.replace('_', ' ').title()} (count: {count}) — "
                    f"no matching entry in Damage assessment")
    if unmatched_fin:
        checks.append({"type": "warning", "section": "Financial → Damage",
                        "message": f"{len(unmatched_fin)} financial category/ies not corroborated by Damage",
                        "details": unmatched_fin})
    else:
        checks.append({"type": "ok", "section": "Financial → Damage",
                        "message": f"All {fin_nonzero} non-zero financial categories are corroborated by damage entries",
                        "details": []})

    # 4. Marengo → Pegasus agreement
    MARENGO_TO_DAMAGE = {
        "blocked road debris":      ["road_blocked", "flood_debris", "downed_trees"],
        "flooded road underwater":  ["flood_submersion", "flood_debris"],
        "collapsed bridge":         ["bridge_damage"],
        "downed power lines road":  ["downed_power_lines"],
        "fire blocking access":     ["fire_damage"],
        "open field landing zone":  [],
    }
    marengo_findings = []
    for query, clips in marengo_results.items():
        expected = MARENGO_TO_DAMAGE.get(query, [])
        corroborated = not expected or any(dt in damage_types_found for dt in expected)
        marengo_findings.append({"query": query, "clips": len(clips), "corroborated": corroborated})
    if marengo_findings:
        n_corr = sum(1 for f in marengo_findings if f["corroborated"])
        checks.append({
            "type": "ok" if n_corr == len(marengo_findings) else "warning",
            "section": "Marengo → Pegasus",
            "message": f"{n_corr}/{len(marengo_findings)} Marengo scene findings corroborated by Pegasus damage entries",
            "details": [
                f"{'✓' if f['corroborated'] else '✗'}  \"{f['query']}\"  ({f['clips']} clip{'s' if f['clips'] != 1 else ''})"
                for f in marengo_findings
            ],
        })

    return {"quality": quality, "health_score": health_score, "consistency_checks": checks}


# ── Analysis pipeline ──────────────────────────────────────────────────────────

def run_analysis(client, index_id, video_id, disaster_type, location_city, status_fn):
    disaster_label = DISASTER_LABELS.get(disaster_type, "natural disaster")

    # --- Geolocation ---
    status_fn("🗺️ Identifying locations and landmarks...")
    geo_resp = client.analyze(video_id=video_id, prompt=f"""
You are analyzing aerial drone footage over {location_city} after a disaster.
Scan for street signs, intersections, neighborhood markers, landmarks, and address numbers.
For each found: TYPE (street_sign|intersection|neighborhood|landmark|address_number),
IDENTIFIER (exact text), CONFIDENCE (high|medium|low), TIMESTAMP_APPROX, NOTES.
Return a JSON array with keys: type, identifier, confidence, timestamp_approx, notes.
Only output JSON.
""")
    geo_items = []
    _pf = {"geo": False, "damage": False, "resources": False, "access": False, "financial": False}
    m = re.search(r'\[.*\]', geo_resp.data, re.DOTALL)
    if m:
        try:
            geo_items = json.loads(m.group())
            _pf["geo"] = isinstance(geo_items, list)
        except json.JSONDecodeError:
            pass

    known = [g["identifier"] for g in geo_items if isinstance(g, dict) and g.get("confidence") in ("high", "medium")]
    location_context = f"{location_city}. Known visible locations: {', '.join(known)}" if known else location_city

    # --- Damage assessment ---
    status_fn("🏚️ Assessing structural damage...")
    damage_resp = client.analyze(video_id=video_id, prompt=f"""
You are an expert disaster damage assessor reviewing aerial drone footage after a {disaster_label}
over {location_context}.
For each damaged area, provide:
1. DAMAGE_TYPE: missing_roof|partial_roof_damage|structural_collapse|fire_damage|
   flood_submersion|flood_debris|road_blocked|bridge_damage|downed_trees|downed_power_lines|vehicle_damage|other
2. SEVERITY: minor|moderate|severe|complete_destruction
3. STRUCTURE_TYPE: residential_home|commercial_building|road|bridge|vehicle|utility_infrastructure|other
4. LOCATION: most specific real-world location using street names or landmarks
5. DESCRIPTION: 1-2 sentence description
6. TIMESTAMP_APPROX: e.g. "0:10-0:30"
Return a JSON array with keys: damage_type, severity, structure_type, location, description, timestamp_approx.
Only output JSON.
""")
    damage_items = []
    m = re.search(r'\[.*\]', damage_resp.data, re.DOTALL)
    if m:
        try:
            damage_items = json.loads(m.group())
            _pf["damage"] = isinstance(damage_items, list)
        except json.JSONDecodeError:
            pass

    # --- Resources ---
    status_fn("🚛 Determining resource requirements...")
    resource_resp = client.analyze(video_id=video_id, prompt=f"""
You are an emergency logistics coordinator reviewing aerial drone footage of a {disaster_label}
impact zone in {location_context}.
Produce a prioritized resource manifest. For each need:
1. RESOURCE: specific supply or service
2. QUANTITY_ESTIMATE: e.g. "20-30 units"
3. TRIGGERED_BY: the damage condition requiring it
4. LOCATION: most specific delivery location
5. PRIORITY: immediate (0-24h)|short_term (1-7 days)|long_term (1-4 weeks)
6. NOTES: special considerations
Return a JSON array with keys: resource, quantity_estimate, triggered_by, location, priority, notes.
Only output JSON.
""")
    resource_items = []
    m = re.search(r'\[.*\]', resource_resp.data, re.DOTALL)
    if m:
        try:
            resource_items = json.loads(m.group())
            _pf["resources"] = isinstance(resource_items, list)
        except json.JSONDecodeError:
            pass

    # --- Access analysis ---
    status_fn("🛣️ Analyzing road access and obstructions...")
    access_resp = client.analyze(video_id=video_id, prompt=f"""
You are a search-and-rescue planner reviewing aerial drone footage of a {disaster_label}
impact zone in {location_context}.
For each road/access corridor:
1. LOCATION_DESC: specific street/intersection name
2. STATUS: passable|partially_blocked|fully_blocked|unknown
3. OBSTRUCTION_TYPE: flooding|debris|structural_collapse|downed_trees|downed_power_lines|fire|mud_landslide|none
4. OBSTRUCTION_DETAIL: what is blocking and extent
5. ALTERNATE_ROUTE: specific alternate street if available
Also identify staging/landing zones with specific locations.
Return JSON object: {{"access_points": [...], "staging_areas": [...]}}. Only output JSON.
""")
    access_data = {"access_points": [], "staging_areas": []}
    m = re.search(r'\{.*\}', access_resp.data, re.DOTALL)
    if m:
        try:
            raw = json.loads(m.group())
            # Normalize keys to lowercase and extract staging area names
            access_data["access_points"] = [
                {k.lower(): v for k, v in ap.items()} if isinstance(ap, dict) else {}
                for ap in raw.get("access_points", raw.get("ACCESS_POINTS", []))
            ]
            staging_raw = raw.get("staging_areas", raw.get("STAGING_AREAS", []))
            access_data["staging_areas"] = [
                s.get("location_desc") or s.get("LOCATION_DESC") or str(s)
                if isinstance(s, dict) else str(s)
                for s in staging_raw
            ]
            _pf["access"] = True
        except json.JSONDecodeError:
            pass

    # --- Marengo scene search ---
    status_fn("🔍 Searching for specific scenes with Marengo...")
    scene_queries = [
        "blocked road debris", "flooded road underwater", "collapsed bridge",
        "downed power lines road", "fire blocking access", "open field landing zone",
    ]
    marengo_results = {}
    for query in scene_queries:
        try:
            results = client.search.query(index_id=index_id, query_text=query, search_options=["visual", "audio"])
            clips = []
            for item in results:
                clips.append({"start": item.start, "end": item.end, "rank": item.rank})
                if len(clips) >= 3:
                    break
            if clips:
                marengo_results[query] = clips
        except Exception:
            pass

    # --- Financial estimate ---
    status_fn("💰 Estimating financial damage...")
    fin_resp = client.analyze(video_id=video_id, prompt=FINANCIAL_PROMPT)
    damage_counts = {}
    m = re.search(r'\{.*\}', fin_resp.data, re.DOTALL)
    if m:
        try:
            damage_counts = json.loads(m.group())
            _pf["financial"] = isinstance(damage_counts, dict)
        except json.JSONDecodeError:
            pass

    cost_rows, total_low, total_mid, total_high = [], 0.0, 0.0, 0.0
    for key, (low, mid, high) in UNIT_COSTS.items():
        count = damage_counts.get(key, 0)
        if not isinstance(count, (int, float)) or count == 0:
            continue
        cost_rows.append({"category": key.replace("_", " ").title(), "count": count,
                          "low": count * low, "mid": count * mid, "high": count * high})
        total_low += count * low
        total_mid += count * mid
        total_high += count * high

    metrics = compute_metrics(geo_items, damage_items, resource_items, access_data,
                              damage_counts, marengo_results, _pf)
    return {
        "location_city": location_city,
        "disaster_type": disaster_type,
        "geo_items": geo_items,
        "damage_items": damage_items,
        "resource_items": resource_items,
        "access_data": access_data,
        "marengo_results": marengo_results,
        "damage_counts": damage_counts,
        "cost_rows": cost_rows,
        "total_low": total_low,
        "total_mid": total_mid,
        "total_high": total_high,
        "confidence": damage_counts.get("assessment_confidence", "medium"),
        "generated_at": datetime.now().isoformat(),
        "metrics": metrics,
    }


def build_report_md(r: dict) -> str:
    ts = datetime.fromisoformat(r["generated_at"]).strftime("%Y-%m-%d %H:%M")
    lines = [
        "# DISASTER ASSESSMENT REPORT",
        f"**Location:** {r['location_city']}",
        f"**Disaster Type:** {r['disaster_type'].title()}",
        f"**Generated:** {ts}",
        f"**Confidence:** {r['confidence'].upper()}",
        "", "---", "",
    ]
    if r["geo_items"]:
        lines += ["## Identified Locations", "",
                  "| Type | Identifier | Confidence | Timestamp | Notes |",
                  "|---|---|---|---|---|"]
        for g in r["geo_items"]:
            lines.append(f"| {g.get('type','').replace('_',' ').title()} | {g.get('identifier','N/A')} | {g.get('confidence','?')} | {g.get('timestamp_approx','N/A')} | {g.get('notes','')} |")
        lines += ["", "---", ""]

    sev_order = {"complete_destruction": 0, "severe": 1, "moderate": 2, "minor": 3}
    lines.append("## Damage Assessment")
    for item in sorted(r["damage_items"], key=lambda x: sev_order.get(x.get("severity", "minor"), 4)):
        sev = item.get("severity", "").replace("_", " ").upper()
        lines += [f"### [{sev}] {item.get('damage_type','').replace('_',' ').title()} — {item.get('structure_type','').replace('_',' ').title()}",
                  f"- **Location:** {item.get('location','N/A')}",
                  f"- **Timestamp:** {item.get('timestamp_approx','N/A')}",
                  f"- {item.get('description','')}", ""]

    lines += ["---", "", "## Resource Requirements", "",
              "| Resource | Quantity | Priority | Location | Notes |", "|---|---|---|---|---|"]
    p_order = {"immediate": 0, "short_term": 1, "long_term": 2}
    for r2 in sorted(r["resource_items"], key=lambda x: p_order.get(x.get("priority", "long_term"), 3)):
        lines.append(f"| {r2.get('resource','N/A')} | {r2.get('quantity_estimate','N/A')} | {r2.get('priority','N/A').replace('_',' ').title()} | {r2.get('location','N/A')} | {r2.get('notes','')} |")

    lines += ["", "---", "", "## Access & Route Analysis", "", "### Road Conditions"]
    for ap in r["access_data"].get("access_points", []):
        s = ap.get("status", "unknown").replace("_", " ").upper()
        lines.append(f"**[{s}]** {ap.get('location_desc','?')}")
        if s != "PASSABLE":
            lines += [f"- Obstruction: {ap.get('obstruction_type','').replace('_',' ').title()} — {ap.get('obstruction_detail','')}",
                      f"- Alternate: {ap.get('alternate_route','N/A')}"]
        lines.append("")
    if r["access_data"].get("staging_areas"):
        lines.append("### Staging / Landing Zones")
        for s in r["access_data"]["staging_areas"]:
            lines.append(f"- {s}")
        lines.append("")

    lines += ["---", "", "## Financial Damage Estimate", "",
              "| Category | Count | Low | Mid | High |", "|---|---|---|---|---|"]
    for row in r["cost_rows"]:
        lines.append(f"| {row['category']} | {row['count']} | ${row['low']:,.0f} | ${row['mid']:,.0f} | ${row['high']:,.0f} |")
    lines += [f"| **TOTAL** | | **${r['total_low']:,.0f}** | **${r['total_mid']:,.0f}** | **${r['total_high']:,.0f}** |",
              "", "> Estimates based on FEMA and insurance industry averages.", "",
              "---", "*Generated by Disaster Zone Aerial Assessment System · TwelveLabs Marengo + Pegasus*"]
    return "\n".join(lines)


# ── Sidebar ────────────────────────────────────────────────────────────────────

# ── Geocoding ──────────────────────────────────────────────────────────────────

def _nominatim(query: str) -> tuple[float, float] | None:
    try:
        r = _requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": "DisasterAssessmentApp/1.0"},
            timeout=6,
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None


DAMAGE_TYPE_COLOR = {
    "missing_roof":        [249, 115,  22, 220],
    "partial_roof_damage": [251, 146,  60, 220],
    "structural_collapse": [220,  38,  38, 220],
    "fire_damage":         [153,  27,  27, 220],
    "flood_submersion":    [ 59, 130, 246, 220],
    "flood_debris":        [ 96, 165, 250, 220],
    "road_blocked":        [234, 179,   8, 220],
    "bridge_damage":       [124,  58, 237, 220],
    "downed_trees":        [ 34, 197,  94, 220],
    "downed_power_lines":  [253, 224,  71, 220],
    "vehicle_damage":      [156, 163, 175, 220],
    "other":               [255, 255, 255, 220],
}

DAMAGE_TYPE_EMOJI = {
    "missing_roof":        "🟠 Roof Loss",
    "partial_roof_damage": "🟧 Partial Roof Damage",
    "structural_collapse": "🔴 Structural Collapse",
    "fire_damage":         "🟥 Fire Damage",
    "flood_submersion":    "🔵 Flood Submersion",
    "flood_debris":        "💠 Flood Debris",
    "road_blocked":        "🟡 Road Blocked",
    "bridge_damage":       "🟣 Bridge Damage",
    "downed_trees":        "🟢 Downed Trees",
    "downed_power_lines":  "💛 Downed Power Lines",
    "vehicle_damage":      "⚪ Vehicle Damage",
    "other":               "⬜ Other",
}


def geocode_all(results: dict) -> list[dict]:
    import random
    city = results["location_city"]
    points = []
    seen: set[str] = set()
    rng = random.Random(42)

    # Pre-geocode city center as fallback anchor
    city_coords = _nominatim(city)
    time.sleep(1.1)

    def try_geocode(loc: str):
        """Try progressively simpler queries; fall back to city center + offset."""
        # Strategy 1: full location + city
        coords = _nominatim(f"{loc}, {city}")
        time.sleep(1.1)
        if coords:
            return coords, False
        # Strategy 2: first segment before comma / & / "and"
        simplified = re.split(r'[,&]| and | near ', loc, maxsplit=1)[0].strip()
        if simplified and simplified != loc:
            coords = _nominatim(f"{simplified}, {city}")
            time.sleep(1.1)
            if coords:
                return coords, False
        # Strategy 3: city center + small deterministic offset so markers don't stack
        if city_coords:
            lat = city_coords[0] + rng.uniform(-0.008, 0.008)
            lon = city_coords[1] + rng.uniform(-0.008, 0.008)
            return (lat, lon), True   # True = approximate placement
        return None, False

    # ── Damage items (all categories) ─────────────────────────────────────────
    for item in results["damage_items"]:
        loc = (item.get("location") or "").strip()
        if not loc or loc in seen:
            continue
        seen.add(loc)
        coords, approx = try_geocode(loc)
        if coords:
            dtype = item.get("damage_type", "other")
            color = list(DAMAGE_TYPE_COLOR.get(dtype, DAMAGE_TYPE_COLOR["other"]))
            if approx:
                color[3] = 120   # more transparent = approximate
            points.append({
                "lat": coords[0], "lon": coords[1],
                "color": color,
                "radius": 90,
                "tooltip": (
                    f"{'[LOCATION APPROX] ' if approx else ''}"
                    f"DAMAGE — {dtype.replace('_', ' ').title()}\n"
                    f"Severity: {item.get('severity','').replace('_',' ').upper()}\n"
                    f"Location: {loc}\n"
                    f"{item.get('description','')}"
                ),
            })

    # ── Access points (all statuses) ──────────────────────────────────────────
    STATUS_COLOR = {
        "fully_blocked":    [239,  68,  68, 220],
        "partially_blocked":[234, 179,   8, 220],
        "passable":         [ 34, 197,  94, 180],
        "unknown":          [100, 100, 100, 180],
    }
    for ap in results["access_data"].get("access_points", []):
        loc = (ap.get("location_desc") or "").strip()
        if not loc or loc in seen:
            continue
        seen.add(loc)
        coords, approx = try_geocode(loc)
        if coords:
            status = ap.get("status", "unknown")
            color = list(STATUS_COLOR.get(status, STATUS_COLOR["unknown"]))
            if approx:
                color[3] = 120
            points.append({
                "lat": coords[0], "lon": coords[1],
                "color": color,
                "radius": 70,
                "tooltip": (
                    f"{'[LOCATION APPROX] ' if approx else ''}"
                    f"ROUTE — {status.replace('_', ' ').upper()}\n"
                    f"Obstruction: {ap.get('obstruction_type','').replace('_',' ').title()}\n"
                    f"{ap.get('obstruction_detail','')}\n"
                    f"Location: {loc}"
                ),
            })

    # ── Staging areas ─────────────────────────────────────────────────────────
    for sa in results["access_data"].get("staging_areas", []):
        loc = str(sa).strip()
        if not loc or loc in seen:
            continue
        seen.add(loc)
        coords, approx = try_geocode(loc)
        if coords:
            color = [34, 197, 94, 220 if not approx else 120]
            points.append({
                "lat": coords[0], "lon": coords[1],
                "color": color,
                "radius": 70,
                "tooltip": f"{'[LOCATION APPROX] ' if approx else ''}STAGING AREA\n{loc}",
            })

    return points


# ── Sidebar ────────────────────────────────────────────────────────────────────

api_key = os.getenv("TWELVELABS_API_KEY", "")

with st.sidebar:
    st.markdown("## 🚨 Disaster Assessment")
    st.markdown("Aerial drone footage analysis powered by **TwelveLabs**")
    st.divider()

    st.markdown("**Upload Drone Footage**")
    video_file = st.file_uploader("Drone footage", type=["mp4", "mov", "avi", "mkv"], label_visibility="collapsed")
    video_url = None

    disaster_type = st.selectbox("Disaster Type", ["auto", "tornado", "hurricane", "wildfire", "flood", "earthquake"],
                                 format_func=lambda x: x.title())
    location_city = st.text_input("City / Region", value="St. Louis, MO")
    index_name = st.text_input("Index Name", value="disaster_assessment_index",
                               help="Reuse a name to skip re-indexing the same video")

    st.divider()
    run_btn = st.button("▶ Run Analysis", type="primary", use_container_width=True,
                        disabled=not api_key or not video_file)

    if not api_key:
        st.warning("TWELVELABS_API_KEY not found in .env", icon="⚠️")

    if "results" in st.session_state and st.session_state.results:
        if st.button("🗑 Clear Results", use_container_width=True):
            for key in ("results", "video_bytes", "map_points", "repeatability"):
                st.session_state.pop(key, None)
            st.rerun()


# ── Header ─────────────────────────────────────────────────────────────────────

st.markdown("# 🚨 Disaster Zone Aerial Assessment System")
st.caption("Upload drone footage to generate a full damage, resource, route, and financial assessment.")


# ── Run pipeline ───────────────────────────────────────────────────────────────

if run_btn and api_key and video_file:
    client = TwelveLabs(api_key=api_key)

    # Save uploaded file to temp path so TwelveLabs can read it
    tmp_path = None
    if video_file:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(video_file.name).suffix) as tmp:
            tmp.write(video_file.read())
            tmp_path = tmp.name
        video_file.seek(0)
        st.session_state["video_bytes"] = video_file.read()
        video_file.seek(0)
    else:
        st.session_state["video_bytes"] = None

    with st.status("Running assessment pipeline…", expanded=True) as status:
        def step(msg):
            st.write(msg)

        step("📁 Setting up index…")
        # Get or create index
        index_id = None
        for idx in client.indexes.list():
            if idx.index_name == index_name:
                index_id = idx.id
                break
        if not index_id:
            idx = client.indexes.create(
                index_name=index_name,
                models=[
                    {"model_name": "marengo3.0", "model_options": ["visual", "audio"]},
                    {"model_name": "pegasus1.2",  "model_options": ["visual", "conversation"]},
                ]
            )
            index_id = idx.id

        step("📤 Uploading and indexing video…")
        if tmp_path:
            with open(tmp_path, "rb") as f:
                task = client.tasks.create(index_id=index_id, video_file=f)
        else:
            task = client.tasks.create(index_id=index_id, video_url=video_url)

        completed = client.tasks.wait_for_done(task.id, sleep_interval=10)
        if completed.status != "ready":
            st.error(f"Indexing failed: {completed.status}")
            st.stop()

        video_id = completed.video_id
        step(f"✅ Video indexed: `{video_id}`")

        results = run_analysis(client, index_id, video_id, disaster_type, location_city, step)
        results["video_id"] = video_id
        results["index_id"] = index_id

        n_locs = len(results["damage_items"]) + len(results["access_data"].get("access_points", []))
        step(f"🗺️ Geocoding {n_locs} locations for map…")
        st.session_state["map_points"] = geocode_all(results)

        status.update(label="✅ Assessment complete!", state="complete")

    st.session_state["results"] = results

    # Clean up temp file
    if tmp_path:
        Path(tmp_path).unlink(missing_ok=True)


# ── Display results ────────────────────────────────────────────────────────────

if "results" in st.session_state and st.session_state.results:
    r = st.session_state.results

    # Top stats bar
    n_blocked = sum(1 for ap in r["access_data"].get("access_points", []) if ap.get("status") == "fully_blocked")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Damage Events",    len(r["damage_items"]))
    col2.metric("Resources Needed", len(r["resource_items"]))
    col3.metric("Blocked Routes",   n_blocked)
    col4.metric("Est. Mid Damage",  f"${r['total_mid']:,.0f}")

    st.divider()

    # ── Map ────────────────────────────────────────────────────────────────────
    map_points = st.session_state.get("map_points", [])
    with st.expander("🗺️ Damage & Route Map", expanded=bool(map_points)):
        if map_points:
            import pandas as pd
            import pydeck as pdk
            df_map = pd.DataFrame(map_points)
            layer = pdk.Layer(
                "ScatterplotLayer",
                data=df_map,
                get_position=["lon", "lat"],
                get_fill_color="color",
                get_radius="radius",
                radius_scale=6,
                pickable=True,
                opacity=0.85,
            )
            center_lat = df_map["lat"].mean()
            center_lon = df_map["lon"].mean()
            view = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=13, pitch=0)
            st.pydeck_chart(pdk.Deck(
                layers=[layer],
                initial_view_state=view,
                tooltip={"text": "{tooltip}"},
                map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            ))
            # Legend — damage types present in this assessment
            st.markdown("**Damage categories**")
            present_types = {
                item.get("damage_type", "other")
                for item in r["damage_items"] if isinstance(item, dict)
            }
            legend_items = [DAMAGE_TYPE_EMOJI[t] for t in DAMAGE_TYPE_COLOR if t in present_types]
            legend_items += ["🔴 Fully blocked route", "🟡 Partially blocked", "🟢 Passable / Staging"]
            leg_cols = st.columns(min(len(legend_items), 4))
            for i, label in enumerate(legend_items):
                leg_cols[i % 4].markdown(label)
            st.caption("Faded markers = location could not be geocoded precisely (placed near city center)")
        elif "map_points" in st.session_state:
            st.info("No locations could be geocoded — location descriptions may be too vague for the geocoder.")
        else:
            st.info("Run an analysis to generate the map.")

    st.divider()

    # Video + report side by side
    left, right = st.columns([1, 1], gap="large")

    with left:
        st.subheader("📹 Drone Footage")
        if st.session_state.get("video_bytes"):
            st.video(st.session_state["video_bytes"])
        elif video_url:
            st.video(video_url)
        else:
            st.info("Video not available for playback.")

        st.divider()
        report_md = build_report_md(r)
        st.download_button(
            "⬇ Download Full Report (.md)",
            data=report_md,
            file_name=f"disaster_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
            mime="text/markdown",
            use_container_width=True,
        )
        st.download_button(
            "⬇ Download Raw Data (.json)",
            data=json.dumps(r, indent=2),
            file_name=f"disaster_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
            use_container_width=True,
        )

    with right:
        tab_geo, tab_damage, tab_resources, tab_access, tab_finance, tab_validation = st.tabs(
            ["📍 Locations", "🏚️ Damage", "🚛 Resources", "🛣️ Access", "💰 Financial", "✅ Validation"]
        )

        # --- Locations tab ---
        with tab_geo:
            if r["geo_items"]:
                import pandas as pd
                df = pd.DataFrame([{
                    "Type": g.get("type", "").replace("_", " ").title(),
                    "Identifier": g.get("identifier", ""),
                    "Confidence": g.get("confidence", ""),
                    "Timestamp": g.get("timestamp_approx", ""),
                    "Notes": g.get("notes", ""),
                } for g in r["geo_items"] if isinstance(g, dict)])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No location markers were identified in the footage.")

        # --- Damage tab ---
        with tab_damage:
            sev_order = {"complete_destruction": 0, "severe": 1, "moderate": 2, "minor": 3}
            sev_color = {"complete_destruction": "🔴", "severe": "🟠", "moderate": "🟡", "minor": "🟢"}
            sorted_damage = sorted(r["damage_items"], key=lambda x: sev_order.get(x.get("severity", "minor"), 4))
            if sorted_damage:
                for item in sorted_damage:
                    sev = item.get("severity", "unknown")
                    emoji = sev_color.get(sev, "⚪")
                    with st.expander(f"{emoji} **{item.get('damage_type','').replace('_',' ').title()}** — {item.get('structure_type','').replace('_',' ').title()}  ·  {sev.replace('_',' ').upper()}"):
                        st.markdown(f"**📍 Location:** {item.get('location', 'N/A')}")
                        st.markdown(f"**⏱ Timestamp:** {item.get('timestamp_approx', 'N/A')}")
                        st.markdown(item.get("description", ""))
            else:
                st.info("No damage events identified.")

        # --- Resources tab ---
        with tab_resources:
            priority_color = {"immediate": "🔴", "short_term": "🟡", "long_term": "🟢"}
            p_order = {"immediate": 0, "short_term": 1, "long_term": 2}
            sorted_r = sorted(r["resource_items"], key=lambda x: p_order.get(x.get("priority", "long_term"), 3))
            if sorted_r:
                for res in sorted_r:
                    pri = res.get("priority", "")
                    emoji = priority_color.get(pri, "⚪")
                    with st.expander(f"{emoji} **{res.get('resource','N/A')}** — {res.get('quantity_estimate','?')}"):
                        st.markdown(f"**📍 Delivery Location:** {res.get('location', 'N/A')}")
                        st.markdown(f"**⚡ Priority:** {pri.replace('_',' ').title()}")
                        st.markdown(f"**Triggered by:** {res.get('triggered_by','N/A')}")
                        if res.get("notes"):
                            st.caption(res["notes"])
            else:
                st.info("No resource requirements identified.")

        # --- Access tab ---
        with tab_access:
            status_color = {"fully_blocked": "🔴", "partially_blocked": "🟡", "passable": "🟢", "unknown": "⚫"}
            for ap in r["access_data"].get("access_points", []):
                s = ap.get("status", "unknown")
                emoji = status_color.get(s, "⚪")
                with st.expander(f"{emoji} **{ap.get('location_desc','Unknown')}** — {s.replace('_',' ').upper()}"):
                    if s != "passable":
                        st.markdown(f"**Obstruction:** {ap.get('obstruction_type','').replace('_',' ').title()} — {ap.get('obstruction_detail','')}")
                        st.markdown(f"**Alternate Route:** {ap.get('alternate_route','N/A')}")

            staging = r["access_data"].get("staging_areas", [])
            if staging:
                st.markdown("**Staging / Landing Zones**")
                for s in staging:
                    st.markdown(f"- {s}")

            if r["marengo_results"]:
                st.divider()
                st.markdown("**Marengo Scene Timestamps**")
                for query, clips in r["marengo_results"].items():
                    top = clips[0] if clips else None
                    if top:
                        st.markdown(f"- **{query.title()}:** {top['start']:.0f}s – {top['end']:.0f}s")

        # --- Financial tab ---
        with tab_finance:
            if r["cost_rows"]:
                import pandas as pd
                df = pd.DataFrame([{
                    "Damage Category": row["category"],
                    "Count": row["count"],
                    "Low Estimate": f"${row['low']:,.0f}",
                    "Mid Estimate": f"${row['mid']:,.0f}",
                    "High Estimate": f"${row['high']:,.0f}",
                } for row in r["cost_rows"]])
                st.dataframe(df, use_container_width=True, hide_index=True)

                c1, c2, c3 = st.columns(3)
                c1.metric("Low Estimate",  f"${r['total_low']:,.0f}",  delta_color="off")
                c2.metric("Mid Estimate",  f"${r['total_mid']:,.0f}",  delta_color="off")
                c3.metric("High Estimate", f"${r['total_high']:,.0f}", delta_color="off")

                st.caption(f"Confidence: **{r['confidence'].upper()}** · Covers visible damage only · Based on FEMA and insurance industry averages")
            else:
                st.info("No financial data available.")

        # --- Validation tab ---
        with tab_validation:
            if "metrics" not in r:
                st.info("Validation metrics not available for this result. Re-run the analysis to generate them.")
            else:
                mx = r["metrics"]
                q  = mx["quality"]

                # Overall health score
                score = mx["health_score"]
                color = "#27ae60" if score >= 75 else "#e67e22" if score >= 50 else "#e74c3c"
                label = "Good" if score >= 75 else "Fair" if score >= 50 else "Poor"
                st.markdown(
                    f"<div style='text-align:center; padding:16px 0 8px'>"
                    f"<span style='font-size:3rem; font-weight:700; color:{color}'>{score}</span>"
                    f"<span style='font-size:1rem; color:{color}; margin-left:8px'>/ 100 — {label}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.caption("Score = parse success (40%) + field completeness (40%) + financial coverage (20%)")
                st.divider()

                # Response quality table
                st.markdown("#### Response Quality")
                import pandas as pd
                rows = [
                    {"Section": "Geo / Locations",
                     "Parsed": "✓" if q["geo"]["parse_ok"] else "✗",
                     "Items": q["geo"]["count"],
                     "High-conf IDs": q["geo"].get("high_conf", "—"),
                     "Completeness": f"{q['geo']['completeness']:.0f}%"},
                    {"Section": "Damage",
                     "Parsed": "✓" if q["damage"]["parse_ok"] else "✗",
                     "Items": q["damage"]["count"],
                     "High-conf IDs": "—",
                     "Completeness": f"{q['damage']['completeness']:.0f}%"},
                    {"Section": "Resources",
                     "Parsed": "✓" if q["resources"]["parse_ok"] else "✗",
                     "Items": q["resources"]["count"],
                     "High-conf IDs": "—",
                     "Completeness": f"{q['resources']['completeness']:.0f}%"},
                    {"Section": "Access",
                     "Parsed": "✓" if q["access"]["parse_ok"] else "✗",
                     "Items": f"{q['access']['count']} routes · {q['access']['staging']} staging",
                     "High-conf IDs": "—",
                     "Completeness": f"{q['access']['completeness']:.0f}%"},
                    {"Section": "Financial",
                     "Parsed": "✓" if q["financial"]["parse_ok"] else "✗",
                     "Items": f"{q['financial']['nonzero_cats']} / 11 categories",
                     "High-conf IDs": q["financial"]["confidence"].upper(),
                     "Completeness": "—"},
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.divider()

                # Cross-section consistency
                st.markdown("#### Cross-Section Consistency")
                type_emoji = {"ok": "✅", "warning": "⚠️", "info": "ℹ️"}
                for chk in mx["consistency_checks"]:
                    emoji = type_emoji.get(chk["type"], "•")
                    with st.expander(f"{emoji} **{chk['section']}** — {chk['message']}"):
                        if chk["details"]:
                            for d in chk["details"]:
                                st.markdown(f"- {d}")
                        else:
                            st.markdown("_No issues found._")

                st.divider()
                st.markdown("#### Repeatability Testing")
                st.caption(
                    "Runs the financial damage-count prompt 3× on the same video and measures "
                    "how consistent Pegasus is across independent calls. Lower variance = higher reliability."
                )
                rep_btn = st.button("🔄 Run 3-Iteration Repeatability Test", use_container_width=True)
                if rep_btn:
                    if not api_key:
                        st.error("API key required — enter it in the sidebar.")
                    elif not r.get("video_id"):
                        st.error("Video ID missing — re-run the main analysis first.")
                    else:
                        rep_client = TwelveLabs(api_key=api_key)
                        iterations = []
                        with st.spinner("Running 3 independent iterations…"):
                            for _ in range(3):
                                try:
                                    resp = rep_client.analyze(video_id=r["video_id"], prompt=FINANCIAL_PROMPT)
                                    mm = re.search(r'\{.*\}', resp.data, re.DOTALL)
                                    if mm:
                                        counts = json.loads(mm.group())
                                        iterations.append({
                                            k: v for k, v in counts.items()
                                            if k != "assessment_confidence" and isinstance(v, (int, float))
                                        })
                                    else:
                                        iterations.append({})
                                except Exception:
                                    iterations.append({})
                        st.session_state["repeatability"] = iterations

                if st.session_state.get("repeatability"):
                    import pandas as pd, math
                    iters = st.session_state["repeatability"]
                    all_keys = sorted({k for it in iters for k in it})
                    rep_rows = []
                    cvs = []
                    for key in all_keys:
                        vals = [it.get(key, 0) for it in iters]
                        mean = sum(vals) / len(vals)
                        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
                        std = math.sqrt(variance)
                        cv = (std / mean * 100) if mean > 0 else 0.0
                        cvs.append(cv)
                        rep_rows.append({
                            "Damage Category": key.replace("_", " ").title(),
                            "Run 1": int(vals[0]), "Run 2": int(vals[1]), "Run 3": int(vals[2]),
                            "Mean": f"{mean:.1f}",
                            "Std Dev": f"{std:.1f}",
                            "CV %": f"{cv:.0f}%",
                            "Consistent": "✓" if cv <= 20 else "~" if cv <= 50 else "✗",
                        })
                    st.dataframe(pd.DataFrame(rep_rows), use_container_width=True, hide_index=True)

                    overall_cv = sum(cvs) / len(cvs) if cvs else 0
                    rep_score = max(0, round(100 - overall_cv))
                    rep_color = "#27ae60" if rep_score >= 75 else "#e67e22" if rep_score >= 50 else "#e74c3c"
                    rep_label = "High" if rep_score >= 75 else "Medium" if rep_score >= 50 else "Low"
                    st.markdown(
                        f"<div style='text-align:center; padding:12px 0'>"
                        f"<span style='font-size:2rem; font-weight:700; color:{rep_color}'>{rep_score}</span>"
                        f"<span style='font-size:0.9rem; color:{rep_color}; margin-left:8px'>"
                        f"/ 100 — {rep_label} Consistency</span></div>",
                        unsafe_allow_html=True,
                    )
                    st.caption("CV ≤ 20% = consistent ✓  |  CV 21–50% = moderate ~  |  CV > 50% = variable ✗")

else:
    st.markdown("""
    ### How to use
    1. Enter your **TwelveLabs API key** in the sidebar
    2. Upload drone footage or provide a video URL
    3. Set the disaster type and city
    4. Click **▶ Run Analysis**

    The system will identify damage, recommend resources, analyze road access, and estimate financial impact.
    """)
