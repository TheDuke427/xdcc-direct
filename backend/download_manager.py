"""
In-memory download queue with persistence. Each download job runs in its own asyncio task.
"""
import asyncio
import uuid
import os
import json
import logging
from enum import Enum
from dataclasses import dataclass, asdict, fields as dc_fields
from typing import Callable, Awaitable

import aiofiles

from irc_client import IRCClient

logger = logging.getLogger(__name__)


class Status(str, Enum):
    QUEUED = "queued"
    CONNECTING = "connecting"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadJob:
    id: str
    server: str
    port: int
    ssl: bool
    nickname: str
    channel: str
    bot: str
    pack: str
    status: Status = Status.QUEUED
    filename: str = ""
    received: int = 0
    total: int = 0
    error: str = ""
    file_path: str = ""
    speed: float = 0.0
    eta: int | None = None
    message: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["progress"] = round(self.received / self.total * 100, 1) if self.total else 0
        return d


BroadcastFn = Callable[[dict], Awaitable[None]]

_JOB_FIELDS: set[str] = {f.name for f in dc_fields(DownloadJob)}


class DownloadManager:
    def __init__(self, download_dir: str, max_concurrent: int = 3):
        self.download_dir = download_dir
        self._max_concurrent = max_concurrent
        self._jobs: dict[str, DownloadJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._broadcast: BroadcastFn | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._persist_path = os.path.join(download_dir, ".jobs.json")
        self._load_persisted()

    def set_broadcast(self, fn: BroadcastFn):
        self._broadcast = fn
        # Create semaphore here — called from lifespan (async context)
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_persisted(self):
        try:
            with open(self._persist_path) as f:
                data = json.load(f)
            for d in data:
                kwargs = {k: v for k, v in d.items() if k in _JOB_FIELDS}
                kwargs["status"] = Status(kwargs.get("status", Status.QUEUED))
                job = DownloadJob(**kwargs)
                if job.status in (Status.QUEUED, Status.CONNECTING, Status.DOWNLOADING):
                    job.status = Status.FAILED
                    job.error = "Interrupted (server restarted)"
                self._jobs[job.id] = job
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception:
            logger.exception("Failed to load persisted jobs")

    async def _save_jobs(self):
        try:
            data = json.dumps([j.to_dict() for j in self._jobs.values()])
            async with aiofiles.open(self._persist_path, "w") as f:
                await f.write(data)
        except Exception:
            logger.exception("Failed to persist jobs")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in self._jobs.values()]

    def get_job(self, job_id: str) -> dict | None:
        job = self._jobs.get(job_id)
        return job.to_dict() if job else None

    def add_job(
        self,
        server: str,
        port: int,
        ssl: bool,
        nickname: str,
        channel: str,
        bot: str,
        pack: str,
    ) -> dict:
        job = DownloadJob(
            id=str(uuid.uuid4()),
            server=server,
            port=port,
            ssl=ssl,
            nickname=nickname,
            channel=channel,
            bot=bot,
            pack=pack,
        )
        self._jobs[job.id] = job
        task = asyncio.create_task(self._run(job))
        self._tasks[job.id] = task
        return job.to_dict()

    def cancel_job(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        job = self._jobs.get(job_id)
        if task and not task.done():
            task.cancel()
        if job and job.status not in (Status.COMPLETE, Status.FAILED, Status.CANCELLED):
            job.status = Status.CANCELLED
            asyncio.create_task(self._notify(job))
            return True
        return False

    def delete_job(self, job_id: str) -> bool:
        self.cancel_job(job_id)
        existed = job_id in self._jobs
        self._jobs.pop(job_id, None)
        self._tasks.pop(job_id, None)
        if existed:
            asyncio.create_task(self._save_jobs())
        return existed

    def list_files(self) -> list[dict]:
        files = []
        for name in os.listdir(self.download_dir):
            full = os.path.join(self.download_dir, name)
            if os.path.isfile(full) and not name.startswith("."):
                files.append({"name": name, "size": os.path.getsize(full)})
        return sorted(files, key=lambda f: f["name"])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(self, job: DownloadJob):
        try:
            # Broadcast initial QUEUED state; job waits here if at concurrency limit
            await self._notify(job)
            async with self._semaphore:
                job.status = Status.CONNECTING
                await self._notify(job)

                async def progress(info: dict):
                    job.status = Status.DOWNLOADING
                    job.filename = info["filename"]
                    job.received = info["received"]
                    job.total = info["total"]
                    job.speed = info.get("speed", 0.0)
                    job.eta = info.get("eta")
                    job.message = ""
                    await self._notify(job)

                async def bot_message(msg: str):
                    job.message = msg
                    await self._notify(job)

                client = IRCClient(
                    server=job.server,
                    port=job.port,
                    nickname=job.nickname,
                    channel=job.channel or None,
                    bot=job.bot,
                    pack=job.pack,
                    download_dir=self.download_dir,
                    progress_cb=progress,
                    message_cb=bot_message,
                    ssl=job.ssl,
                )
                file_path = await client.run()
                job.file_path = file_path
                job.speed = 0.0
                job.eta = None
                job.status = Status.COMPLETE
        except asyncio.CancelledError:
            job.status = Status.CANCELLED
        except asyncio.TimeoutError:
            job.status = Status.FAILED
            job.error = "Timed out waiting for bot response"
        except Exception as exc:
            logger.exception("Download %s failed", job.id)
            job.status = Status.FAILED
            job.error = str(exc)
        finally:
            await self._notify(job)

    async def _notify(self, job: DownloadJob):
        if self._broadcast:
            try:
                await self._broadcast(job.to_dict())
            except Exception:
                pass
        await self._save_jobs()
