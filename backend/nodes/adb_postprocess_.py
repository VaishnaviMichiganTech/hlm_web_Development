"""
adb_postprocess_.py
===================
Place in: hlm_web/backend/nodes/

All three files share time_nsec (same PTP-synced Pi clock).
Alignment uses pandas merge_asof on time_nsec — no conversion, no interpolation.

    data.csv   time_nsec | ch0..ch7          (DAQ lux, ~1613 Hz)
    ncom.csv   time_nsec | Latitude[deg] |   (GPS/IMU, ~100 Hz)
                           Pitch[rad] | ...
    rcom.csv   time_nsec | Resultant_range[m] (range, ~100 Hz)

Called from postprocess_.py:
    try:
        from nodes.adb_postprocess_ import run_adb_report
        run_adb_report(root, meta=self.meta)
    except Exception as e:
        logger.logger.error(f"ADB report failed: {e}")
"""

import json, math, os, sys, pathlib, warnings
import numpy as np
warnings.filterwarnings("ignore")

try:
    import pandas as pd
except ImportError:
    raise ImportError("pip install pandas")

RAD2DEG  = 180.0 / math.pi
DAQ_GAIN = 6.8      # matches postprocess_.py fix_lux(gain=6.8)


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_meta(root):
    with open(pathlib.Path(root) / "metadata.json") as f:
        return json.load(f)

def _find(root, meta, meta_keys, fallbacks):
    for k in meta_keys:
        v = meta.get(k, "")
        if v and os.path.exists(v):
            return v
    for name in fallbacks:
        p = pathlib.Path(root) / name
        if p.exists():
            return str(p)
    return None


# ── core merge: align all sensors on time_nsec ───────────────────────────────

def merge_on_time_nsec(daq_path, ncom_path, rcom_path, channel_names):
    """
    Reads all three CSVs and merges them on time_nsec using merge_asof.
    Returns a single DataFrame indexed by time_nsec with all needed columns.

    All files share the same Pi wall-clock (PTP-synced Unix nanoseconds).
    No time conversion or overlap check needed.
    """
    # ── DAQ ──────────────────────────────────────────────────────────────
    daq = pd.read_csv(daq_path)
    daq["time_nsec"] = daq["time_nsec"].astype(np.int64)
    daq = daq.sort_values("time_nsec").drop_duplicates("time_nsec")

    # Apply gain to lux channels
    for name in channel_names:
        if name in daq.columns:
            daq[name] = daq[name] * DAQ_GAIN
        else:
            print(f"  [DAQ]  WARNING: channel {name!r} not in data.csv")

    avail = [c for c in daq.columns if c.startswith("ch")]
    found = [n for n in channel_names if n in daq.columns]
    if not found:
        print(f"  [DAQ]  WARNING: none of {channel_names} found. Available: {avail}")
        found = avail
        for name in found:
            daq[name] = daq[name] * DAQ_GAIN
    print(f"  [DAQ]  {len(daq)} rows | channels: {found}")

    # ── NCOM ─────────────────────────────────────────────────────────────
    ncom = pd.read_csv(ncom_path)
    ncom["time_nsec"] = ncom["time_nsec"].astype(np.int64)
    ncom = ncom.sort_values("time_nsec").drop_duplicates("time_nsec")

    # Convert units from ostx_decoder output:
    #   Latitude[deg], Longitude[deg] → already degrees
    #   Pitch[rad], Roll[rad]         → convert to degrees
    #   North/East/Down_velocity[m/s] → compute scalar speed
    if "Pitch[rad]" in ncom.columns:
        ncom["pitch_deg"] = ncom["Pitch[rad]"] * RAD2DEG
    else:
        ncom["pitch_deg"] = 0.0
        print("  [NCOM] WARNING: no Pitch column — using 0")

    for vc in ["North_velocity[m/s]", "East_velocity[m/s]", "Down_velocity[m/s]"]:
        if vc not in ncom.columns:
            ncom[vc] = 0.0
    ncom["speed_ms"] = np.sqrt(
        ncom["North_velocity[m/s]"]**2 +
        ncom["East_velocity[m/s]"]**2 +
        ncom["Down_velocity[m/s]"]**2)

    keep_ncom = ["time_nsec", "Latitude[deg]", "Longitude[deg]",
                 "Altitude[m]", "pitch_deg", "speed_ms"]
    ncom = ncom[[c for c in keep_ncom if c in ncom.columns]]
    print(f"  [NCOM] {len(ncom)} rows | "
          f"lat {ncom.get('Latitude[deg]', pd.Series([0])).iloc[0]:.4f}°")

    # ── RCOM ─────────────────────────────────────────────────────────────
    range_col = None
    if rcom_path and os.path.exists(rcom_path):
        rcom = pd.read_csv(rcom_path)
        rcom["time_nsec"] = rcom["time_nsec"].astype(np.int64)

        # Filter target 1 only
        if "Target_number" in rcom.columns:
            rcom = rcom[rcom["Target_number"] == 1]

        if "Resultant_range[m]" in rcom.columns:
            range_col = "Resultant_range[m]"
        elif "Lateral_range[m]" in rcom.columns:
            rcom["Resultant_range[m]"] = np.sqrt(
                rcom["Lateral_range[m]"]**2 + rcom["Longitudinal_range[m]"]**2)
            range_col = "Resultant_range[m]"

        if range_col:
            rcom = rcom[["time_nsec", range_col]].sort_values("time_nsec")
            rcom = rcom.drop_duplicates("time_nsec")
            print(f"  [RCOM] {len(rcom)} rows | "
                  f"range {rcom[range_col].min():.1f}–{rcom[range_col].max():.1f} m")
        else:
            print(f"  [RCOM] no range columns found")
            rcom = None
    else:
        print(f"  [RCOM] not found")
        rcom = None

    # ── merge_asof: NCOM → DAQ (nearest time_nsec) ────────────────────
    merged = pd.merge_asof(
        daq.sort_values("time_nsec"),
        ncom.sort_values("time_nsec"),
        on="time_nsec",
        direction="nearest",
        tolerance=50_000_000  # 50 ms max gap
    )

    # ── merge_asof: RCOM → merged ─────────────────────────────────────
    if rcom is not None and range_col:
        merged = pd.merge_asof(
            merged.sort_values("time_nsec"),
            rcom.sort_values("time_nsec"),
            on="time_nsec",
            direction="nearest",
            tolerance=50_000_000
        )
    else:
        merged["Resultant_range[m]"] = np.nan

    n_with_range = merged["Resultant_range[m]"].notna().sum()
    n_with_pitch = merged["pitch_deg"].notna().sum() if "pitch_deg" in merged.columns else 0
    print(f"  [Merged] {len(merged)} rows | "
          f"range valid: {n_with_range} | pitch valid: {n_with_pitch}")

    return merged, found


