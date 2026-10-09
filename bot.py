import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Sweden timezone.
# Change this if your bot should use another timezone.
TIMEZONE = ZoneInfo("Europe/Stockholm")


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class TimePeriod:
    start: datetime
    end: datetime | None = None

@dataclass
class Nap(TimePeriod):
    pass


@dataclass
class Wake(TimePeriod):
    pass


# @dataclass
# class NapState:
#     today_naps: list[Nap] = {}
#     last_nap: Nap | None = None
#     is_sleeping: boolean = False
#     morning_wake: datetime | None = None
#     # Message containing the current timer/status.
#     # We edit this message instead of sending a new one.
#     status_message_id: int | None = None

#     # Temporary time being edited before starting sleep.
#     pending_sleep_start: datetime | None = None

#     # Message containing the time picker.
#     time_picker_message_id: int | None = None

# Storing wake periods instead of Naps for easier night transition calculation

@dataclass
class WakeState:
    today_wakes: list[Wake] = field(default_factory=list)
    last_wake: Wake | None = None
    morning_wake: datetime | None = None

def is_awake(state: WakeState) -> boolean:
    if state.today_wakes is None:
        return False
    if state.today_wakes[-1].end is None:
        return True
    return False
    
def is_sleeping(state: WakeState) -> boolean:
    return not is_awake(state)

# For each date storing list of Wake periods
# Key = Telegram chat_id
CHAT_DATA: dict[int, dict[date, list[Wake]]] = {}

def set_chat_data(chat_data) :
    CHAT_DATA = chat_data

def get_state(chat_id: int) -> WakeState:
    if chat_id not in CHAT_DATA:
        return None     # TODO handle it better

    today = now().date()
    today_wakes = CHAT_DATA.get(chat_id, {}).get(today, [])
    if today_wakes:
        morning_wake = today_wakes[0].start

    last_wake = today_wakes[-1] if today_wakes else None
    return WakeState(today_wakes = today_wakes, last_wake = last_wake, morning_wake = morning_wake)


# ============================================================
# TIME HELPERS
# ============================================================

def now() -> datetime:
    """Current time in the bot's configured timezone."""
    return datetime.now(TIMEZONE)


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def format_duration(delta: timedelta) -> str:
    """
    Format duration as HH:MM.

    Seconds are deliberately ignored because the bot tracks
    baby sleep/wake time at minute resolution.
    """
    total_minutes = max(0, int(delta.total_seconds() // 60))

    hours = total_minutes // 60
    minutes = total_minutes % 60

    return f"{hours:02d}:{minutes:02d}"


def elapsed_since(start: datetime, end: datetime | None = None) -> timedelta:
    if end is None:
        end = now()

    return max(timedelta(0), end - start)

# ============================================================
# STATUS TEXT
# ============================================================

def build_status_text(state: WakeState) -> str:

    if is_sleeping(state):

        nap: Nap = Nap(start = state.last_wake.end, end = None)
        duration = format_duration(elapsed_since(nap.start))

        return (
            "😴 <b>Sleeping</b>\n\n"
            f"Started: <b>{format_time(nap.start)}</b>\n"
            f"Duration: <b>{duration}</b>\n\n"
        )

    wake = state.last_wake
    duration = format_duration(elapsed_since(wake.start))

    return (
        "👶 <b>Awake</b>\n\n"
        f"Awake since: <b>{format_time(wake.start)}</b>\n"
        f"Wake time: <b>{duration}</b>\n\n"
    )



# ============================================================
# /start
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    # /start acts as a refresh when the user returns to the bot.
    await send_status(chat_id, context)


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start_command)
    )

    print("Baby sleep tracker is running...")

    application.run_polling()


if __name__ == "__main__":
    main()