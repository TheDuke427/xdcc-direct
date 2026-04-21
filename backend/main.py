"""
FastAPI app: REST endpoints + WebSocket broadcast for XDCC download manager.
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import httpx
from bs4 import BeautifulSoup
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


async def _search_sunxdcc(client: httpx.AsyncClient, q: str) -> list[dict]:
    try:
        r = await client.get("https://sunxdcc.com/deliver.php", params={"sterm": q, "page": 0})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("sunxdcc.com search failed: %s", e)
        return []
    network = data.get("network", [])
    channel = data.get("channel", [])
    bot     = data.get("bot", [])
    fsize   = data.get("fsize", [])
    fname   = data.get("fname", [])
    packnum = data.get("packnum", [])
    gets    = data.get("gets", [])
    n = min(len(network), len(bot), len(fname), len(packnum))
    return [
        {
            "bot": bot[i],
            "pack": packnum[i],
            "filename": fname[i],
            "size": fsize[i] if i < len(fsize) else "",
            "server": network[i],
            "port": 6667,
            "channel": channel[i] if i < len(channel) else "",
            "gets": gets[i] if i < len(gets) else "",
            "source": "sunxdcc",
        }
        for i in range(min(n, 100))
    ]


async def _search_xdcceu(client: httpx.AsyncClient, q: str) -> list[dict]:
    try:
        r = await client.get(
            "https://www.xdcc.eu/search.php",
            params={"searchkey": q},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning("xdcc.eu search failed: %s", e)
        return []
    results = []
    try:
        soup = BeautifulSoup(r.text, "html.parser")
        for tr in soup.select("#table tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            results.append({
                "bot": tds[2].get_text(strip=True),
                "pack": tds[3].get_text(strip=True),
                "filename": tds[5].get_text(strip=True),
                "size": tds[4].get_text(strip=True),
                "server": tds[0].get_text(strip=True),
                "port": 6667,
                "channel": tds[1].get_text(strip=True),
                "gets": "",
                "source": "xdcc.eu",
            })
    except Exception as e:
        logger.warning("xdcc.eu parse failed: %s", e)
    return results[:100]


@app.get("/api/search")
async def search_xdcc(q: str = ""):
    if len(q.strip()) < 2:
        return []
    async with httpx.AsyncClient(timeout=15.0) as client:
        sun, eu = await asyncio.gather(
            _search_sunxdcc(client, q),
            _search_xdcceu(client, q),
        )
    if not sun and not eu:
        raise HTTPException(502, "All search sources failed")

    # Merge, deduplicate by (bot, pack), xdcc.eu first (fresher index)
    seen: set[tuple] = set()
    merged = []
    for r in eu + sun:
        key = (r["bot"].lower(), r["pack"].lower())
        if key not in seen:
            seen.add(key)
            merged.append(r)
    return merged[:200]


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
