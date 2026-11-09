from enum import Enum
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

import database as db


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Sweden timezone.
# Change this if your bot should use another timezone.
TIMEZONE = ZoneInfo("Europe/Stockholm")

async def post_init(app: Application) -> None:
    await db.init_db()


async def post_shutdown(app: Application) -> None:
    await db.close_db()


# ============================================================
# DATA STRUCTURES
# ============================================================

class Operation(Enum):
    StartSleep = "StartSleep"
    StopSleep = "StopSleep"
    WakeWindowUpdate = "WakeWindowUpdate"

@dataclass
class TimePeriod:
    start: datetime | None = None
    end: datetime | None = None

@dataclass
class Nap(TimePeriod):
    pass

@dataclass
class Wake(TimePeriod):
    pass


@dataclass
class SleepState:
    is_sleeping: bool = False
    today_naps: list[Nap] = field(default_factory=list)
    last_nap: Nap | None = None
    last_wake: Wake | None = None
    morning_start: datetime | None = None


@dataclass
class ChatState:
    # Message to edit
    status_message_id: int | None = None

    # Temporary time being edited before starting sleep tracking
    pending_sleep_time: datetime | None = None

    pending_operation: Operation | None = None


@dataclass
class WakeWindowDraft:
    windows: list[timedelta]
    modified_index: int | None   # None = adding a new window


# Key = Telegram chat_id
SLEEP_DATA: dict[int, SleepState] = {}
WAKE_DATA: dict[int, WakeWindowDraft] = {}
CHAT_DATA: dict[int, ChatState] = {}


# ============================================================
# TIME HELPERS
# ============================================================

def now() -> datetime:
    """Current time in the bot's configured timezone."""
    return datetime.now(TIMEZONE)


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


def format_window(window: timedelta) -> str:
    total_minutes = max(0, int(window.total_seconds() // 60))

    hours = total_minutes // 60
    minutes = total_minutes % 60

    return f"{hours}h{minutes}min" if hours > 0 else f"{minutes}min"


def elapsed_since(start: datetime, end: datetime | None = None) -> timedelta:
    if end is None:
        end = now()

    return max(timedelta(0), end - start)


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def format_period(period: TimePeriod) -> str:
    start = format_time(period.start) if period.start else "unknown"
    end = format_time(period.end) if period.end else "ongoing"
    return f"{start}–{end}"


def get_today_total_wake_duration(sleep_state: SleepState) -> timedelta:
    if sleep_state.morning_start is None and not sleep_state.today_naps:
        return timedelta(0)

    total_duration = timedelta(0)

    # if there is no morning_start, count from first nap end
    starting_moment = sleep_state.morning_start or sleep_state.today_naps[0].end

    total_duration = elapsed_since(starting_moment)
    total_duration -= get_today_total_sleep_duration(sleep_state)

    return total_duration


def get_today_total_sleep_duration(sleep_state: SleepState) -> timedelta:
    total_duration = timedelta(0)
    for nap in sleep_state.today_naps:
        if nap.start is not None:
            total_duration += elapsed_since(nap.start, nap.end)

    return total_duration

# ============================================================
# STATE HELPERS
# ============================================================

# retrieves data from database and builds SleepState object
async def refresh_sleep_data(chat_id: int):
    rows = await db.get_today_or_open_naps(chat_id)
    naps: list[Nap] = [
        Nap(
            start.astimezone(TIMEZONE),
            end.astimezone(TIMEZONE) if end is not None else None,
        )
        for start, end in rows
    ]

    if not naps:
        SLEEP_DATA[chat_id] = None
        return

    is_sleeping = naps[-1].end is None
    last_nap = naps[-1]

    last_wake_start = next(
        (nap.end for nap in reversed(naps[-2:]) if nap.end is not None),
        None,
    )
    last_wake_end = last_nap.start if is_sleeping else None
    last_wake = Wake(start=last_wake_start, end=last_wake_end)

    today = now().date()

    morning_start = None
    if naps[0].start is not None and naps[0].start.date() != today:
        # Remove night sleep that started on a previous day
        morning_start = naps[0].end
        naps.remove(naps[0])

    SLEEP_DATA[chat_id] = SleepState(
        today_naps=naps,
        is_sleeping=is_sleeping,
        last_nap=last_nap,
        last_wake=last_wake,
        morning_start=morning_start,
    )

# returns saved sleep state if exists or retrieves data from database 
async def get_sleep_state(chat_id: int) -> SleepState:
    if chat_id not in SLEEP_DATA:
        await refresh_sleep_data(chat_id)
    return SLEEP_DATA[chat_id]


def get_wake_data(chat_id: int) -> list[timedelta]:
    if chat_id not in WAKE_DATA:
        WAKE_DATA[chat_id] = []
    return WAKE_DATA[chat_id]


def get_chat_state(chat_id: int) -> ChatState:
    if chat_id not in CHAT_DATA:
        CHAT_DATA[chat_id] = ChatState()
    return CHAT_DATA[chat_id]


async def track_sleep_start(chat_id: int, start_time: datetime):
    nap_id = await db.add_nap(chat_id, start_time.replace(second=0, microsecond=0))
    await refresh_sleep_data(chat_id)  # refresh local state


async def track_sleep_end(chat_id: int, end_time: datetime):
    nap_id = await db.end_nap(chat_id, end_time.replace(second=0, microsecond=0))
    await refresh_sleep_data(chat_id)  # refresh local state


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard(state: SleepState) -> InlineKeyboardMarkup:

    if state is None or not state.is_sleeping:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data="refresh",
                ),
                InlineKeyboardButton(
                    "😴 Start Sleep",
                    callback_data="start_sleep_button",
                )
            ],
        ])

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data="refresh",
            ),
            InlineKeyboardButton(
                "⏹ Stop Sleep",
                callback_data="stop_sleep",
            )
        ],
    ])


