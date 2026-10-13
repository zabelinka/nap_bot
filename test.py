import unittest
import bot

from datetime import date, datetime, timedelta
from bot import TIMEZONE

def my_test():

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
            # bot.Wake(
            #     start=datetime(today.year, today.month, today.day, 16, 0, tzinfo=TIMEZONE),
            #     end=None,
            # ),
        ]
    })

    bot.SLEEP_DATA = {
        123: test_data
    }

    print(bot.SLEEP_DATA[123])
    state : bot.SleepState = bot.get_sleep_state(123)
    # print(state)
    print()
    print("Awake" if bot.is_awake(state) else "Sleeping")
    bot.track_sleep_end(123, datetime(today.year, today.month, today.day, 16, 0, tzinfo=TIMEZONE))
    print(bot.SLEEP_DATA[123])
    print("Awake" if bot.is_awake(state) else "Sleeping")

    bot.track_sleep_start(123, datetime(today.year, today.month, today.day, 17, 10, tzinfo=TIMEZONE))
    print(bot.SLEEP_DATA[123])
    print("Awake" if bot.is_awake(state) else "Sleeping")

    print("---------------------------------")



    bot.get_sleep_state(555)
    print(bot.SLEEP_DATA[555])  # should be empty
    bot.track_sleep_start(555, datetime(today.year, today.month, today.day, 9, 0, tzinfo=TIMEZONE))
    print(bot.SLEEP_DATA[555])  # should be SLEEP_DATA:  2026-10-13: [unknown–09:00]

    bot.track_sleep_end(555, datetime(today.year, today.month, today.day, 10, 0, tzinfo=TIMEZONE))
    print(bot.SLEEP_DATA[555]) # should be SLEEP_DATA:  2026-10-13: [unknown–09:00, 10:00–ongoing]

class TestSleepTracking(unittest.TestCase):
    def setUp(self):
        self.today = date.today()
        self.user_id = 555
        bot.SLEEP_DATA = {}

    def test_track_sleep_start_and_end_for_new_user(self):
        bot.get_sleep_state(self.user_id)
        self.assertIn(self.user_id, bot.SLEEP_DATA)

        bot.track_sleep_start(
            self.user_id,
            datetime(self.today.year, self.today.month, self.today.day, 9, 0, tzinfo=TIMEZONE),
        )
        rendered = str(bot.SLEEP_DATA[self.user_id])
        self.assertIn(f"{self.today}: [unknown–09:00]", rendered)

        bot.track_sleep_end(
            self.user_id,
            datetime(self.today.year, self.today.month, self.today.day, 10, 0, tzinfo=TIMEZONE),
        )
        rendered = str(bot.SLEEP_DATA[self.user_id])
        self.assertIn(f"{self.today}: [unknown–09:00, 10:00–ongoing]", rendered)

if __name__ == "__main__":
    unittest.main()