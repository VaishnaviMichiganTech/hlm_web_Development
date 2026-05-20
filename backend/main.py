from fastapi import FastAPI, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from routes import lux_meter
from routes import camera_feed
from routes import recorder
from routes import logger_view
from routes import records_browser
from routes import config_editor
from routes import report #v




@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context: runs once on startup and once on shutdown.
    """

    yield

    cleanup(app)
    print("🧹 Cleaned up global state.")


def cleanup(app : FastAPI):
    pass

app = FastAPI(title="LHM Dashboard", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(lux_meter.router, prefix="/api/lux_sensors")
app.include_router(camera_feed.router, prefix="/api/camera_feed")
app.include_router(recorder.router, prefix="/api/record")
app.include_router(logger_view.router, prefix="/api/logger")
app.include_router(records_browser.router, prefix="/api/records")
app.include_router(config_editor.router, prefix="/api")
app.include_router(report.router, prefix="/api/records") #v

@app.get("/")
def root():
    return {"message": "Backend is running"}