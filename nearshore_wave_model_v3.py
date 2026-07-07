"""Kayak Pula v3 provisional nearshore wave-height model.

This reduced-order model follows linear-wave shoaling and Snell refraction for
waves that approach the coast directly. It treats swell and wind-sea energy
separately when Open-Meteo provides those components. A finite-depth,
fetch-limited estimate supplies a conservative local wind-sea contribution.

The model is deliberately labelled provisional: diffraction around headlands,
bottom friction and detailed bathymetry require a 2-D spectral model such as
SWAN. When a direct ray cannot reach the coast, v3 uses an explicit conservative
height-transmission floor instead of pretending to resolve those processes.
"""

from __future__ import annotations

import math
from typing import Any


GRAVITY = 9.81
BREAKING_GAMMA = 0.78
SHELTERED_HEIGHT_TRANSMISSION = 0.50
DEFAULT_LOCAL_WIND_FETCH_M = 20_000.0


def signed_angle_difference(direction_deg: float, reference_deg: float) -> float:
    """Return the shortest signed angular difference in [-180, 180)."""
    return (direction_deg - reference_deg + 180.0) % 360.0 - 180.0


def wavenumber(period_s: float, depth_m: float) -> float:
    """Solve the linear dispersion relation omega^2 = g k tanh(k h)."""
    if period_s <= 0 or depth_m <= 0:
        raise ValueError("period and depth must be positive")

    omega = 2.0 * math.pi / period_s
    k = max(omega**2 / GRAVITY, omega / math.sqrt(GRAVITY * depth_m))

    for _ in range(40):
        kh = k * depth_m
        tanh_kh = math.tanh(kh)
        sech_sq = 1.0 / math.cosh(kh) ** 2 if kh < 350 else 0.0
        value = GRAVITY * k * tanh_kh - omega**2
        derivative = GRAVITY * (tanh_kh + kh * sech_sq)
        next_k = k - value / derivative
        if next_k <= 0:
            next_k = k / 2.0
        if abs(next_k - k) <= 1e-12 * max(1.0, k):
            return next_k
        k = next_k

    return k


def wave_kinematics(period_s: float, depth_m: float) -> dict[str, float]:
    """Return deep/local phase and group velocities from linear wave theory."""
    omega = 2.0 * math.pi / period_s
    deep_phase_speed = GRAVITY / omega
    deep_group_speed = deep_phase_speed / 2.0

    k = wavenumber(period_s, depth_m)
    kh = k * depth_m
    phase_speed = omega / k
    two_kh = 2.0 * kh
    # x / sinh(x) tends to zero in deep water. Evaluating sinh directly for
    # very short wind-wave periods can overflow even though the limit is simple.
    depth_term = 0.0 if two_kh > 50.0 else two_kh / math.sinh(two_kh)
    group_factor = 0.5 * (1.0 + depth_term)
    group_speed = group_factor * phase_speed
    return {
        "k_local_1_m": k,
        "deep_phase_speed_m_s": deep_phase_speed,
        "deep_group_speed_m_s": deep_group_speed,
        "local_phase_speed_m_s": phase_speed,
        "local_group_speed_m_s": group_speed,
    }


