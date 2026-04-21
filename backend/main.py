"""
FastAPI app: REST endpoints + WebSocket broadcast for XDCC download manager.
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from download_manager import DownloadManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./downloads")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "3"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

manager = DownloadManager(DOWNLOAD_DIR, max_concurrent=MAX_CONCURRENT)

# Active WebSocket connections
_ws_clients: set[WebSocket] = set()


async def broadcast(payload: dict):
    msg = json.dumps(payload)
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.set_broadcast(broadcast)
    yield


app = FastAPI(title="XDCC Download Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------

class AddDownloadRequest(BaseModel):
    server: str = Field(..., examples=["irc.rizon.net"])
    port: int = Field(6667, ge=1, le=65535)
    ssl: bool = False
    nickname: str = Field("xdccuser", min_length=1, max_length=30)
    channel: str = Field("", description="IRC channel to join first (optional)")
    bot: str = Field(..., description="Bot nickname", examples=["Ginpachi-Sensei"])
    pack: str = Field(..., description="Pack number, e.g. #123", examples=["#123"])


# ------------------------------------------------------------------
# REST endpoints
# ------------------------------------------------------------------

@app.get("/api/downloads")
def list_downloads():
    return manager.list_jobs()


@app.post("/api/downloads", status_code=201)
async def add_download(req: AddDownloadRequest):
    job = manager.add_job(
        server=req.server,
        port=req.port,
        ssl=req.ssl,
        nickname=req.nickname,
        channel=req.channel,
        bot=req.bot,
        pack=req.pack,
    )
    return job


@app.get("/api/downloads/{job_id}")
def get_download(job_id: str):
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.delete("/api/downloads/{job_id}", status_code=204)
async def delete_download(job_id: str):
    if not manager.delete_job(job_id):
        raise HTTPException(404, "Job not found")


@app.post("/api/downloads/{job_id}/cancel", status_code=204)
async def cancel_download(job_id: str):
    if not manager.cancel_job(job_id):
        raise HTTPException(404, "Job not found or already finished")


@app.get("/api/search")
async def search_xdcc(q: str = ""):
    if len(q.strip()) < 2:
        return []
    async with httpx.AsyncClient(timeout=10.0) as client:
        bots_r, search_r = await asyncio.gather(
            client.get("https://nibl.co/api/bots"),
            client.get("https://nibl.co/api/search", params={"query": q}),
        )

    bots_by_id = {}
    try:
        bots_by_id = {b["id"]: b for b in bots_r.json().get("bots", [])}
    except Exception:
        logger.warning("nibl.co /api/bots parse failed (status=%s body=%r)", bots_r.status_code, bots_r.text[:200])

    results = []
    for r in search_r.json().get("results", [])[:100]:
        bot = bots_by_id.get(r.get("botId"), {})
        results.append({
            "bot": r.get("botNickname", ""),
            "pack": f"#{r.get('packNumber', '')}",
            "filename": r.get("name", ""),
            "size": r.get("size", ""),
            "server": "irc.rizon.net",
            "port": 6667,
            "channel": bot.get("channel", ""),
            "gets": r.get("gets", 0),
        })
    return results


@app.get("/api/files")
def list_files():
    return manager.list_files()


@app.get("/api/files/{filename}")
def download_file(filename: str):
    # Prevent path traversal
    safe = os.path.basename(filename)
    path = os.path.join(DOWNLOAD_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=safe, media_type="application/octet-stream")


# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    # Send current state on connect
    for job in manager.list_jobs():
        await ws.send_text(json.dumps(job))
    try:
        while True:
            # Keep connection alive; client can send pings
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(ws)
