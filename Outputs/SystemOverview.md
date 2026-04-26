# Disaster Zone Aerial Assessment System

**Automated post-disaster triage from drone footage using TwelveLabs Marengo + Pegasus**

---

## Overview

The Disaster Zone Aerial Assessment System ingests aerial drone video of a disaster-affected area and automatically produces a full intelligence report in minutes — without requiring manual review of the footage. It is designed to help first responders and emergency managers rapidly understand the scope of damage, prioritize resource deployment, and identify safe access corridors.

The system was developed as a hackathon project targeting the St. Louis, MO region and demonstrated against tornado drone footage of the Park Ridge neighborhood.

---

## System Architecture

```
Drone Video
    │
    ▼
┌─────────────────────────────────────────────┐
│          TwelveLabs Indexing Pipeline        │
│  marengo3.0  ──  visual + audio embeddings  │
│  pegasus1.2  ──  visual + conversation       │
└─────────────────────────────────────────────┘
    │
    ├── Pegasus: Location Identification
    ├── Pegasus: Damage Assessment
    ├── Pegasus: Resource Requirements
    ├── Pegasus: Access & Route Analysis
    ├── Pegasus: Financial Damage Estimation
    └── Marengo: Semantic Scene Search (6 queries)
    │
    ▼
┌─────────────────────────────────────────────┐
│         Validation & Metrics Engine          │
│  • Response quality scoring                  │
│  • Cross-section consistency checks          │
│  • Repeatability testing (3-iteration)       │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│           Output Interfaces                  │
│  • Streamlit web app  (app.py)               │
│  • Jupyter notebook   (disaster_assessment)  │
│  • Markdown report    (.md)                  │
│  • Structured JSON    (_data.json)           │
└─────────────────────────────────────────────┘
```

---

## Features

| Feature | Description |
|---|---|
| **Location Identification** | Extracts street signs, intersections, neighborhood markers, and landmarks visible in the footage to ground all analysis in real-world coordinates |
| **Damage Assessment** | Classifies each damaged structure by type, severity, structure category, and location |
| **Resource Manifest** | Produces a prioritized list of emergency supplies and services triggered by observed damage |
| **Access & Route Analysis** | Identifies passable, partially blocked, and fully blocked roads with obstruction details and alternate routes |
| **Marengo Scene Search** | Uses semantic video search to surface specific scene timestamps (debris, flooding, fire, landing zones) |
| **Financial Estimation** | Counts damage instances and applies FEMA/insurance unit cost ranges to produce low/mid/high estimates |
| **Interactive Map** | Geocodes all damage locations and route blockages onto an interactive pydeck map, color-coded by damage type |
| **Validation Suite** | Scores response quality, field completeness, and runs 4 cross-section consistency checks |
| **Repeatability Testing** | Runs the financial count prompt 3× on the same video and reports coefficient of variation per category |
| **Dual Interface** | Streamlit web app for demo use; Jupyter notebook for iterative development and inspection |

---

## Damage Type Classification

The system identifies 12 distinct damage categories:

| Code | Label | Map Color |
|---|---|---|
| `missing_roof` | Complete Roof Loss | Orange |
| `partial_roof_damage` | Partial Roof Damage | Light Orange |
| `structural_collapse` | Structural Collapse | Red |
| `fire_damage` | Fire Damage | Dark Red |
| `flood_submersion` | Flood Submersion | Blue |
| `flood_debris` | Flood Debris | Light Blue |
| `road_blocked` | Road Blocked | Yellow |
| `bridge_damage` | Bridge Damage | Purple |
| `downed_trees` | Downed Trees | Green |
| `downed_power_lines` | Downed Power Lines | Bright Yellow |
| `vehicle_damage` | Vehicle Damage | Grey |
| `other` | Other | White |

Severity levels: `minor` · `moderate` · `severe` · `complete_destruction`

---

## Sample Output — STL Tornado Assessment

**Video:** Aerial drone footage, Park Ridge neighborhood, St. Louis, MO  
**Disaster type:** Tornado / severe windstorm  
**Assessment confidence:** MEDIUM  
**Analysis date:** 2026-04-26

### Identified Locations

| Type | Identifier | Confidence | Timestamp |
|---|---|---|---|
| Neighborhood | Park Ridge | High | 00:00–00:16 |

### Damage Assessment

