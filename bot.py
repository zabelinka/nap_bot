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
    start: datetime | None = None
    end: datetime | None = None

@dataclass
class Nap(TimePeriod):
    pass


@dataclass
class Wake(TimePeriod):
    pass


@dataclass
class ChatState:
    # Message containing the current timer/status.
    # We edit this message to report a completed period (nap or wake)
    status_message_id: int | None = None

    # Temporary time being edited before starting sleep tracking
    pending_sleep_time: datetime | None = None


# Storing wake periods instead of Naps for easier night transition calculation
@dataclass
class SleepState:
    is_sleeping: bool = False
    today_naps: list[Nap] = field(default_factory=list)
    last_nap: Nap | None = None
    last_wake: Wake | None = None
    morning_start: datetime | None = None


# For each chat_id, we store a list of Naps
class SleepData(list[Nap]):
    def __str__(self) -> str:
        if not self:
            return "SleepData (empty)"

        naps = ",\n  ".join(format_period(nap) for nap in self)
        return f"SLEEP_DATA:\n  {naps}"


# Key = Telegram chat_id
SLEEP_DATA: dict[int, SleepData] = {}
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
    total_duration = timedelta(0)

    if sleep_state.morning_start:
        total_duration = elapsed_since(sleep_state.morning_start)
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

# returns list of naps for current user(chat_id)
def get_sleep_data(chat_id: int) -> SleepData:
    if chat_id not in SLEEP_DATA:
        print(f"No sleep data for chat_id={chat_id}")
        SLEEP_DATA[chat_id] = SleepData()
    return SLEEP_DATA[chat_id]


def get_sleep_state(chat_id: int) -> SleepState:
    sleep_data = get_sleep_data(chat_id)
    if not sleep_data:
        return None

    is_sleeping = sleep_data[-1].end is None
    last_nap = sleep_data[-1]

    last_wake_start = next(
        (nap.end for nap in reversed(sleep_data[-2:]) if nap.end is not None),
        None,
    )
    last_wake_end = last_nap.start if is_sleeping else None
    last_wake = Wake(start=last_wake_start, end=last_wake_end)

    today = now().date()

    # TODO when loading data from SQL no need to go through all the days, stop when not today
    today_naps = []
    morning_start = None
    for nap in reversed(sleep_data):
        if nap.start is not None and nap.start.date() == today:
            today_naps.append(nap)
        elif (
            nap.start is not None
            and nap.start.date() != today
            and nap.end is not None
            and nap.end.date() == today
        ):
            morning_start = nap.end
            break

    today_naps.sort(key=lambda nap: nap.start)

    return SleepState(
        today_naps=today_naps,
        is_sleeping=is_sleeping,
        last_nap=last_nap,
        last_wake=last_wake,
        morning_start=morning_start,
    )


def get_chat_state(chat_id: int) -> SleepState:
    if chat_id not in CHAT_DATA:
        CHAT_DATA[chat_id] = ChatState()

    return CHAT_DATA[chat_id]


def track_sleep_start(chat_id: int, start_time: datetime):
    sleep_data = get_sleep_data(chat_id)
    sleep_data.append(Nap(start=start_time, end=None))


