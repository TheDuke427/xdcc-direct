import aiosqlite
import logging
import os
import time

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/app/downloads/xdcc_index.db")
STALE_SECONDS = 7 * 86400

STARTER_CHANNELS = [
    ("CoreIRC", "irc.coreirc.net",  6667, 0, "#elitewarez"),
    ("Rizon",   "irc.rizon.net",    6667, 0, "#elitewarez"),
    ("Abjects", "irc.abjects.net",  6667, 0, "#elitewarez"),
]


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                network TEXT    NOT NULL,
                server  TEXT    NOT NULL,
                port    INTEGER NOT NULL DEFAULT 6667,
                ssl     INTEGER NOT NULL DEFAULT 0,
                channel TEXT    NOT NULL,
                UNIQUE(server, channel)
            );

            CREATE TABLE IF NOT EXISTS packs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                network   TEXT    NOT NULL,
                server    TEXT    NOT NULL,
                port      INTEGER NOT NULL,
                ssl       INTEGER NOT NULL DEFAULT 0,
                channel   TEXT    NOT NULL,
                bot       TEXT    NOT NULL,
                pack      TEXT    NOT NULL,
                filename  TEXT    NOT NULL,
                size      TEXT,
                gets      INTEGER,
                last_seen INTEGER NOT NULL,
                UNIQUE(server, bot, pack)
            );

            CREATE INDEX IF NOT EXISTS idx_filename  ON packs(filename COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_last_seen ON packs(last_seen);
        """)
        try:
            await conn.execute("ALTER TABLE packs ADD COLUMN ssl INTEGER NOT NULL DEFAULT 0")
            await conn.commit()
        except Exception:
            pass  # column already exists
        row = await (await conn.execute("SELECT COUNT(*) FROM channels")).fetchone()
        if row[0] == 0:
            await conn.executemany(
                "INSERT OR IGNORE INTO channels (network, server, port, ssl, channel) VALUES (?,?,?,?,?)",
                STARTER_CHANNELS,
            )
        await conn.commit()


async def bulk_upsert_packs(rows: list[dict]):
    if not rows:
        return
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executemany(
            """
            INSERT INTO packs (network, server, port, ssl, channel, bot, pack, filename, size, gets, last_seen)
            VALUES (:network, :server, :port, :ssl, :channel, :bot, :pack, :filename, :size, :gets, :now)
            ON CONFLICT(server, bot, pack) DO UPDATE SET
                filename  = excluded.filename,
                size      = excluded.size,
                gets      = excluded.gets,
                ssl       = excluded.ssl,
                last_seen = excluded.last_seen
            """,
            [{**r, "now": now} for r in rows],
        )
        await conn.commit()


async def search_packs(query: str, limit: int = 200) -> list[dict]:
    terms = query.strip().split()
    if not terms:
        return []
    cutoff = int(time.time()) - STALE_SECONDS
    conditions = " AND ".join("filename LIKE ?" for _ in terms)
    params = [f"%{t}%" for t in terms] + [cutoff, limit]
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            f"SELECT * FROM packs WHERE ({conditions}) AND last_seen > ? ORDER BY last_seen DESC LIMIT ?",
            params,
        )
        return [dict(r) for r in await cur.fetchall()]


async def prune_stale() -> int:
    cutoff = int(time.time()) - STALE_SECONDS
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("DELETE FROM packs WHERE last_seen < ?", (cutoff,))
        await conn.commit()
        return cur.rowcount


async def get_channels() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM channels ORDER BY network, channel")
        return [dict(r) for r in await cur.fetchall()]


async def add_channel(network: str, server: str, port: int, ssl: bool, channel: str) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO channels (network, server, port, ssl, channel) VALUES (?,?,?,?,?)",
            (network, server, port, int(ssl), channel),
        )
        await conn.commit()
        return cur.lastrowid


async def remove_channel(channel_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        await conn.commit()


async def purge_channel_packs(channel_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        row = await (await conn.execute(
            "SELECT server, port, channel FROM channels WHERE id = ?", (channel_id,)
        )).fetchone()
        if not row:
            return 0
        cur = await conn.execute(
            "DELETE FROM packs WHERE server = ? AND port = ? AND channel = ?", row
        )
        await conn.commit()
        return cur.rowcount


async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as conn:
        total = await (await conn.execute("SELECT COUNT(*) FROM packs")).fetchone()
        fresh = await (await conn.execute(
            "SELECT COUNT(*) FROM packs WHERE last_seen > ?",
            (int(time.time()) - STALE_SECONDS,)
        )).fetchone()
        return {"total_packs": total[0], "fresh_packs": fresh[0]}