# ── build csv_data and mat_data for process_from_data ────────────────────────

def build_data_dicts(merged, channel_names):
    """
    Converts merged DataFrame into csv_data + mat_data dicts.
    time is divided by 1e9 → seconds, only for physics (cumtrapz needs seconds).
    """
    t_s = merged["time_nsec"].to_numpy(dtype=float) / 1e9

    lat   = merged.get("Latitude[deg]",  pd.Series(np.zeros(len(merged)))).to_numpy(dtype=float)
    lon   = merged.get("Longitude[deg]", pd.Series(np.zeros(len(merged)))).to_numpy(dtype=float)
    alt   = merged.get("Altitude[m]",    pd.Series(np.zeros(len(merged)))).to_numpy(dtype=float)
    pitch = merged.get("pitch_deg",      pd.Series(np.zeros(len(merged)))).to_numpy(dtype=float)
    roll  = np.zeros(len(merged))
    speed = merged.get("speed_ms",       pd.Series(np.zeros(len(merged)))).to_numpy(dtype=float)
    rng   = merged.get("Resultant_range[m]", pd.Series(np.full(len(merged), np.nan))).to_numpy(dtype=float)

    csv_data = dict(time=t_s, lat=lat, lon=lon, alt=alt,
                    pitch=pitch, roll=roll, speed=speed, range=rng)

    mat_data = dict(time=t_s, range=rng, pitch=pitch)
    channel_nums = []
    for name in channel_names:
        if name in merged.columns:
            num = int(name.replace("ch", ""))
            mat_data[f"lux{num}"] = merged[name].to_numpy(dtype=float)
            channel_nums.append(num)

    return csv_data, mat_data, channel_nums


# ── main entry point ─────────────────────────────────────────────────────────

