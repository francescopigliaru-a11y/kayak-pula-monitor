# Kayak Pula Monitor

Daily nearshore wave forecast monitor for Santa Margherita di Pula.

The monitor downloads real Open-Meteo data, applies the Kayak Pula v2 nearshore
wave model, and writes:

- `outputs/latest_forecast.csv`
- `outputs/latest_forecast.md`
- `outputs/good_window_status.txt`
- `outputs/good_window.md`, only when a favorable kayak window exists

No API key is required.

## Site parameters

- Latitude: `38.930`
- Longitude: `8.924`
- Distance from shore: `350 m`
- Coast bearing: `65 deg`
- Local depth: `3.5 m`
- Beach slope beta: `0.015`

## Local usage

```bash
python -m pip install -r requirements.txt
python forecast_monitor.py
```

The script prints a compact summary for the next hours and updates the files in
`outputs/`.

## Data sources

- Marine forecast endpoint: `https://marine-api.open-meteo.com/v1/marine`
- Weather forecast endpoint: `https://api.open-meteo.com/v1/forecast`

Hourly marine variables:

```text
wave_height,wave_direction,wave_period
```

Hourly weather variables:

```text
temperature_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m
```

## GitHub Actions

The workflow `.github/workflows/daily-forecast.yml` runs daily and can also be
started manually with `workflow_dispatch`. It installs Python, installs
`requirements.txt`, runs:

```bash
python forecast_monitor.py
```

and uploads the `outputs/` directory as an artifact. If
`outputs/good_window.md` exists, the workflow creates a GitHub Issue titled
`Kayak Pula - finestra favorevole per uscita` using that file as the body.

The issue is deliberately cautious: it reports conditions favorable to verify,
not a guarantee that going out is safe. Always check local conditions, real wind,
currents, and personal ability.

## Favorable kayak windows

Initial alert thresholds:

- `Hs_nearshore_m <= 0.20`
- `wind_speed_kmh <= 20`
- `breaking` is not `true`

The status file contains:

- `GOOD_WINDOW` when at least one future favorable hour exists.
- `NO_GOOD_WINDOW` when no future favorable hour exists.

## Output table

The Markdown and CSV forecast use this table shape:

| time | Hs_nearshore_m | Tp_s | Dir | breaking | temperature_C | wind_speed_kmh | wind_dir |
| --- | ---: | ---: | --- | --- | ---: | ---: | --- |

## Notes

This is a forecast aid, not a navigation or safety system. Open-Meteo coastal
and marine grids can differ from local nearshore conditions. Always check the
sea directly and use conservative judgement.
