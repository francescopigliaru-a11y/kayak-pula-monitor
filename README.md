# Kayak Pula Monitor

Daily nearshore wave forecast monitor for Santa Margherita di Pula.

The monitor downloads real Open-Meteo data and applies the provisional Kayak
Pula v3 nearshore wave model. The unchanged v2 formula remains available only
for comparison in the model audit. The monitor writes:

- `outputs/latest_forecast.csv`
- `outputs/latest_forecast.md`
- `outputs/good_window_status.txt`
- `outputs/good_window.md`, only when a favorable kayak window exists
- `outputs/model_audit.csv`
- `outputs/model_audit.md`

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

The script prints a compact summary and updates the files in `outputs/`. Every
report covers exactly `36` hourly rows, starting at `08:00` Europe/Rome on the
day after the monitor runs. For example, a run at 18:00 on July 3 covers July 4
at 08:00 through July 5 at 19:00.

The model audit keeps the raw offshore Open-Meteo wave height, swell and
wind-wave components, the v3 estimate and the old v2 result. V3 has no
artificial `0.05 m` floor. The audit flags hours where a sheltered-direction
fallback is required because a one-point model cannot resolve diffraction and
detailed bathymetric shelter.

## V3 physical model

The active v3 estimate uses:

- the finite-depth linear dispersion relation;
- energy-flux shoaling and Snell refraction for waves directly approaching the
  shoreline;
- separate swell and wind-wave energy when supplied by Open-Meteo;
- finite-depth, fetch-limited local wind-wave growth for winds arriving from
  the sea;
- the depth-limited breaking cap `Hs <= 0.78 * depth`;
- an explicit conservative height-transmission factor of `0.50` when the
  configured shoreline geometry does not admit a direct wave ray.

The last item is a safety-oriented fallback, not a calibrated diffraction
model. The configured local wind fetch (`20 km`) is also provisional. A proper
2-D nearshore model would require detailed bathymetry and a spectral solver such
as SWAN. Relevant technical references are the [SWAN scientific documentation](https://swanmodel.sourceforge.io/online_doc/swantech/node5.html),
its [dissipation and breaking formulation](https://swanmodel.sourceforge.io/online_doc/swantech/node16.html),
and the USACE Coastal Engineering Manual methods for wave transformation and
fetch-limited wind-wave growth.

## Data sources

- Marine forecast endpoint: `https://marine-api.open-meteo.com/v1/marine`
- Weather forecast endpoint: `https://api.open-meteo.com/v1/forecast`

Hourly marine variables:

```text
wave_height,wave_direction,wave_period,wind_wave_height,wind_wave_direction,
wind_wave_period,swell_wave_height,swell_wave_direction,swell_wave_period
```

Hourly weather variables:

```text
temperature_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m
```

## GitHub Actions

The workflow `.github/workflows/daily-forecast.yml` is started with
`workflow_dispatch` by the external cron-job.org trigger at 18:00 Europe/Rome.
This avoids relying on GitHub's delayed scheduled queue. It installs Python,
installs `requirements.txt`, runs:

```bash
python forecast_monitor.py
```

and uploads the `outputs/` directory as an artifact. If
`outputs/good_window.md` exists, the workflow creates one GitHub Issue per day
using the good-window report as the body.

The issue is deliberately cautious: it reports conditions favorable to verify,
not a guarantee that going out is safe. Always check local conditions, real wind,
currents, and personal ability.

## Favorable kayak windows

Initial alert thresholds:

- `Hs_nearshore_m <= 0.20`
- `wind_speed_kmh <= 20`
- `breaking` is not `true`
- Alerts consider the `36` hours beginning at `08:00` Europe/Rome on the day
  after the monitor runs.
- A positive alert requires at least `2` consecutive favorable hours.
- The wave threshold is evaluated against the unrounded v3 value; rounding to
  two decimals cannot turn a marginally higher wave into a favorable hour.

## Local observations

Real local checks used for future calibration are stored in
`observations/local_observations.csv`. Visual evidence is recorded
qualitatively unless a scale or instrument supports a numerical wave height.
Model replay values are kept separate from observed values.

The status file contains:

- `GOOD_WINDOW` when at least one future favorable block exists.
- `NO_GOOD_WINDOW` when no future favorable hour exists.

The workflow does not create duplicate favorable-window issues for the same
calendar day, even if many hourly attempts succeed or the workflow is launched
manually more than once.

## Output table

The Markdown and CSV forecast use this table shape:

| time | Hs_nearshore_m | Tp_s | Dir | breaking | temperature_C | wind_speed_kmh | wind_dir |
| --- | ---: | ---: | --- | --- | ---: | ---: | --- |

`latest_forecast.csv` also includes the offshore API height, API direction,
swell and wind-wave heights, v3 transfer ratio and confidence, and the v2
comparison. Complete diagnostics are available in `model_audit.csv`.

## Notes

This is a forecast aid, not a navigation or safety system. V3 is physically
more coherent than v2 but remains uncalibrated. Open-Meteo coastal and marine
grids can differ from local nearshore conditions. Always check the sea directly
and use conservative judgement.
