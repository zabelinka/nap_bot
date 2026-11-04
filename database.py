import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg_pool import AsyncConnectionPool


DATABASE_URL = os.environ["DATABASE_URL"]

TIMEZONE = ZoneInfo("Europe/Stockholm")

pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)


async def init_db() -> None:
    await pool.open()
    async with pool.connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS naps (
                id         SERIAL PRIMARY KEY,
                baby_id    INTEGER NOT NULL,
                start_time TIMESTAMPTZ NOT NULL,
                end_time   TIMESTAMPTZ,
                CHECK (end_time IS NULL OR end_time > start_time)
            );
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_naps_baby_start "
            "ON naps (baby_id, start_time DESC);"
        )


        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wake_windows (
                id         SERIAL PRIMARY KEY,
                baby_id    INTEGER NOT NULL,
                windows    INTERVAL[] NOT NULL DEFAULT '{}'
            );
            """
        )

        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_wake_windows_baby "
            "ON wake_windows (baby_id);"
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
