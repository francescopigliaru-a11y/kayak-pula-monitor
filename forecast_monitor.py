"""Daily forecast monitor for nearshore kayak conditions at Santa Margherita di Pula."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from nearshore_wave_model_v2 import nearshore_wave


LAT = 38.930
LON = 8.924
DISTANCE_FROM_SHORE_M = 350
COAST_BEARING_DEG = 65
LOCAL_DEPTH_M = 3.5
BEACH_SLOPE_BETA = 0.015
TIMEZONE = "Europe/Rome"
LOCAL_TZ = ZoneInfo(TIMEZONE)

MARINE_ENDPOINT = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
OUTPUT_DIR = Path("outputs")
CSV_PATH = OUTPUT_DIR / "latest_forecast.csv"
REPORT_PATH = OUTPUT_DIR / "latest_forecast.md"
GOOD_WINDOW_PATH = OUTPUT_DIR / "good_window.md"
GOOD_WINDOW_STATUS_PATH = OUTPUT_DIR / "good_window_status.txt"

GOOD_HS_THRESHOLD_M = 0.20
GOOD_WIND_THRESHOLD_KMH = 20.0
MAX_GOOD_ROWS_IN_REPORT = 12

OUTPUT_COLUMNS = [
    "time",
    "Hs_nearshore_m",
    "Tp_s",
    "Dir",
    "breaking",
    "temperature_C",
    "wind_speed_kmh",
    "wind_dir",
]


def fetch_json(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch JSON from Open-Meteo and surface clear API errors."""
    url = f"{endpoint}?{urlencode(params, doseq=True)}"
    request = Request(url, headers={"User-Agent": "kayak-pula-monitor/1.0"})

    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Open-Meteo HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Open-Meteo: {exc.reason}") from exc

    data = json.loads(payload)
    if data.get("error"):
        raise RuntimeError(f"Open-Meteo API error: {data.get('reason', 'unknown error')}")
    return data


def safe_float(value: Any) -> float | None:
    """Convert a value to float while preserving missing values."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_number(value: float | None, digits: int = 2) -> str:
    """Format optional numbers for Markdown and CSV output."""
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def cardinal_direction(degrees: float | None) -> str:
    """Convert degrees to a 16-point compass direction."""
    if degrees is None:
        return ""

    labels = [
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    ]
    index = int((degrees % 360) / 22.5 + 0.5) % 16
    return labels[index]


def is_breaking_true(value: Any) -> bool:
    """Return True only for explicit breaking values."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "yes", "1"}


def hourly_by_time(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return hourly Open-Meteo data keyed by timestamp."""
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    rows: dict[str, dict[str, Any]] = {}

    for index, timestamp in enumerate(times):
        row: dict[str, Any] = {}
        for key, values in hourly.items():
            if key == "time":
                continue
            if isinstance(values, list) and index < len(values):
                row[key] = values[index]
            else:
                row[key] = None
        rows[timestamp] = row

    return rows


def fetch_forecasts() -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch wave and weather forecasts from Open-Meteo."""
    marine_params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "wave_height,wave_direction,wave_period",
        "timezone": TIMEZONE,
        "forecast_days": 5,
        "cell_selection": "sea",
    }
    weather_params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "timezone": TIMEZONE,
        "forecast_days": 5,
    }

    return fetch_json(MARINE_ENDPOINT, marine_params), fetch_json(WEATHER_ENDPOINT, weather_params)