def time_picker_keyboard() -> InlineKeyboardMarkup:
    """
    Keyboard for adjusting the 'Now' time.

    Example:
        12:00
        -5 min   +5 min
        -1 min   +1 min
        Done
    """

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "− 30 min",
                callback_data="sleep_time:-30",
            ),
            InlineKeyboardButton(
                "+ 30 min",
                callback_data="sleep_time:+30",
            ),
        ],
        [
            InlineKeyboardButton(
                "− 5",
                callback_data="sleep_time:-5",
            ),
            InlineKeyboardButton(
                "− 1",
                callback_data="sleep_time:-1",
            ),
            InlineKeyboardButton(
                "+ 1",
                callback_data="sleep_time:+1",
            ),
            InlineKeyboardButton(
                "+ 5",
                callback_data="sleep_time:+5",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="cancel_sleep",
            ),
            InlineKeyboardButton(
                "✅ Save",
                callback_data="confirm_sleep",
            )
        ],
    ])


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Set wake windows",
                callback_data="set_wake_windows",
            ),
        ],
    ])


# ============================================================
# STATUS TEXT
# ============================================================

def build_status_text(state: SleepState) -> str:
    if state is None :
        return (
        "👶 <b>No data registered</b>\n\n"
        "Start tracking your baby sleep now!")

    last_nap: Nap = state.last_nap
    if state.is_sleeping:
        duration = format_duration(elapsed_since(last_nap.start))

        return (
            "😴 <b>Sleeping</b>\n\n"
            f"Started: <b>{format_time(last_nap.start)}</b>\n"
            f"Duration: <b>{duration}</b>\n\n"
        )

    duration = format_duration(elapsed_since(last_nap.end))
    total_duration = format_duration(get_today_total_wake_duration(state))

    return (
        "👶 <b>Awake</b>\n\n"
        f"Awake since: <b>{format_time(last_nap.end)}</b>\n"
        f"Wake time: <b>{duration}</b>\n\n"
        f"Total Wake for Today: <b>{total_duration}</b>\n\n"
    )


def build_last_completed_period_text(state: SleepState) -> str:
    if state is None or state.last_wake.start is None:
        return ("No previous data recorded")

    if state.is_sleeping:
        # Build text about last wake window
        wake: Wake = state.last_wake
        duration = format_duration(elapsed_since(wake.start, wake.end))
        total_duration = format_duration(get_today_total_wake_duration(state))

        return (
            "✅👶<b>Wake Window Completed</b>\n\n"
            f"<b>{format_time(wake.start)} - {format_time(wake.end)} </b> ({duration})\n"
            f"Total Wake for Today: <b>{total_duration}</b>\n\n"
        )

    # Awake. Build text about last nap
    nap: Nap = state.last_nap
    duration = format_duration(elapsed_since(nap.start, nap.end))
    total_duration = format_duration(get_today_total_sleep_duration(state))

    return (
        "✅😴 <b>Nap Completed</b>\n\n"
        f"<b>{format_time(nap.start)} - {format_time(nap.end)}</b> ({duration})\n"
        f"Total Sleep for Today: <b>{total_duration}</b>\n\n"
    )


# ============================================================
# MESSAGES
# ============================================================

async def send_status(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
):
    state = await get_sleep_state(chat_id)

    message = await context.bot.send_message(
        chat_id=chat_id,
        text=build_status_text(state),
        parse_mode="HTML",
        reply_markup=main_keyboard(state),
    )


