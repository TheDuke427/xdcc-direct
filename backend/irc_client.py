"""
Async IRC client with XDCC/DCC SEND support.
"""
import asyncio
import re
import struct
import socket
import logging
import os
import time
from collections import deque
import aiofiles
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

DCC_SEND_RE = re.compile(
    r'DCC SEND "?([^"]+)"?\s+(\d+)\s+(\d+)\s+(\d+)',
    re.IGNORECASE,
)
DCC_ACCEPT_RE = re.compile(
    r'DCC ACCEPT "?([^"]+)"?\s+(\d+)\s+(\d+)',
    re.IGNORECASE,
)


class IRCClient:
    """
    Minimal async IRC client that connects, joins a channel, triggers an XDCC
    send, handles the DCC SEND CTCP, downloads the file, and disconnects.
    """

    def __init__(
        self,
        server: str,
        port: int,
        nickname: str,
        channel: str | None,
        bot: str,
        pack: str,
        download_dir: str,
        progress_cb: Callable[[dict], Awaitable[None]],
        message_cb: Callable[[str], Awaitable[None]] | None = None,
        ssl: bool = False,
    ):
        self.server = server
        self.port = port
        self.nickname = nickname
        self.channel = channel
        self.bot = bot
        self.pack = pack
        self.download_dir = download_dir
        self.progress_cb = progress_cb
        self.message_cb = message_cb
        self.ssl = ssl

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._registered = False
        self._buf = b""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> str:
        """Connect, download, disconnect. Returns the saved file path."""
        await self._connect()
        await self._register()
        file_path = await self._wait_for_dcc()
        await self._disconnect()
        return file_path

    # ------------------------------------------------------------------
    # IRC plumbing
    # ------------------------------------------------------------------

    async def _connect(self):
        logger.info("Connecting to %s:%d", self.server, self.port)
        if self.ssl:
            import ssl as ssl_mod
            ctx = ssl_mod.create_default_context()
            self._reader, self._writer = await asyncio.open_connection(
                self.server, self.port, ssl=ctx
            )
        else:
            self._reader, self._writer = await asyncio.open_connection(
                self.server, self.port
            )

    async def _register(self):
        nick = self.nickname
        nick_attempt = 0
        await self._send(f"NICK {nick}")
        await self._send(f"USER {nick} 0 * :{nick}")

        async for line in self._lines():
            logger.debug("IRC << %s", line)
            if line.startswith("PING"):
                token = line.split(":", 1)[1] if ":" in line else line.split()[1]
                await self._send(f"PONG :{token}")
            # 433 = ERR_NICKNAMEINUSE — retry with a numeric suffix
            if re.search(r"^:\S+ 433 ", line):
                nick_attempt += 1
                nick = f"{self.nickname}_{nick_attempt}"
                logger.info("Nick taken, retrying as %s", nick)
                await self._send(f"NICK {nick}")
                continue
            if re.search(r"^:\S+ 001 ", line):
                self.nickname = nick
                self._registered = True
                break

        if self.channel:
            await self._send(f"JOIN {self.channel}")
            # Wait for 366 (End of /NAMES) — confirms server has processed our JOIN
            async for line in self._lines():
                logger.debug("IRC << %s", line)
                if line.startswith("PING"):
                    token = line.split(":", 1)[1] if ":" in line else line.split()[1]
                    await self._send(f"PONG :{token}")
                if re.search(r"^:\S+ 366 ", line):
                    break

        logger.info("Sending XDCC request to %s: xdcc send %s", self.bot, self.pack)
        await self._send(f"PRIVMSG {self.bot} :xdcc send {self.pack}")

    async def _wait_for_dcc(self) -> str:
        """Read IRC lines until we get a DCC SEND CTCP, then download.
        Handles DCC RESUME in the same loop to avoid buffer splits."""
        pending_resume: tuple | None = None  # (filename, ip, port, filesize)

        async for line in self._lines():
            logger.debug("IRC << %s", line)
            if line.startswith("PING"):
                token = line.split(":", 1)[1] if ":" in line else line.split()[1]
                await self._send(f"PONG :{token}")

            # Surface bot messages and server errors to the UI
            if self.message_cb:
                parts = line.split()
                if len(parts) >= 4:
                    sender_nick = parts[0].lstrip(":").split("!")[0]
                    cmd = parts[1] if len(parts) > 1 else ""
                    # NOTICE/PRIVMSG from the bot (queue position, send confirmation, etc.)
                    if cmd in ("NOTICE", "PRIVMSG") and sender_nick.lower() == self.bot.lower():
                        text = line.split(":", 2)[-1].strip() if line.count(":") >= 2 else ""
                        if text:
                            logger.info("Bot message: %s", text)
                            await self.message_cb(text)
                    # 401 = No such nick, 403 = No such channel
                    elif cmd in ("401", "403"):
                        text = line.split(":", 2)[-1].strip() if line.count(":") >= 2 else line
                        logger.info("Server error: %s", text)
                        await self.message_cb(f"Server: {text}")

            if "DCC SEND" in line:
                m = DCC_SEND_RE.search(line)
                if m:
                    filename = m.group(1)
                    ip = socket.inet_ntoa(struct.pack("!I", int(m.group(2))))
                    port = int(m.group(3))
                    filesize = int(m.group(4))
                    logger.info(
                        "DCC SEND: %s from %s:%d (%d bytes)", filename, ip, port, filesize
                    )

                    dest = os.path.join(self.download_dir, os.path.basename(filename))
                    if os.path.isfile(dest):
                        existing = os.path.getsize(dest)
                        if 0 < existing < filesize:
                            logger.info("Partial file found (%d bytes), attempting resume", existing)
                            await self._send(
                                f"PRIVMSG {self.bot} :\x01DCC RESUME {filename} {port} {existing}\x01"
                            )
                            pending_resume = (filename, ip, port, filesize)
                            continue  # wait for DCC ACCEPT

                    return await self._dcc_download(filename, ip, port, filesize, 0)

            if pending_resume and "DCC ACCEPT" in line:
                m = DCC_ACCEPT_RE.search(line)
                if m:
                    offset = int(m.group(3))
                    filename, ip, port, filesize = pending_resume
                    logger.info("DCC ACCEPT: resuming %s at byte %d", filename, offset)
                    return await self._dcc_download(filename, ip, port, filesize, offset)

        raise RuntimeError("Connection closed before DCC SEND received")

    async def _disconnect(self):
        if self._writer:
            try:
                await self._send("QUIT :bye")
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # DCC download
    # ------------------------------------------------------------------

    async def _dcc_download(
        self, filename: str, ip: str, port: int, filesize: int, resume_offset: int = 0
    ) -> str:
        safe_name = os.path.basename(filename)
        dest = os.path.join(self.download_dir, safe_name)

        reader, writer = await asyncio.open_connection(ip, port)
        received = resume_offset

        # Rolling 5-second window for speed calculation
        speed_window: deque[tuple[float, int]] = deque()

        mode = "r+b" if resume_offset > 0 else "wb"
        async with aiofiles.open(dest, mode) as f:
            if resume_offset > 0:
                await f.seek(resume_offset)
            while received < filesize:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                await f.write(chunk)
                received += len(chunk)
                # Send 4-byte big-endian ack
                writer.write(struct.pack("!I", received & 0xFFFFFFFF))
                await writer.drain()

                now = time.monotonic()
                speed_window.append((now, received))
                while len(speed_window) > 1 and now - speed_window[0][0] > 5:
                    speed_window.popleft()

                speed = 0.0
                eta = None
                if len(speed_window) >= 2:
                    dt = speed_window[-1][0] - speed_window[0][0]
                    db = speed_window[-1][1] - speed_window[0][1]
                    if dt > 0:
                        speed = db / dt
                        remaining = filesize - received
                        if speed > 0:
                            eta = int(remaining / speed)

                await self.progress_cb({
                    "received": received,
                    "total": filesize,
                    "filename": safe_name,
                    "speed": speed,
                    "eta": eta,
                })

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        if received < filesize:
            raise RuntimeError(f"Incomplete download: {received}/{filesize} bytes")

        return dest

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _send(self, line: str):
        self._writer.write(f"{line}\r\n".encode())
        await self._writer.drain()

    async def _lines(self):
        while True:
            while b"\r\n" in self._buf:
                line, self._buf = self._buf.split(b"\r\n", 1)
                yield line.decode(errors="replace")
            data = await self._reader.read(4096)
            if not data:
                return
            self._buf += data
