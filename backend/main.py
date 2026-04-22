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

import db
from download_manager import DownloadManager
from indexer import Indexer
from irc_client import IRCClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOWNLOAD_DIR      = os.environ.get("DOWNLOAD_DIR", "./downloads")
GLUETUN_CONTROL   = os.environ.get("GLUETUN_CONTROL", "http://localhost:8000")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "3"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

manager = DownloadManager(DOWNLOAD_DIR, max_concurrent=MAX_CONCURRENT)
indexer = Indexer()

_ws_clients: set[WebSocket] = set()


async def broadcast(payload: dict):
    msg  = json.dumps(payload)
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
    await indexer.start()
    yield
    await indexer.stop()


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
    server:   str  = Field(..., examples=["irc.rizon.net"])
    port:     int  = Field(6667, ge=1, le=65535)
    ssl:      bool = False
    nickname: str  = Field("xdccuser", min_length=1, max_length=30)
    channel:  str  = Field("", description="IRC channel to join first (optional)")
    bot:      str  = Field(..., description="Bot nickname", examples=["Ginpachi-Sensei"])
    pack:     str  = Field(..., description="Pack number, e.g. #123", examples=["#123"])


class PackListRequest(BaseModel):
    server:   str
    port:     int  = Field(6667, ge=1, le=65535)
    ssl:      bool = False
    nickname: str  = Field("xdccuser", min_length=1, max_length=30)
    channel:  str  = ""
    bot:      str


class AddChannelRequest(BaseModel):
    network: str  = Field(..., examples=["Rizon"])
    server:  str  = Field(..., examples=["irc.rizon.net"])
    port:    int  = Field(6667, ge=1, le=65535)
    ssl:     bool = False
    channel: str  = Field(..., examples=["#elitewarez"])


# ------------------------------------------------------------------
# Download endpoints
# ------------------------------------------------------------------

@app.get("/api/downloads")
def list_downloads():
    return manager.list_jobs()


@app.post("/api/downloads", status_code=201)
async def add_download(req: AddDownloadRequest):
    return manager.add_job(
        server=req.server, port=req.port, ssl=req.ssl,
        nickname=req.nickname, channel=req.channel,
        bot=req.bot, pack=req.pack,
    )


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


# ------------------------------------------------------------------
# Pack list
# ------------------------------------------------------------------

@app.post("/api/packlist")
async def fetch_pack_list(req: PackListRequest):
    async def noop(_): pass
    client = IRCClient(
        server=req.server, port=req.port, ssl=req.ssl,
        nickname=req.nickname, channel=req.channel, bot=req.bot,
        pack="", download_dir="", progress_cb=noop,
    )
    try:
        packs = await asyncio.wait_for(client.run_list(), timeout=150)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timed out waiting for pack list")
    except Exception as e:
        raise HTTPException(502, str(e))
    return packs


# ------------------------------------------------------------------
# Search (local index)
# ------------------------------------------------------------------

@app.get("/api/search")
async def search_xdcc(q: str = ""):
    if len(q.strip()) < 2:
        return []
    results = await db.search_packs(q)
    return results


# ------------------------------------------------------------------
# Index management
# ------------------------------------------------------------------

@app.get("/api/index/status")
async def index_status():
    stats = await db.get_stats()
    return {**stats, "channels": indexer.status()}


@app.get("/api/index/channels")
async def list_channels():
    return await db.get_channels()


@app.post("/api/index/channels", status_code=201)
async def add_channel(req: AddChannelRequest):
    try:
        ch_id = await indexer.add_channel(
            network=req.network, server=req.server,
            port=req.port, ssl=req.ssl, channel=req.channel,
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"id": ch_id}


@app.delete("/api/index/channels/{channel_id}", status_code=204)
async def remove_channel(channel_id: int):
    await indexer.remove_channel(channel_id)


# ------------------------------------------------------------------
# VPN control (proxies to gluetun HTTP control server)
# ------------------------------------------------------------------

@app.get("/api/vpn/status")
async def get_vpn_status():
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{GLUETUN_CONTROL}/v1/vpn/status")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise HTTPException(502, f"VPN control unreachable: {e}")


class VpnStatusRequest(BaseModel):
    status: str  # "running" or "stopped"


@app.put("/api/vpn/status")
async def set_vpn_status(req: VpnStatusRequest):
    if req.status not in ("running", "stopped"):
        raise HTTPException(400, "status must be 'running' or 'stopped'")
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.put(
                f"{GLUETUN_CONTROL}/v1/vpn/status",
                json={"status": req.status},
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise HTTPException(502, f"VPN control unreachable: {e}")


# ------------------------------------------------------------------
# Files
# ------------------------------------------------------------------

@app.get("/api/files")
def list_files():
    return manager.list_files()


@app.get("/api/files/{filename}")
def download_file(filename: str):
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
    for job in manager.list_jobs():
        await ws.send_text(json.dumps(job))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(ws)