async def edit_status(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Edit the existing status message.
    """

    state = get_chat_state(chat_id)

    if state.status_message_id is None:
        await send_status(chat_id, context)
        return

    sleep_state = await get_sleep_state(chat_id)
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=state.status_message_id,
            text=build_status_text(sleep_state),
            parse_mode="HTML",
            reply_markup=main_keyboard(sleep_state),
        )

    except Exception as e:
        # The message might have been deleted.
        # In that case create a new one.
        print(f"Could not edit status message: {e}")

        await send_status(chat_id, context)


async def refresh(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    state = get_chat_state(chat_id)
    state.status_message_id = query.message.message_id

    await edit_status(chat_id, context)


async def show_last_completed_period(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Edit the existing status message with the last completed period (nap or wake)
    """

    state = get_chat_state(chat_id)

    sleep_state = await get_sleep_state(chat_id)
    message_text = build_last_completed_period_text(sleep_state)
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=state.status_message_id,
            text=message_text,
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"Could not edit status message: {e}")

        await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            parse_mode="HTML"
        )


# ============================================================
# TRACK SLEEP
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id
    await send_status(chat_id, context)


async def start_sleep_command(update, context):
    chat_id = update.message.chat_id
    sleep_state = await get_sleep_state(chat_id)
    if sleep_state is not None and sleep_state.is_sleeping:
        print("Error. The baby is already sleeping.")
        # TODO show warning to user
        return

    chat_state = get_chat_state(chat_id)
    chat_state.pending_operation = Operation.StartSleep
    chat_state.pending_sleep_time = now()

    await show_time_picker(chat_id, context)


async def start_sleep_button(update, context):
    query = update.callback_query
    chat_id = query.message.chat_id

    sleep_state = await get_sleep_state(chat_id)
    if sleep_state is not None and sleep_state.is_sleeping:
        print("Error. The baby is already sleeping.")
        # TODO show warning to user
        return

    await query.answer()
    chat_state = get_chat_state(chat_id)
    chat_state.pending_operation = Operation.StartSleep
    chat_state.pending_sleep_time = now()
    chat_state.status_message_id = query.message.message_id

    await show_time_picker(chat_id, context)


def get_time_picker_text(chat_id: int, selected_time: datetime):
    operation = get_chat_state(chat_id).pending_operation
    if operation == Operation.StopSleep:
        return ("🕐 <b>SLEEP END</b>\n\n"
                f"<b>{format_time(selected_time)}</b>\n\n"
                "Adjust the time if needed")

    if operation == Operation.StartSleep:
        return ("🕐 <b>SLEEP START</b>\n\n"
                f"<b>{format_time(selected_time)}</b>\n\n"
                "Adjust the time if needed")

    if operation == Operation.WakeWindowUpdate:
        return ("🕐 <b>SET WAKE WINDOW</b>\n\n"
                f"<b>{format_time(selected_time)}</b>\n\n"
                "Adjust the duration with buttons below")


async def show_time_picker(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,

):
    chat_state = get_chat_state(chat_id)

    time_picker_text = get_time_picker_text(
        chat_id,
        chat_state.pending_sleep_time,
    )

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=chat_state.status_message_id,
            text=time_picker_text,
            parse_mode="HTML",
            reply_markup=time_picker_keyboard(),
        )
    except Exception as e:
        print(f"Could not edit status message: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=time_picker_text,
            parse_mode="HTML",
            reply_markup=time_picker_keyboard(),
        )


async def adjust_sleep_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    chat_state = get_chat_state(chat_id)

    if chat_state.pending_sleep_time is None:
        return

    # callback_data is:
    # sleep_time:+1
    # sleep_time:-5

    _, amount = query.data.split(":")

    minutes = int(amount)
    chat_state.pending_sleep_time += timedelta(minutes=minutes)

    await query.edit_message_text(
        text=(get_time_picker_text(chat_id, chat_state.pending_sleep_time)),
        parse_mode="HTML",
        reply_markup=time_picker_keyboard(),
    )


