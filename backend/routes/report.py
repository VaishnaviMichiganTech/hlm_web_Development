"""
routes/report.py
================
Add to main.py:
    from routes import report
    app.include_router(report.router, prefix="/api/records")
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
import json, os

router = APIRouter()
BASE_DIR = Path("/DATABASE")


@router.get("/report")
def get_report(path: str):
    """
    Serve the ADB report HTML for a given recording folder.
    The HTML file is adb_report_v3.html which reads report_data.json
    from the same directory via a relative path.
    Usage: GET /api/records/report?path=20260204T000
    """
    folder = (BASE_DIR / path / "adb_result").resolve()

    if not str(folder).startswith(str(BASE_DIR)):
        raise HTTPException(status_code=400, detail="Invalid path")

    html_path = folder / "adb_report.html"
    if not html_path.exists():
        # Try the template name
        html_path = folder / "adb_report_v3.html"

    if not html_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report not found. Has ADB post-processing been run? "
                   f"Expected: {html_path}")

    return FileResponse(str(html_path), media_type="text/html")


@router.get("/report-data")
def get_report_data(path: str):
    """Return raw report_data.json for a recording folder."""
    json_path = (BASE_DIR / path / "adb_result" / "report_data.json").resolve()
    if not str(json_path).startswith(str(BASE_DIR)):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="report_data.json not found")
    with open(json_path) as f:
        return json.load(f)


@router.get("/report-status")
def report_status(path: str):
    """Check whether a report exists for a given folder."""
    json_path = (BASE_DIR / path / "adb_result" / "report_data.json").resolve()
    return {"has_report": json_path.exists()}