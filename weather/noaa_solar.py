"""noaa_solar.py

NOAA solar position algorithm. Computes sun elevation angle from geographic
coordinates and UTC datetime.
"""

import math
from datetime import datetime, timezone as dt_timezone
from typing import Optional


def _julian_date(dt_utc: datetime) -> float:
    """Convert a UTC datetime to Julian Date."""
    y = dt_utc.year
    m = dt_utc.month
    d = dt_utc.day + (dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0) / 24.0

    if m <= 2:
        y -= 1
        m += 12

    a = int(y / 100)
    b = 2 - a + int(a / 4)

    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def get_sun_elevation(
    latitude: float,
    longitude: float,
    at: Optional[datetime] = None,
) -> float:
    """Sun elevation angle in degrees at the given time (or now).

    Pure math from lat/lon/UTC datetime, no API call.
    Uses the NOAA solar position algorithm.

    Parameters
    ----------
    latitude : float
        Geographic latitude in decimal degrees.
    longitude : float
        Geographic longitude in decimal degrees.
    at : datetime, optional
        Timezone-aware datetime to evaluate. Defaults to the current UTC time.
        Raises ValueError if a naive datetime is provided.
    """
    if at is None:
        at = datetime.now(tz=dt_timezone.utc)
    elif at.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware")
    else:
        at = at.astimezone(dt_timezone.utc)

    # Julian date
    jd = _julian_date(at)
    # Julian century
    jc = (jd - 2451545.0) / 36525.0

    # Solar geometry
    geom_mean_long = (280.46646 + jc * (36000.76983 + 0.0003032 * jc)) % 360
    geom_mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    eccent = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)

    anom_rad = math.radians(geom_mean_anom)
    sun_eq_ctr = (
        math.sin(anom_rad) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + math.sin(2 * anom_rad) * (0.019993 - 0.000101 * jc)
        + math.sin(3 * anom_rad) * 0.000289
    )

    sun_true_long = geom_mean_long + sun_eq_ctr
    sun_app_long = sun_true_long - 0.00569 - 0.00478 * math.sin(
        math.radians(125.04 - 1934.136 * jc)
    )

    mean_obliq = (
        23.0 + (26.0 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60.0) / 60.0
    )
    obliq_corr = mean_obliq + 0.00256 * math.cos(
        math.radians(125.04 - 1934.136 * jc)
    )

    # Solar declination
    declination = math.degrees(
        math.asin(math.sin(math.radians(obliq_corr)) * math.sin(math.radians(sun_app_long)))
    )

    # Equation of time (minutes)
    var_y = math.tan(math.radians(obliq_corr / 2)) ** 2
    geom_mean_long_rad = math.radians(geom_mean_long)
    eq_of_time = 4 * math.degrees(
        var_y * math.sin(2 * geom_mean_long_rad)
        - 2 * eccent * math.sin(anom_rad)
        + 4 * eccent * var_y * math.sin(anom_rad) * math.cos(2 * geom_mean_long_rad)
        - 0.5 * var_y * var_y * math.sin(4 * geom_mean_long_rad)
        - 1.25 * eccent * eccent * math.sin(2 * anom_rad)
    )

    # Hour angle
    utc_hours = at.hour + at.minute / 60.0 + at.second / 3600.0
    true_solar_time = (utc_hours * 60 + eq_of_time + 4 * longitude) % 1440
    if true_solar_time < 0:
        true_solar_time += 1440

    hour_angle = true_solar_time / 4 - 180
    if hour_angle < -180:
        hour_angle += 360

    # Solar elevation
    lat_rad = math.radians(latitude)
    decl_rad = math.radians(declination)
    elevation = math.degrees(
        math.asin(
            math.sin(lat_rad) * math.sin(decl_rad)
            + math.cos(lat_rad) * math.cos(decl_rad) * math.cos(math.radians(hour_angle))
        )
    )

    return elevation
