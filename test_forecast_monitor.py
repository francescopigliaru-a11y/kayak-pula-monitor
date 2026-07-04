import unittest
from datetime import datetime, timedelta

from forecast_monitor import (
    FORECAST_WINDOW_HOURS,
    LOCAL_TZ,
    forecast_window_bounds,
    rows_in_forecast_window,
)
from nearshore_wave_model_v2 import nearshore_wave, nearshore_wave_details


class ForecastWindowTests(unittest.TestCase):
    def test_run_at_18_starts_next_day_at_08_for_36_hours(self) -> None:
        run_time = datetime(2026, 7, 3, 18, 0, tzinfo=LOCAL_TZ)
        start, end = forecast_window_bounds(run_time)

        source_start = datetime(2026, 7, 4, 0, 0, tzinfo=LOCAL_TZ)
        rows = [
            {"time": (source_start + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M")}
            for hour in range(72)
        ]
        selected = rows_in_forecast_window(rows, start, end)

        self.assertEqual(start.isoformat(timespec="minutes"), "2026-07-04T08:00+02:00")
        self.assertEqual(len(selected), FORECAST_WINDOW_HOURS)
        self.assertEqual(selected[0]["time"], "2026-07-04T08:00")
        self.assertEqual(selected[-1]["time"], "2026-07-05T19:00")

    def test_model_details_preserve_result_and_expose_floor(self) -> None:
        details = nearshore_wave_details(0.42, 4.7, 278, wind_speed=3.8 / 3.6)

        self.assertIsNotNone(details)
        self.assertEqual(nearshore_wave(0.42, 4.7, 278, wind_speed=3.8 / 3.6), 0.05)
        self.assertEqual(details["Hs_final_m"], 0.05)
        self.assertLess(details["Hs_raw_m"], 0.05)
        self.assertTrue(details["floor_applied"])


if __name__ == "__main__":
    unittest.main()