async def confirm_sleep(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    chat_state = get_chat_state(chat_id)

    if chat_state.pending_sleep_time is None:
        return

    if chat_state.pending_operation == Operation.WakeWindowUpdate:
        wake_window = timedelta(
            hours=chat_state.pending_sleep_time.hour,
            minutes=chat_state.pending_sleep_time.minute,
        )
        if wake_window > timedelta(minutes=0):
            window_draft = get_wake_data(chat_id)
            if window_draft.modified_index is not None:
                # Update existing wake window
                window_draft.windows[window_draft.modified_index] = wake_window
            else:
                window_draft.windows.append(wake_window)
            await db.set_wake_windows(chat_id, window_draft.windows)

        chat_state.pending_sleep_time = None
        chat_state.pending_operation = None

        await set_wake_windows(update, context)
        return

    start_time = chat_state.pending_sleep_time

    # Update sleep data
    sleep_state = await get_sleep_state(chat_id)
    if sleep_state is None or not sleep_state.is_sleeping:
        await track_sleep_start(chat_id, start_time)
    else:
        await track_sleep_end(chat_id, start_time)

    # Clear temporary picker state
    chat_state.pending_sleep_time = None

    # Update current message to show the last completed period
    chat_state.status_message_id = query.message.message_id
    await show_last_completed_period(chat_id, context)
    
    chat_state.status_message_id = None
    chat_state.pending_sleep_time = None
    chat_state.pending_operation = None

    # Send a new message with a current ongoing period
    await send_status(chat_id, context)


async def cancel_sleep(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    chat_state = get_chat_state(chat_id)

    if chat_state.pending_operation == Operation.WakeWindowUpdate:
        chat_state.pending_sleep_time = None
        chat_state.pending_operation = None

        await set_wake_windows(update, context)
        return

    chat_state.status_message_id = query.message.message_id
    chat_state.pending_sleep_time = None
    chat_state.pending_operation = None

    await edit_status(chat_id, context)


async def stop_sleep(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    chat_id = query.message.chat_id
    sleep_state = await get_sleep_state(chat_id)
    chat_state = get_chat_state(chat_id)

    if not sleep_state.is_sleeping:
        print("Error. The baby is already awake.")
        return

    chat_state.status_message_id = query.message.message_id
    chat_state.pending_operation = Operation.StopSleep
    chat_state.pending_sleep_time = now()

    await query.answer()
    await show_time_picker(chat_id, context)


# ============================================================
# SETTINGS
# ============================================================

async def settings_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text="<b>Settings</b>",
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
    )


async def set_wake_windows(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    windows = await db.get_wake_windows(chat_id)
    WAKE_DATA[chat_id] = WakeWindowDraft(windows=windows, modified_index=None)

    keyboard = [
        [InlineKeyboardButton(f"{i+1}: {format_window(w)}", callback_data=f"wake:{i}")]
        for i, w in enumerate(windows)
    ]
    keyboard.append([InlineKeyboardButton("➕ Add Wake Window", callback_data="wake:add")])

    await query.edit_message_text(
        text="<b>Wake Windows</b>\n\n"
             "Tap the wake window to edit or add a new one.\n\n",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def edit_wake_window(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split(":")[1])

    chat_id = query.message.chat_id
    window_draft = get_wake_data(chat_id)
    window_draft.modified_index = index
    windows = window_draft.windows

    initial_time = windows[index] if index < len(windows) else timedelta(minutes=0)

    chat_state = get_chat_state(chat_id)
    chat_state.pending_sleep_time =  datetime.now(TIMEZONE).replace(
        hour=int(initial_time.total_seconds() // 3600),
        minute=int(initial_time.total_seconds() % 3600 // 60),
        second=0, microsecond=0,
    )
    chat_state.pending_operation = Operation.WakeWindowUpdate
    chat_state.status_message_id = query.message.message_id

    await show_time_picker(chat_id, context)


async def add_wake_window(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    chat_state = get_chat_state(chat_id)
    chat_state.pending_sleep_time = datetime.now(TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    chat_state.pending_operation = Operation.WakeWindowUpdate
    chat_state.status_message_id = query.message.message_id

    await show_time_picker(chat_id, context)


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("start_sleep", start_sleep_command)
    )

    application.add_handler(
        CommandHandler("settings", settings_command)
    )

    # Callback buttons
    application.add_handler(
        CallbackQueryHandler(
            start_sleep_button,
            pattern=r"^start_sleep_button$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            adjust_sleep_time,
            pattern=r"^sleep_time:[+-]\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            confirm_sleep,
            pattern=r"^confirm_sleep$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cancel_sleep,
            pattern=r"^cancel_sleep$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            stop_sleep,
            pattern=r"^stop_sleep$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            refresh,
            pattern=r"^refresh$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            set_wake_windows,
            pattern=r"^set_wake_windows$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            add_wake_window,
            pattern=r"^wake:add$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            edit_wake_window,
            pattern=r"^wake:\d+$",
        )
    )

    print("Baby sleep tracker is running...")

    application.run_polling()


if __name__ == "__main__":
    main()
