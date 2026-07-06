import math
import unittest
from datetime import datetime, timedelta

from forecast_monitor import (
    FORECAST_WINDOW_HOURS,
    LOCAL_TZ,
    forecast_window_bounds,
    is_good_kayak_window,
    rows_in_forecast_window,
)
from nearshore_wave_model_v2 import nearshore_wave, nearshore_wave_details
from nearshore_wave_model_v3 import (
    nearshore_wave_v3_details,
    signed_angle_difference,
    wavenumber,
)


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

    def test_v3_dispersion_solution_is_physical(self) -> None:
        period = 4.6
        depth = 3.5
        k = wavenumber(period, depth)
        omega = 2 * 3.141592653589793 / period

        self.assertAlmostEqual(omega**2, 9.81 * k * math.tanh(k * depth), places=10)

    def test_v3_angle_difference_wraps_at_north(self) -> None:
        self.assertEqual(signed_angle_difference(350, 10), -20)
        self.assertEqual(signed_angle_difference(10, 350), 20)

    def test_v3_does_not_collapse_observed_july_case_to_floor(self) -> None:
        details = nearshore_wave_v3_details(
            0.36,
            4.6,
            279,
            bearing=65,
            depth=3.5,
            wind_speed=10 / 3.6,
            wind_direction=202.5,
            swell_height=0.32,
            swell_period=3.95,
            swell_direction=280,
            wind_wave_height=0.08,
            wind_wave_period=1.55,
            wind_wave_direction=265,
        )

        self.assertIsNotNone(details)
        self.assertGreaterEqual(details["Hs_final_m"], 0.18)
        self.assertLessEqual(details["Hs_final_m"], 0.30)
        self.assertTrue(details["sheltered_fallback_used"])
        self.assertGreater(details["local_wind_wave"]["height_m"], 0.10)

    def test_v3_normal_incidence_uses_shoaling_without_directional_penalty(self) -> None:
        details = nearshore_wave_v3_details(
            0.50,
            5.0,
            155,
            bearing=65,
            depth=3.5,
            wind_speed=0,
        )

        self.assertIsNotNone(details)
        self.assertTrue(details["total_component"]["direct_ray"])
        self.assertAlmostEqual(details["total_component"]["Kr"], 1.0, places=8)
        self.assertGreater(details["Hs_final_m"], 0.35)

    def test_alert_threshold_uses_unrounded_v3_height(self) -> None:
        row = {
            "Hs_nearshore_m": 0.20,
            "wind_speed_kmh": 10.0,
            "breaking": "false",
            "_model": {"Hs_raw_m": 0.204},
        }

        self.assertFalse(is_good_kayak_window(row))


if __name__ == "__main__":
    unittest.main()