def transform_component(
    height_m: float | None,
    period_s: float | None,
    direction_from_deg: float | None,
    *,
    seaward_normal_deg: float,
    depth_m: float,
    breaking_gamma: float = BREAKING_GAMMA,
    sheltered_transmission: float = SHELTERED_HEIGHT_TRANSMISSION,
) -> dict[str, Any] | None:
    """Transform one significant-height component to the local depth.

    Directions use the meteorological convention: the direction waves come
    from. A direct ray exists only when that direction lies on the seaward side
    of the shoreline (absolute incidence below 90 degrees).
    """
    if height_m is None or period_s is None or direction_from_deg is None:
        return None
    if height_m < 0 or period_s <= 0 or depth_m <= 0:
        return None
    if height_m == 0:
        return {
            "input_height_m": 0.0,
            "height_m": 0.0,
            "incidence_offshore_deg": 0.0,
            "incidence_local_deg": 0.0,
            "Ks": 1.0,
            "Kr": 1.0,
            "transfer": 1.0,
            "direct_ray": True,
            "breaking_limited": False,
        }

    incidence = signed_angle_difference(direction_from_deg, seaward_normal_deg)
    kinematics = wave_kinematics(period_s, depth_m)
    ks = math.sqrt(
        kinematics["deep_group_speed_m_s"] / kinematics["local_group_speed_m_s"]
    )

    if abs(incidence) < 89.0:
        theta_off = math.radians(incidence)
        snell_argument = (
            kinematics["local_phase_speed_m_s"]
            / kinematics["deep_phase_speed_m_s"]
            * math.sin(theta_off)
        )
        snell_argument = max(-1.0, min(1.0, snell_argument))
        theta_local = math.asin(snell_argument)
        kr = math.sqrt(
            max(math.cos(theta_off), 0.0) / max(math.cos(theta_local), 0.05)
        )
        transfer = ks * kr
        direct_ray = True
    else:
        theta_local = math.nan
        kr = sheltered_transmission
        transfer = ks * sheltered_transmission
        direct_ray = False

    transformed = height_m * transfer
    breaking_limit = breaking_gamma * depth_m
    limited = transformed > breaking_limit
    return {
        "input_height_m": height_m,
        "height_m": min(transformed, breaking_limit),
        "incidence_offshore_deg": incidence,
        "incidence_local_deg": None if math.isnan(theta_local) else math.degrees(theta_local),
        "Ks": ks,
        "Kr": kr,
        "transfer": transfer,
        "direct_ray": direct_ray,
        "breaking_limited": limited,
    }


def fetch_limited_wind_wave(
    wind_speed_m_s: float,
    wind_direction_from_deg: float | None,
    *,
    seaward_normal_deg: float,
    depth_m: float,
    fetch_m: float = DEFAULT_LOCAL_WIND_FETCH_M,
) -> dict[str, float | bool | None]:
    """Estimate local wind sea with finite-depth SMB/CEM growth equations.

    The estimate assumes a sufficiently persistent wind and is therefore used
    conservatively. The configured fetch is reduced by wind obliquity and set
    to zero for winds arriving from land.
    """
    if wind_speed_m_s <= 0 or wind_direction_from_deg is None:
        return {
            "height_m": 0.0,
            "period_s": 0.0,
            "effective_fetch_m": 0.0,
            "from_sea": False,
            "incidence_deg": None,
        }

    incidence = signed_angle_difference(wind_direction_from_deg, seaward_normal_deg)
    exposure = max(math.cos(math.radians(incidence)), 0.0)
    effective_fetch = fetch_m * exposure
    if effective_fetch <= 0 or wind_speed_m_s < 0.5:
        return {
            "height_m": 0.0,
            "period_s": 0.0,
            "effective_fetch_m": 0.0,
            "from_sea": False,
            "incidence_deg": incidence,
        }

    u2 = wind_speed_m_s**2
    dimensionless_depth = GRAVITY * depth_m / u2
    dimensionless_fetch = GRAVITY * effective_fetch / u2

    depth_height = math.tanh(0.530 * dimensionless_depth**0.75)
    fetch_height = math.tanh(
        0.0125 * dimensionless_fetch**0.42 / max(depth_height, 1e-9)
    )
    height_m = 0.283 * u2 / GRAVITY * depth_height * fetch_height

    depth_period = math.tanh(0.833 * dimensionless_depth**0.375)
    fetch_period = math.tanh(
        0.077 * dimensionless_fetch**0.25 / max(depth_period, 1e-9)
    )
    period_s = 2.40 * math.pi * wind_speed_m_s / GRAVITY * depth_period * fetch_period
    return {
        "height_m": height_m,
        "period_s": period_s,
        "effective_fetch_m": effective_fetch,
        "from_sea": True,
        "incidence_deg": incidence,
    }


