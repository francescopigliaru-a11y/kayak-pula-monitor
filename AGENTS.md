# Project Instructions

This repository monitors nearshore kayak conditions at Santa Margherita di Pula.

Operational rules for agents:

- Do not simulate forecast data. Use real Open-Meteo responses only.
- Do not add API keys or secrets.
- Keep the Kayak Pula v2 formula in `nearshore_wave_model_v2.py` unchanged unless
  a technical fix is required for missing or non-physical inputs.
- Keep dependencies minimal. Prefer the Python standard library when practical.
- Preserve the output paths:
  - `outputs/latest_forecast.csv`
  - `outputs/latest_forecast.md`
  - `outputs/good_window_status.txt`
  - `outputs/good_window.md` when favorable kayak windows exist
  - `outputs/model_audit.csv`
  - `outputs/model_audit.md`
- Preserve raw Open-Meteo wave values and model diagnostics in the audit
  outputs. Never present the `0.05 m` model floor as a direct observation.
- Positive kayak alerts must stay cautious. Use language such as "conditions
  favorable to verify" and never state that an outing is surely safe.
- Positive kayak alerts only consider the 36-hour window beginning at 08:00
  Europe/Rome on the day after the monitor runs, and require at least two
  consecutive favorable hours.
- The GitHub Actions workflow is triggered externally with `workflow_dispatch`
  at 18:00 Europe/Rome and creates at most one favorable-window issue per day.
- Before changing the GitHub Actions schedule or model parameters, explain the
  reason in the change summary.
- Treat the forecast as informational. Do not present it as navigation-grade or
  safety-certified guidance.
