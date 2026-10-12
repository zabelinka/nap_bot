import bot
from datetime import date, datetime, timedelta
from bot import TIMEZONE

def main():

    print("Testing bot functions here...")

    bot.SLEEP_DATA = {
    123: {
            date(2026, 10, 9): [
                bot.Wake(start=datetime(2026, 10, 9, 6, 0, tzinfo=TIMEZONE), end=datetime(2026, 10, 9, 9, 50, tzinfo=TIMEZONE)),
                bot.Wake(start=datetime(2026, 10, 9, 11, 30, tzinfo=TIMEZONE), end=datetime(2026, 10, 9, 15, 30, tzinfo=TIMEZONE)),
            ],
            date(2026, 10, 8): [
                bot.Wake(start=datetime(2026, 10, 8, 6, 10), end=datetime(2026, 10, 8, 10, 0)),
            ],
        },
    }

    # print(bot.CHAT_DATA)
    state : bot.SleepState = bot.get_sleep_state(123)
    print(state)
    print()
    print(bot.is_awake(state))
    print(bot.is_sleeping(state))
    print(bot.build_status_text(state))
    


if __name__ == "__main__":
    main()