def run_adb_report(root, meta=None, scenario=None, vehicle="Unknown",
                   adb_process_dir=None):
    root = pathlib.Path(root)
    print(f"\n{'='*60}\n  ADB Post-Process: {root.name}\n{'='*60}")

    if adb_process_dir:
        sys.path.insert(0, str(adb_process_dir))
    try:
        import adb_process as _adb
    except ImportError:
        raise ImportError("adb_process.py not found. Place in nodes/ or pass adb_process_dir=")

    if meta is None:
        meta = _load_meta(root)

    # scenario_config_number is 1-indexed (set_scenario route does +1)
    if scenario is None:
        sc_raw = meta.get("scenario_config_number")
        if sc_raw is None:
            print("  WARNING: no scenario in metadata — ADB report skipped")
            return None
        scenario = int(sc_raw)

    # Channel names: prefer CHANNEL_MAP from metadata (has actual recorded channels)
    # Fall back to test_catalog for live system where CHANNEL_MAP uses logical names
    ch_map = meta.get("config", {}).get("DAQ", {}).get("CHANNEL_MAP", {})
    if ch_map:
        channel_names = []
        for k in sorted(ch_map.keys(), key=lambda x: int(x)):
            v = ch_map[k]
            name = v[0] if isinstance(v, list) else v
            if name and not str(name).startswith("Null"):
                channel_names.append(name)
        print(f"  Channels from CHANNEL_MAP: {channel_names}")
    else:
        catalog_path = pathlib.Path(__file__).parent.parent / ".configs" / "test_catalog.json"
        channel_names = ["ch0", "ch1"]
        if catalog_path.exists():
            with open(catalog_path) as f:
                cat = json.load(f)
            ch = (cat.get("scenario_configs", {})
                     .get(str(scenario), {})
                     .get("lux_requirements", {})
                     .get("channels"))
            if ch:
                channel_names = ch
        print(f"  Channels from test_catalog: {channel_names}")
    print(f"  Scenario: {scenario} | Vehicle: {vehicle} | Channels: {channel_names}")

    daq_path  = _find(root, meta, ["DAQ", "csv"],  ["data.csv", "daq.csv"])
    ncom_path = _find(root, meta, ["NCOM"],         ["ncom.csv", "NCOM.csv"])
    rcom_path = _find(root, meta, ["RCOM"],         ["rcom.csv", "RCOM.csv"])

    if not daq_path:
        raise FileNotFoundError(f"DAQ/lux CSV not found in {root}")
    if not ncom_path:
        raise FileNotFoundError(f"NCOM CSV not found in {root}")

    print(f"  DAQ : {daq_path}")
    print(f"  NCOM: {ncom_path}")
    print(f"  RCOM: {rcom_path or 'not found'}")

    merged, found = merge_on_time_nsec(daq_path, ncom_path, rcom_path, channel_names)
    csv_data, mat_data, channel_nums = build_data_dicts(merged, found)

    outdir = str(root / "adb_result")
    os.makedirs(outdir, exist_ok=True)

    from datetime import datetime
    rec = meta.get("recording", root.name)
    try:
        from datetime import datetime as _dt
        ds = _dt.strptime(rec[:8], "%Y%m%d").strftime("%m%d%Y")
    except ValueError:
        ds = "01012026"

    report = _adb.process_from_data(
        csv_data=csv_data, mat_data=mat_data,
        scenario_num=scenario, vehicle=vehicle,
        channels=channel_nums, outdir=outdir,
        csv_path=ncom_path or "", mat_path=f"LuxRTK_{rec}_Scenario{scenario}_{ds}.mat",
    )

    if report:
        overall = report.get("compliance", {}).get("overall_pass", False)
        meta["adb_result"] = "PASS" if overall else "FAIL"
        meta["adb_report"] = str(pathlib.Path(outdir) / "report_data.json")
        with open(root / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2, default=str)
        print(f"\n  ADB: {meta['adb_result']}  →  {meta['adb_report']}")

    # Copy report template into adb_result so it can be served directly
    import shutil as _shutil
    _html_src = pathlib.Path(__file__).parent.parent.parent / "adb_report_v3.html"
    if _html_src.exists():
        _shutil.copy(_html_src, pathlib.Path(outdir) / "adb_report_v3.html")
    else:
        print(f"  WARNING: adb_report_v3.html not found at {_html_src}")

    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder",          required=True)
    ap.add_argument("--scenario",        type=int,   default=None)
    ap.add_argument("--vehicle",                     default="Unknown")
    ap.add_argument("--adb_process_dir",             default=None)
    args = ap.parse_args()
    run_adb_report(args.folder, scenario=args.scenario,
                   vehicle=args.vehicle, adb_process_dir=args.adb_process_dir)