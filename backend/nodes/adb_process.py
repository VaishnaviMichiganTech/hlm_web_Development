"""
adb_process.py
==============
FMVSS 108 ADB Compliance Processor
Replaces all MATLAB scripts for ADB headlight compliance analysis.

Usage:
    python adb_process.py --csv path/to/mobile-NNN.csv
                          --mat path/to/LuxRTK_TestNN_ScenarioN_MMDDYYYY.mat
                          --scenario N
                          --vehicle "Ford F-150"
                          [--channels 2 3]
                          [--outdir path/to/output_folder]

Outputs (written to outdir, defaults to same folder as CSV):
    report_data.json          -- all processed results for HTML report
    plot_grade_pitch.png      -- grade and pitch vs signed range
    plot_pitch_diagnostic.png -- pitch exclusion diagnostic with lux overlaid
    plot_lux.png              -- lux vs range with FMVSS bin limits (all channels)
    plot_speed_lux.png        -- speed + lux composite
    plot_elevation.png        -- elevation profile vs distance
    plot_roc.png              -- GPS trajectory + circle fit (rotated frame)

Dependencies:
    pip install numpy scipy matplotlib mat73
"""

import argparse
import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.signal import butter, filtfilt
from scipy.optimize import minimize
from scipy.integrate import cumulative_trapezoid as cumtrapz
def _nan_to_null(obj):
    """Recursively replace float NaN/Inf with None so JSON stays valid."""
    import math
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _nan_to_null(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_nan_to_null(v) for v in obj]
    return obj
# ---- MAT file loading (handles both old and new HDF5-based v7.3) ----
try:
    import mat73
    HAS_MAT73 = True
except ImportError:
    HAS_MAT73 = False
try:
    import scipy.io as sio
    HAS_SIO = True
except ImportError:
    HAS_SIO = False

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
import matplotlib.patheffects as pe

warnings.filterwarnings("ignore")


SCENARIOS = {
    1: dict(max_range=220, x_max=270,
            fm_ranges=[[15,30],[30,60],[60,120],[120,220]],
            fm_limits=[3.1, 1.8, 0.6, 0.3],
            direction="Opposite", curve_dir="N/A", roc="Straight",
            roc_min=None, roc_max=None,
            speed_min=60, speed_max=70, speed_min_kph=96.6, speed_max_kph=112.7,
            superelev="0-2"),
    2: dict(max_range=60,  x_max=110,
            fm_ranges=[[15,30],[30,60]],
            fm_limits=[3.1, 1.8],
            direction="Opposite", curve_dir="Left", roc="85-115",
            roc_min=85, roc_max=115,
            speed_min=25, speed_max=30, speed_min_kph=40.2, speed_max_kph=48.3,
            superelev="0-2"),
    3: dict(max_range=150, x_max=200,
            fm_ranges=[[15,30],[30,60],[60,120]],
            fm_limits=[3.1, 1.8, 0.6],
            direction="Opposite", curve_dir="Left", roc="210-250",
            roc_min=210, roc_max=250,
            speed_min=40, speed_max=45, speed_min_kph=64.4, speed_max_kph=72.4,
            superelev="0-2"),
    4: dict(max_range=220, x_max=270,
            fm_ranges=[[15,30],[30,60],[60,120],[120,220]],
            fm_limits=[3.1, 1.8, 0.6, 0.3],
            direction="Opposite", curve_dir="Left", roc="335-400",
            roc_min=335, roc_max=400,
            speed_min=50, speed_max=55, speed_min_kph=80.5, speed_max_kph=88.5,
            superelev="0-2"),
    5: dict(max_range=50,  x_max=100,
            fm_ranges=[[15,30],[30,50]],
            fm_limits=[3.1, 1.8],
            direction="Opposite", curve_dir="Right", roc="210-250",
            roc_min=210, roc_max=250,
            speed_min=40, speed_max=45, speed_min_kph=64.4, speed_max_kph=72.4,
            superelev="0-2"),
    6: dict(max_range=70,  x_max=120,
            fm_ranges=[[15,30],[30,60],[60,70]],
            fm_limits=[3.1, 1.8, 0.6],
            direction="Opposite", curve_dir="Right", roc="335-400",
            roc_min=335, roc_max=400,
            speed_min=50, speed_max=55, speed_min_kph=80.5, speed_max_kph=88.5,
            superelev="0-2"),
    7: dict(max_range=100, x_max=150,
            fm_ranges=[[15,30],[30,60],[60,100]],
            fm_limits=[18.9, 18.9, 4.0],
            direction="Same", curve_dir="N/A", roc="Straight",
            roc_min=None, roc_max=None,
            speed_min=60, speed_max=70, speed_min_kph=96.6, speed_max_kph=112.7,
            superelev="0-2"),
    8: dict(max_range=100, x_max=150,
            fm_ranges=[[15,30],[30,60],[60,100]],
            fm_limits=[18.9, 18.9, 4.0],
            direction="Same", curve_dir="Left", roc="210-250",
            roc_min=210, roc_max=250,
            speed_min=40, speed_max=45, speed_min_kph=64.4, speed_max_kph=72.4,
            superelev="0-2"),
}


STYLE = dict(
    data_blue  = "#2a6db5",
    orange     = "#d4601a",
    red        = "#c0392b",
    gray       = "#555555",
    lt_gray    = "#aaaaaa",
    green      = "#2a7a2a",
    purple     = "#6a2abf",
    lux_colors = ["#d4601a", "#2a6db5", "#2a7a2a", "#6a2abf", "#4a4a4a"],
    bin_colors = ["#d4601a", "#2a6db5", "#2a7a2a", "#6a2abf"],
    fig_dpi    = 150,
    fig_face   = "#fafaf8",
    ax_face    = "#fdfdfc",
    grid_color = "#e0dcd4",
)

