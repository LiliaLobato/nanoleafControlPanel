"""Tests for sun elevation calculation and Julian date helper."""

from datetime import datetime, timezone

import pytest

from noaa_solar import _julian_date
from tests.conftest import _make_instance


class TestSunElevation:
    """Cross-check against known NOAA values for Bellevue, WA (47.6144, -122.1923).

    Tolerance: +/- 0.5 deg as specified in the plan.
    Reference values computed with the same NOAA formula for consistency.
    """

    def _make_weather(self):
        return _make_instance("clear.json")

    @pytest.mark.parametrize("utc_dt, expected_elevation", [
        # Summer solstice noon: Jun 21 2025 12:00 PDT = 19:00 UTC
        (datetime(2025, 6, 21, 19, 0, 0, tzinfo=timezone.utc), 62.02),
        # Winter solstice noon: Dec 21 2025 12:00 PST = 20:00 UTC
        (datetime(2025, 12, 21, 20, 0, 0, tzinfo=timezone.utc), 18.93),
        # Equinox dawn: Mar 20 2025 06:30 PDT = 13:30 UTC (below horizon)
        (datetime(2025, 3, 20, 13, 30, 0, tzinfo=timezone.utc), -7.69),
        # Equinox dusk: Sep 22 2025 19:00 PDT = Sep 23 02:00 UTC (near horizon)
        (datetime(2025, 9, 23, 2, 0, 0, tzinfo=timezone.utc), 0.11),
    ])
    def test_known_elevations(self, utc_dt, expected_elevation):
        w = self._make_weather()
        elevation = w.get_sun_elevation(at=utc_dt)
        assert abs(elevation - expected_elevation) < 0.5, (
            f"Expected elevation ~{expected_elevation} deg at {utc_dt.isoformat()}, "
            f"got {elevation:.2f} deg; NOAA formula result is outside 0.5 deg tolerance."
        )

    def test_summer_noon_is_highest(self):
        """Summer noon should have the highest elevation of the four reference points."""
        w = self._make_weather()
        summer_noon = datetime(2025, 6, 21, 19, 0, 0, tzinfo=timezone.utc)
        elev = w.get_sun_elevation(at=summer_noon)
        assert elev > 60, (
            f"Expected summer solstice noon elevation > 60 deg, "
            f"got {elev:.2f} deg; check NOAA formula for summer solstice inputs."
        )

    def test_below_horizon_negative(self):
        """Before sunrise, elevation should be negative."""
        w = self._make_weather()
        pre_dawn = datetime(2025, 3, 20, 13, 30, 0, tzinfo=timezone.utc)
        elev = w.get_sun_elevation(at=pre_dawn)
        assert elev < 0, (
            f"Expected pre-dawn elevation to be negative (below horizon), "
            f"got {elev:.2f} deg; check NOAA formula for before-sunrise time."
        )

    def test_rejects_naive_datetime(self):
        """Must raise ValueError for naive (non-tz-aware) datetime."""
        w = self._make_weather()
        with pytest.raises(ValueError, match="timezone-aware"):
            w.get_sun_elevation(at=datetime(2025, 6, 21, 12, 0, 0))

    def test_defaults_to_now(self):
        """Calling with no argument should return a float (uses current time)."""
        w = self._make_weather()
        elev = w.get_sun_elevation()
        assert isinstance(elev, float), (
            f"Expected get_sun_elevation() with no args to return a float, "
            f"got {type(elev).__name__}; the default 'at=None' path must resolve to now."
        )
        assert -90 <= elev <= 90, (
            f"Expected sun elevation to be between -90 and 90 deg, "
            f"got {elev:.2f} deg; result is physically impossible."
        )


class TestJulianDate:
    def test_j2000_epoch(self):
        """J2000.0 epoch (2000-01-01 12:00 UTC) should be JD 2451545.0."""
        j2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        jd = _julian_date(j2000)
        assert abs(jd - 2451545.0) < 0.0001, (
            f"Expected JD 2451545.0 for J2000.0 epoch, "
            f"got {jd:.4f}; Julian date formula is incorrect for this reference point."
        )

    def test_known_date(self):
        """2025-06-21 00:00 UTC should give a known JD value near 2460847.5."""
        dt = datetime(2025, 6, 21, 0, 0, 0, tzinfo=timezone.utc)
        jd = _julian_date(dt)
        assert abs(jd - 2460847.5) < 0.01, (
            f"Expected JD ~2460847.5 for 2025-06-21 00:00 UTC, "
            f"got {jd:.4f}; Julian date formula produced an unexpected result."
        )
