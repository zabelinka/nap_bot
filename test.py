import bot
from datetime import date, datetime, timedelta
from bot import TIMEZONE

def main():

    print("Testing bot functions here...")


    # bot.SLEEP_DATA = {123: test_data}
    # print(test_data)

    # t = bot.SleepData()
    # print(t)
    today = date.today()
    test_data = bot.SleepData({
        today: [
            bot.Wake(
                start=datetime(today.year, today.month, today.day, 6, 0, tzinfo=TIMEZONE),
                end=datetime(today.year, today.month, today.day, 9, 50, tzinfo=TIMEZONE),
            ),
            bot.Wake(
                start=datetime(today.year, today.month, today.day, 11, 30, tzinfo=TIMEZONE),
                end=datetime(today.year, today.month, today.day, 15, 30, tzinfo=TIMEZONE),
            ),
            bot.Wake(
                start=datetime(today.year, today.month, today.day, 16, 0, tzinfo=TIMEZONE),
                end=None,
            ),
        ]
    })

    bot.SLEEP_DATA = {
        123: test_data
    }

    print(bot.SLEEP_DATA[123])
    state : bot.SleepState = bot.get_sleep_state(123)
    print(state)
    print()
    print(bot.is_awake(state))
    print(bot.is_sleeping(state))
    print(bot.build_status_text(state))
    


if __name__ == "__main__":
    main()