def apply_style(ax, xlabel="", ylabel="", title=""):
    ax.set_facecolor(STYLE["ax_face"])
    ax.grid(True, color=STYLE["grid_color"], linewidth=0.7, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cccccc")
    ax.spines["bottom"].set_color("#cccccc")
    ax.tick_params(labelsize=9, color="#888888")
    if xlabel: ax.set_xlabel(xlabel, fontsize=9, color="#444")
    if ylabel: ax.set_ylabel(ylabel, fontsize=9, color="#444")
    if title:  ax.set_title(title, fontsize=10, color="#222", fontweight="bold", pad=6)


# ================================================================
# CSV READER
# Mirrors MATLAB CSV parsing: velocity auto-detect m/s vs km/h,
# range column fallback list, NaN filtering, unique-stable dedup.
# ================================================================
def read_csv(csv_path):
    import csv
    print(f"  Reading CSV: {csv_path}")
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        headers = [h.strip() for h in headers]
        rows = list(reader)

    def col(patterns):
        for pat in patterns:
            p = pat.lower()
            for i, h in enumerate(headers):
                if p in h.lower():
                    return i
        return None

    i_time  = col(["[Tag 0 SN40745 Real-Time]Time (GPS s)"])
    i_lat   = col(["Latitude (deg)"])
    i_lon   = col(["Longitude (deg)"])
    i_alt   = col(["Altitude (m)"])
    i_pitch = col(["Pitch (deg)"])
    i_roll  = col(["Roll (deg)"])

    # Velocity: prefer m/s, fall back to km/h
    i_vel_ms  = col(["Velocity forward (m/s)"])
    i_vel_kmh = col(["Velocity forward (km/h)"])
    vel_factor = 1.0
    if i_vel_ms is not None:
        i_vel = i_vel_ms
        print("    Velocity: m/s")
    elif i_vel_kmh is not None:
        i_vel = i_vel_kmh
        vel_factor = 1.0 / 3.6
        print("    Velocity: km/h -> m/s")
    else:
        raise ValueError("No velocity column found in CSV")

    # Range column: try Tag2 first (Scenarios 7-8), then Tag1
    i_range = col([
        "[Tag 2 SN Range]Target 1 ISO resultant range (m)",
        "[Tag 1 SN Range]Target 1 ISO resultant range (m)",
        "[Tag 2 SN Range]Target 1 range horizontal (m)",
        "[Tag 1 SN Range]Target 1 range horizontal (m)",
        "[Tag 2 SN Range]Target 1 range forward (m)",
        "[Tag 1 SN Range]Target 1 range forward (m)",
    ])
    if i_range is None:
        raise ValueError("No range column found in CSV")
    print(f"    Range col: {headers[i_range]}")

    def safe_float(val):
        try: return float(val)
        except: return np.nan

    data = {"time":[], "lat":[], "lon":[], "alt":[], "pitch":[],
            "roll":[], "speed":[], "range":[]}

    for row in rows:
        try:
            t = safe_float(row[i_time])
            if np.isnan(t): continue
            data["time"].append(t)
            data["lat"].append(safe_float(row[i_lat]))
            data["lon"].append(safe_float(row[i_lon]))
            data["alt"].append(safe_float(row[i_alt]))
            data["pitch"].append(safe_float(row[i_pitch]))
            data["roll"].append(safe_float(row[i_roll]))
            data["speed"].append(safe_float(row[i_vel]) * vel_factor)
            data["range"].append(safe_float(row[i_range]))
        except IndexError:
            continue

    for k in data:
        data[k] = np.array(data[k], dtype=float)

    # Filter NaN rows
    valid = (np.isfinite(data["time"]) & np.isfinite(data["speed"]) &
             np.isfinite(data["lat"])  & np.isfinite(data["lon"])   &
             np.isfinite(data["range"]))
    for k in data:
        data[k] = data[k][valid]

    # Deduplicate on GPS time (unique stable, first occurrence)
    _, ia = np.unique(data["time"], return_index=True)
    for k in data:
        data[k] = data[k][ia]

    print(f"    Rows after clean: {len(data['time'])}")
    return data


# ================================================================
# MAT FILE READER
# TestData struct fields: time, range, pitch, lux0..lux4
# Tries scipy.io first (v5), falls back to mat73 (v7.3 HDF5)
# ================================================================
def read_mat(mat_path, channels):
    print(f"  Reading MAT: {mat_path}")
    td = None
    if HAS_SIO:
        try:
            raw = sio.loadmat(mat_path, squeeze_me=True, struct_as_record=False)
            td = raw.get("TestData", None)
        except Exception:
            td = None

    if td is None and HAS_MAT73:
        try:
            raw = mat73.loadmat(mat_path)
            td_raw = raw.get("TestData", None)
            if td_raw is not None:
                # mat73 returns dicts
                class Obj:
                    pass
                td = Obj()
                for k, v in td_raw.items():
                    setattr(td, k, np.array(v).flatten())
        except Exception as e:
            raise RuntimeError(f"Could not read MAT file: {e}")

    if td is None:
        raise RuntimeError("TestData struct not found in MAT file")

    def get_field(obj, name):
        if isinstance(obj, dict):
            return np.array(obj.get(name, [])).flatten()
        return np.array(getattr(obj, name, [])).flatten()

    result = {
        "time":  get_field(td, "time"),
        "range": get_field(td, "range"),
        "pitch": get_field(td, "pitch"),
    }
    for ch in channels:
        fname = f"lux{ch}"
        arr = get_field(td, fname)
        if arr.size > 0:
            result[f"lux{ch}"] = arr
            print(f"    Found channel {ch}: {fname}, {arr.size} samples")
        else:
            print(f"    Channel {ch} ({fname}) not found in MAT")

    print(f"    MAT range: {result['range'].size} samples, "
          f"pitch: {result['pitch'].size} samples")
    return result


# ================================================================
# SIGNED RANGE
# Negative = approach, positive = departure.
# Closest approach (min range) is reference point (distance=0).
# ================================================================
def make_signed_range(raw_range):
    idx_min = int(np.argmin(raw_range))
    signed = raw_range.copy()
    signed[idx_min+1:] = -raw_range[idx_min+1:]
    return signed, idx_min


# ================================================================
# GRADE CALCULATION
# Uniform 2m distance grid -> altitude gradient -> Butterworth filter
# Matches MATLAB: cumtrapz for dist, 2m uniform spacing, 3rd order
# Butterworth 0.1Hz cutoff, filtfilt.
# ================================================================
def compute_grade(time_arr, speed_arr, alt_arr, range_arr):
    dist = cumtrapz(speed_arr, time_arr, initial=0)

    # Unique stable on distance
    _, ia = np.unique(dist, return_index=True)
    dist2 = dist[ia]; alt2 = alt_arr[ia]

    if len(dist2) < 2:
        # Not enough points -- return flat zero grade
        dist_sample = np.array([0.0, 1.0])
        alt_sample  = np.array([float(alt_arr[0]), float(alt_arr[0])])
        return dist_sample, alt_sample, np.zeros(2)

    # Sort by distance
    sort_idx = np.argsort(dist2)
    dist2 = dist2[sort_idx]; alt2 = alt2[sort_idx]

    dist_interval = 2.0
    if not np.isfinite(dist2[-1]) or dist2[-1] <= 0:
        return np.array([0.0, 1.0]), np.array([alt2[0], alt2[0]]), np.zeros(2)
    dist_sample = np.arange(0, dist2[-1], dist_interval)

    alt_sample = np.interp(dist_sample, dist2, alt2)

    d_alt  = np.diff(alt_sample)
    d_dist = np.diff(dist_sample)
    grade_angle = np.degrees(np.arctan(d_alt / d_dist))
    grade_angle = np.concatenate([[0], grade_angle])
    grade_angle = np.convolve(grade_angle, np.ones(3)/3, mode="same")  # movmedian approx

    # Effective sampling frequency for Butterworth
    mean_speed = np.mean(speed_arr[speed_arr > 1]) if np.any(speed_arr > 1) else 5.0
    Fs = mean_speed / dist_interval
    Fs = max(Fs, 2.0)
    cutoff = 0.1
    norm_cutoff = cutoff / (Fs / 2)
    norm_cutoff = min(norm_cutoff, 0.99)

    b, a = butter(3, norm_cutoff, btype="low")

    # filtfilt requires input length > padlen (3 * max(len(a), len(b)) = 12 for order 3)
    # If too short, fall back to a simple moving average instead
    min_len = 3 * max(len(a), len(b))
    if len(grade_angle) > min_len:
        grade_filt = filtfilt(b, a, grade_angle)
    else:
        # Short track: use moving average with window = min(5, len//2)
        win = max(1, min(5, len(grade_angle) // 2))
        grade_filt = np.convolve(grade_angle, np.ones(win)/win, mode="same")

    grade_pct = np.tan(np.radians(grade_filt)) * 100

    return dist_sample, alt_sample, grade_pct


# ================================================================
# ROC FITTING  (mirrors MATLAB fit_circle_ls + fit_circle_constrained)
# ================================================================
def fit_circle_ls(x, y):
    """Unconstrained least-squares circle fit."""
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if len(x) < 3:
        return np.nan, np.nan, np.nan
    A = np.column_stack([2*x, 2*y, np.ones(len(x))])
    b = x**2 + y**2
    try:
        p, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return np.nan, np.nan, np.nan
    xc, yc, c = p
    R = np.sqrt(xc**2 + yc**2 + c)
    if not np.isfinite(R) or R <= 0 or R > 50000:
        return np.nan, np.nan, np.nan
    return xc, yc, R


def fit_circle_constrained(x, y, R_min, R_max):
    """Constrained circle fit using scipy minimize (mirrors MATLAB fminsearch)."""
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if len(x) < 3:
        return np.nan, np.nan, np.nan

    xc0, yc0, _ = fit_circle_ls(x, y)
    if not np.isfinite(xc0):
        xc0, yc0 = np.mean(x), np.mean(y) + (R_min + R_max) / 2

    def obj(c):
        dists = np.sqrt((x - c[0])**2 + (y - c[1])**2)
        R_opt = np.clip(np.mean(dists), R_min, R_max)
        return np.sqrt(np.mean((dists - R_opt)**2))

    res = minimize(obj, [xc0, yc0], method="Nelder-Mead",
                   options={"xatol": 0.01, "fatol": 1e-6, "maxiter": 5000})
    xc, yc = res.x
    dists = np.sqrt((x - xc)**2 + (y - yc)**2)
    R = float(np.clip(np.mean(dists), R_min, R_max))
    return xc, yc, R


def compute_roc(lat, lon, range_raw, sc):
    """
    Full ROC computation matching MATLAB ROC script:
    1. GPS -> local XY (meters)
    2. Find measurement segment (rangeMax to rangeMin on approach)
    3. Rotate so travel aligns with +Y
    4. Auto-select fit method based on arc angle threshold (10 deg)
    5. Return fit results, residuals, road edges
    """
    if sc["roc_min"] is None:
        return None  # Straight scenario

    Re = 6378137.0
    lat0 = np.nanmean(lat)
    lon0 = np.nanmean(lon)
    x_all = (np.radians(lon) - np.radians(lon0)) * np.cos(np.radians(lat0)) * Re
    y_all = (np.radians(lat) - np.radians(lat0)) * Re

    # Find measurement segment on approach
    idx_closest = int(np.argmin(range_raw))
    rng_approach = range_raw[:idx_closest+1]

    i_max = int(np.argmin(np.abs(rng_approach - sc["max_range"])))
    i_min = int(np.argmin(np.abs(rng_approach - sc["fm_ranges"][0][0])))
    if i_max > i_min:
        i_max, i_min = i_min, i_max

    x_seg_raw = x_all[i_max:i_min+1]
    y_seg_raw = y_all[i_max:i_min+1]
    arc_len = float(np.sum(np.sqrt(np.diff(x_seg_raw)**2 + np.diff(y_seg_raw)**2)))

    # Rotation: align travel direction to +Y
    travel_dx = x_seg_raw[-1] - x_seg_raw[0]
    travel_dy = y_seg_raw[-1] - y_seg_raw[0]
    travel_ang = np.arctan2(travel_dy, travel_dx)
    theta = np.pi/2 - travel_ang

    def rot(x, y):
        return (x*np.cos(theta) - y*np.sin(theta),
                x*np.sin(theta) + y*np.cos(theta))

    x_all_r, y_all_r = rot(x_all, y_all)
    x_seg  = x_all_r[i_max:i_min+1]
    y_seg  = y_all_r[i_max:i_min+1]
    x_acc  = x_all_r[:i_max]
    y_acc  = y_all_r[:i_max]

    roc_min = sc["roc_min"]
    roc_max = sc["roc_max"]
    ARC_THRESH = 10.0  # degrees

    # Preliminary unconstrained fit to decide method
    xc_unc, yc_unc, R_unc = fit_circle_ls(x_seg, y_seg)
    R_ref = R_unc if np.isfinite(R_unc) else (roc_min + roc_max) / 2
    arc_angle_prelim = np.degrees(arc_len / R_ref)

    use_constrained = (
        arc_angle_prelim < ARC_THRESH or
        not np.isfinite(R_unc) or
        R_unc < roc_min * 0.3 or
        R_unc > roc_max * 5.0
    )

    if use_constrained:
        method = "Constrained"
        xc, yc, R = fit_circle_constrained(x_seg, y_seg, roc_min, roc_max)
    else:
        method = "Unconstrained"
        xc, yc, R = xc_unc, yc_unc, R_unc

    if not np.isfinite(R):
        return dict(failed=True, method=method)

    dists     = np.sqrt((x_seg - xc)**2 + (y_seg - yc)**2)
    residuals = dists - R
    rms_resid = float(np.sqrt(np.mean(residuals**2)))
    max_resid = float(np.max(np.abs(residuals)))
    arc_angle = float(np.degrees(arc_len / R))

    # FMVSS ASTM E29 rounding: round max_resid to 1 decimal
    max_resid_rounded = round(max_resid, 1)

    geo_pass = (roc_min <= R <= roc_max)
    dev_pass = (max_resid_rounded <= 0.5)

    # Rotation direction (CCW = left curve)
    travel_x = x_seg[-1] - x_seg[0]
    travel_y = y_seg[-1] - y_seg[0]
    to_ctr_x = xc - (x_seg[0] + x_seg[-1]) / 2
    to_ctr_y = yc - (y_seg[0] + y_seg[-1]) / 2
    cross_z  = travel_x * to_ctr_y - travel_y * to_ctr_x
    is_ccw   = cross_z > 0

    return dict(
        failed       = False,
        method       = method,
        xc=float(xc), yc=float(yc), R=float(R),
        residuals    = residuals.tolist(),
        rms_resid    = rms_resid,
        max_resid    = max_resid,
        max_resid_r  = max_resid_rounded,
        arc_angle    = arc_angle,
        arc_len      = arc_len,
        geo_pass     = bool(geo_pass),
        dev_pass     = bool(dev_pass),
        is_ccw       = bool(is_ccw),
        x_seg        = x_seg.tolist(),
        y_seg        = y_seg.tolist(),
        x_acc        = x_acc.tolist(),
        y_acc        = y_acc.tolist(),
        x_fix        = float(x_all_r[idx_closest]),
        y_fix        = float(y_all_r[idx_closest]),
        theta_rot    = float(theta),
        i_seg_start  = int(i_max),
        i_seg_end    = int(i_min),
    )


# ================================================================
# PITCH EXCLUSION  (S14.9.3.12.2.1(c))
# ================================================================
def compute_pitch_exclusion(pitch_arr, range_signed, sc):
    meas_mask = (range_signed >= sc["fm_ranges"][0][0]) & (range_signed <= sc["max_range"])
    if not np.any(meas_mask):
        return np.nan, np.nan, np.nan, np.zeros(len(pitch_arr), dtype=bool)

    avg_pitch   = float(np.mean(pitch_arr[meas_mask]))
    pitch_upper = avg_pitch + 0.3
    pitch_lower = avg_pitch - 0.3

    excl_mask = np.abs(pitch_arr - avg_pitch) > 0.3
    excl_mask = excl_mask & meas_mask

    return avg_pitch, pitch_upper, pitch_lower, excl_mask


# ================================================================
# LUX BIN COMPLIANCE  (S14.9.3.12.2)
# ASTM E29: round to nearest 0.1 lux
# ================================================================
def compute_lux_compliance(mat_data, channels, range_signed, excl_mask, sc):
    results = {}
    for ch in channels:
        key = f"lux{ch}"
        if key not in mat_data:
            continue
        lux = mat_data[key]
        ch_results = []
        for (d1, d2), lim in zip(sc["fm_ranges"], sc["fm_limits"]):
            bin_mask       = (range_signed >= d1) & (range_signed <= d2)
            bin_mask_valid = bin_mask & ~excl_mask

            max_before = float(np.max(lux[bin_mask])) if np.any(bin_mask) else np.nan
            max_after  = float(np.max(lux[bin_mask_valid])) if np.any(bin_mask_valid) else np.nan

            # ASTM E29 rounding to 0.1
            max_after_rounded = round(max_after, 1) if not np.isnan(max_after) else np.nan
            passed = (max_after_rounded <= lim) if not np.isnan(max_after_rounded) else False

            ch_results.append(dict(
                d1=d1, d2=d2, limit=lim,
                max_before=max_before,
                max_after=max_after,
                max_after_rounded=max_after_rounded,
                passed=bool(passed),
            ))
        results[ch] = ch_results
    return results


# ================================================================
# PLOT 1: GRADE + PITCH  (Fig 1 from MATLAB script 1/2)
# ================================================================
def plot_grade_pitch(signed_range_csv, grade_pct_on_sr, pitch_raw,
                     signed_range_mat, sc, outpath):
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), facecolor=STYLE["fig_face"],
                             sharex=False)
    fig.subplots_adjust(hspace=0.38)

    xlim = (-5, sc["max_range"])
    yl   = (-4, 4)
    ticks = _make_ticks(sc)

    # --- Grade ---
    ax = axes[0]
    apply_style(ax, ylabel="Grade (%)", title=f"Grade and Pitch vs Range -- FMVSS 108 Scenario {sc['_num']}")
    mask = (signed_range_csv >= -5) & (signed_range_csv <= sc["max_range"])
    ax.plot(signed_range_csv[mask], grade_pct_on_sr[mask],
            color=STYLE["data_blue"], lw=1.8, label="Grade (%)", zorder=3)
    mean_g = float(np.mean(grade_pct_on_sr[
        (signed_range_csv >= sc["fm_ranges"][0][0]) & (signed_range_csv <= sc["max_range"])
    ]))
    ax.axhline(mean_g,  color=STYLE["red"],     lw=1.8, ls="--", label=f"Mean grade {mean_g:.2f}%")
    ax.axhline( 2,      color=STYLE["lt_gray"], lw=1.5, ls="--", label="FMVSS +2%")
    ax.axhline(-2,      color=STYLE["lt_gray"], lw=1.5, ls="--", label="FMVSS -2%")
    ax.axvline(0, color="black", lw=1.2, ls="--", alpha=0.5)
    ax.set_xlim(xlim); ax.set_ylim(yl)
    ax.invert_xaxis()
    ax.set_xticks(ticks); ax.set_xticklabels([str(int(t)) for t in ticks], fontsize=8)
    _add_meas_range_markers(ax, sc, xlim)
    ax.legend(fontsize=8, loc="best", framealpha=0.8)

    # --- Pitch ---
    ax = axes[1]
    apply_style(ax, xlabel="Range to fixture (m)", ylabel="Pitch (deg)")
    mask2 = (signed_range_mat >= -5) & (signed_range_mat <= sc["max_range"])
    ax.plot(signed_range_mat[mask2], pitch_raw[mask2],
            color=STYLE["data_blue"], lw=1.8, label="Pitch (deg)", zorder=3)
    ax.axhline( 2.5, color=STYLE["lt_gray"], lw=1.5, ls="--", label="FMVSS +2.5 deg")
    ax.axhline(-2.5, color=STYLE["lt_gray"], lw=1.5, ls="--", label="FMVSS -2.5 deg")
    ax.axvline(0, color="black", lw=1.2, ls="--", alpha=0.5)
    ax.set_xlim(xlim); ax.set_ylim(yl)
    ax.invert_xaxis()
    ax.set_xticks(ticks); ax.set_xticklabels([str(int(t)) for t in ticks], fontsize=8)
    _add_meas_range_markers(ax, sc, xlim)
    ax.legend(fontsize=8, loc="best", framealpha=0.8)

    fig.savefig(outpath, dpi=STYLE["fig_dpi"], bbox_inches="tight",
                facecolor=STYLE["fig_face"])
    plt.close(fig)
    print(f"  Saved: {outpath}")


# ================================================================
# HELPER: add measurement range x-axis markers (vertical lines + labels)
# ================================================================
def _add_meas_range_markers(ax, sc, xlim):
    """Mark measurement range min and max on x-axis with vertical dashed lines."""
    d_min = sc["fm_ranges"][0][0]   # e.g. 15 m
    d_max = sc["max_range"]         # e.g. 60 m
    ax.axvline(d_min, color="#888", lw=1.2, ls="--", zorder=1)
    ax.axvline(d_max, color="#888", lw=1.2, ls="--", zorder=1)
    y_bot, y_top = ax.get_ylim()
    offset = (y_top - y_bot) * 0.03
    ax.text(d_min, y_bot + offset, f" {d_min} m", fontsize=7.5,
            color="#666", va="bottom", ha="left", style="italic")
    ax.text(d_max, y_bot + offset, f"{d_max} m ", fontsize=7.5,
            color="#666", va="bottom", ha="right", style="italic")


# ================================================================
# HELPER: build xticks including meas range endpoints
# ================================================================
def _make_ticks(sc):
    tick_step = max(10, round(sc["max_range"] / 5 / 10) * 10)
    ticks = set(np.arange(0, sc["max_range"] + 1, tick_step).tolist())
    ticks.add(float(sc["fm_ranges"][0][0]))   # meas min
    ticks.add(float(sc["max_range"]))          # meas max
    ticks = sorted(ticks)
    return np.array(ticks)


# ================================================================
# PLOT 2: PITCH DIAGNOSTIC  (mirrors MATLAB script 3 Fig 1)
# Changes: no PASS/FAIL text on markers (markers only), meas range lines on all axes
# ================================================================
def plot_pitch_diagnostic(mat_data, channels, signed_range_mat,
                          avg_pitch, excl_mask, sc, outpath):
    n_ch = sum(1 for ch in channels if f"lux{ch}" in mat_data)
    n_panels = 1 + n_ch
    fig, axes = plt.subplots(n_panels, 1, figsize=(11, 3.2*n_panels + 0.5),
                             facecolor=STYLE["fig_face"])
    if n_panels == 1:
        axes = [axes]
    fig.subplots_adjust(hspace=0.50)

    pitch_upper = avg_pitch + 0.3
    pitch_lower = avg_pitch - 0.3
    xlim = (0, sc["max_range"])
    pitch_arr = mat_data["pitch"]
    ticks = _make_ticks(sc)

    meas_mask = (signed_range_mat >= sc["fm_ranges"][0][0]) & \
                (signed_range_mat <= sc["max_range"])
    range_plot = signed_range_mat[meas_mask]
    pitch_plot = pitch_arr[meas_mask]
    excl_plot  = excl_mask[meas_mask]

    # --- Pitch panel ---
    ax = axes[0]
    apply_style(ax, ylabel="Pitch (deg)",
                title=f"S14.9.3.12.2.1(c) Pitch Exclusion -- Scenario {sc['_num']}  "
                      f"({sc['direction']} Direction)")

    ax.fill_between(range_plot, pitch_lower, pitch_upper,
                    color=STYLE["red"], alpha=0.10, zorder=1)
    ax.plot(range_plot, pitch_plot, color=STYLE["data_blue"], lw=1.8,
            label="Pitch (deg)", zorder=3)
    if np.any(excl_plot):
        ax.scatter(range_plot[excl_plot], pitch_plot[excl_plot],
                   color=STYLE["red"], s=28, zorder=5,
                   label=f"Excluded ({int(np.sum(excl_plot))} pts)")
    ax.axhline(avg_pitch,   color=STYLE["red"], lw=2.0, ls="-",
               label=f"Avg pitch {avg_pitch:.3f} deg")
    ax.axhline(pitch_upper, color=STYLE["red"], lw=1.4, ls="--",
               label=f"Avg +0.3 = {pitch_upper:.3f} deg")
    ax.axhline(pitch_lower, color=STYLE["red"], lw=1.4, ls="--",
               label=f"Avg -0.3 = {pitch_lower:.3f} deg")

    margin = 0.15
    if np.isfinite(pitch_lower) and np.isfinite(pitch_upper):
        ax.set_ylim(pitch_lower - margin, pitch_upper + margin)
    else:
        ax.set_ylim(-1, 1)
        ax.text(0.5, 0.5, "Pitch data unavailable", transform=ax.transAxes,
                ha="center", va="center", fontsize=11, color="#aaa")
    ax.set_xlim(xlim)
    ax.invert_xaxis()
    ax.set_xticks(ticks); ax.set_xticklabels([str(int(t)) for t in ticks], fontsize=8)
    _add_meas_range_markers(ax, sc, xlim)
    ax.legend(fontsize=7.5, loc="upper right", framealpha=0.85,
              bbox_to_anchor=(1.0, 1.0))

    # --- Lux panels (one per channel) ---
    bin_colors = STYLE["bin_colors"]
    y_max = 25 if sc["direction"] == "Same" else 5
    ch_idx = 0
    for ch in channels:
        key = f"lux{ch}"
        if key not in mat_data:
            continue
        lux = mat_data[key][meas_mask]
        color = STYLE["lux_colors"][ch_idx % len(STYLE["lux_colors"])]
        ax = axes[1 + ch_idx]
        apply_style(ax,
                    xlabel="Range to fixture (m)" if ch_idx == n_ch - 1 else "",
                    ylabel="Illuminance (lux)",
                    title=f"Lux Ch{ch} -- pitch-excluded samples in red")

        ax.plot(range_plot, lux, color=color, lw=1.8, label=f"Lux Ch{ch}", zorder=3)

        if np.any(excl_plot):
            ax.scatter(range_plot[excl_plot], lux[excl_plot],
                       color=STYLE["red"], s=28, zorder=5,
                       label=f"Pitch-excluded ({int(np.sum(excl_plot))} pts)")

        for bi, ((d1, d2), lim) in enumerate(zip(sc["fm_ranges"], sc["fm_limits"])):
            bin_mask  = (range_plot >= d1) & (range_plot <= d2)
            bin_valid = bin_mask & ~excl_plot
            c = bin_colors[bi % len(bin_colors)]

            ax.plot([d2, d1], [lim, lim], "--", color=c, lw=2.0,
                    label=f"FMVSS {d1}-{d2}m: {lim} lux")
            ax.axvline(d1, color="#bbb", lw=0.7, ls=":", zorder=0)
            ax.axvline(d2, color="#bbb", lw=0.7, ls=":", zorder=0)

            # Bin max BEFORE -- black open square, no text
            if np.any(bin_mask):
                idx_b = np.argmax(lux[bin_mask])
                r_b = range_plot[bin_mask][idx_b]
                v_b = lux[bin_mask][idx_b]
                ax.plot(r_b, v_b, "s", color="black", ms=8, zorder=6,
                        markerfacecolor="none", markeredgewidth=2.0)

            # Bin max AFTER -- green or red filled triangle, no text
            if np.any(bin_valid):
                idx_v = np.argmax(lux[bin_valid])
                r_v = range_plot[bin_valid][idx_v]
                v_v = lux[bin_valid][idx_v]
                passed = round(v_v, 1) <= lim
                tc = STYLE["green"] if passed else STYLE["red"]
                ax.plot(r_v, v_v, "^", color=tc, ms=9, zorder=7,
                        markerfacecolor=tc)

        # Legend marker proxies
        ax.plot([], [], "s", color="black", ms=8, markerfacecolor="none",
                markeredgewidth=2.0, label="Bin max before exclusion")
        ax.plot([], [], "^", color=STYLE["green"], ms=9,
                markerfacecolor=STYLE["green"], label="Bin max after exclusion (PASS)")
        ax.plot([], [], "^", color=STYLE["red"], ms=9,
                markerfacecolor=STYLE["red"], label="Bin max after exclusion (FAIL)")

        ax.set_ylim(0, y_max)
        ax.set_xlim(xlim)
        ax.invert_xaxis()
        ax.set_xticks(ticks); ax.set_xticklabels([str(int(t)) for t in ticks], fontsize=8)
        _add_meas_range_markers(ax, sc, xlim)
        ax.legend(fontsize=7, loc="upper left", framealpha=0.85, ncol=2)
        ch_idx += 1

    fig.savefig(outpath, dpi=STYLE["fig_dpi"], bbox_inches="tight",
                facecolor=STYLE["fig_face"])
    plt.close(fig)
    print(f"  Saved: {outpath}")


# ================================================================
# PLOT 3: LUX vs RANGE -- one panel per channel
# ================================================================
def plot_lux(mat_data, channels, signed_range_mat, excl_mask, sc, outpath):
    valid_chs = [ch for ch in channels if f"lux{ch}" in mat_data]
    n_ch = len(valid_chs)
    if n_ch == 0:
        return

    fig, axes = plt.subplots(n_ch, 1, figsize=(11, 3.2*n_ch + 0.5),
                             facecolor=STYLE["fig_face"])
    if n_ch == 1:
        axes = [axes]
    fig.subplots_adjust(hspace=0.50)
    fig.suptitle(f"Illuminance vs Range -- FMVSS 108 Scenario {sc['_num']}  "
                 f"({sc['direction']} Direction)",
                 fontsize=10, fontweight="bold", y=0.99)

    xlim = (0, sc["max_range"])
    ticks = _make_ticks(sc)
    bin_colors = STYLE["bin_colors"]
    y_max = 25 if sc["direction"] == "Same" else 5

    meas_mask = (signed_range_mat >= sc["fm_ranges"][0][0]) & \
                (signed_range_mat <= sc["max_range"])
    range_plot = signed_range_mat[meas_mask]
    excl_plot  = excl_mask[meas_mask]

    for ch_idx, ch in enumerate(valid_chs):
        lux = mat_data[f"lux{ch}"][meas_mask]
        color = STYLE["lux_colors"][ch_idx % len(STYLE["lux_colors"])]
        ax = axes[ch_idx]
        is_last = (ch_idx == n_ch - 1)
        apply_style(ax,
                    xlabel="Range to fixture (m)" if is_last else "",
                    ylabel="Illuminance (lux)",
                    title=f"Lux Ch{ch}")

        ax.plot(range_plot, lux, color=color, lw=1.8, label=f"Lux Ch{ch}", zorder=3)

        if np.any(excl_plot):
            ax.scatter(range_plot[excl_plot], lux[excl_plot],
                       color=STYLE["red"], s=18, zorder=5,
                       label="Pitch-excluded samples")

        for bi, ((d1, d2), lim) in enumerate(zip(sc["fm_ranges"], sc["fm_limits"])):
            c = bin_colors[bi % len(bin_colors)]
            ax.plot([d2, d1], [lim, lim], "--", color=c, lw=2.0,
                    label=f"FMVSS {d1}-{d2}m: {lim} lux")
            ax.axvline(d1, color="#ccc", lw=0.7, ls=":", zorder=0)
            ax.axvline(d2, color="#ccc", lw=0.7, ls=":", zorder=0)

        ax.set_ylim(0, y_max)
        ax.set_xlim(xlim)
        ax.invert_xaxis()
        ax.set_xticks(ticks); ax.set_xticklabels([str(int(t)) for t in ticks], fontsize=8)
        _add_meas_range_markers(ax, sc, xlim)
        ax.legend(fontsize=7.5, loc="upper left", framealpha=0.85, ncol=2)

    fig.savefig(outpath, dpi=STYLE["fig_dpi"], bbox_inches="tight",
                facecolor=STYLE["fig_face"])
    plt.close(fig)
    print(f"  Saved: {outpath}")


# ================================================================
# PLOT 4: SPEED + LUX
# ================================================================
def plot_speed_lux(csv_data, signed_range_csv, mat_data, channels,
                   signed_range_mat, excl_mask, sc, outpath):
    n_lux = sum(1 for ch in channels if f"lux{ch}" in mat_data)
    n_panels = 1 + n_lux
    fig, axes = plt.subplots(n_panels, 1, figsize=(11, 3*n_panels + 0.5),
                             facecolor=STYLE["fig_face"], sharex=False)
    if n_panels == 1:
        axes = [axes]
    fig.subplots_adjust(hspace=0.42)
    fig.suptitle(f"Speed and Illuminance vs Range -- FMVSS 108 Scenario {sc['_num']}  "
                 f"({sc['direction']} Direction)", fontsize=10, fontweight="bold", y=0.99)

    xlim = (0, sc["max_range"])
    ticks = _make_ticks(sc)

    meas_mask_csv = ((signed_range_csv >= sc["fm_ranges"][0][0]) &
                     (signed_range_csv <= sc["max_range"]))
    meas_mask_mat = ((signed_range_mat >= sc["fm_ranges"][0][0]) &
                     (signed_range_mat <= sc["max_range"]))

    # Speed panel
    ax = axes[0]
    apply_style(ax, ylabel="Speed (mph)")
    speed_mph = np.abs(csv_data["speed"][meas_mask_csv]) * 2.23694
    range_csv_plot = signed_range_csv[meas_mask_csv]
    ax.plot(range_csv_plot, speed_mph, color=STYLE["purple"], lw=1.8,
            label="Speed (mph)", zorder=3)
    ax.axhline(sc["speed_min"], color=STYLE["lt_gray"], lw=1.5, ls="--",
               label=f"FMVSS min {sc['speed_min']} mph")
    ax.axhline(sc["speed_max"], color=STYLE["lt_gray"], lw=1.5, ls="--",
               label=f"FMVSS max {sc['speed_max']} mph")
    buf = 10
    ax.set_ylim(sc["speed_min"] - buf, sc["speed_max"] + buf)
    ax.set_xlim(xlim); ax.invert_xaxis()
    ax.set_xticks(ticks); ax.set_xticklabels([str(int(t)) for t in ticks], fontsize=8)
    _add_meas_range_markers(ax, sc, xlim)
    ax.legend(fontsize=8, loc="best", framealpha=0.85)

    # Lux panels
    bin_colors = STYLE["bin_colors"]
    range_mat_plot = signed_range_mat[meas_mask_mat]
    excl_plot      = excl_mask[meas_mask_mat]
    ch_idx = 0
    for ch in channels:
        key = f"lux{ch}"
        if key not in mat_data:
            continue
        lux = mat_data[key][meas_mask_mat]
        color = STYLE["lux_colors"][ch_idx % len(STYLE["lux_colors"])]
        ax = axes[1 + ch_idx]
        apply_style(ax,
                    xlabel="Range to fixture (m)" if ch_idx == n_lux-1 else "",
                    ylabel="Illuminance (lux)")
        ax.plot(range_mat_plot, lux, color=color, lw=1.8, label=f"Lux Ch{ch}", zorder=3)
        if np.any(excl_plot):
            ax.scatter(range_mat_plot[excl_plot], lux[excl_plot],
                       color=STYLE["red"], s=18, zorder=5, label="Pitch-excluded")

        for bi, ((d1, d2), lim) in enumerate(zip(sc["fm_ranges"], sc["fm_limits"])):
            c = bin_colors[bi % len(bin_colors)]
            ax.plot([d2, d1], [lim, lim], "--", color=c, lw=2.0,
                    label=f"FMVSS {d1}-{d2}m: {lim} lux")
            ax.axvline(d1, color="#ccc", lw=0.7, ls=":", zorder=0)
            ax.axvline(d2, color="#ccc", lw=0.7, ls=":", zorder=0)

        y_max = 25 if sc["direction"] == "Same" else 5
        ax.set_ylim(0, y_max); ax.set_xlim(xlim); ax.invert_xaxis()
        ax.set_xticks(ticks); ax.set_xticklabels([str(int(t)) for t in ticks], fontsize=8)
        _add_meas_range_markers(ax, sc, xlim)
        ax.legend(fontsize=7.5, loc="upper left", framealpha=0.85, ncol=2)
        ch_idx += 1

    fig.savefig(outpath, dpi=STYLE["fig_dpi"], bbox_inches="tight",
                facecolor=STYLE["fig_face"])
    plt.close(fig)
    print(f"  Saved: {outpath}")


# ================================================================
# PLOT 5: ELEVATION PROFILE
# ================================================================
def plot_elevation(dist_sample, alt_sample, sc_num, outpath):
    fig, ax = plt.subplots(figsize=(10, 4), facecolor=STYLE["fig_face"])
    apply_style(ax, xlabel="Distance (m)", ylabel="Elevation (m)",
                title=f"Elevation Profile -- FMVSS 108 Scenario {sc_num}")
    
    valid = np.isfinite(alt_sample)
    if not np.any(valid) or len(alt_sample[valid]) < 2:
        ax.text(0.5, 0.5, "Elevation data not available",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=12, color="#aaa")
        fig.savefig(outpath, dpi=STYLE["fig_dpi"], bbox_inches="tight",
                    facecolor=STYLE["fig_face"])
        plt.close(fig)
        print(f"  Saved: {outpath}")
        return
    ax.plot(dist_sample, alt_sample, color=STYLE["data_blue"], lw=1.5)
    alt_max = float(np.max(alt_sample[valid]))
    alt_min = float(np.min(alt_sample[valid]))
    ax.axhline(alt_max, color=STYLE["lt_gray"], lw=1.2, ls="--",
               label=f"Max {alt_max:.2f} m")
    ax.axhline(alt_min, color=STYLE["lt_gray"], lw=1.2, ls="--",
               label=f"Min {alt_min:.2f} m")
    y_lo = np.floor(alt_min) - 2
    y_hi = np.ceil(alt_max)  + 2
    ax.set_ylim(y_lo, y_hi)
    ax.legend(fontsize=8, loc="best", framealpha=0.85)
    fig.savefig(outpath, dpi=STYLE["fig_dpi"], bbox_inches="tight",
                facecolor=STYLE["fig_face"])
    plt.close(fig)
    print(f"  Saved: {outpath}")


# ================================================================
# PLOT 6: ROC GEOMETRY + ROC vs DISTANCE
# Changes: green meas start, red meas end, no-vehicle-entry zone,
#          fit type removed from title/annotation,
#          second plot: instantaneous ROC vs signed range
# ================================================================
def plot_roc(roc, csv_data, sc, outpath):
    if roc is None or roc.get("failed"):
        fig, ax = plt.subplots(figsize=(8, 8), facecolor=STYLE["fig_face"])
        apply_style(ax, title=f"ROC -- Scenario {sc['_num']} (Straight, no ROC requirement)")
        ax.text(0.5, 0.5, "Straight scenario\nNo radius of curvature requirement",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=14, color="#aaa")
        fig.savefig(outpath, dpi=STYLE["fig_dpi"], bbox_inches="tight",
                    facecolor=STYLE["fig_face"])
        plt.close(fig)
        print(f"  Saved: {outpath}")
        return

    x_seg = np.array(roc["x_seg"]); y_seg = np.array(roc["y_seg"])
    x_acc = np.array(roc["x_acc"]); y_acc = np.array(roc["y_acc"])
    xc = roc["xc"]; yc = roc["yc"]; R = roc["R"]
    is_ccw = roc["is_ccw"]

    # Square axis bounds
    all_x = np.concatenate([x_seg, x_acc]) if len(x_acc) else x_seg
    all_y = np.concatenate([y_seg, y_acc]) if len(y_acc) else y_seg
    cx = (np.max(all_x) + np.min(all_x)) / 2
    cy = (np.max(all_y) + np.min(all_y)) / 2
    half = max(np.max(all_x)-np.min(all_x), np.max(all_y)-np.min(all_y)) / 2 * 1.55
    half = max(half, 40)
    ax1 = cx - half; ax2 = cx + half
    ay1 = cy - half; ay2 = cy + half

    # Arc angles for measurement segment
    ang_entry = np.arctan2(y_seg[0]  - yc, x_seg[0]  - xc)
    ang_end   = np.arctan2(y_seg[-1] - yc, x_seg[-1] - xc)
    if is_ccw:
        while ang_end <= ang_entry: ang_end += 2*np.pi
    else:
        while ang_end >= ang_entry: ang_end -= 2*np.pi
    ang_meas = np.linspace(ang_entry, ang_end, 400)

    # Extended arc for no-entry zone (85 deg before measurement start)
    dna_angle = np.radians(85)
    if is_ccw:
        ang_dna = np.linspace(ang_entry, ang_entry - dna_angle, 200)
    else:
        ang_dna = np.linspace(ang_entry, ang_entry + dna_angle, 200)

    road_hw = 3.5

    # Road edges for measurement segment
    d_seg = np.maximum(np.sqrt((x_seg-xc)**2 + (y_seg-yc)**2), 0.001)
    ux = (xc - x_seg) / d_seg; uy = (yc - y_seg) / d_seg
    x_in  = x_seg + road_hw*ux; y_in  = y_seg + road_hw*uy
    x_out = x_seg - road_hw*ux; y_out = y_seg - road_hw*uy
    x_in_full  = np.concatenate([x_in,  [roc["x_fix"]]])
    y_in_full  = np.concatenate([y_in,  [roc["y_fix"]]])
    x_out_full = np.concatenate([x_out, [roc["x_fix"]]])
    y_out_full = np.concatenate([y_out, [roc["y_fix"]]])

    # Tolerance band
    x_tol_in  = xc + (R - 0.5)*np.cos(ang_meas)
    y_tol_in  = yc + (R - 0.5)*np.sin(ang_meas)
    x_tol_out = xc + (R + 0.5)*np.cos(ang_meas)
    y_tol_out = yc + (R + 0.5)*np.sin(ang_meas)

    # No-vehicle-entry zone (road-width band along DNA arc)
    x_dna_in  = xc + (R - road_hw)*np.cos(ang_dna)
    y_dna_in  = yc + (R - road_hw)*np.sin(ang_dna)
    x_dna_out = xc + (R + road_hw)*np.cos(ang_dna)
    y_dna_out = yc + (R + road_hw)*np.sin(ang_dna)
    x_dna_cl  = xc + R*np.cos(ang_dna)
    y_dna_cl  = yc + R*np.sin(ang_dna)

    # ---- Figure 1: GEOMETRY MAP ----
    fig, ax = plt.subplots(figsize=(9, 10), facecolor=STYLE["fig_face"])
    ax.set_facecolor(STYLE["ax_face"])
    ax.grid(True, color=STYLE["grid_color"], linewidth=0.7, zorder=0)

    # Road fill + edges
    fill_x = np.concatenate([x_out_full, x_in_full[::-1]])
    fill_y = np.concatenate([y_out_full, y_in_full[::-1]])
    ax.fill(fill_x, fill_y, color="#e8e8e8", zorder=1)
    ax.plot(x_in_full,  y_in_full,  "k-", lw=2.0, zorder=2)
    ax.plot(x_out_full, y_out_full, "k-", lw=2.0, zorder=2)

    # No-vehicle-entry zone (red hatched fill + dashed centerline)
    dna_fill_x = np.concatenate([x_dna_out, x_dna_in[::-1]])
    dna_fill_y = np.concatenate([y_dna_out, y_dna_in[::-1]])
    ax.fill(dna_fill_x, dna_fill_y, color="#ffcccc", alpha=0.65,
            hatch="///", edgecolor="#d44", linewidth=0.5, zorder=3,
            label="No vehicle entry zone")
    ax.plot(x_dna_cl, y_dna_cl, "r--", lw=1.8, zorder=4)

    # Tolerance band (green)
    tb_x = np.concatenate([x_tol_out, x_tol_in[::-1]])
    tb_y = np.concatenate([y_tol_out, y_tol_in[::-1]])
    ax.fill(tb_x, tb_y, color="#78c878", alpha=0.55, zorder=3,
            label="+/-0.5 m path tolerance (FMVSS)")
    ax.plot(x_tol_in,  y_tol_in,  ":", color="#2a7a2a", lw=0.9, zorder=4)
    ax.plot(x_tol_out, y_tol_out, ":", color="#2a7a2a", lw=0.9, zorder=4)

    # FMVSS ROC limit arcs (orange dashed)
    ax.plot(xc + sc["roc_min"]*np.cos(ang_meas),
            yc + sc["roc_min"]*np.sin(ang_meas),
            "--", color="#c8820a", lw=1.8, zorder=5,
            label=f"FMVSS min R = {sc['roc_min']} m")
    ax.plot(xc + sc["roc_max"]*np.cos(ang_meas),
            yc + sc["roc_max"]*np.sin(ang_meas),
            "--", color="#c8820a", lw=1.8, zorder=5,
            label=f"FMVSS max R = {sc['roc_max']} m")

    # Acceleration area (gray)
    if len(x_acc) > 1:
        ax.plot(x_acc, y_acc, "-", color="#888", lw=1.6, zorder=4,
                label="Test vehicle acceleration area")

    # Nominal path (measurement segment, blue dashed)
    ax.plot(x_seg, y_seg, "b--", lw=2.5, zorder=6,
            label=f"Nominal path  {sc['fm_ranges'][0][0]}-{sc['max_range']} m")

    # Outward label helper
    def outward_label(px, py, text, color, offset=14):
        d = max(np.sqrt((px-xc)**2 + (py-yc)**2), 0.001)
        ox = (px-xc)/d; oy = (py-yc)/d
        lx = np.clip(px + ox*offset, ax1+6, ax2-6)
        ly = np.clip(py + oy*offset, ay1+6, ay2-6)
        ax.text(lx, ly, text, fontsize=9, fontweight="bold",
                color=color, ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.2", facecolor="#fff",
                          edgecolor="none", alpha=0.85))

    # Measurement START -- green square
    ax.plot(x_seg[0], y_seg[0], "s", ms=12, color="#185518",
            markerfacecolor="#2a9a2a", markeredgewidth=1.5,
            zorder=8, label=f"Meas. start ({sc['max_range']} m)")
    outward_label(x_seg[0], y_seg[0], f"{sc['max_range']} m", "#185518", offset=16)

    # Measurement END -- red square
    ax.plot(x_seg[-1], y_seg[-1], "s", ms=12, color="#7a1818",
            markerfacecolor="#c0392b", markeredgewidth=1.5,
            zorder=8, label=f"Meas. end ({sc['fm_ranges'][0][0]} m from fixture)")
    outward_label(x_seg[-1], y_seg[-1],
                  f"{sc['fm_ranges'][0][0]} m", "#7a1818", offset=16)

    # Test fixture box
    bh = max(road_hw * 0.65, 2.5)
    fx, fy = roc["x_fix"], roc["y_fix"]
    fix_rect = plt.Polygon(
        [[fx-bh, fy-bh],[fx+bh, fy-bh],[fx+bh, fy+bh],[fx-bh, fy+bh]],
        closed=True, facecolor="#f8f8f8", edgecolor="black", lw=2.0, zorder=9)
    ax.add_patch(fix_rect)
    ax.plot(fx, fy, "k.", ms=8, zorder=10)
    d_fix = max(np.sqrt((fx-xc)**2+(fy-yc)**2), 0.001)
    ox_f = (fx-xc)/d_fix; oy_f = (fy-yc)/d_fix
    ax.text(fx+ox_f*20, fy+oy_f*20, "Test Fixture", fontsize=9, fontweight="bold",
            ha="center", va="center",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#fff",
                      edgecolor="none", alpha=0.85))
    ax.plot([], [], "s", color="black", markerfacecolor="#f8f8f8",
            ms=9, lw=1.5, label="Test Fixture")

    # Compliance annotation (no fit type)
    geo_pass = roc["geo_pass"]; dev_pass = roc["dev_pass"]
    ann_color = STYLE["green"] if (geo_pass and dev_pass) else STYLE["red"]
    ann_txt = (f"Sc{sc['_num']}  {sc['curve_dir']} Curve\n"
               f"ROC: {sc['roc_min']}-{sc['roc_max']} m\n"
               f"R:   {R:.1f} m  [{'PASS' if geo_pass else 'FAIL'}]\n"
               f"RMS: {roc['rms_resid']:.4f} m\n"
               f"Arc: {roc['arc_angle']:.1f} deg\n"
               f"Dev: {roc['max_resid_r']:.1f} m  [{'PASS' if dev_pass else 'FAIL'}]")
    ax.text(ax1 + (ax2-ax1)*0.02, ay2 - (ay2-ay1)*0.02, ann_txt,
            fontsize=8, va="top", ha="left", family="monospace",
            color=ann_color, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#fff",
                      edgecolor=ann_color, linewidth=1.5, alpha=0.92))

    # Scale bar
    sb = max(10, round(half * 0.22 / 10) * 10)
    sb_x0 = ax1 + (ax2-ax1)*0.05; sb_y0 = ay1 + (ay2-ay1)*0.04
    tick_h = (ay2-ay1)*0.012
    ax.plot([sb_x0, sb_x0+sb], [sb_y0, sb_y0], "k-", lw=2.8, zorder=11)
    ax.plot([sb_x0, sb_x0],    [sb_y0-tick_h, sb_y0+tick_h], "k-", lw=1.8, zorder=11)
    ax.plot([sb_x0+sb, sb_x0+sb],[sb_y0-tick_h, sb_y0+tick_h], "k-", lw=1.8, zorder=11)
    ax.text((sb_x0+sb_x0+sb)/2, sb_y0+(ay2-ay1)*0.025, f"{sb} m",
            fontsize=9, fontweight="bold", ha="center",
            bbox=dict(boxstyle="round,pad=0.1", facecolor="#fff", edgecolor="none"))

    # North arrow
    theta_rot = roc["theta_rot"]
    na_cx = ax2 - (ax2-ax1)*0.08; na_cy = ay2 - (ay2-ay1)*0.08
    na_len = (ay2-ay1)*0.06
    north_ang = np.pi/2 - theta_rot
    ax.annotate("", xy=(na_cx + na_len*np.cos(north_ang),
                        na_cy + na_len*np.sin(north_ang)),
                xytext=(na_cx, na_cy),
                arrowprops=dict(arrowstyle="-|>", color="black", lw=1.8))
    ax.text(na_cx + na_len*np.cos(north_ang)*1.5,
            na_cy + na_len*np.sin(north_ang)*1.5,
            "N", fontsize=10, fontweight="bold", ha="center",
            bbox=dict(boxstyle="round,pad=0.1", facecolor="#fff", edgecolor="none"))

    ax.set_xlim(ax1, ax2); ax.set_ylim(ay1, ay2)
    ax.set_aspect("equal")
    ax.set_xlabel("East  (m, rotated frame)", fontsize=9, color="#444")
    ax.set_ylabel("North  (m, rotated frame)", fontsize=9, color="#444")
    # Title: no fit type
    ax.set_title(f"Radius of Curvature -- FMVSS 108 Scenario {sc['_num']}\n"
                 f"{sc['direction']} Direction  |  {sc['curve_dir']} Curve  |  "
                 f"ROC {sc['roc']} m",
                 fontsize=10, fontweight="bold")
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.legend(loc="lower right", fontsize=7.5, framealpha=0.9, ncol=1)

    fig.savefig(outpath, dpi=STYLE["fig_dpi"], bbox_inches="tight",
                facecolor=STYLE["fig_face"])
    plt.close(fig)
    print(f"  Saved: {outpath}")

    # ---- Figure 2: ROC VALUE vs MEASUREMENT DISTANCE ----
    # Compute instantaneous distance from each GPS point to fitted circle center
    # within the measurement segment, map to signed range
    roc_outpath = outpath.replace(".png", "_vs_distance.png")

    x_seg_arr = np.array(roc["x_seg"])
    y_seg_arr = np.array(roc["y_seg"])
    dist_to_center = np.sqrt((x_seg_arr - xc)**2 + (y_seg_arr - yc)**2)

    # Build a signed range axis for the segment
    # Segment runs from rangeMax down to rangeMin on approach
    n_pts = len(x_seg_arr)
    range_seg = np.linspace(sc["max_range"], sc["fm_ranges"][0][0], n_pts)

    ticks = _make_ticks(sc)
    fig2, ax2p = plt.subplots(figsize=(11, 4), facecolor=STYLE["fig_face"])
    apply_style(ax2p,
                xlabel="Range to fixture (m)",
                ylabel="Radius of curvature (m)",
                title=f"Instantaneous ROC vs Range -- FMVSS 108 Scenario {sc['_num']}  "
                      f"({sc['direction']} Direction  |  {sc['curve_dir']} Curve)")

    ax2p.plot(range_seg, dist_to_center, color=STYLE["data_blue"], lw=1.8,
              label="Path radius (m)", zorder=3)

    # Fitted R horizontal line
    ax2p.axhline(R, color=STYLE["orange"], lw=1.8, ls="-",
                 label=f"Fitted R = {R:.1f} m")

    # FMVSS ROC limits
    ax2p.axhline(sc["roc_min"], color="#c8820a", lw=1.5, ls="--",
                 label=f"FMVSS min R = {sc['roc_min']} m")
    ax2p.axhline(sc["roc_max"], color="#c8820a", lw=1.5, ls="--",
                 label=f"FMVSS max R = {sc['roc_max']} m")

    # Path tolerance band (fitted R +/- 0.5 m) -- very tight, shown as shaded band
    ax2p.fill_between(range_seg, R - 0.5, R + 0.5,
                      color="#78c878", alpha=0.40, label="+/-0.5 m path tolerance")

    # FMVSS ROC band fill
    ax2p.fill_between(range_seg, sc["roc_min"], sc["roc_max"],
                      color="#fde8b0", alpha=0.30, label="FMVSS ROC band")

    y_vals = dist_to_center
    y_lo = min(np.min(y_vals), sc["roc_min"]) - 10
    y_hi = max(np.max(y_vals), sc["roc_max"]) + 10
    ax2p.set_ylim(y_lo, y_hi)
    ax2p.set_xlim(0, sc["max_range"])
    ax2p.invert_xaxis()
    ax2p.set_xticks(ticks); ax2p.set_xticklabels([str(int(t)) for t in ticks], fontsize=8)
    _add_meas_range_markers(ax2p, sc, (0, sc["max_range"]))
    ax2p.legend(fontsize=8, loc="best", framealpha=0.85, ncol=2)

    fig2.savefig(roc_outpath, dpi=STYLE["fig_dpi"], bbox_inches="tight",
                 facecolor=STYLE["fig_face"])
    plt.close(fig2)
    print(f"  Saved: {roc_outpath}")