| Damage Type | Severity | Structure | Location | Timestamp |
|---|---|---|---|---|
| Structural Collapse | Complete Destruction | Commercial building | Park Ridge, St. Louis, MO | 00:23–00:39 |
| Missing Roof | Severe | Residential home | Park Ridge, St. Louis, MO | 00:10–00:22 |
| Downed Trees | Severe | Other | Park Ridge, St. Louis, MO | 00:10–00:22 |
| Downed Power Lines | Severe | Utility infrastructure | Park Ridge, St. Louis, MO | 00:10–00:22 |
| Flood Debris | Severe | Other | Park Ridge, St. Louis, MO | 00:23–00:39 |
| Partial Roof Damage | Moderate | Residential home | Park Ridge, St. Louis, MO | 00:10–00:22 |

### Resource Requirements

| Resource | Quantity | Priority | Triggered By |
|---|---|---|---|
| Heavy-duty tarps | 100–150 units | **Immediate** | Damaged roofs |
| Chainsaw crews | 3–4 crews | **Immediate** | Uprooted/broken trees |
| Water pumps | 5–10 units | **Immediate** | Standing water / flooding |
| Sandbags | 1,000–2,000 bags | **Immediate** | Potential flooding |
| Generators | 10–15 units | **Immediate** | Power outages |
| Structural engineers | 2–3 teams | Short-term | Collapsed/damaged buildings |

### Access & Route Analysis

| Location | Status | Obstruction | Alternate Route |
|---|---|---|---|
| I-64 eastbound on-ramp near Grand Blvd | **Fully Blocked** | Structural collapse — collapsed building across on-ramp | Not visible in footage |
| Kingshighway at Forest Park Ave | Partially Blocked | Downed trees across street | Not visible in footage |
| Central Park | Passable | None | — |

**Staging / Landing Zones:** Central Park · Open field near collapsed church

### Marengo Semantic Scene Search

| Query | Clips Found | Top Match (seconds) |
|---|---|---|
| Blocked road debris | 3 | 133.8s – 140.5s |
| Flooded road underwater | 3 | 188.0s – 194.0s |
| Collapsed bridge | 3 | 63.0s – 70.0s |
| Downed power lines road | 3 | 260.0s – 266.0s |
| Fire blocking access | 3 | 83.5s – 89.8s |
| Open field landing zone | 3 | 11.5s – 17.3s |

### Financial Damage Estimate

| Damage Category | Count | Low | Mid | High |
|---|---|---|---|---|
| Complete Roof Loss | 10 | $100,000 | $180,000 | $300,000 |
| Partial Roof Damage | 20 | $50,000 | $120,000 | $240,000 |
| Structural Collapse | 2 | $160,000 | $300,000 | $600,000 |
| Fire Damage (Partial) | 5 | $75,000 | $250,000 | $600,000 |
| Damaged Vehicles | 5 | $75,000 | $150,000 | $300,000 |
| Downed Utility Poles | 5 | $25,000 | $60,000 | $125,000 |
| Debris Clearance (acres) | 10 | $30,000 | $80,000 | $200,000 |
| **TOTAL** | | **$515,000** | **$1,140,000** | **$2,365,000** |

> Unit costs based on FEMA and insurance industry averages. Covers visible damage only.

---

## Validation Framework

### Response Quality Metrics

Each pipeline section is scored on three dimensions:

| Dimension | Description | Weight |
|---|---|---|
| **Parse success** | Did Pegasus return valid, parseable JSON? | 40% of health score |
| **Field completeness** | What % of required fields are populated per item? | 40% of health score |
| **Financial coverage** | How many of 11 damage categories have non-zero counts? | 20% of health score |

The **Overall Health Score** (0–100) is computed as:

```
Health Score = (parse_success_rate × 40) + (avg_field_completeness × 0.40) + (nonzero_categories / 11 × 20)
```

Color coding: **≥75 = Good** (green) · **50–74 = Fair** (orange) · **<50 = Poor** (red)

### Cross-Section Consistency Checks

Four automated checks validate that the five output sections agree with each other:

| Check | What it validates |
|---|---|
| **Damage → Resources** | Every damage item has at least one resource requirement addressing it |
| **Access → Damage** | Every blocked route has a corroborating damage entry matching its obstruction type |
| **Financial → Damage** | Every non-zero financial category maps to at least one damage item of the corresponding type |
| **Marengo → Pegasus** | Every scene found by Marengo semantic search is corroborated by a Pegasus damage entry |

Results are flagged as ✅ (all consistent), ⚠️ (gaps found), or ℹ️ (nothing to validate).