def build_rows(marine_data: dict[str, Any], weather_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge wave and weather data and calculate nearshore wave height."""
    marine_rows = hourly_by_time(marine_data)
    weather_rows = hourly_by_time(weather_data)
    all_times = sorted(set(marine_rows) | set(weather_rows))

    rows: list[dict[str, Any]] = []
    break_height = 0.78 * LOCAL_DEPTH_M

    for timestamp in all_times:
        marine = marine_rows.get(timestamp, {})
        weather = weather_rows.get(timestamp, {})

        hs_off = safe_float(marine.get("wave_height"))
        tp = safe_float(marine.get("wave_period"))
        wave_dir_deg = safe_float(marine.get("wave_direction"))
        wind_speed_kmh = safe_float(weather.get("wind_speed_10m"))
        wind_speed_ms = None if wind_speed_kmh is None else wind_speed_kmh / 3.6

        hs_nearshore = nearshore_wave(
            hs_off,
            tp,
            wave_dir_deg,
            bearing=COAST_BEARING_DEG,
            depth=LOCAL_DEPTH_M,
            wind_speed=wind_speed_ms or 0,
        )

        wind_dir_deg = safe_float(weather.get("wind_direction_10m"))
        temperature = safe_float(weather.get("temperature_2m"))
        breaking = "true" if hs_nearshore is not None and hs_nearshore >= break_height else "false"

        rows.append(
            {
                "time": timestamp,
                "Hs_nearshore_m": hs_nearshore,
                "Tp_s": tp,
                "Dir": cardinal_direction(wave_dir_deg),
                "breaking": breaking,
                "temperature_C": temperature,
                "wind_speed_kmh": wind_speed_kmh,
                "wind_dir": cardinal_direction(wind_dir_deg),
                "_wave_height_offshore_m": hs_off,
                "_wave_direction_deg": wave_dir_deg,
                "_wind_gusts_kmh": safe_float(weather.get("wind_gusts_10m")),
            }
        )

    return rows


def write_csv(rows: list[dict[str, Any]]) -> None:
    """Write the required CSV output."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "time": row["time"],
                    "Hs_nearshore_m": fmt_number(row["Hs_nearshore_m"]),
                    "Tp_s": fmt_number(row["Tp_s"], 1),
                    "Dir": row["Dir"],
                    "breaking": row["breaking"],
                    "temperature_C": fmt_number(row["temperature_C"], 1),
                    "wind_speed_kmh": fmt_number(row["wind_speed_kmh"], 1),
                    "wind_dir": row["wind_dir"],
                }
            )