# ================================================================
# MAIN PROCESSOR
# ================================================================
def process(csv_path, mat_path, scenario_num, vehicle, channels, outdir):
    sc = SCENARIOS[scenario_num].copy()
    sc["_num"] = scenario_num

    os.makedirs(outdir, exist_ok=True)
    print(f"\n{'='*60}")
    print(f"ADB Processor -- Scenario {scenario_num}  |  {vehicle}")
    print(f"CSV: {csv_path}")
    print(f"MAT: {mat_path}")
    print(f"Out: {outdir}")
    print(f"{'='*60}")

    # ---- Read data ----
    csv_data = read_csv(csv_path)
    mat_data = read_mat(mat_path, channels)

    # ---- Distance + grade (from CSV) ----
    dist_sample, alt_sample, grade_pct = compute_grade(
        csv_data["time"], csv_data["speed"], csv_data["alt"], csv_data["range"])

    # Signed range for CSV (approach-side grade analysis)
    signed_range_csv, _ = make_signed_range(csv_data["range"])

    # Map grade_pct (uniform 2m grid) back to signed_range_csv for plotting
    dist_csv = cumtrapz(csv_data["speed"], csv_data["time"], initial=0)
    _, ia = np.unique(dist_csv, return_index=True)
    dist_csv_u = dist_csv[ia]
    sr_csv_u   = signed_range_csv[ia]

    if len(dist_sample) > 1 and len(grade_pct) > 1:
        grade_on_sr = np.interp(dist_csv_u, dist_sample, grade_pct)
    else:
        # Fallback: flat grade if dist_sample is too short
        grade_on_sr = np.zeros(len(dist_csv_u))

    # ---- Signed range for MAT ----
    signed_range_mat, _ = make_signed_range(mat_data["range"])

    # ---- Pitch exclusion ----
    avg_pitch, pitch_upper, pitch_lower, excl_mask = compute_pitch_exclusion(
        mat_data["pitch"], signed_range_mat, sc)

    # ---- Lux compliance ----
    lux_compliance = compute_lux_compliance(
        mat_data, channels, signed_range_mat, excl_mask, sc)

    # ---- ROC ----
    roc = compute_roc(csv_data["lat"], csv_data["lon"], csv_data["range"], sc)

    # ---- Speed stats ----
    # Use absolute speed (always positive) and only approach phase
    # (signed_range positive = approach, negative = departure)
    # Also require speed > 0 to exclude stationary samples
    speed_ms  = np.abs(csv_data["speed"])   # absolute value handles any sign issues
    speed_mph = speed_ms * 2.23694

    # Approach phase within measurement window only
    meas_mask_csv = ((signed_range_csv >= sc["fm_ranges"][0][0]) &
                     (signed_range_csv <= sc["max_range"]) &
                     (speed_ms > 0.5))   # exclude near-stationary samples

    speed_in_range = speed_mph[meas_mask_csv]
    if len(speed_in_range) == 0:
        # Fallback: use all approach samples with speed > threshold
        fallback_mask = (signed_range_csv > 0) & (speed_ms > 0.5)
        speed_in_range = speed_mph[fallback_mask]

    speed_mean    = float(np.mean(speed_in_range))    if len(speed_in_range) else np.nan
    speed_min_meas= float(np.min(speed_in_range))     if len(speed_in_range) else np.nan
    speed_max_meas= float(np.max(speed_in_range))     if len(speed_in_range) else np.nan
    speed_pass    = (not np.isnan(speed_mean) and
                     speed_mean >= sc["speed_min"] - 1 and
                     speed_mean <= sc["speed_max"] + 1)

    # ---- Grade stats ----
    grade_in_meas = grade_on_sr[
        (dist_csv_u >= 0) & (dist_csv_u <= dist_csv_u[-1])]
    grade_approach_mask = ((signed_range_csv[ia] >= sc["fm_ranges"][0][0]) &
                           (signed_range_csv[ia] <= sc["max_range"]))
    grade_in_range = grade_on_sr[grade_approach_mask]
    grade_mean  = float(np.mean(grade_in_range)) if len(grade_in_range) else np.nan
    grade_max   = float(np.max(np.abs(grade_in_range))) if len(grade_in_range) else np.nan
    grade_pass  = grade_max <= 2.0 if not np.isnan(grade_max) else False

    # ---- Overall pass/fail ----
    all_lux_pass = all(
        b["passed"] for ch_res in lux_compliance.values() for b in ch_res)
    roc_pass = (roc is None or roc.get("failed") or
                (roc["geo_pass"] and roc["dev_pass"]))
    overall_pass = speed_pass and grade_pass and all_lux_pass and roc_pass

    # ---- Generate plots ----
    print("\n--- Generating plots ---")

    # Infer test number and date from MAT filename
    mat_name = Path(mat_path).stem
    import re
    m_test = re.search(r"Test(\d+)", mat_name, re.I)
    m_date = re.search(r"(\d{2})(\d{2})(\d{4})", mat_name)
    test_num  = m_test.group(1) if m_test else "?"
    date_str  = f"{m_date.group(2)}/{m_date.group(1)}/{m_date.group(3)}" if m_date else "?"

    plot_grade_pitch(
        signed_range_csv[ia], grade_on_sr,
        mat_data["pitch"], signed_range_mat, sc,
        os.path.join(outdir, "plot_grade_pitch.png"))

    plot_pitch_diagnostic(
        mat_data, channels, signed_range_mat,
        avg_pitch, excl_mask, sc,
        os.path.join(outdir, "plot_pitch_diagnostic.png"))

    plot_lux(
        mat_data, channels, signed_range_mat, excl_mask, sc,
        os.path.join(outdir, "plot_lux.png"))

    plot_speed_lux(
        csv_data, signed_range_csv[ia],
        mat_data, channels, signed_range_mat, excl_mask, sc,
        os.path.join(outdir, "plot_speed_lux.png"))

    plot_elevation(
        dist_sample, alt_sample, scenario_num,
        os.path.join(outdir, "plot_elevation.png"))

    plot_roc(roc, csv_data, sc,
             os.path.join(outdir, "plot_roc.png"))

    # ---- GPS for satellite map ----
    # Store lat/lon subsampled, plus the corresponding signed range value
    # so the HTML can correctly colour measurement vs non-measurement segments
    gps_points = []
    gps_ranges = []
    for la, lo, sr in zip(csv_data["lat"], csv_data["lon"], signed_range_csv):
        if np.isfinite(la) and np.isfinite(lo):
            gps_points.append([float(la), float(lo)])
            gps_ranges.append(float(sr))
    # Subsample to keep JSON small
    gps_points = gps_points[::5]
    gps_ranges = gps_ranges[::5]

    # ---- Build JSON ----
    report = dict(
        meta=dict(
            test_num   = test_num,
            vehicle    = vehicle,
            scenario   = scenario_num,
            date       = date_str,
            csv_file   = Path(csv_path).name,
            mat_file   = Path(mat_path).name,
            channels   = channels,
            generated  = datetime.now().isoformat(timespec="seconds"),
        ),
        scenario_params=dict(
            direction    = sc["direction"],
            curve_dir    = sc["curve_dir"],
            roc          = sc["roc"],
            roc_min      = sc["roc_min"],
            roc_max      = sc["roc_max"],
            speed_min    = sc["speed_min"],
            speed_max    = sc["speed_max"],
            speed_min_kph= sc["speed_min_kph"],
            speed_max_kph= sc["speed_max_kph"],
            fm_ranges    = sc["fm_ranges"],
            fm_limits    = sc["fm_limits"],
            max_range    = sc["max_range"],
            superelev    = sc["superelev"],
        ),
        compliance=dict(
            overall_pass = overall_pass,
            speed=dict(
                mean_mph = round(speed_mean, 2),
                min_mph  = round(speed_min_meas, 2),
                max_mph  = round(speed_max_meas, 2),
                limit_min= sc["speed_min"],
                limit_max= sc["speed_max"],
                passed   = speed_pass,
            ),
            grade=dict(
                mean_pct = round(grade_mean, 3),
                max_abs_pct = round(grade_max, 3),
                passed   = grade_pass,
            ),
            pitch=dict(
                avg_pitch    = round(avg_pitch, 4),
                upper_thresh = round(pitch_upper, 4),
                lower_thresh = round(pitch_lower, 4),
                n_excluded   = int(np.sum(excl_mask)),
                n_total      = int(np.sum(
                    (signed_range_mat >= sc["fm_ranges"][0][0]) &
                    (signed_range_mat <= sc["max_range"]))),
            ),
            roc=roc if roc is not None else dict(failed=True, method="N/A (Straight)"),
            lux=lux_compliance,
            lux_overall_pass=all_lux_pass,
        ),
        gps_points=gps_points,
        gps_ranges=gps_ranges,
        plots=dict(
            grade_pitch      = "plot_grade_pitch.png",
            pitch_diagnostic = "plot_pitch_diagnostic.png",
            lux              = "plot_lux.png",
            speed_lux        = "plot_speed_lux.png",
            elevation        = "plot_elevation.png",
            roc              = "plot_roc.png",
            roc_vs_distance  = "plot_roc_vs_distance.png",
        ),
    )

    json_path = os.path.join(outdir, "report_data.json")
    with open(json_path, "w") as f:
        json.dump(_nan_to_null(report), f, indent=2)
    print(f"\n  Saved: {json_path}")

    # ---- Console summary ----
    print(f"\n{'='*60}")
    print(f"  SUMMARY -- Scenario {scenario_num}  |  {vehicle}")
    print(f"{'='*60}")
    print(f"  Speed:   {speed_mean:.1f} mph avg  (limit {sc['speed_min']}-{sc['speed_max']})  "
          f"[{'PASS' if speed_pass else 'FAIL'}]")
    print(f"  Grade:   {grade_mean:.2f}% mean, max {grade_max:.2f}%  (limit +/-2%)  "
          f"[{'PASS' if grade_pass else 'FAIL'}]")
    print(f"  Pitch:   avg {avg_pitch:.3f} deg,  {np.sum(excl_mask)} samples excluded")
    if roc and not roc.get("failed"):
        print(f"  ROC:     R={roc['R']:.1f} m  ({sc['roc_min']}-{sc['roc_max']} m)  "
              f"[{'PASS' if roc['geo_pass'] else 'FAIL'}]")
        print(f"           Max dev {roc['max_resid_r']:.1f} m  "
              f"[{'PASS' if roc['dev_pass'] else 'FAIL'}]")
    for ch, bins in lux_compliance.items():
        for b in bins:
            print(f"  Lux Ch{ch} {b['d1']}-{b['d2']}m:  "
                  f"after={b['max_after_rounded']:.1f} lux  "
                  f"(limit {b['limit']})  [{'PASS' if b['passed'] else 'FAIL'}]")
    print(f"\n  OVERALL: {'PASS' if overall_pass else 'FAIL'}")
    print(f"{'='*60}\n")

    return report



