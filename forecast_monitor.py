"""Daily forecast monitor for nearshore kayak conditions at Santa Margherita di Pula."""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from nearshore_wave_model_v2 import nearshore_wave_details


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
MODEL_AUDIT_CSV_PATH = OUTPUT_DIR / "model_audit.csv"
MODEL_AUDIT_PATH = OUTPUT_DIR / "model_audit.md"

GOOD_HS_THRESHOLD_M = 0.20
GOOD_WIND_THRESHOLD_KMH = 20.0
FORECAST_START_HOUR = 8
FORECAST_WINDOW_HOURS = 36
MIN_CONSECUTIVE_GOOD_HOURS = 2
MAX_GOOD_ROWS_IN_REPORT = 12

OUTPUT_COLUMNS = [
    "time",
    "Hs_offshore_API_m",
    "Hs_nearshore_m",
    "Hs_model_raw_m",
    "model_floor_applied",
    "Tp_s",
    "Dir",
    "Dir_API_deg",
    "breaking",
    "temperature_C",
    "wind_speed_kmh",
    "wind_dir",
]

MODEL_AUDIT_COLUMNS = [
    "time",
    "Hs_offshore_API_m",
    "Tp_API_s",
    "Dir_API_deg",
    "Dir_API",
    "theta_off_deg",
    "Ks",
    "Kr",
    "atten",
    "angle_factor",
    "Hs_pred_m",
    "H_wind_m",
    "Hs_model_raw_m",
    "Hs_nearshore_display_m",
    "model_floor_applied",
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

        model = nearshore_wave_details(
            hs_off,
            tp,
            wave_dir_deg,
            bearing=COAST_BEARING_DEG,
            depth=LOCAL_DEPTH_M,
            wind_speed=wind_speed_ms or 0,
        )
        hs_nearshore = None if model is None else model["Hs_final_m"]

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
                "_model": model,
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
                    "Hs_offshore_API_m": fmt_number(row["_wave_height_offshore_m"]),
                    "Hs_nearshore_m": fmt_number(row["Hs_nearshore_m"]),
                    "Hs_model_raw_m": fmt_number((row["_model"] or {}).get("Hs_raw_m"), 4),
                    "model_floor_applied": str(bool((row["_model"] or {}).get("floor_applied"))).lower(),
                    "Tp_s": fmt_number(row["Tp_s"], 1),
                    "Dir": row["Dir"],
                    "Dir_API_deg": fmt_number(row["_wave_direction_deg"], 0),
                    "breaking": row["breaking"],
                    "temperature_C": fmt_number(row["temperature_C"], 1),
                    "wind_speed_kmh": fmt_number(row["wind_speed_kmh"], 1),
                    "wind_dir": row["wind_dir"],
                }
            )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance between two coordinates."""
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lon / 2) ** 2
    )
    return radius_km * 2 * math.asin(math.sqrt(value))


def audit_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the raw API wave and the unchanged model transformation."""
    offshore = [row["_wave_height_offshore_m"] for row in rows if row["_wave_height_offshore_m"] is not None]
    raw_model = [
        row["_model"]["Hs_raw_m"]
        for row in rows
        if row.get("_model") is not None
    ]
    displayed = [row["Hs_nearshore_m"] for row in rows if row["Hs_nearshore_m"] is not None]
    floor_hits = sum(bool((row.get("_model") or {}).get("floor_applied")) for row in rows)
    return {
        "offshore_min": min(offshore) if offshore else None,
        "offshore_max": max(offshore) if offshore else None,
        "raw_min": min(raw_model) if raw_model else None,
        "raw_max": max(raw_model) if raw_model else None,
        "displayed_min": min(displayed) if displayed else None,
        "displayed_max": max(displayed) if displayed else None,
        "floor_hits": floor_hits,
        "row_count": len(rows),
    }


