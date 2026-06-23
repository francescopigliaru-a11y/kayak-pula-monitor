"""Kayak Pula v2 nearshore wave-height model."""

import math


def nearshore_wave(Hs_off, Tp, Dir_off, bearing=65, depth=3.5, wind_speed=0):
    """Estimate nearshore significant wave height from offshore conditions.

    The formula is the Kayak Pula v2 model supplied for this project. The only
    defensive additions are input checks for missing or non-physical values so
    the monitoring script can skip bad API rows without failing the full run.
    ``wind_speed`` is expected in m/s. Values above 40 are treated as km/h and
    converted to m/s, following the supplied model.
    """
    if Hs_off is None or Tp is None or Dir_off is None:
        return None
    if Tp <= 0 or depth <= 0:
        return None

    g = 9.81
    if wind_speed is None:
        wind_speed = 0
    if wind_speed > 40:
        wind_speed = wind_speed / 3.6
    theta_off = math.radians(Dir_off - (bearing + 90))
    w = 2 * math.pi / Tp
    k = w**2 / g
    c = w / k
    cg_off = g * Tp / (4 * math.pi)
    cg_loc = 0.5 * c * (1 + 2 * k * depth / math.sinh(2 * k * depth))
    Ks = min(math.sqrt(cg_off / cg_loc), 1.5)
    Kr = min(math.sqrt(abs(math.cos(theta_off)) / max(abs(math.cos(theta_off)), 0.05)), 1.5)
    atten = math.exp(- (abs(math.degrees(theta_off)) / 70)**2)
    angle_factor = max(0.2, math.cos(theta_off))
    Hs_pred = Hs_off * Ks * Kr * angle_factor * atten
    H_break = 0.78 * depth
    Hs_coastal = min(Hs_pred, H_break)
    H_wind = 0.016 * (wind_speed**2 / g)
    Hs_final = math.sqrt(Hs_coastal**2 + H_wind**2)
    return round(max(Hs_final, 0.05), 2)