def track_sleep_end(chat_id: int, end_time: datetime):
    sleep_data = get_sleep_data(chat_id)
    if not sleep_data or sleep_data[-1].end is not None:
        print("Error: No ongoing sleep period to end.")
        return
    sleep_data[-1].end = end_time


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard(state: SleepState) -> InlineKeyboardMarkup:

    if state is None or not state.is_sleeping:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "😴 Start Sleep",
                    callback_data="start_sleep_button",
                )
            ],
            # [
            #     InlineKeyboardButton(
            #         "🔄 Refresh",
            #         callback_data="refresh",
            #     )
            # ],
        ])

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⏹ Stop Sleep",
                callback_data="stop_sleep",
            )
        ],
        # [
        #     InlineKeyboardButton(
        #         "🔄 Refresh",
        #         callback_data="refresh",
        #     )
        # ],
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

    return (
        "👶 <b>Awake</b>\n\n"
        f"Awake since: <b>{format_time(last_nap.end)}</b>\n"
        f"Wake time: <b>{duration}</b>\n\n"
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
    state = get_sleep_state(chat_id)

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

    No new Telegram message is created.
    """

    state = get_chat_state(chat_id)

    if state.status_message_id is None:
        await send_status(chat_id, context)
        return

    sleep_state = get_sleep_state(chat_id)
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


async def show_last_completed_period(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Edit the existing status message with the last completed period (nap or wake)
    """

    state = get_chat_state(chat_id)

    sleep_state = get_sleep_state(chat_id)
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
    state = get_sleep_state(update.message.chat_id)
    if state is not None and state.is_sleeping:
        print("Error. The baby is already sleeping.")
        # TODO show warning to user
        return

    await show_time_picker(update.message.chat_id, context)

async def start_sleep_button(update, context):

    query = update.callback_query
    chat_id = query.message.chat_id

    state = get_sleep_state(chat_id)
    if state is not None and state.is_sleeping:
        print("Error. The baby is already sleeping.")
        # TODO show warning to user
        return

    await query.answer()

    chat_state = get_chat_state(chat_id)
    chat_state.status_message_id = query.message.message_id
    await show_time_picker(chat_id, context)


def get_time_picker_text(selected_time: datetime, sleep_state: SleepState | None):
    if sleep_state is not None and sleep_state.is_sleeping:
        return ("🕐 <b>SLEEP END</b>\n\n"
                f"<b>{format_time(selected_time)}</b>\n\n"
                "Adjust the time if needed")

    return ("🕐 <b>SLEEP START</b>\n\n"
            f"<b>{format_time(selected_time)}</b>\n\n"
            "Adjust the time if needed")





async def show_time_picker(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE
):
    sleep_state = get_sleep_state(chat_id)
    chat_state = get_chat_state(chat_id)

    # Start from current time.
    chat_state.pending_sleep_time = now()
    time_picker_text = get_time_picker_text(chat_state.pending_sleep_time, sleep_state)

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=chat_state.status_message_id,
            text=time_picker_text,
            parse_mode="HTML",
            reply_markup=time_picker_keyboard(),
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=time_picker_text,
            parse_mode="HTML",
            reply_markup=time_picker_keyboard()
        )

async def adjust_sleep_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    state = get_chat_state(chat_id)

    if state.pending_sleep_time is None:
        return

    # callback_data is:
    # sleep_time:+1
    # sleep_time:-5

    _, amount = query.data.split(":")

    minutes = int(amount)

    state.pending_sleep_time += timedelta(minutes=minutes)
    sleep_state = get_sleep_state(chat_id)

    await query.edit_message_text(
        text=(get_time_picker_text(state.pending_sleep_time, sleep_state)),
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
    sleep_state = get_sleep_state(chat_id)

    if chat_state.pending_sleep_time is None:
        return

    start_time = chat_state.pending_sleep_time

    # Update sleep data
    if sleep_state is None or not sleep_state.is_sleeping:
        track_sleep_start(chat_id, start_time)
    else:
        track_sleep_end(chat_id, start_time)

    # Clear temporary picker state
    chat_state.pending_sleep_time = None

    # Update current message to show the last completed period
    chat_state.status_message_id = query.message.message_id
    await show_last_completed_period(chat_id, context)

    chat_state.status_message_id = None

    # Send a new message with a current ongoing period
    await send_status(chat_id, context)

async def cancel_sleep(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    state = get_chat_state(chat_id)

    state.pending_sleep_time = None

    state.status_message_id = query.message.message_id

    await edit_status(chat_id, context)



async def stop_sleep(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    chat_id = query.message.chat_id
    sleep_state = get_sleep_state(chat_id)
    chat_state = get_chat_state(chat_id)

    if not sleep_state.is_sleeping:
        print("Error. The baby is already awake.")
        return

    chat_state.status_message_id = query.message.message_id
    
    await query.answer()
    await show_time_picker(chat_id, context)


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

    application.add_handler(
        CommandHandler("start_sleep", start_sleep_command)
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

    print("Baby sleep tracker is running...")

    application.run_polling()


if __name__ == "__main__":
    main()