def write_model_audit(
    rows: list[dict[str, Any]],
    marine_data: dict[str, Any],
    run_time: datetime,
    window_start: datetime,
    window_end: datetime,
) -> None:
    """Write detailed API-to-nearshore model diagnostics."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = audit_summary(rows)
    grid_lat = safe_float(marine_data.get("latitude"))
    grid_lon = safe_float(marine_data.get("longitude"))
    grid_distance = None
    if grid_lat is not None and grid_lon is not None:
        grid_distance = haversine_km(LAT, LON, grid_lat, grid_lon)

    with MODEL_AUDIT_CSV_PATH.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=MODEL_AUDIT_COLUMNS)
        writer.writeheader()
        for row in rows:
            model = row.get("_model") or {}
            writer.writerow(
                {
                    "time": row["time"],
                    "Hs_offshore_API_m": fmt_number(row["_wave_height_offshore_m"]),
                    "Tp_API_s": fmt_number(row["Tp_s"], 1),
                    "Dir_API_deg": fmt_number(row["_wave_direction_deg"], 0),
                    "Dir_API": row["Dir"],
                    "theta_off_deg": fmt_number(model.get("theta_off_deg"), 1),
                    "Ks": fmt_number(model.get("Ks"), 3),
                    "Kr": fmt_number(model.get("Kr"), 3),
                    "atten": fmt_number(model.get("atten"), 4),
                    "angle_factor": fmt_number(model.get("angle_factor"), 3),
                    "Hs_pred_m": fmt_number(model.get("Hs_pred_m"), 4),
                    "H_wind_m": fmt_number(model.get("H_wind_m"), 4),
                    "Hs_model_raw_m": fmt_number(model.get("Hs_raw_m"), 4),
                    "Hs_nearshore_display_m": fmt_number(row["Hs_nearshore_m"]),
                    "model_floor_applied": str(bool(model.get("floor_applied"))).lower(),
                }
            )

    lines = [
        "# Kayak Pula model audit",
        "",
        f"Generated: {run_time.isoformat(timespec='seconds')}",
        f"Forecast window: {forecast_window_label(window_start, window_end)}",
        "",
        "## API source",
        "",
        f"- Requested coordinate: {LAT}, {LON}",
    ]
    if grid_lat is not None and grid_lon is not None:
        lines.append(f"- Marine grid coordinate returned by Open-Meteo: {grid_lat}, {grid_lon}")
    if grid_distance is not None:
        lines.append(f"- Approximate distance from requested point: {grid_distance:.1f} km")
    lines.extend(
        [
            "- Wave direction is the direction waves come from.",
            "",
            "## Transformation summary",
            "",
            f"- Offshore API Hs range: {fmt_number(summary['offshore_min'])}-{fmt_number(summary['offshore_max'])} m",
            f"- Model raw Hs range before the 0.05 m floor: {fmt_number(summary['raw_min'], 4)}-{fmt_number(summary['raw_max'], 4)} m",
            f"- Displayed nearshore Hs range: {fmt_number(summary['displayed_min'])}-{fmt_number(summary['displayed_max'])} m",
            f"- 0.05 m display floor applied: {summary['floor_hits']}/{summary['row_count']} hours",
            "",
            "The 0.05 m value is a display floor when `model_floor_applied=true`; it is not an API observation of a 5 cm wave.",
            "",
            "## First 12 hourly diagnostics",
            "",
            "| time | Hs_API_m | Dir_API_deg | Tp_s | theta_deg | atten | angle_factor | Hs_model_raw_m | Hs_display_m | floor |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in rows[:12]:
        model = row.get("_model") or {}
        lines.append(
            "| {time} | {api} | {direction} | {tp} | {theta} | {atten} | {angle} | {raw} | {displayed} | {floor} |".format(
                time=row["time"],
                api=fmt_number(row["_wave_height_offshore_m"]),
                direction=fmt_number(row["_wave_direction_deg"], 0),
                tp=fmt_number(row["Tp_s"], 1),
                theta=fmt_number(model.get("theta_off_deg"), 1),
                atten=fmt_number(model.get("atten"), 4),
                angle=fmt_number(model.get("angle_factor"), 3),
                raw=fmt_number(model.get("Hs_raw_m"), 4),
                displayed=fmt_number(row["Hs_nearshore_m"]),
                floor=str(bool(model.get("floor_applied"))).lower(),
            )
        )
    MODEL_AUDIT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_time(timestamp: str) -> datetime | None:
    """Parse Open-Meteo local timestamps."""
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def forecast_window_bounds(run_time: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the 36-hour window starting at 08:00 on the following day."""
    current = run_time or datetime.now(LOCAL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=LOCAL_TZ)
    else:
        current = current.astimezone(LOCAL_TZ)

    next_day = current.date() + timedelta(days=1)
    start = datetime(
        next_day.year,
        next_day.month,
        next_day.day,
        FORECAST_START_HOUR,
        tzinfo=LOCAL_TZ,
    )
    return start, start + timedelta(hours=FORECAST_WINDOW_HOURS)


