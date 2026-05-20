# Luminosity Measurement System — ADB Headlight Compliance Testing

**hlm_web** + **ADB Report Generation Integration**

Developed at the American Center for Mobility (ACM) in partnership with Michigan Technological University (MTU) for FMVSS 108 Adaptive Driving Beam (ADB) headlight compliance testing.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Hardware Architecture](#2-hardware-architecture)
3. [Repository Structure](#3-repository-structure)
4. [Data Flow — End to End](#4-data-flow--end-to-end)
5. [Original System (hlm_web)](#5-original-system-hlm_web)
6. [ADB Report Generation Integration (New)](#6-adb-report-generation-integration-new)
7. [File-by-File Reference](#7-file-by-file-reference)
8. [Setup Guide](#8-setup-guide)
9. [Running the System — Step by Step](#9-running-the-system--step-by-step)
10. [GitHub Workflow — Fork, Branch, PR](#10-github-workflow--fork-branch-pr)
11. [Test Catalog Configuration](#11-test-catalog-configuration)
12. [FMVSS 108 Scenario Reference](#12-fmvss-108-scenario-reference)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. System Overview

This system records, processes, and evaluates headlight illuminance data for ADB compliance under three standards:

| Standard | Description |
|---|---|
| **FMVSS 108** | US Federal Motor Vehicle Safety Standard — vehicle-level track test |

The system has two major components:

**hlm_web** — the recording system running on a Raspberry Pi. Records lux (photometer), GPS/IMU (OxTS INS), and range (OxTS RCOM) data over UDP. Provides a web interface for test operators.

**ADB Report Generation** — post-processing layer  that reads the recorded CSVs, runs FMVSS 108 compliance analysis, generates plots, and produces an interactive HTML report.

---

## 2. Hardware Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Test Vehicle                         │
│  OxTS INS (SN40745)                                    │
│    → NCOM packets (GPS, IMU)   UDP → Raspberry Pi      │
│    → RCOM packets (range)      UDP → Raspberry Pi      │
│                                                         │
│  NI DAQ system                                         │
│    → Lux sensor packets        UDP → Raspberry Pi      │
│                                                         │
│  Camera (USB)                  V4L2 → Raspberry Pi     │
└─────────────────────────────────────────────────────────┘
                           │
                    Raspberry Pi 4
                    (PTP clock sync)
                    /home/dev/hlm_web/
                    /DATABASE/  (recordings)
                           │
                    Web Interface
                    http://<PI_IP>:3000
                    (operator laptop browser)
```

**Time synchronisation**: All sensors share the same clock — the Pi's PTP-synced Unix wall clock. Every packet from every sensor is timestamped with `time_nsec` (nanoseconds since Unix epoch, Jan 1 1970). This is the master time reference for all data alignment — **no GPS time conversion is needed**.

---

## 3. Repository Structure

```
hlm_web/
├── backend/
│   ├── main.py                     FastAPI app — registers all routes
│   ├── requirements.txt
│   ├── .configs/
│   │   ├── default.cfg             Default sensor configuration
│   │   ├── current.cfg             Active config (written by UI)
│   │   └── test_catalog.json       FMVSS 108 scenario definitions + lux limits
│   ├── nodes/
│   │   ├── core.py                 Recording lifecycle (start/stop)
│   │   ├── lux_.py                 DAQ binary → data.csv converter
│   │   ├── rt_.py                  OxTS binary → ncom.csv / rcom.csv converter
│   │   ├── ostx_decoder_.py        OxTS NCOM/RCOM packet decoder
│   │   ├── postprocess_.py         System compliance checks (range/speed/ROC/lux)
│   │   ├── adb_postprocess_.py     ★ NEW — ADB report pipeline adapter
│   │   ├── adb_process.py          ★ NEW — Full FMVSS 108 analysis + plots
│   │   ├── config_manager_.py      Config file loader
│   │   ├── file_manager_.py        Recording folder management
│   │   ├── logger_.py              Redis-backed logging
│   │   ├── network_manager_.py     UDP socket management
│   │   └── camera_.py              Video recording
│   └── routes/
│       ├── recorder.py             /api/record/start, /stop, /api/set_scenario
│       ├── config_editor.py        /api/config, /api/test_catalog, /api/set_scenario
│       ├── records_browser.py      /api/records/list, /download
│       ├── report.py               ★ NEW — /api/records/report
│       ├── lux_meter.py            /api/lux_sensors/stream (SSE)
│       ├── logger_view.py          /api/logger/logs (SSE)
│       └── camera_feed.py          /api/camera_feed/stream
└── frontend/
    └── src/
        ├── App.jsx
        ├── Home.jsx                Live dashboard (lux + camera + record button)
        ├── Config.jsx              Sensor config editor
        ├── RecordingsBrowser.jsx   ★ MODIFIED — added Report button
        └── components/
            ├── LuxSensors.jsx      Live lux chart
            ├── TestCatalog.jsx     Scenario dropdown + auto post-process toggle
            ├── LoggerFeed.jsx      Live log stream
            └── CameraFeed.jsx      Live camera feed

adb_report_v3.html                  ★ NEW — standalone interactive HTML report
```

★ = files added or modified as part of this integration

---

## 4. Data Flow — End to End

```
HARDWARE                  RECORDING                 POST-PROCESSING           REPORT
─────────                 ─────────                 ───────────────           ──────

NI DAQ (lux)              lux_.py                   adb_postprocess_.py
  UDP packets    ──────►  data.bin ──────────────►  read_ncom()
  ~1613 Hz                data.csv                  merge_on_time_nsec()
  ch0..ch7                time_nsec | ch0..ch7       │                        adb_report_v3.html
  (raw ADC)                                          │                        reads report_data.json
                                                     ▼                        shows:
OxTS INS (GPS)            rt_.py                    adb_process.py            - Compliance table
  NCOM UDP     ──────────► ncom.bin ────────────►   process_from_data()       - Lux plots
  ~100 Hz                 ncom.csv                   compute_grade()           - ROC geometry
  Lat/Lon/Pitch           time_nsec                  make_signed_range()       - Speed/pitch
  velocities              Latitude[deg]              compute_pitch_exclusion() - GPS map
  Pitch[rad]              Pitch[rad]                 compute_lux_compliance()  - PASS/FAIL
                          North_velocity[m/s]        compute_roc()
                                                     → plot_*.png
OxTS RCOM (range)         rt_.py                    → report_data.json
  RCOM UDP     ──────────► rcom.bin ────────────►
  ~100 Hz                 rcom.csv
  Resultant_range[m]      time_nsec
  Target_number           Resultant_range[m]

Camera                    camera_.py
  V4L2         ──────────► camera_feed.mp4

                          metadata.json             metadata.json updated
                          (written on Stop)  ──────► result: PASS/FAIL
                                                     adb_result: PASS/FAIL
                                                     adb_report: path
```

### Time alignment

All three CSVs share `time_nsec` from the same PTP-synced clock. Alignment uses `pandas.merge_asof` with a 50ms tolerance:

```python
merged = pd.merge_asof(daq, ncom, on='time_nsec', direction='nearest', tolerance=50_000_000)
merged = pd.merge_asof(merged, rcom, on='time_nsec', direction='nearest', tolerance=50_000_000)
```

Result: every lux sample has the nearest GPS position, pitch, and range value attached.

### Unit conversions applied in adb_postprocess_.py

| Column | Raw unit | Converted to |
|---|---|---|
| `Latitude[deg]` | degrees | degrees (no change — ostx_decoder already converts) |
| `Pitch[rad]` | radians | degrees (× 180/π) |
| `Roll[rad]` | radians | degrees (× 180/π) |
| `North/East/Down_velocity[m/s]` | m/s components | scalar speed = norm2 |
| `ch0..ch7` | raw ADC float | lux (× gain 6.8) |
| `time_nsec` | Unix nanoseconds | seconds (÷ 1e9) for physics only |

---

## 5. Original System (hlm_web)

### Recording lifecycle (`core.py`)

**Start recording** (`POST /api/record/start`):
1. Reads `current.cfg` for sensor IPs/ports/channels
2. Creates timestamped folder in `/DATABASE/YYYYMMDDTNN/`
3. Opens UDP sockets for DAQ, NCOM, RCOM
4. Starts binary recording threads

**Stop recording** (`POST /api/record/stop`):
1. Stops all recording threads
2. `lux_.py`: converts `data.bin` → `data.csv` (time_nsec, ch0..ch7)
3. `rt_.py`: converts `ncom.bin` → `ncom.csv`, `rcom.bin` → `rcom.csv`
4. Writes `metadata.json` with all file paths + scenario number
5. If "Auto Post-Process" enabled: calls `TestPostProcess.process()`

### System post-processing (`postprocess_.py`)

`TestPostProcess.process(root)` runs four checks:

| Method | What it checks |
|---|---|
| `load()` | Reads metadata.json, loads scenario limits from test_catalog.json |
| `clean()` | Merges CSVs, applies gain 6.8, Butterworth 35Hz filter |
| `validate()` | Range coverage, speed within limits, ROC within limits |
| `examine()` | Max lux per bin vs FMVSS limits, with soft +20% allowance |

Result written to `metadata.json["result"]` as `TEST_PASSED` / `TEST_FAILED`.

### Frontend pages

| Page | URL | Function |
|---|---|---|
| Home | `/` | Live lux chart, camera feed, record button, scenario selector |
| Recordings | `/recordings` | Browse past tests, download, view report |
| Config | `/config` | Edit sensor IPs, ports, channel names |

---

## 6. ADB Report Generation Integration (New)

### What was added

Three new backend files, one modified backend file, one modified frontend file:

#### `nodes/adb_postprocess_.py` — the adapter

Bridges between the hlm_web recording format and `adb_process.py`. Called automatically at the end of `TestPostProcess.process()`.

Key function: `run_adb_report(root, meta)`

1. Reads channel names from `metadata.json["config"]["DAQ"]["CHANNEL_MAP"]`
2. Finds `data.csv`, `ncom.csv`, `rcom.csv` via metadata paths
3. Calls `merge_on_time_nsec()` — aligns all three on `time_nsec`
4. Converts units (Pitch radians→degrees, time ns→seconds)
5. Calls `adb_process.process_from_data()`
6. Writes result back to `metadata.json["adb_result"]`

#### `nodes/adb_process.py` — the analysis engine

Full FMVSS 108 compliance analysis. Two entry points:

- `process(csv_path, mat_path, ...)` — original entry point, reads OxTS mobile-NNN.csv + MAT file
- `process_from_data(csv_data, mat_data, ...)` — **new entry point**, accepts pre-read dicts from `adb_postprocess_.py`

Key analysis functions:

| Function | FMVSS reference | What it does |
|---|---|---|
| `compute_grade()` | Track geometry | cumtrapz(speed,time) → distance; 2m grid; Butterworth 0.1Hz |
| `make_signed_range()` | — | Negative=approach, zero=closest point, positive=departure |
| `compute_pitch_exclusion()` | S14.9.3.12.2.1(c) | avg pitch ±0.3° threshold; marks excluded samples |
| `compute_lux_compliance()` | S14.9.3.12.2 | Max lux per bin, ASTM E29 rounding to 0.1 lux |
| `compute_roc()` | Table XXII | GPS→local XY; circle fit; constrained when arc < 10° |
| `_nan_to_null()` | — | Converts NaN/Inf to null for valid JSON output |

Six plots generated per test:

| File | Content |
|---|---|
| `plot_grade_pitch.png` | Grade (%) and pitch (deg) vs signed range |
| `plot_pitch_diagnostic.png` | Pitch exclusion zone + lux with excluded samples marked |
| `plot_lux.png` | Lux vs range with FMVSS bin limits |
| `plot_speed_lux.png` | Speed and lux composite |
| `plot_elevation.png` | Elevation profile |
| `plot_roc.png` | GPS trajectory + circle fit (rotated frame) |

#### `routes/report.py` — serves the report

```
GET /api/records/report?path=<folder>      → serves adb_report.html
GET /api/records/report-data?path=<folder> → returns report_data.json
GET /api/records/report-status?path=<folder> → {"has_report": true/false}
```

#### `adb_report_v3.html` — interactive HTML report

Self-contained single-file report. Reads `report_data.json` from the same directory. Shows:
- Overall PASS/FAIL banner
- Compliance table (speed, grade, pitch, ROC, lux per bin per channel)
- All six plots embedded
- Interactive GPS satellite map
- Scenario parameters reference

#### Changes to `postprocess_.py`

Three lines added at the end of `TestPostProcess.process()`:

```python
try:
    from nodes.adb_postprocess_ import run_adb_report
    run_adb_report(root, meta=self.meta)
except Exception as e:
    logger.logger.error(f"ADB report failed: {e}")
```

#### Changes to `RecordingsBrowser.jsx`

Report button added to each test row in the recordings list. Opens `adb_report_v3.html` in a new tab.

---

## 7. File-by-File Reference

### What each file reads and writes

| File | Reads | Writes |
|---|---|---|
| `lux_.py` | `data.bin` | `data.csv` (time_nsec, ch0..ch7) |
| `rt_.py` | `ncom.bin`, `rcom.bin` | `ncom.csv`, `rcom.csv` |
| `postprocess_.py` | `data.csv`, `ncom.csv`, `rcom.csv`, `metadata.json` | `metadata.json` (result field) |
| `adb_postprocess_.py` | `data.csv`, `ncom.csv`, `rcom.csv`, `metadata.json` | `adb_result/report_data.json`, `adb_result/plot_*.png`, `metadata.json` (adb_result field) |
| `adb_process.py` | (data dicts from adb_postprocess_) | `report_data.json`, `plot_*.png` |
| `report.py` | `adb_result/adb_report_v3.html`, `adb_result/report_data.json` | (HTTP response) |

### Column names in each CSV

**data.csv** (from `lux_.py`):
```
time_nsec | ch0 | ch1 | ch2 | ch3 | ch4 | ch5 | ch6 | ch7
```
Names come from `CHANNEL_MAP` in `current.cfg`. Default is `ch0`–`ch7`. Null channels appear as `Null_N` and are dropped in postprocess.

**ncom.csv** (from `rt_.py` + `ostx_decoder_.py`):
```
time_nsec | Latitude[deg] | Longitude[deg] | Altitude[m] |
North_velocity[m/s] | East_velocity[m/s] | Down_velocity[m/s] |
Heading[rad] | Pitch[rad] | Roll[rad] | Time[s]
```
Note: `Latitude[deg]` and `Longitude[deg]` are **already in degrees** — `ostx_decoder_.py` calls `math.degrees()` during decode.
Note: `Pitch[rad]` and `Roll[rad]` are in **radians** — `adb_postprocess_.py` converts to degrees.

**rcom.csv** (from `rt_.py` + `ostx_decoder_.py`):
```
time_nsec | Target_number | Lateral_range[m] | Longitudinal_range[m] |
Resultant_range[m] | Lateral_range_rate[m/s] | ...
```
`Resultant_range[m]` is used directly. `Target_number == 1` filter applied.

---

## 8. Setup Guide

### Raspberry Pi setup (original hlm_web)

```bash
# 1. Install system packages
sudo apt install libopencv-dev libzmq3-dev npm cmake
sudo ldconfig

# 2. Clone repository
git clone https://github.com/VaishnaviMichiganTech/hlm_web.git
cd hlm_web
git checkout dev

# 3. Build C++ video recorder
cd backend/nodes
mkdir build && cd build
cmake .. && make

# 4. Install Node.js dependencies
cd ../../../frontend
npm install

# 5. Create Python virtual environment
python3 -m venv ~/hlm_env --system-site-packages
echo "source ~/hlm_env/bin/activate" >> ~/.bashrc
source ~/hlm_env/bin/activate

# 6. Install Python dependencies
pip install -r backend/requirements.txt
pip install utm mat73

# 7. Run the server
cd /home/dev/hlm_web
./run_dev.sh
```

Web interface available at `http://<PI_IP>:3000`

### Windows analysis environment

```powershell
conda create -n hlm_analysis python=3.11 -y
conda activate hlm_analysis
pip install numpy scipy matplotlib pandas mat73 utm
```

### Verify installation

```powershell
cd "path\to\HLB_System"
python preflight_check.py
```

All 42+ checks should pass.

---

## 9. Running the System — Step by Step

### Pre-test setup (once per session)

1. Power on Raspberry Pi and sensors
2. Connect laptop to Pi network
3. Open `http://<PI_IP>:3000` in browser
4. Go to Config page — verify sensor IPs and channel names match hardware
5. Check DAQ `CHANNEL_MAP` matches your photometer wiring (e.g. `ch0`=left sensor, `ch1`=right sensor)

### Per-test procedure

**Step 1 — Select scenario**
- On the Home page, use the "Test Catalog" dropdown to select the scenario (1–8)
- This sends `POST /api/set_scenario` → sets `core_.current_scenario`
- ⚠️ Must be done before every recording — does not persist between sessions

**Step 2 — Enable auto post-process**
- Check "Auto Post-Process?" checkbox
- This sends `POST /api/postprocess_toggle` → sets `core_.postprocess_enabled = True`

**Step 3 — Start recording**
- Click Record button → `POST /api/record/start`
- Pi creates `/DATABASE/YYYYMMDDTNN/` folder
- Binary recording starts for all enabled sensors

**Step 4 — Run test**
- Drive the test scenario at the required speed
- Monitor live lux on the Home page

**Step 5 — Stop recording**
- Click Stop → `POST /api/record/stop`
- Binary files converted to CSVs automatically
- `metadata.json` written with all file paths and scenario number
- `TestPostProcess.process()` runs (range/speed/ROC/lux validation)
- `run_adb_report()` runs (full FMVSS compliance analysis + plots)
- `metadata.json` updated with `result` and `adb_result`

**Step 6 — View report**
- Go to Recordings browser
- Find the test folder (grouped by Today/Yesterday/Last Week)
- Click **Report** button → opens `adb_report_v3.html` in new tab
- Report shows PASS/FAIL with all plots and compliance table

### What the report contains

```
OVERALL: PASS / FAIL
│
├── Test info (scenario, vehicle, date, channels)
├── Scenario parameters (speed limits, ROC, measurement range)
│
├── Compliance table
│   ├── Speed: mean mph, PASS/FAIL
│   ├── Grade: mean%, max%, PASS/FAIL
│   ├── Pitch: avg, threshold, excluded samples
│   ├── ROC: fitted R, deviation, PASS/FAIL (curved scenarios)
│   └── Lux per bin per channel: max lux, limit, PASS/FAIL
│
├── Plots (embedded PNGs)
│   ├── Grade + Pitch vs range
│   ├── Pitch exclusion diagnostic
│   ├── Lux vs range with FMVSS limits
│   ├── Speed + Lux composite
│   ├── Elevation profile
│   └── ROC geometry map
│
└── GPS satellite map (interactive)
```

---

## 10. GitHub Workflow — Fork, Branch, PR

### One-time setup: fork the original repo

```bash
# 1. Go to https://github.com/mojtaba1989/hlm_web
# 2. Click Fork → creates https://github.com/VaishnaviMichiganTech/hlm_web

# 3. Clone YOUR fork
git clone https://github.com/VaishnaviMichiganTech/hlm_web.git
cd hlm_web

# 4. Add original repo as upstream
git remote add upstream https://github.com/mojtaba1989/hlm_web.git

# 5. Checkout dev branch
git checkout dev
```

### Create your integration branch

```bash
git checkout dev
git pull upstream dev              # sync with original
git checkout -b adb-report-integration
```

### Add all new/modified files

```bash
# New files
cp path/to/adb_process.py       backend/nodes/adb_process.py
cp path/to/adb_postprocess_.py  backend/nodes/adb_postprocess_.py
cp path/to/report.py            backend/routes/report.py
cp path/to/adb_report_v3.html   adb_report_v3.html

# Modified files (already edited in place)
# backend/nodes/postprocess_.py
# backend/main.py
# backend/.configs/test_catalog.json
# frontend/src/RecordingsBrowser.jsx

git add backend/nodes/adb_process.py
git add backend/nodes/adb_postprocess_.py
git add backend/routes/report.py
git add backend/nodes/postprocess_.py
git add backend/main.py
git add backend/.configs/test_catalog.json
git add frontend/src/RecordingsBrowser.jsx
git add adb_report_v3.html

git commit -m "feat: FMVSS 108 ADB compliance report generation

- Add adb_process.py: full compliance analysis engine
  (grade, pitch exclusion, lux bins, ROC fitting, 6 plots)
- Add adb_postprocess_.py: adapter between hlm_web CSVs and
  adb_process — merges data.csv/ncom.csv/rcom.csv on time_nsec
- Add routes/report.py: /api/records/report endpoint
- Modify postprocess_.py: call run_adb_report() after system checks
- Modify main.py: register report router
- Modify test_catalog.json: add channels field to all 8 scenarios
- Modify RecordingsBrowser.jsx: add Report button per test row
- Add adb_report_v3.html: interactive HTML report template"

git push origin adb-report-integration
```

### Open pull request

```
1. Go to https://github.com/VaishnaviMichiganTech/hlm_web
2. Click "Compare & pull request"
3. Base: mojtaba1989/hlm_web  dev
   Head: VaishnaviMichiganTech/hlm_web  adb-report-integration
4. Title: "FMVSS 108 ADB Compliance Report Generation"
5. Description: paste the commit message above
6. Submit
```

### Keeping in sync with upstream

```bash
git checkout dev
git pull upstream dev
git checkout adb-report-integration
git rebase dev
git push origin adb-report-integration --force-with-lease
```

---

## 11. Test Catalog Configuration

`.configs/test_catalog.json` defines all 8 FMVSS 108 scenarios.

Each scenario entry:

```json
"1": {
  "description": "Straight road, opposite direction, 60-70 mph",
  "scenario_info": {
    "Test Vehicle Speed (mph)": { "min": 60, "max": 70 },
    "Radius of Curve (m.)": "Straight",
    "Measurement Distance Range (m)": { "min": 15, "max": 220 }
  },
  "lux_requirements": {
    "channels": ["ch0", "ch1"],
    "illuminance_distance_intervals": [
      { "min_distance_m": 15,  "max_distance_m": 30,  "max_illuminance_lux": 3.1 },
      { "min_distance_m": 30,  "max_distance_m": 60,  "max_illuminance_lux": 1.8 },
      { "min_distance_m": 60,  "max_distance_m": 120, "max_illuminance_lux": 0.6 },
      { "min_distance_m": 120, "max_distance_m": 220, "max_illuminance_lux": 0.3 }
    ]
  }
}
```

**`channels`** must match the `CHANNEL_MAP` names in `current.cfg`. For a system with photometers on channels 0 and 1 of the NI DAQ, this is `["ch0", "ch1"]`.

---

## 12. FMVSS 108 Scenario Reference

| Sc | Direction | Curve | Speed (mph) | ROC (m) | Max Range (m) |
|---|---|---|---|---|---|
| 1 | Opposite | Straight | 60–70 | N/A | 220 |
| 2 | Opposite | Left | 25–30 | 85–115 | 60 |
| 3 | Opposite | Left | 40–45 | 210–250 | 150 |
| 4 | Opposite | Left | 50–55 | 335–400 | 220 |
| 5 | Opposite | Right | 40–45 | 210–250 | 50 |
| 6 | Opposite | Right | 50–55 | 335–400 | 70 |
| 7 | Same | Straight | 60–70 | N/A | 100 |
| 8 | Same | Left | 40–45 | 210–250 | 100 |

**Lux limits** (opposite direction, all bins):

| Distance (m) | Max lux | Soft limit (+20%) |
|---|---|---|
| 15–30 | 3.1 | 3.72 |
| 30–60 | 1.8 | 2.16 |
| 60–120 | 0.6 | 0.72 |
| 120–220 | 0.3 | 0.36 |

**Pitch exclusion** (S14.9.3.12.2.1(c)): samples where pitch deviates more than ±0.3° from the mean pitch across the measurement distance are excluded from bin-max calculation.

**ROC compliance**: fitted radius R must be within scenario limits. Max path deviation from fitted circle ≤ 0.5m (ASTM E29 rounded to 1 decimal).

---

## 13. Troubleshooting

### Report button opens a blank page

The `adb_result/` folder exists but `adb_report_v3.html` is not inside it. After recording, copy the template:
```bash
cp /home/dev/hlm_web/adb_report_v3.html /DATABASE/<FOLDER>/adb_result/
```
Or serve from a central location and update `report.py` to point to it.

### "Could not parse JSON" in report

`report_data.json` contains Python `NaN` values. Ensure `_nan_to_null()` is present in `adb_process.py` and is called before `json.dump()`. Verify with:
```bash
grep "_nan_to_null" backend/nodes/adb_process.py
```

### Channels not found in data.csv

`adb_postprocess_.py` reads channel names from `metadata.json["config"]["DAQ"]["CHANNEL_MAP"]`. If this key is missing (old metadata schema), it falls back to `test_catalog.json`. Ensure `current.cfg` has the correct `CHANNEL_MAP` before recording.

### scenario_config_number missing from metadata

User did not select a scenario before recording. The scenario dropdown in the UI must be set **before** clicking Record. The setting does not persist between page loads.

### NCOM and DAQ time ranges don't overlap

This only happens if `adb_postprocess_.py` in `nodes/` is an old version that uses GPS time conversion instead of direct `time_nsec` merge. Verify:
```bash
grep "merge_on_time_nsec" backend/nodes/adb_postprocess_.py
```

### postprocess_ fails with `No module named 'utm'`

```bash
pip install utm --break-system-packages   # on Pi
pip install utm                           # on Windows analysis env
```

### ROC plot shows "Straight scenario"

This is correct for Scenarios 1 and 7. No ROC requirement exists for straight scenarios.

---

## References

- DOT/NHTSA, 49 CFR Part 571, FMVSS No. 108, Final Rule, February 1, 2022
- 49 CFR 571.108 (up to date as of 4/23/2025)
- SAE J3069 — Adaptive Driving Beam Systems, Jun 2016
- UN Regulation No. 48, December 3, 2024
- Mazzae et al., ADB Headlamps Test Repeatability Assessment, DOT HS 813 213, 2022
- Mazzae et al., ADB Headlighting Systems Rulemaking Support Testing, DOT HS 813 267, 2022

---

*Developed by Vaishnavi Balambeed — Michigan Technological University (MTU) / American Center for Mobility (ACM), 2025–2026*

GitHub: [VaishnaviMichiganTech](https://github.com/VaishnaviMichiganTech)