def parse_time(timestamp: str) -> datetime | None:
    """Parse Open-Meteo local timestamps."""
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def upcoming_rows(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    """Return the next forecast rows from the current local time."""
    now = datetime.now(LOCAL_TZ)
    future_rows = [row for row in rows if (parse_time(row["time"]) or now) >= now]
    return (future_rows or rows)[:limit]


def future_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return forecast rows that are not already in the past."""
    now = datetime.now(LOCAL_TZ)
    return [row for row in rows if (parse_time(row["time"]) or now) >= now]


def is_good_kayak_window(row: dict[str, Any]) -> bool:
    """Apply the positive kayak-window thresholds."""
    hs = row.get("Hs_nearshore_m")
    wind = row.get("wind_speed_kmh")
    return (
        hs is not None
        and wind is not None
        and hs <= GOOD_HS_THRESHOLD_M
        and wind <= GOOD_WIND_THRESHOLD_KMH
        and not is_breaking_true(row.get("breaking"))
    )


def good_kayak_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select future rows that satisfy the favorable kayak thresholds."""
    return [row for row in future_rows(rows) if is_good_kayak_window(row)]


def group_consecutive_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group favorable rows into consecutive hourly windows."""
    sorted_rows = sorted(rows, key=lambda row: parse_time(row["time"]) or datetime.max.replace(tzinfo=LOCAL_TZ))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_time: datetime | None = None

    for row in sorted_rows:
        row_time = parse_time(row["time"])
        if row_time is None:
            continue
        if previous_time is None or row_time - previous_time <= timedelta(hours=1, minutes=5):
            current.append(row)
        else:
            groups.append(current)
            current = [row]
        previous_time = row_time

    if current:
        groups.append(current)

    return groups


def window_score(group: list[dict[str, Any]]) -> tuple[int, float, float, str]:
    """Rank windows by duration first, then calmer average sea and wind."""
    hs_values = [row["Hs_nearshore_m"] for row in group if row["Hs_nearshore_m"] is not None]
    wind_values = [row["wind_speed_kmh"] for row in group if row["wind_speed_kmh"] is not None]
    avg_hs = sum(hs_values) / len(hs_values) if hs_values else 99.0
    avg_wind = sum(wind_values) / len(wind_values) if wind_values else 99.0
    return (-len(group), avg_hs, avg_wind, group[0]["time"])


def best_good_window(good_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick the recommended favorable window."""
    groups = group_consecutive_rows(good_rows)
    if not groups:
        return []
    return min(groups, key=window_score)


def window_label(group: list[dict[str, Any]]) -> str:
    """Format a recommended time range for a group of hourly rows."""
    if not group:
        return "unavailable"
    if len(group) == 1:
        return group[0]["time"]
    return f"{group[0]['time']} - {group[-1]['time']}"


def markdown_rows(rows: list[dict[str, Any]]) -> list[str]:
    """Format rows using the project forecast table schema."""
    lines = [
        "| time | Hs_nearshore_m | Tp_s | Dir | breaking | temperature_C | wind_speed_kmh | wind_dir |",
        "| --- | ---: | ---: | --- | --- | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {time} | {hs} | {tp} | {direction} | {breaking} | {temp} | {wind} | {wind_dir} |".format(
                time=row["time"],
                hs=fmt_number(row["Hs_nearshore_m"]),
                tp=fmt_number(row["Tp_s"], 1),
                direction=row["Dir"],
                breaking=row["breaking"],
                temp=fmt_number(row["temperature_C"], 1),
                wind=fmt_number(row["wind_speed_kmh"], 1),
                wind_dir=row["wind_dir"],
            )
        )
    return lines


def write_good_window_outputs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Write positive kayak alert files and return favorable rows."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    good_rows = good_kayak_rows(rows)

    if not good_rows:
        GOOD_WINDOW_STATUS_PATH.write_text("NO_GOOD_WINDOW\n", encoding="utf-8")
        if GOOD_WINDOW_PATH.exists():
            GOOD_WINDOW_PATH.unlink()
        return []

    best_window = best_good_window(good_rows)
    report_rows = good_rows[:MAX_GOOD_ROWS_IN_REPORT]
    lines = [
        "# Possibile finestra per kayak",
        "",
        f"Controllo: {datetime.now(LOCAL_TZ).isoformat(timespec='seconds')}",
        "",
        "Condizioni favorevoli da verificare per uscita in kayak",
        "",
        f"- Ore favorevoli trovate: {len(good_rows)}",
        f"- Migliore fascia oraria consigliata: {window_label(best_window)}",
        f"- Soglie: Hs_nearshore_m <= {GOOD_HS_THRESHOLD_M:.2f} m, wind_speed_kmh <= {GOOD_WIND_THRESHOLD_KMH:.0f}, breaking non true",
        "- Controllare sempre condizioni locali, vento reale, correnti e capacità personali.",
        "",
        "## Ore favorevoli",
        "",
    ]
    lines.extend(markdown_rows(report_rows))
    if len(good_rows) > len(report_rows):
        lines.extend(["", f"_Mostrate le prime {len(report_rows)} di {len(good_rows)} ore favorevoli._"])

    GOOD_WINDOW_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    GOOD_WINDOW_STATUS_PATH.write_text("GOOD_WINDOW\n", encoding="utf-8")
    return good_rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact summary for console and Markdown report."""
    valid_hs = [row["Hs_nearshore_m"] for row in rows if row["Hs_nearshore_m"] is not None]
    max_row = None
    if valid_hs:
        max_row = max((row for row in rows if row["Hs_nearshore_m"] is not None), key=lambda row: row["Hs_nearshore_m"])

    calm_rows = [
        row
        for row in rows
        if row["Hs_nearshore_m"] is not None and row["Hs_nearshore_m"] <= 0.5 and not is_breaking_true(row["breaking"])
    ]

    return {
        "count": len(rows),
        "valid_count": len(valid_hs),
        "max_row": max_row,
        "calm_rows": calm_rows[:6],
    }


def write_markdown(rows: list[dict[str, Any]]) -> None:
    """Write a Markdown report with the required forecast table."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = summarize(rows)
    max_row = summary["max_row"]
    good_rows = good_kayak_rows(rows)
    best_window = best_good_window(good_rows)
    report_good_rows = good_rows[:MAX_GOOD_ROWS_IN_REPORT]

    lines = [
        "# Kayak Pula nearshore forecast",
        "",
        f"Generated: {datetime.now(LOCAL_TZ).isoformat(timespec='seconds')}",
        "",
        "## Site parameters",
        "",
        f"- Location: Santa Margherita di Pula ({LAT}, {LON})",
        f"- Distance from shore: {DISTANCE_FROM_SHORE_M} m",
        f"- Coast bearing: {COAST_BEARING_DEG} deg",
        f"- Local depth: {LOCAL_DEPTH_M} m",
        f"- Beach slope beta: {BEACH_SLOPE_BETA}",
        "- Data source: Open-Meteo Marine API and Forecast API, without API key",
        "",
        "## Summary",
        "",
        f"- Forecast rows: {summary['count']}",
        f"- Rows with nearshore estimate: {summary['valid_count']}",
    ]

    if max_row:
        lines.append(
            f"- Maximum nearshore Hs: {fmt_number(max_row['Hs_nearshore_m'])} m at {max_row['time']}"
        )
    else:
        lines.append("- Maximum nearshore Hs: unavailable")

    if summary["calm_rows"]:
        calm_times = ", ".join(row["time"] for row in summary["calm_rows"][:4])
        lines.append(f"- First calmer windows (Hs <= 0.50 m): {calm_times}")
    else:
        lines.append("- First calmer windows (Hs <= 0.50 m): none in current forecast")

    lines.extend(
        [
            "",
            "## Finestre favorevoli kayak",
            "",
        ]
    )

    if good_rows:
        lines.extend(
            [
                "Condizioni favorevoli da verificare per uscita in kayak.",
                "",
                f"- Migliore fascia oraria consigliata: {window_label(best_window)}",
                f"- Ore favorevoli trovate: {len(good_rows)}",
                "- Controllare sempre condizioni locali, vento reale, correnti e capacità personali.",
                "",
            ]
        )
        lines.extend(markdown_rows(report_good_rows))
        if len(good_rows) > len(report_good_rows):
            lines.extend(["", f"_Mostrate le prime {len(report_good_rows)} di {len(good_rows)} ore favorevoli._"])
    else:
        lines.append("Nessuna finestra consigliata secondo le soglie impostate.")

    lines.extend(
        [
            "",
            "## Forecast table",
            "",
        ]
    )
    lines.extend(markdown_rows(rows))

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_console_summary(rows: list[dict[str, Any]]) -> None:
    """Print a short terminal summary for the current run."""
    summary = summarize(rows)
    max_row = summary["max_row"]

    print("Kayak Pula forecast monitor")
    print(f"Rows: {summary['valid_count']}/{summary['count']} with nearshore estimates")
    if max_row:
        print(f"Max Hs nearshore: {fmt_number(max_row['Hs_nearshore_m'])} m at {max_row['time']}")

    print("\nNext hours:")
    for row in upcoming_rows(rows, limit=8):
        hs = fmt_number(row["Hs_nearshore_m"])
        tp = fmt_number(row["Tp_s"], 1)
        temp = fmt_number(row["temperature_C"], 1)
        wind = fmt_number(row["wind_speed_kmh"], 1)
        print(
            f"- {row['time']}: Hs={hs or 'n/a'} m, Tp={tp or 'n/a'} s, "
            f"wave={row['Dir'] or 'n/a'}, wind={wind or 'n/a'} km/h {row['wind_dir'] or ''}, "
            f"T={temp or 'n/a'} C, breaking={row['breaking']}"
        )


def main() -> int:
    """Run the full monitor."""
    try:
        marine_data, weather_data = fetch_forecasts()
        rows = build_rows(marine_data, weather_data)
        write_csv(rows)
        good_rows = write_good_window_outputs(rows)
        write_markdown(rows)
        print_console_summary(rows)
        print(f"\nKayak favorable windows: {len(good_rows)}")
        print(f"Status file: {GOOD_WINDOW_STATUS_PATH}")
        if good_rows:
            print(f"Good-window report: {GOOD_WINDOW_PATH}")
    except Exception as exc:
        print(f"forecast_monitor.py failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
