"""
Zone 2 Analysis Engine.
Monitors workout data and detects issues like:
- Heart rate out of Zone 2
- Cardiac drift (HR creeping up at constant power)
- Power/HR decoupling (efficiency dropping)
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from collections import deque
import numpy as np


@dataclass
class Alert:
    """Represents an alert to show the user."""
    type: str  # 'hr_high', 'hr_low', 'cardiac_drift', 'decoupling'
    message: str
    severity: str  # 'warning', 'critical'
    timestamp: float = field(default_factory=time.time)


@dataclass
class WorkoutStats:
    """Rolling statistics for the workout."""
    avg_hr: float = 0.0
    avg_power: float = 0.0
    avg_cadence: float = 0.0
    time_in_zone: float = 0.0  # seconds
    time_above_zone: float = 0.0
    time_below_zone: float = 0.0
    efficiency_factor: float = 0.0  # Power / HR ratio
    cardiac_drift_percent: float = 0.0


class ZoneAnalyzer:
    """Analyzes workout data for Zone 2 training quality."""

    def __init__(
        self,
        zone2_low: int = 126,
        zone2_high: int = 140,
        drift_threshold: float = 5.0,
        decoupling_threshold: float = 10.0,
        hr_alert_delay: float = 10.0
    ):
        self.zone2_low = zone2_low
        self.zone2_high = zone2_high
        self.drift_threshold = drift_threshold
        self.decoupling_threshold = decoupling_threshold
        self.hr_alert_delay = hr_alert_delay

        # Data storage for analysis
        self._hr_history: deque = deque(maxlen=3600)  # 1 hour at 1Hz
        self._power_history: deque = deque(maxlen=3600)
        self._cadence_history: deque = deque(maxlen=3600)
        self._timestamps: deque = deque(maxlen=3600)

        # For cardiac drift calculation (first half vs second half)
        self._first_half_hr: List[int] = []
        self._first_half_power: List[int] = []
        self._second_half_hr: List[int] = []
        self._second_half_power: List[int] = []

        # Alert state tracking
        self._hr_out_of_zone_start: Optional[float] = None
        self._last_alert_time: dict = {}
        self._alert_cooldown = 30.0  # seconds between repeat alerts

        # Zone time tracking
        self._time_in_zone = 0.0
        self._time_above = 0.0
        self._time_below = 0.0
        self._last_update_time: Optional[float] = None

        # Workout start time
        self._workout_start: Optional[float] = None

    def update(self, hr: int, power: int, cadence: int, phase: str = 'main') -> List[Alert]:
        """
        Update analyzer with new data point.
        Returns list of any new alerts triggered.

        Args:
            hr: Heart rate in bpm
            power: Power in watts
            cadence: Cadence in rpm
            phase: Current workout phase ('warmup', 'main', 'cooldown', etc.)
        """
        now = time.time()
        alerts = []

        if self._workout_start is None:
            self._workout_start = now

        # Store data
        self._hr_history.append(hr)
        self._power_history.append(power)
        self._cadence_history.append(cadence)
        self._timestamps.append(now)

        # Update zone time tracking (only during main phase)
        if self._last_update_time is not None:
            dt = now - self._last_update_time
            if phase == 'main':
                if self.zone2_low <= hr <= self.zone2_high:
                    self._time_in_zone += dt
                elif hr > self.zone2_high:
                    self._time_above += dt
                else:
                    self._time_below += dt
        self._last_update_time = now

        # Only check HR zone alerts during main phase (not warmup/cooldown)
        if phase == 'main':
            hr_alert = self._check_hr_zone(hr, now)
            if hr_alert:
                alerts.append(hr_alert)

            # Check for cardiac drift (need sufficient data)
            if len(self._hr_history) > 300:  # At least 5 minutes
                drift_alert = self._check_cardiac_drift(now)
                if drift_alert:
                    alerts.append(drift_alert)

            # Check for power/HR decoupling
            if len(self._hr_history) > 300:
                decoupling_alert = self._check_decoupling(now)
                if decoupling_alert:
                    alerts.append(decoupling_alert)

        return alerts

    def _check_hr_zone(self, hr: int, now: float) -> Optional[Alert]:
        """Check if HR is out of Zone 2."""
        in_zone = self.zone2_low <= hr <= self.zone2_high

        if in_zone:
            self._hr_out_of_zone_start = None
            return None

        # HR is out of zone
        if self._hr_out_of_zone_start is None:
            self._hr_out_of_zone_start = now

        # Check if we've been out of zone long enough
        time_out = now - self._hr_out_of_zone_start
        if time_out < self.hr_alert_delay:
            return None

        # Check cooldown
        alert_type = 'hr_high' if hr > self.zone2_high else 'hr_low'
        if not self._can_alert(alert_type, now):
            return None

        self._last_alert_time[alert_type] = now

        if hr > self.zone2_high:
            return Alert(
                type='hr_high',
                message=f"Heart rate too HIGH: {hr} bpm (Zone 2 max: {self.zone2_high}). Ease up!",
                severity='warning' if hr < self.zone2_high + 10 else 'critical'
            )
        else:
            return Alert(
                type='hr_low',
                message=f"Heart rate too LOW: {hr} bpm (Zone 2 min: {self.zone2_low}). Push a bit harder!",
                severity='warning'
            )

    def _check_cardiac_drift(self, now: float) -> Optional[Alert]:
        """
        Check for cardiac drift - HR increasing at same power output.
        Compares first half of workout to second half.
        """
        if not self._can_alert('cardiac_drift', now):
            return None

        n = len(self._hr_history)
        half = n // 2

        first_half_hr = list(self._hr_history)[:half]
        first_half_power = list(self._power_history)[:half]
        second_half_hr = list(self._hr_history)[half:]
        second_half_power = list(self._power_history)[half:]

        # Calculate average HR and power for each half
        avg_hr_1 = np.mean(first_half_hr) if first_half_hr else 0
        avg_hr_2 = np.mean(second_half_hr) if second_half_hr else 0
        avg_power_1 = np.mean(first_half_power) if first_half_power else 0
        avg_power_2 = np.mean(second_half_power) if second_half_power else 0

        if avg_hr_1 == 0 or avg_power_1 == 0:
            return None

        # Calculate efficiency factor for each half (power/hr)
        ef_1 = avg_power_1 / avg_hr_1
        ef_2 = avg_power_2 / avg_hr_2 if avg_hr_2 > 0 else 0

        # Cardiac drift = decrease in efficiency
        if ef_1 > 0:
            drift_percent = ((ef_1 - ef_2) / ef_1) * 100

            if drift_percent > self.drift_threshold:
                self._last_alert_time['cardiac_drift'] = now
                return Alert(
                    type='cardiac_drift',
                    message=f"Cardiac drift detected: {drift_percent:.1f}%. Your HR is creeping up - sign of fatigue.",
                    severity='warning' if drift_percent < 10 else 'critical'
                )

        return None

    def _check_decoupling(self, now: float) -> Optional[Alert]:
        """
        Check for power/HR decoupling - efficiency dropping significantly.
        """
        if not self._can_alert('decoupling', now):
            return None

        # Look at last 5 minutes vs previous 5 minutes
        recent_hr = list(self._hr_history)[-300:]
        recent_power = list(self._power_history)[-300:]
        older_hr = list(self._hr_history)[-600:-300] if len(self._hr_history) > 600 else []
        older_power = list(self._power_history)[-600:-300] if len(self._power_history) > 600 else []

        if not older_hr or not older_power:
            return None

        avg_hr_recent = np.mean(recent_hr)
        avg_power_recent = np.mean(recent_power)
        avg_hr_older = np.mean(older_hr)
        avg_power_older = np.mean(older_power)

        if avg_hr_older == 0 or avg_power_older == 0:
            return None

        # Check if power dropped but HR stayed same/increased
        power_change = ((avg_power_recent - avg_power_older) / avg_power_older) * 100
        hr_change = ((avg_hr_recent - avg_hr_older) / avg_hr_older) * 100

        # Decoupling: power down, HR up or stable
        if power_change < -5 and hr_change > -2:
            decoupling = abs(power_change - hr_change)
            if decoupling > self.decoupling_threshold:
                self._last_alert_time['decoupling'] = now
                return Alert(
                    type='decoupling',
                    message=f"Power/HR decoupling: Power down {abs(power_change):.1f}% but HR unchanged. Consider ending soon.",
                    severity='warning'
                )

        return None

    def _can_alert(self, alert_type: str, now: float) -> bool:
        """Check if we can fire an alert (cooldown passed)."""
        last = self._last_alert_time.get(alert_type, 0)
        return (now - last) > self._alert_cooldown

    def get_stats(self) -> WorkoutStats:
        """Get current workout statistics."""
        hr_list = list(self._hr_history)
        power_list = list(self._power_history)
        cadence_list = list(self._cadence_history)

        avg_hr = np.mean(hr_list) if hr_list else 0
        avg_power = np.mean(power_list) if power_list else 0
        avg_cadence = np.mean(cadence_list) if cadence_list else 0

        # Calculate efficiency factor
        ef = avg_power / avg_hr if avg_hr > 0 else 0

        # Calculate cardiac drift
        n = len(hr_list)
        drift = 0.0
        if n > 300:
            half = n // 2
            ef_1 = np.mean(power_list[:half]) / np.mean(hr_list[:half]) if np.mean(hr_list[:half]) > 0 else 0
            ef_2 = np.mean(power_list[half:]) / np.mean(hr_list[half:]) if np.mean(hr_list[half:]) > 0 else 0
            if ef_1 > 0:
                drift = ((ef_1 - ef_2) / ef_1) * 100

        return WorkoutStats(
            avg_hr=avg_hr,
            avg_power=avg_power,
            avg_cadence=avg_cadence,
            time_in_zone=self._time_in_zone,
            time_above_zone=self._time_above,
            time_below_zone=self._time_below,
            efficiency_factor=ef,
            cardiac_drift_percent=drift
        )

    def get_zone_status(self, hr: int) -> Tuple[str, str]:
        """
        Get current zone status.
        Returns (status, color) tuple.
        """
        if hr < self.zone2_low:
            return ("BELOW ZONE 2", "blue")
        elif hr > self.zone2_high:
            return ("ABOVE ZONE 2", "red")
        else:
            return ("IN ZONE 2", "green")

    def reset(self):
        """Reset analyzer for a new workout."""
        self._hr_history.clear()
        self._power_history.clear()
        self._cadence_history.clear()
        self._timestamps.clear()
        self._hr_out_of_zone_start = None
        self._last_alert_time.clear()
        self._time_in_zone = 0.0
        self._time_above = 0.0
        self._time_below = 0.0
        self._last_update_time = None
        self._workout_start = None

    def update_zones(self, zone2_low: int, zone2_high: int):
        """Update Zone 2 boundaries."""
        self.zone2_low = zone2_low
        self.zone2_high = zone2_high
