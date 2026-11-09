import os
import secrets
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from psycopg_pool import AsyncConnectionPool
from psycopg.errors import UniqueViolation


DATABASE_URL = os.environ["DATABASE_URL"]

TIMEZONE = ZoneInfo("Europe/Stockholm")

pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)


async def init_db() -> None:
    await pool.open()
    async with pool.connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS babies (
                baby_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
            );

            
            CREATE TABLE IF NOT EXISTS baby_chats (
                chat_id BIGINT PRIMARY KEY,   -- a chat belongs to exactly one baby
                baby_id BIGINT NOT NULL REFERENCES babies (baby_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_baby_chats_baby_id ON baby_chats (baby_id);

            
            CREATE TABLE IF NOT EXISTS baby_invites (
                code       TEXT PRIMARY KEY,
                baby_id    BIGINT NOT NULL REFERENCES babies (baby_id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS naps (
                id         SERIAL PRIMARY KEY,
                baby_id    BIGINT NOT NULL REFERENCES babies (baby_id) ON DELETE CASCADE,
                start_time TIMESTAMPTZ NOT NULL,
                end_time   TIMESTAMPTZ,
                CHECK (end_time IS NULL OR end_time > start_time)
            );
            CREATE INDEX IF NOT EXISTS idx_naps_baby_start ON naps (baby_id, start_time DESC);

            CREATE TABLE IF NOT EXISTS wake_windows (
                id         SERIAL PRIMARY KEY,
                baby_id    BIGINT PRIMARY KEY REFERENCES babies (baby_id) ON DELETE CASCADE,
                windows    INTERVAL[] NOT NULL DEFAULT '{}'
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_wake_windows_baby ON wake_windows (baby_id);

            """
        )


async def close_db() -> None:
    await pool.close()


async def add_nap(baby_id: int, start_time: datetime, end_time: datetime | None = None) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO naps (baby_id, start_time, end_time)
            VALUES (%s, %s, %s)
            RETURNING id;
            """,
            (baby_id, start_time, end_time),
        )
        row = await cur.fetchone()
        return row[0]


async def end_nap(baby_id: int, end_time: datetime) -> tuple | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            UPDATE naps
            SET end_time = %s
            WHERE id = (
                SELECT id
                FROM naps
                WHERE baby_id = %s AND end_time IS NULL
                ORDER BY start_time DESC
                LIMIT 1
            )
            RETURNING id, start_time, end_time;
            """,
            (end_time, baby_id),
        )
        return await cur.fetchone()  # None if there was no open nap


async def get_today_or_open_naps(baby_id: int) -> list[tuple[datetime, datetime | None]]:
    """Naps that ended today (local time) or have no end time yet."""
    today = datetime.now(TIMEZONE).date()
    day_start = datetime.combine(today, time.min, tzinfo=TIMEZONE)

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT start_time, end_time
            FROM naps
            WHERE baby_id = %s
                AND (
                    end_time IS NULL
                    OR (end_time >= %s)
                    )
            ORDER BY start_time;
            """,
            (baby_id, day_start),
        )
        return await cur.fetchall()


async def get_last_nap(baby_id: int):
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, baby_id, start_time, end_time
            FROM naps
            WHERE baby_id = %s
            ORDER BY start_time DESC
            LIMIT 1;
            """,
            (baby_id,),
        )
        return await cur.fetchone()  # None if there are no naps yet


async def get_wake_windows(baby_id: int) -> list[timedelta] | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT windows FROM wake_windows WHERE baby_id = %s;",
            (baby_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else []


async def set_wake_windows(baby_id: int, windows: list[timedelta]) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO wake_windows (baby_id, windows)
            VALUES (%s, %s::interval[])
            ON CONFLICT (baby_id)
            DO UPDATE SET windows = EXCLUDED.windows;
            """,
            (baby_id, windows),
        )


# ==========================================
# INVITATION CODES
# ==========================================

# no 0/O/1/I/L, so codes are easy to read out and type
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_TTL = timedelta(hours=24)


def _new_code(length: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


# Do I need it? Can I join the tables when accessing naps table>
async def get_baby_id(chat_id: int) -> int | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT baby_id FROM baby_chats WHERE chat_id = %s;", (chat_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def create_baby(chat_id: int) -> int:
    """Creates a baby and links this chat to it"""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO babies DEFAULT VALUES RETURNING baby_id;"
            )
            (baby_id,) = await cur.fetchone()
            await conn.execute(
                "INSERT INTO baby_chats (chat_id, baby_id) VALUES (%s, %s);",
                (chat_id, baby_id),
            )
            return baby_id


async def create_invite(baby_id: int) -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            # housekeeping: drop expired codes
            await conn.execute("DELETE FROM baby_invites WHERE expires_at <= now();")
            for _ in range(5):
                code = _new_code()
                try:
                    async with conn.transaction():  # savepoint
                        await conn.execute(
                            """
                            INSERT INTO baby_invites (code, baby_id, expires_at)
                            VALUES (%s, %s, now() + %s);
                            """,
                            (code, baby_id, INVITE_TTL),
                        )
                    return code
                except UniqueViolation:
                    continue
            raise RuntimeError("Could not generate a unique invite code")


async def redeem_invite(chat_id: int, code: str) -> int | None:
    """Single use: deletes the code and links the chat. None if invalid/expired."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """
                DELETE FROM baby_invites
                WHERE code = %s AND expires_at > now()
                RETURNING baby_id;
                """,
                (code,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            baby_id = row[0]
            await conn.execute(
                """
                INSERT INTO baby_chats (chat_id, baby_id) VALUES (%s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET baby_id = EXCLUDED.baby_id;
                """,
                (chat_id, baby_id),
            )
            return baby_id