# ================================================================
# process_from_data
# Accepts pre-read data dicts instead of file paths.
# Called by adb_postprocess_.py so no bridge files are needed.
# Identical logic to process() -- just skips read_csv / read_mat.
# ================================================================
def process_from_data(csv_data, mat_data, scenario_num, vehicle,
                      channels, outdir, csv_path="", mat_path=""):
    """
    Run full ADB compliance analysis on pre-read data.

    Parameters
    ----------
    csv_data  : dict  time(s), lat(deg), lon(deg), alt(m),
                      pitch(deg), roll(deg), speed(m/s), range(m)
    mat_data  : dict  time(s), range(m), pitch(deg), lux0..luxN
    scenario_num : int 1-8
    vehicle   : str
    channels  : list[int]  e.g. [0, 1]
    outdir    : str  output directory
    csv_path  : str  used only for report metadata display
    mat_path  : str  used only for report metadata display
    """
    import re
    from scipy.integrate import cumulative_trapezoid as cumtrapz

    sc = SCENARIOS[scenario_num].copy()
    sc["_num"] = scenario_num
    os.makedirs(outdir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"ADB Processor (from data) -- Scenario {scenario_num}  |  {vehicle}")
    print(f"Out: {outdir}")
    print(f"{'='*60}")

    # ---- Grade (from csv_data, time in seconds) ----
    dist_sample, alt_sample, grade_pct = compute_grade(
        csv_data["time"], csv_data["speed"], csv_data["alt"], csv_data["range"])

    signed_range_csv, _ = make_signed_range(csv_data["range"])

    dist_csv = cumtrapz(csv_data["speed"], csv_data["time"], initial=0)
    _, ia = np.unique(dist_csv, return_index=True)
    dist_csv_u = dist_csv[ia]
    sr_csv_u   = signed_range_csv[ia]

    if len(dist_sample) > 1 and len(grade_pct) > 1:
        grade_on_sr = np.interp(dist_csv_u, dist_sample, grade_pct)
    else:
        grade_on_sr = np.zeros(len(dist_csv_u))

    # ---- Signed range for MAT ----
    signed_range_mat, _ = make_signed_range(mat_data["range"])

    # ---- Pitch exclusion ----
    avg_pitch, pitch_upper, pitch_lower, excl_mask = compute_pitch_exclusion(
        mat_data["pitch"], signed_range_mat, sc)

    # ---- Lux compliance ----
    lux_compliance = compute_lux_compliance(
        mat_data, channels, signed_range_mat, excl_mask, sc)

    # ---- ROC ----
    roc = compute_roc(csv_data["lat"], csv_data["lon"], csv_data["range"], sc)

    # ---- Speed stats ----
    speed_ms  = np.abs(csv_data["speed"])
    speed_mph = speed_ms * 2.23694
    meas_mask_csv = ((signed_range_csv >= sc["fm_ranges"][0][0]) &
                     (signed_range_csv <= sc["max_range"]) &
                     (speed_ms > 0.5))
    speed_in_range = speed_mph[meas_mask_csv]
    if len(speed_in_range) == 0:
        fallback_mask = (signed_range_csv > 0) & (speed_ms > 0.5)
        speed_in_range = speed_mph[fallback_mask]

    speed_mean     = float(np.mean(speed_in_range))     if len(speed_in_range) else np.nan
    speed_min_meas = float(np.min(speed_in_range))      if len(speed_in_range) else np.nan
    speed_max_meas = float(np.max(speed_in_range))      if len(speed_in_range) else np.nan
    speed_pass     = (not np.isnan(speed_mean) and
                      speed_mean >= sc["speed_min"] - 1 and
                      speed_mean <= sc["speed_max"] + 1)

    # ---- Grade stats ----
    grade_in_range  = grade_on_sr[(dist_csv_u >= 0) & (dist_csv_u <= dist_csv_u[-1])]
    grade_mean = float(np.mean(grade_in_range))        if len(grade_in_range) else np.nan
    grade_max  = float(np.max(np.abs(grade_in_range))) if len(grade_in_range) else np.nan
    grade_pass = grade_max <= 2.0                       if not np.isnan(grade_max) else False

    # ---- Overall pass/fail ----
    all_lux_pass = all(b["passed"] for ch_res in lux_compliance.values() for b in ch_res)
    roc_pass = (roc is None or roc.get("failed") or (roc["geo_pass"] and roc["dev_pass"]))
    overall_pass = speed_pass and grade_pass and all_lux_pass and roc_pass

    # ---- Plots ----
    print("\n--- Generating plots ---")
    mat_stem = Path(mat_path).stem if mat_path else "Unknown"
    m_test = re.search(r"Test(\d+)", mat_stem, re.I)
    m_date = re.search(r"(\d{2})(\d{2})(\d{4})", mat_stem)
    test_num = m_test.group(1) if m_test else "?"
    date_str = (f"{m_date.group(2)}/{m_date.group(1)}/{m_date.group(3)}"
                if m_date else "?")

    plot_grade_pitch(
        signed_range_csv[ia], grade_on_sr,
        mat_data["pitch"], signed_range_mat, sc,
        os.path.join(outdir, "plot_grade_pitch.png"))

    plot_pitch_diagnostic(
        mat_data, channels, signed_range_mat,
        avg_pitch, excl_mask, sc,
        os.path.join(outdir, "plot_pitch_diagnostic.png"))

    plot_lux(
        mat_data, channels, signed_range_mat, excl_mask, sc,
        os.path.join(outdir, "plot_lux.png"))

    plot_speed_lux(
        csv_data, signed_range_csv[ia],
        mat_data, channels, signed_range_mat, excl_mask, sc,
        os.path.join(outdir, "plot_speed_lux.png"))

    plot_elevation(
        dist_sample, alt_sample, scenario_num,
        os.path.join(outdir, "plot_elevation.png"))

    plot_roc(roc, csv_data, sc,
             os.path.join(outdir, "plot_roc.png"))

    # ---- GPS for satellite map ----
    gps_points, gps_ranges = [], []
    for la, lo, sr in zip(csv_data["lat"], csv_data["lon"], signed_range_csv):
        if np.isfinite(la) and np.isfinite(lo):
            gps_points.append([float(la), float(lo)])
            gps_ranges.append(float(sr))
    gps_points = gps_points[::5]
    gps_ranges = gps_ranges[::5]

    # ---- Report JSON ----
    report = dict(
        meta=dict(
            test_num  = test_num,
            vehicle   = vehicle,
            scenario  = scenario_num,
            date      = date_str,
            csv_file  = Path(csv_path).name if csv_path else "",
            mat_file  = Path(mat_path).name if mat_path else "",
            channels  = channels,
            generated = datetime.now().isoformat(timespec="seconds"),
        ),
        scenario_params=dict(
            direction    = sc["direction"],
            curve_dir    = sc["curve_dir"],
            roc          = sc["roc"],
            roc_min      = sc["roc_min"],
            roc_max      = sc["roc_max"],
            speed_min    = sc["speed_min"],
            speed_max    = sc["speed_max"],
            speed_min_kph= sc["speed_min_kph"],
            speed_max_kph= sc["speed_max_kph"],
            fm_ranges    = sc["fm_ranges"],
            fm_limits    = sc["fm_limits"],
            max_range    = sc["max_range"],
            superelev    = sc["superelev"],
        ),
        compliance=dict(
            overall_pass = overall_pass,
            speed=dict(
                mean_mph = round(speed_mean, 2),
                min_mph  = round(speed_min_meas, 2),
                max_mph  = round(speed_max_meas, 2),
                limit_min= sc["speed_min"],
                limit_max= sc["speed_max"],
                passed   = speed_pass,
            ),
            grade=dict(
                mean_pct    = round(grade_mean, 3),
                max_abs_pct = round(grade_max, 3),
                passed      = grade_pass,
            ),
            pitch=dict(
                avg_pitch    = round(avg_pitch, 4),
                upper_thresh = round(pitch_upper, 4),
                lower_thresh = round(pitch_lower, 4),
                n_excluded   = int(np.sum(excl_mask)),
                n_total      = int(np.sum(
                    (signed_range_mat >= sc["fm_ranges"][0][0]) &
                    (signed_range_mat <= sc["max_range"]))),
            ),
            roc          = roc if roc is not None else dict(failed=True, method="N/A (Straight)"),
            lux          = lux_compliance,
            lux_overall_pass = all_lux_pass,
        ),
        gps_points = gps_points,
        gps_ranges = gps_ranges,
        plots=dict(
            grade_pitch      = "plot_grade_pitch.png",
            pitch_diagnostic = "plot_pitch_diagnostic.png",
            lux              = "plot_lux.png",
            speed_lux        = "plot_speed_lux.png",
            elevation        = "plot_elevation.png",
            roc              = "plot_roc.png",
            roc_vs_distance  = "plot_roc_vs_distance.png",
        ),
    )

    json_path = os.path.join(outdir, "report_data.json")
    with open(json_path, "w") as f:
        json.dump(_nan_to_null(report), f, indent=2)
    print(f"\n  Saved: {json_path}")

    print(f"\n{'='*60}")
    print(f"  SUMMARY -- Scenario {scenario_num}  |  {vehicle}")
    print(f"{'='*60}")
    print(f"  Speed:  {speed_mean:.1f} mph  ({sc['speed_min']}-{sc['speed_max']})  "
          f"[{'PASS' if speed_pass else 'FAIL'}]")
    print(f"  Grade:  {grade_mean:.2f}% mean, max {grade_max:.2f}%  "
          f"[{'PASS' if grade_pass else 'FAIL'}]")
    print(f"  Pitch:  avg {avg_pitch:.3f} deg, {int(np.sum(excl_mask))} excluded")
    if roc and not roc.get("failed"):
        print(f"  ROC:    R={roc['R']:.1f} m  ({sc['roc_min']}-{sc['roc_max']})  "
              f"[{'PASS' if roc['geo_pass'] else 'FAIL'}]")
    for ch, bins in lux_compliance.items():
        for b in bins:
            print(f"  Lux Ch{ch} {b['d1']}-{b['d2']}m:  "
                  f"{b['max_after_rounded']:.1f} lux  (limit {b['limit']})  "
                  f"[{'PASS' if b['passed'] else 'FAIL'}]")
    print(f"\n  OVERALL: {'PASS' if overall_pass else 'FAIL'}")
    print(f"{'='*60}\n")
    return report



if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FMVSS 108 ADB Compliance Processor")
    parser.add_argument("--csv",      required=True,  help="Path to mobile-NNN.csv")
    parser.add_argument("--mat",      required=True,  help="Path to LuxRTK_*.mat")
    parser.add_argument("--scenario", required=True,  type=int, choices=range(1,9),
                        help="Scenario number 1-8")
    parser.add_argument("--vehicle",  default="Ford F-150",
                        help="Vehicle name (e.g. 'Ford F-150')")
    parser.add_argument("--channels", nargs="+", type=int, default=[2, 3],
                        help="Lux channel numbers e.g. --channels 2 3")
    parser.add_argument("--outdir",   default=None,
                        help="Output directory (defaults to same folder as CSV)")
    args = parser.parse_args()

    outdir = args.outdir or str(Path(args.csv).parent)
    process(args.csv, args.mat, args.scenario, args.vehicle,
            args.channels, outdir)