### Repeatability Testing

The financial damage-count prompt is run **3 independent times** on the same indexed video. For each damage category the system computes:

- **Mean** count across 3 runs
- **Standard deviation**
- **Coefficient of Variation (CV%)** = (std ÷ mean) × 100

| CV Range | Label | Indicator |
|---|---|---|
| ≤ 20% | Consistent | ✓ |
| 21–50% | Moderate | ~ |
| > 50% | Variable | ✗ |

**Overall Consistency Score** = max(0, 100 − avg_CV), color-coded on the same green/orange/red scale.

---

## Tech Stack

| Component | Technology |
|---|---|
| Video understanding | [TwelveLabs](https://twelvelabs.io) Marengo 3.0 + Pegasus 1.2 |
| Web application | [Streamlit](https://streamlit.io) 1.56 |
| Interactive map | [pydeck](https://pydeck.gl) 0.9 + CARTO dark tiles |
| Geocoding | OpenStreetMap Nominatim (no API key required) |
| Data tables | pandas 3.0 |
| Notebook interface | Jupyter + rich |
| Video download | yt-dlp |
| Environment | Python 3.14, uv |

---

## File Structure

```
├── app.py                          # Streamlit web application
├── disaster_assessment.ipynb       # Jupyter analysis notebook
├── download_video.py               # yt-dlp YouTube downloader helper
├── .env                            # API keys (not committed)
├── .env.example                    # Key template
├── .streamlit/
│   └── config.toml                 # Disables usage stats prompt
├── downloads/                      # Downloaded drone footage
└── disaster_report_*.md            # Generated assessment reports
    disaster_report_*_data.json     # Structured JSON output
```

---

## Setup

**Prerequisites:** Python 3.11+, [uv](https://github.com/astral-sh/uv), a [TwelveLabs API key](https://twelvelabs.io)

```bash
# 1. Clone the repository
git clone <repo-url>
cd <repo-dir>

# 2. Create virtual environment and install dependencies
uv venv
uv pip install streamlit pandas twelvelabs python-dotenv yt-dlp pydeck requests

# 3. Set your API key
echo "TWELVELABS_API_KEY=your_key_here" > .env

# 4. Run the web app
.venv/Scripts/python -m streamlit run app.py   # Windows
# or
.venv/bin/python -m streamlit run app.py        # Mac/Linux
```

The app opens at `http://localhost:8501`. Upload drone footage, select a disaster type and city, and click **Run Analysis**.

### Downloading Footage from YouTube

```bash
python download_video.py <youtube_url> --quality 720p --output-dir downloads/
```

---

## Usage — Web App

1. Upload drone footage (MP4, MOV, AVI, MKV) via the sidebar
2. Select the disaster type and enter the city/region
3. Click **▶ Run Analysis** — the pipeline runs all 5 Pegasus prompts + 6 Marengo queries
4. Results appear across 6 tabs: **Locations · Damage · Resources · Access · Financial · Validation**
5. The **map** auto-populates with geocoded damage and route markers
6. Download the full report as `.md` or raw data as `.json`

---

## Usage — Jupyter Notebook

Run cells sequentially. Each cell is independent and stores its output in a shared variable so later cells can reference it even if earlier cells are not re-run.

| Cell | Purpose |
|---|---|
| `325ffe74` | Configuration — set `VIDEO_PATH`, `DISASTER_TYPE`, `LOCATION_CITY` |
| `4832e9d4` | Create or reuse TwelveLabs index |
| `57169e18` | Upload and index the video |
| `fd5d4ece` | Location identification (Pegasus) |
| `da071fbf` | Damage assessment (Pegasus) |
| `aec90a65` | Resource requirements (Pegasus) |
| `a2bde6e4` | Access & route analysis (Pegasus) |
| `4d2dc837` | Semantic scene search (Marengo) |
| `914b97e4` | Financial estimation (Pegasus + UNIT_COSTS) |
| `bff863d4` | Report generation |
| `e3c03e8c` | Save `.md` and `_data.json` to disk |

---

## Models

| Model | Role | Options |
|---|---|---|
| `marengo3.0` | Semantic video embedding and scene search | `visual`, `audio` |
| `pegasus1.2` | Video-to-text generation for all 5 analysis prompts | `visual`, `conversation` |

Both models are registered on a single shared TwelveLabs index so each video is indexed once and can be queried by both models without re-upload.

---

*Disaster Zone Aerial Assessment System — built for the University of Missouri Hackathon*