def rows_in_forecast_window(
    rows: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Select rows in the configured window, with the end excluded."""
    selected: list[dict[str, Any]] = []
    for row in rows:
        row_time = parse_time(row["time"])
        if row_time is None or row_time < start or row_time >= end:
            continue
        selected.append(row)
    return selected


def forecast_window_label(start: datetime, end: datetime) -> str:
    """Describe the hourly interval using its first and last included rows."""
    last_hour = end - timedelta(hours=1)
    return (
        f"{start.isoformat(timespec='minutes')} - "
        f"{last_hour.isoformat(timespec='minutes')} ({FORECAST_WINDOW_HOURS} ore)"
    )


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
    """Select forecast-window rows that satisfy the kayak thresholds."""
    return [row for row in rows if is_good_kayak_window(row)]


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


def best_good_window(good_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick the first favorable window with enough consecutive hours."""
    groups = group_consecutive_rows(good_rows)
    for group in groups:
        if len(group) >= MIN_CONSECUTIVE_GOOD_HOURS:
            return group
    return []


def qualified_good_rows(good_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return favorable rows that belong to sufficiently long windows."""
    groups = group_consecutive_rows(good_rows)
    rows: list[dict[str, Any]] = []
    for group in groups:
        if len(group) >= MIN_CONSECUTIVE_GOOD_HOURS:
            rows.extend(group)
    return rows


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


def write_good_window_outputs(
    rows: list[dict[str, Any]],
    run_time: datetime,
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    """Write positive kayak alert files and return favorable rows."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    good_rows = good_kayak_rows(rows)
    best_window = best_good_window(good_rows)
    qualified_rows = qualified_good_rows(good_rows)
    model_audit = audit_summary(rows)

    if not best_window:
        GOOD_WINDOW_STATUS_PATH.write_text("NO_GOOD_WINDOW\n", encoding="utf-8")
        if GOOD_WINDOW_PATH.exists():
            GOOD_WINDOW_PATH.unlink()
        return []

    report_rows = qualified_rows[:MAX_GOOD_ROWS_IN_REPORT]
    lines = [
        "# Possibile finestra per kayak",
        "",
        f"Controllo: {run_time.isoformat(timespec='seconds')}",
        "",
        "Condizioni favorevoli da verificare per uscita in kayak",
        "",
        f"- Intervallo analizzato: {forecast_window_label(window_start, window_end)}",
        f"- Ore favorevoli trovate nell'intervallo: {len(qualified_rows)}",
        f"- Migliore fascia oraria consigliata: {window_label(best_window)}",
        f"- Onda offshore Open-Meteo nell'intervallo: {fmt_number(model_audit['offshore_min'])}-{fmt_number(model_audit['offshore_max'])} m",
        f"- Limite minimo 0.05 m applicato dal modello: {model_audit['floor_hits']}/{model_audit['row_count']} ore",
        f"- Soglie: Hs_nearshore_m <= {GOOD_HS_THRESHOLD_M:.2f} m, wind_speed_kmh <= {GOOD_WIND_THRESHOLD_KMH:.0f}, breaking non true",
        f"- Richiesta minima: almeno {MIN_CONSECUTIVE_GOOD_HOURS} ore consecutive.",
        "- Controllare sempre condizioni locali, vento reale, correnti e capacità personali.",
        "- Il valore 0.05 m puo essere il minimo visualizzato dal modello, non una misura diretta dell'onda.",
        "",
        "## Ore favorevoli",
        "",
    ]
    lines.extend(markdown_rows(report_rows))
    if len(qualified_rows) > len(report_rows):
        lines.extend(["", f"_Mostrate le prime {len(report_rows)} di {len(qualified_rows)} ore favorevoli._"])

    GOOD_WINDOW_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    GOOD_WINDOW_STATUS_PATH.write_text("GOOD_WINDOW\n", encoding="utf-8")
    return qualified_rows


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


def write_markdown(
    rows: list[dict[str, Any]],
    run_time: datetime,
    window_start: datetime,
    window_end: datetime,
) -> None:
    """Write a Markdown report with the required forecast table."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = summarize(rows)
    model_audit = audit_summary(rows)
    max_row = summary["max_row"]
    good_rows = good_kayak_rows(rows)
    best_window = best_good_window(good_rows)
    qualified_rows = qualified_good_rows(good_rows)
    report_good_rows = qualified_rows[:MAX_GOOD_ROWS_IN_REPORT]

    lines = [
        "# Kayak Pula nearshore forecast",
        "",
        f"Generated: {run_time.isoformat(timespec='seconds')}",
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
        f"- Forecast window: {forecast_window_label(window_start, window_end)}",
        f"- Forecast rows: {summary['count']}",
        f"- Rows with nearshore estimate: {summary['valid_count']}",
        f"- Offshore Open-Meteo Hs range: {fmt_number(model_audit['offshore_min'])}-{fmt_number(model_audit['offshore_max'])} m",
        f"- Raw model Hs range before display floor: {fmt_number(model_audit['raw_min'], 4)}-{fmt_number(model_audit['raw_max'], 4)} m",
        f"- 0.05 m display floor applied: {model_audit['floor_hits']}/{model_audit['row_count']} hours",
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

    if best_window:
        lines.extend(
            [
                "Condizioni favorevoli da verificare per uscita in kayak.",
                "",
                f"- Migliore fascia oraria consigliata: {window_label(best_window)}",
                f"- Ore favorevoli trovate nell'intervallo: {len(qualified_rows)}",
                f"- Richiesta minima: almeno {MIN_CONSECUTIVE_GOOD_HOURS} ore consecutive.",
                "- Controllare sempre condizioni locali, vento reale, correnti e capacità personali.",
                "",
            ]
        )
        lines.extend(markdown_rows(report_good_rows))
        if len(qualified_rows) > len(report_good_rows):
            lines.extend(["", f"_Mostrate le prime {len(report_good_rows)} di {len(qualified_rows)} ore favorevoli._"])
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


def print_console_summary(
    rows: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
) -> None:
    """Print a short terminal summary for the current run."""
    summary = summarize(rows)
    max_row = summary["max_row"]

    print("Kayak Pula forecast monitor")
    print(f"Forecast window: {forecast_window_label(window_start, window_end)}")
    print(f"Rows: {summary['valid_count']}/{summary['count']} with nearshore estimates")
    if max_row:
        print(f"Max Hs nearshore: {fmt_number(max_row['Hs_nearshore_m'])} m at {max_row['time']}")

    print("\nFirst forecast hours:")
    for row in rows[:8]:
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
        run_time = datetime.now(LOCAL_TZ)
        window_start, window_end = forecast_window_bounds(run_time)
        marine_data, weather_data = fetch_forecasts()
        all_rows = build_rows(marine_data, weather_data)
        rows = rows_in_forecast_window(all_rows, window_start, window_end)
        if not rows:
            raise RuntimeError(
                "No Open-Meteo rows available for forecast window "
                f"{forecast_window_label(window_start, window_end)}"
            )
        write_csv(rows)
        write_model_audit(rows, marine_data, run_time, window_start, window_end)
        good_rows = write_good_window_outputs(rows, run_time, window_start, window_end)
        write_markdown(rows, run_time, window_start, window_end)
        print_console_summary(rows, window_start, window_end)
        print(f"\nKayak favorable windows: {len(good_rows)}")
        print(f"Status file: {GOOD_WINDOW_STATUS_PATH}")
        print(f"Model audit: {MODEL_AUDIT_PATH}")
        if good_rows:
            print(f"Good-window report: {GOOD_WINDOW_PATH}")
    except Exception as exc:
        print(f"forecast_monitor.py failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
