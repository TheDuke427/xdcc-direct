"""
Passive IRC indexer: joins configured channels and records every pack
announcement it sees into the local SQLite database.
"""
import asyncio
import logging
import re
import time

import db
from irc_client import _resolve, PACK_LIST_RE, _strip_colors

logger = logging.getLogger(__name__)

RECONNECT_MIN  = 30    # seconds before first reconnect attempt
RECONNECT_MAX  = 300   # cap for exponential backoff
FLUSH_INTERVAL = 5     # seconds between DB flushes
FLUSH_BATCH    = 200   # also flush when this many rows are pending


class ChannelIndexer:
    """Maintains one IRC connection, indexes pack announcements passively."""

    def __init__(self, cfg: dict):
        self.id       = cfg["id"]
        self.network  = cfg["network"]
        self.server   = cfg["server"]
        self.port     = int(cfg["port"])
        self.ssl      = bool(cfg["ssl"])
        self.channel  = cfg["channel"]

        self.connected:     bool      = False
        self.last_activity: int|None  = None
        self.packs_seen:    int       = 0

        self._task:    asyncio.Task | None = None
        self._stop:    bool               = False
        self._pending: list[dict]         = []

    # ------------------------------------------------------------------

    def start(self):
        self._stop = False
        self._task = asyncio.create_task(
            self._run_forever(), name=f"idx:{self.network}:{self.channel}"
        )

    def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()

    # ------------------------------------------------------------------

    async def _run_forever(self):
        delay = RECONNECT_MIN
        while not self._stop:
            try:
                await self._connect_and_index()
                delay = RECONNECT_MIN  # reset on clean disconnect
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    "Indexer %s %s lost connection: %s", self.network, self.channel, e
                )
            self.connected = False
            if not self._stop:
                logger.info(
                    "Indexer %s %s reconnecting in %ds", self.network, self.channel, delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX)

    async def _connect_and_index(self):
        host = await _resolve(self.server)
        logger.info("Indexer %s %s connecting to %s:%d", self.network, self.channel, host, self.port)

        if self.ssl:
            import ssl as ssl_mod
            ctx = ssl_mod.create_default_context()
            reader, writer = await asyncio.open_connection(host, self.port, ssl=ctx)
        else:
            reader, writer = await asyncio.open_connection(host, self.port)

        nick = f"idx{self.id}"
        try:
            buf = await self._register(reader, writer, nick)
            buf = await self._join(reader, writer, buf)
            self.connected = True
            logger.info("Indexer %s %s listening", self.network, self.channel)
            await asyncio.gather(
                self._listen(reader, writer, buf),
                self._flush_loop(),
            )
        finally:
            self.connected = False
            await self._flush()
            try:
                writer.write(b"QUIT :bye\r\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # IRC handshake helpers (self-contained — don't share IRCClient state)
    # ------------------------------------------------------------------

    async def _register(self, reader, writer, nick: str) -> bytes:
        buf = b""
        attempt = 0

        async def send(line: str):
            writer.write(f"{line}\r\n".encode())
            await writer.drain()

        await send(f"NICK {nick}")
        await send(f"USER {nick} 0 * :{nick}")

        while True:
            data = await asyncio.wait_for(reader.read(4096), timeout=60)
            if not data:
                raise RuntimeError("Connection closed during registration")
            buf += data
            while b"\r\n" in buf:
                line_b, buf = buf.split(b"\r\n", 1)
                line = line_b.decode(errors="replace")
                if line.startswith("PING"):
                    token = line.split(":", 1)[1] if ":" in line else line.split()[1]
                    await send(f"PONG :{token}")
                if re.search(r"^:\S+ 433 ", line):
                    attempt += 1
                    nick = f"idx{self.id}_{attempt}"
                    await send(f"NICK {nick}")
                if re.search(r"^:\S+ 001 ", line):
                    return buf

    async def _join(self, reader, writer, buf: bytes) -> bytes:
        async def send(line: str):
            writer.write(f"{line}\r\n".encode())
            await writer.drain()

        await send(f"JOIN {self.channel}")
        while True:
            while b"\r\n" in buf:
                line_b, buf = buf.split(b"\r\n", 1)
                line = line_b.decode(errors="replace")
                if line.startswith("PING"):
                    token = line.split(":", 1)[1] if ":" in line else line.split()[1]
                    await send(f"PONG :{token}")
                if re.search(r"^:\S+ 366 ", line):
                    return buf
                if re.search(r"^:\S+ (47[0-9]|405|403) ", line):
                    raise RuntimeError(f"Cannot join {self.channel}")
            data = await asyncio.wait_for(reader.read(4096), timeout=60)
            if not data:
                raise RuntimeError("Connection closed during join")
            buf += data

    async def _listen(self, reader, writer, buf: bytes):
        async def send(line: str):
            writer.write(f"{line}\r\n".encode())
            await writer.drain()

        while True:
            while b"\r\n" in buf:
                line_b, buf = buf.split(b"\r\n", 1)
                line = line_b.decode(errors="replace")
                if line.startswith("PING"):
                    token = line.split(":", 1)[1] if ":" in line else line.split()[1]
                    await send(f"PONG :{token}")
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                sender = parts[0].lstrip(":").split("!")[0]
                cmd    = parts[1]
                if cmd in ("PRIVMSG", "NOTICE"):
                    raw = line.split(":", 2)[-1].strip() if line.count(":") >= 2 else ""
                    text = _strip_colors(raw)
                    m = PACK_LIST_RE.search(text)
                    if m:
                        self._pending.append({
                            "network": self.network,
                            "server":  self.server,
                            "port":    self.port,
                            "channel": self.channel,
                            "bot":     sender,
                            "pack":    f"#{m.group(1)}",
                            "filename": m.group(4).strip(),
                            "size":    m.group(3),
                            "gets":    int(m.group(2)),
                        })
                        self.last_activity = int(time.time())
                        self.packs_seen += 1
                        if len(self._pending) >= FLUSH_BATCH:
                            await self._flush()
            data = await reader.read(4096)
            if not data:
                raise RuntimeError("Connection closed")
            buf += data

    async def _flush(self):
        if not self._pending:
            return
        rows, self._pending = self._pending, []
        try:
            await db.bulk_upsert_packs(rows)
        except Exception as e:
            logger.warning("DB flush failed: %s", e)

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            await self._flush()


# ──────────────────────────────────────────────────────────────────────


class Indexer:
    """Manages all ChannelIndexer tasks."""

    def __init__(self):
        self._indexers: dict[int, ChannelIndexer] = {}
        self._prune_task: asyncio.Task | None = None

    async def start(self):
        await db.init_db()
        for ch in await db.get_channels():
            self._launch(ch)
        self._prune_task = asyncio.create_task(self._prune_loop(), name="idx:prune")
        logger.info("Indexer started with %d channels", len(self._indexers))

    async def stop(self):
        for idx in self._indexers.values():
            idx.stop()
        if self._prune_task:
            self._prune_task.cancel()

    def _launch(self, ch: dict):
        idx = ChannelIndexer(ch)
        idx.start()
        self._indexers[ch["id"]] = idx

    async def add_channel(self, network: str, server: str, port: int, ssl: bool, channel: str) -> int:
        ch_id = await db.add_channel(network, server, port, ssl, channel)
        self._launch({"id": ch_id, "network": network, "server": server,
                      "port": port, "ssl": ssl, "channel": channel})
        return ch_id

    async def remove_channel(self, channel_id: int):
        if channel_id in self._indexers:
            self._indexers[channel_id].stop()
            del self._indexers[channel_id]
        await db.remove_channel(channel_id)

    def status(self) -> list[dict]:
        return [
            {
                "id":            idx.id,
                "network":       idx.network,
                "server":        idx.server,
                "port":          idx.port,
                "channel":       idx.channel,
                "connected":     idx.connected,
                "last_activity": idx.last_activity,
                "packs_seen":    idx.packs_seen,
            }
            for idx in self._indexers.values()
        ]

    async def _prune_loop(self):
        while True:
            await asyncio.sleep(3600)
            removed = await db.prune_stale()
            if removed:
                logger.info("Pruned %d stale packs", removed)