def nearshore_wave_v3_details(
    Hs_off: float | None,
    Tp: float | None,
    Dir_off: float | None,
    *,
    bearing: float = 65.0,
    depth: float = 3.5,
    distance_from_shore_m: float = 350.0,
    beach_slope_beta: float = 0.015,
    wind_speed: float = 0.0,
    wind_direction: float | None = None,
    wind_fetch_m: float = DEFAULT_LOCAL_WIND_FETCH_M,
    swell_height: float | None = None,
    swell_period: float | None = None,
    swell_direction: float | None = None,
    wind_wave_height: float | None = None,
    wind_wave_period: float | None = None,
    wind_wave_direction: float | None = None,
) -> dict[str, Any] | None:
    """Return the provisional v3 estimate and transparent diagnostics."""
    if Hs_off is None or Tp is None or Dir_off is None:
        return None
    if Hs_off < 0 or Tp <= 0 or depth <= 0:
        return None

    seaward_normal = (bearing + 90.0) % 360.0
    total_component = transform_component(
        Hs_off,
        Tp,
        Dir_off,
        seaward_normal_deg=seaward_normal,
        depth_m=depth,
    )
    if total_component is None:
        return None

    swell_component = transform_component(
        swell_height,
        swell_period or Tp,
        swell_direction,
        seaward_normal_deg=seaward_normal,
        depth_m=depth,
    )
    api_wind_component = transform_component(
        wind_wave_height,
        wind_wave_period or Tp,
        wind_wave_direction,
        seaward_normal_deg=seaward_normal,
        depth_m=depth,
    )
    local_wind = fetch_limited_wind_wave(
        max(wind_speed, 0.0),
        wind_direction,
        seaward_normal_deg=seaward_normal,
        depth_m=depth,
        fetch_m=wind_fetch_m,
    )

    component_inputs_available = swell_height is not None or wind_wave_height is not None
    sheltered_fallback_used = not total_component["direct_ray"]

    if component_inputs_available:
        known_energy = 0.0
        transformed_energy = 0.0
        if swell_component is not None and swell_height is not None:
            known_energy += swell_height**2
            transformed_energy += swell_component["height_m"] ** 2
            sheltered_fallback_used |= not swell_component["direct_ray"]
        if api_wind_component is not None and wind_wave_height is not None:
            known_energy += wind_wave_height**2
            sheltered_fallback_used |= not api_wind_component["direct_ray"]

        unresolved_height = math.sqrt(max(Hs_off**2 - known_energy, 0.0))
        unresolved_component = transform_component(
            unresolved_height,
            Tp,
            Dir_off,
            seaward_normal_deg=seaward_normal,
            depth_m=depth,
        )
        if unresolved_component is not None:
            transformed_energy += unresolved_component["height_m"] ** 2

        api_wind_local = 0.0 if api_wind_component is None else api_wind_component["height_m"]
        wind_sea_height = max(api_wind_local, float(local_wind["height_m"]))
        transformed_energy += wind_sea_height**2
        combined_unlimited = math.sqrt(transformed_energy)
    else:
        unresolved_height = 0.0
        unresolved_component = None
        wind_sea_height = float(local_wind["height_m"])
        combined_unlimited = max(total_component["height_m"], wind_sea_height)

    breaking_limit = BREAKING_GAMMA * depth
    hs_final = min(combined_unlimited, breaking_limit)
    slope_depth = distance_from_shore_m * beach_slope_beta
    return {
        "model_version": "v3-provisional",
        "Hs_final_m": round(max(hs_final, 0.0), 2),
        "Hs_raw_m": hs_final,
        "Hs_unlimited_m": combined_unlimited,
        "transfer_ratio": hs_final / Hs_off if Hs_off > 0 else 1.0,
        "breaking": combined_unlimited >= breaking_limit,
        "H_break_m": breaking_limit,
        "seaward_normal_deg": seaward_normal,
        "total_component": total_component,
        "swell_component": swell_component,
        "api_wind_component": api_wind_component,
        "unresolved_component": unresolved_component,
        "unresolved_offshore_height_m": unresolved_height,
        "local_wind_wave": local_wind,
        "wind_sea_height_m": wind_sea_height,
        "sheltered_fallback_used": sheltered_fallback_used,
        "component_inputs_available": component_inputs_available,
        "profile_depth_from_slope_m": slope_depth,
        "profile_depth_mismatch_m": slope_depth - depth,
        "confidence": "provisional-low" if sheltered_fallback_used else "provisional-medium",
    }


def nearshore_wave_v3(*args: Any, **kwargs: Any) -> float | None:
    """Return only the v3 significant wave-height estimate."""
    details = nearshore_wave_v3_details(*args, **kwargs)
    return None if details is None else details["Hs_final_m"]
