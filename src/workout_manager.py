"""
Workout Manager - handles structured workouts with ERG mode.
Supports warmup ramps, steady-state intervals, and cooldowns.
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Dict
from enum import Enum


class WorkoutPhase(Enum):
    NOT_STARTED = "not_started"
    WARMUP = "warmup"
    MAIN = "main"
    INTERVAL = "interval"
    RECOVERY = "recovery"
    COOLDOWN = "cooldown"
    COMPLETED = "completed"


class WorkoutType(Enum):
    ZONE2 = "zone2"
    VO2MAX = "vo2max"
    SWEET_SPOT = "sweet_spot"
    TEMPO = "tempo"


# Workout definitions with metadata
WORKOUT_LIBRARY: Dict[str, dict] = {
    "zone2": {
        "name": "Zone 2 (HR Targeted)",
        "description": "HR-targeted endurance - power auto-adjusts to keep HR in zone",
        "frequency_hint": "Power auto-adjusts to maintain target HR",
        "duration_minutes": 60,
        "intensity": "Low"
    },
    "vo2max": {
        "name": "VO2max Intervals",
        "description": "5x3min hard intervals - builds aerobic capacity (1x/week if only cycling once)",
        "frequency_hint": "1x per week - best if only doing 1 cycling session",
        "duration_minutes": 35,
        "intensity": "High"
    },
    "sweet_spot": {
        "name": "Sweet Spot",
        "description": "2x20min @ 88-93% FTP - efficient endurance builder (1x/week)",
        "frequency_hint": "1x per week alongside Zone 2 sessions",
        "duration_minutes": 55,
        "intensity": "Medium-High"
    },
    "tempo": {
        "name": "Tempo/Threshold",
        "description": "2x15min @ 95-100% FTP - lactate tolerance (1x/week)",
        "frequency_hint": "1x per week alongside Zone 2 sessions",
        "duration_minutes": 45,
        "intensity": "High"
    }
}


@dataclass
class WorkoutSegment:
    """A segment of the workout."""
    name: str
    duration_seconds: int
    target_power_start: int  # Starting power (for ramps)
    target_power_end: int    # Ending power (for ramps, same as start for steady)
    phase: WorkoutPhase

    def get_power_at_time(self, elapsed_seconds: int) -> int:
        """Get target power at a specific time within this segment."""
        if self.target_power_start == self.target_power_end:
            return self.target_power_start

        # Linear interpolation for ramps
        progress = min(1.0, elapsed_seconds / self.duration_seconds)
        power = self.target_power_start + (self.target_power_end - self.target_power_start) * progress
        return int(power)


@dataclass
class WorkoutConfig:
    """Configuration for a workout based on FTP."""
    ftp: int = 215

    # HR Zone 2 targets (configured by user)
    hr_zone2_low: int = 124
    hr_zone2_high: int = 143

    # Zone 2 is typically 56-75% of FTP, we'll use 65% as starting point for HR-targeted mode
    @property
    def zone2_power(self) -> int:
        return int(self.ftp * 0.65)  # Starting power for HR-targeted mode

    @property
    def zone2_low(self) -> int:
        return int(self.ftp * 0.50)  # Min power bound for HR targeting

    @property
    def zone2_high(self) -> int:
        return int(self.ftp * 0.80)  # Max power bound for HR targeting

    @property
    def warmup_start_power(self) -> int:
        return int(self.ftp * 0.40)

    @property
    def cooldown_end_power(self) -> int:
        return int(self.ftp * 0.40)

    @property
    def hr_target(self) -> int:
        """Target HR is middle of Zone 2."""
        return (self.hr_zone2_low + self.hr_zone2_high) // 2


class WorkoutManager:
    """Manages structured workouts with ERG mode control."""

    def __init__(self, ftp: int = 215, hr_zone2_low: int = 124, hr_zone2_high: int = 143):
        self.config = WorkoutConfig(ftp=ftp, hr_zone2_low=hr_zone2_low, hr_zone2_high=hr_zone2_high)
        self.segments: List[WorkoutSegment] = []
        self.current_segment_index: int = -1
        self.workout_start_time: Optional[float] = None
        self.segment_start_time: Optional[float] = None

        self.on_power_change: Optional[Callable[[int], None]] = None
        self.on_phase_change: Optional[Callable[[WorkoutPhase, str], None]] = None
        self.on_workout_complete: Optional[Callable[[], None]] = None

        self._last_target_power = 0
        self._is_running = False
        self._current_workout_type = "zone2"

        # HR-targeted power control for Zone 2
        self._hr_target_mode = False  # Only active for Zone 2 main phase
        self._current_adaptive_power = 0  # Current power in HR-target mode
        self._last_hr_adjustment_time = 0.0
        self._hr_adjustment_interval = 30.0  # Adjust every 30 seconds
        self._power_step = 5  # Adjust by 5W at a time
        self._hr_samples: List[int] = []  # Recent HR samples for averaging

        # Build default workout: 5min warmup, 50min Z2, 5min cooldown
        self._build_workout("zone2")

    def _build_workout(self, workout_type: str):
        """Build workout segments based on type."""
        self._current_workout_type = workout_type
        ftp = self.config.ftp

        if workout_type == "zone2":
            # Zone 2: 5min warmup, 50min steady Z2, 5min cooldown
            self.segments = [
                WorkoutSegment(
                    name="Warmup",
                    duration_seconds=5 * 60,
                    target_power_start=self.config.warmup_start_power,
                    target_power_end=self.config.zone2_power,
                    phase=WorkoutPhase.WARMUP
                ),
                WorkoutSegment(
                    name="Zone 2",
                    duration_seconds=50 * 60,
                    target_power_start=self.config.zone2_power,
                    target_power_end=self.config.zone2_power,
                    phase=WorkoutPhase.MAIN
                ),
                WorkoutSegment(
                    name="Cooldown",
                    duration_seconds=5 * 60,
                    target_power_start=self.config.zone2_power,
                    target_power_end=self.config.cooldown_end_power,
                    phase=WorkoutPhase.COOLDOWN
                ),
            ]

        elif workout_type == "vo2max":
            # VO2max: 5min warmup, 5x(3min @ 120% + 3min recovery), 5min cooldown
            vo2_power = int(ftp * 1.20)
            recovery_power = int(ftp * 0.50)

            self.segments = [
                WorkoutSegment(
                    name="Warmup",
                    duration_seconds=5 * 60,
                    target_power_start=self.config.warmup_start_power,
                    target_power_end=int(ftp * 0.65),
                    phase=WorkoutPhase.WARMUP
                ),
            ]

            # 5 intervals
            for i in range(5):
                self.segments.append(WorkoutSegment(
                    name=f"Interval {i+1}",
                    duration_seconds=3 * 60,
                    target_power_start=vo2_power,
                    target_power_end=vo2_power,
                    phase=WorkoutPhase.INTERVAL
                ))
                if i < 4:  # No recovery after last interval
                    self.segments.append(WorkoutSegment(
                        name=f"Recovery {i+1}",
                        duration_seconds=3 * 60,
                        target_power_start=recovery_power,
                        target_power_end=recovery_power,
                        phase=WorkoutPhase.RECOVERY
                    ))

            self.segments.append(WorkoutSegment(
                name="Cooldown",
                duration_seconds=5 * 60,
                target_power_start=recovery_power,
                target_power_end=self.config.cooldown_end_power,
                phase=WorkoutPhase.COOLDOWN
            ))

        elif workout_type == "sweet_spot":
            # Sweet Spot: 5min warmup, 2x(20min @ 90% FTP + 5min recovery), 5min cooldown
            ss_power = int(ftp * 0.90)
            recovery_power = int(ftp * 0.55)

            self.segments = [
                WorkoutSegment(
                    name="Warmup",
                    duration_seconds=5 * 60,
                    target_power_start=self.config.warmup_start_power,
                    target_power_end=int(ftp * 0.65),
                    phase=WorkoutPhase.WARMUP
                ),
                WorkoutSegment(
                    name="Sweet Spot 1",
                    duration_seconds=20 * 60,
                    target_power_start=ss_power,
                    target_power_end=ss_power,
                    phase=WorkoutPhase.MAIN
                ),
                WorkoutSegment(
                    name="Recovery",
                    duration_seconds=5 * 60,
                    target_power_start=recovery_power,
                    target_power_end=recovery_power,
                    phase=WorkoutPhase.RECOVERY
                ),
                WorkoutSegment(
                    name="Sweet Spot 2",
                    duration_seconds=20 * 60,
                    target_power_start=ss_power,
                    target_power_end=ss_power,
                    phase=WorkoutPhase.MAIN
                ),
                WorkoutSegment(
                    name="Cooldown",
                    duration_seconds=5 * 60,
                    target_power_start=recovery_power,
                    target_power_end=self.config.cooldown_end_power,
                    phase=WorkoutPhase.COOLDOWN
                ),
            ]

        elif workout_type == "tempo":
            # Tempo: 5min warmup, 2x(15min @ 97% FTP + 5min recovery), 5min cooldown
            tempo_power = int(ftp * 0.97)
            recovery_power = int(ftp * 0.55)

            self.segments = [
                WorkoutSegment(
                    name="Warmup",
                    duration_seconds=5 * 60,
                    target_power_start=self.config.warmup_start_power,
                    target_power_end=int(ftp * 0.65),
                    phase=WorkoutPhase.WARMUP
                ),
                WorkoutSegment(
                    name="Tempo 1",
                    duration_seconds=15 * 60,
                    target_power_start=tempo_power,
                    target_power_end=tempo_power,
                    phase=WorkoutPhase.MAIN
                ),
                WorkoutSegment(
                    name="Recovery",
                    duration_seconds=5 * 60,
                    target_power_start=recovery_power,
                    target_power_end=recovery_power,
                    phase=WorkoutPhase.RECOVERY
                ),
                WorkoutSegment(
                    name="Tempo 2",
                    duration_seconds=15 * 60,
                    target_power_start=tempo_power,
                    target_power_end=tempo_power,
                    phase=WorkoutPhase.MAIN
                ),
                WorkoutSegment(
                    name="Cooldown",
                    duration_seconds=5 * 60,
                    target_power_start=recovery_power,
                    target_power_end=self.config.cooldown_end_power,
                    phase=WorkoutPhase.COOLDOWN
                ),
            ]

    def set_workout_type(self, workout_type: str):
        """Change the workout type."""
        if workout_type in WORKOUT_LIBRARY:
            self._build_workout(workout_type)

    def get_workout_types(self) -> List[dict]:
        """Get list of available workout types with metadata."""
        return [
            {"id": k, **v} for k, v in WORKOUT_LIBRARY.items()
        ]

    @property
    def current_workout_type(self) -> str:
        return self._current_workout_type

    def set_ftp(self, ftp: int):
        """Update FTP and rebuild workout."""
        self.config = WorkoutConfig(
            ftp=ftp,
            hr_zone2_low=self.config.hr_zone2_low,
            hr_zone2_high=self.config.hr_zone2_high
        )
        self._build_workout(self._current_workout_type)

    def set_hr_zones(self, hr_low: int, hr_high: int):
        """Update HR Zone 2 boundaries."""
        self.config = WorkoutConfig(
            ftp=self.config.ftp,
            hr_zone2_low=hr_low,
            hr_zone2_high=hr_high
        )

    def start(self):
        """Start the workout."""
        self.workout_start_time = time.time()
        self.segment_start_time = time.time()
        self.current_segment_index = 0
        self._is_running = True

        # Reset HR-targeting state
        self._hr_target_mode = False
        self._current_adaptive_power = self.config.zone2_power
        self._last_hr_adjustment_time = time.time()
        self._hr_samples.clear()

        segment = self.segments[0]
        if self.on_phase_change:
            self.on_phase_change(segment.phase, segment.name)

        # Set initial power
        initial_power = segment.get_power_at_time(0)
        self._last_target_power = initial_power
        if self.on_power_change:
            self.on_power_change(initial_power)

    def stop(self):
        """Stop the workout."""
        self._is_running = False
        self.current_segment_index = -1
        self._hr_target_mode = False
        self._hr_samples.clear()

    def add_hr_sample(self, hr: int):
        """Add an HR sample for averaging (call this from HR data callback)."""
        if self._hr_target_mode:
            self._hr_samples.append(hr)
            # Keep last 30 samples (~30 seconds of data)
            if len(self._hr_samples) > 30:
                self._hr_samples.pop(0)

    def get_hr_adjusted_power(self) -> Optional[int]:
        """
        Calculate power adjustment based on HR.
        Returns new power target if adjustment needed, None otherwise.
        Only active during Zone 2 main phase.
        """
        if not self._hr_target_mode or len(self._hr_samples) < 10:
            return None

        now = time.time()
        if now - self._last_hr_adjustment_time < self._hr_adjustment_interval:
            return None

        self._last_hr_adjustment_time = now

        # Calculate average HR from recent samples
        avg_hr = sum(self._hr_samples) / len(self._hr_samples)
        target_hr = self.config.hr_target
        hr_low = self.config.hr_zone2_low
        hr_high = self.config.hr_zone2_high

        old_power = self._current_adaptive_power
        new_power = old_power

        # Adjust power based on HR deviation
        if avg_hr > hr_high + 5:
            # HR way too high - bigger reduction
            new_power = old_power - (self._power_step * 2)
        elif avg_hr > hr_high:
            # HR above zone - reduce power
            new_power = old_power - self._power_step
        elif avg_hr < hr_low - 5:
            # HR way too low - bigger increase
            new_power = old_power + (self._power_step * 2)
        elif avg_hr < hr_low:
            # HR below zone - increase power
            new_power = old_power + self._power_step
        elif avg_hr > target_hr + 3:
            # HR above target (but in zone) - slight reduction
            new_power = old_power - self._power_step
        elif avg_hr < target_hr - 3:
            # HR below target (but in zone) - slight increase
            new_power = old_power + self._power_step

        # Clamp to bounds
        new_power = max(self.config.zone2_low, min(self.config.zone2_high, new_power))

        if new_power != old_power:
            self._current_adaptive_power = new_power
            self._last_target_power = new_power
            return new_power

        return None

    @property
    def is_hr_target_mode(self) -> bool:
        """Check if HR-targeted mode is active."""
        return self._hr_target_mode

    @property
    def hr_target(self) -> int:
        """Get target HR for Zone 2."""
        return self.config.hr_target

    def update(self) -> Optional[int]:
        """
        Update workout state. Call this regularly (e.g., every second).
        Returns target power if it changed, None otherwise.
        """
        if not self._is_running or self.current_segment_index < 0:
            return None

        if self.current_segment_index >= len(self.segments):
            # Workout complete
            self._is_running = False
            if self.on_workout_complete:
                self.on_workout_complete()
            if self.on_phase_change:
                self.on_phase_change(WorkoutPhase.COMPLETED, "Complete")
            return None

        now = time.time()
        segment = self.segments[self.current_segment_index]
        segment_elapsed = now - self.segment_start_time

        # Check if segment is complete
        if segment_elapsed >= segment.duration_seconds:
            self.current_segment_index += 1
            self.segment_start_time = now

            if self.current_segment_index >= len(self.segments):
                # Workout complete
                self._is_running = False
                self._hr_target_mode = False
                if self.on_workout_complete:
                    self.on_workout_complete()
                if self.on_phase_change:
                    self.on_phase_change(WorkoutPhase.COMPLETED, "Complete")
                return None

            # Move to next segment
            segment = self.segments[self.current_segment_index]
            segment_elapsed = 0

            # Enable/disable HR-target mode based on phase and workout type
            if self._current_workout_type == "zone2" and segment.phase == WorkoutPhase.MAIN:
                self._hr_target_mode = True
                self._current_adaptive_power = self.config.zone2_power
                self._hr_samples.clear()
                self._last_hr_adjustment_time = now
            else:
                self._hr_target_mode = False

            if self.on_phase_change:
                self.on_phase_change(segment.phase, segment.name)

        # For Zone 2 main phase, use HR-targeted power (handled separately via get_hr_adjusted_power)
        # For other phases/workouts, use segment-defined power
        if self._hr_target_mode:
            target_power = self._current_adaptive_power
        else:
            target_power = segment.get_power_at_time(int(segment_elapsed))

        # Only notify if power changed
        if target_power != self._last_target_power:
            self._last_target_power = target_power
            if self.on_power_change:
                self.on_power_change(target_power)
            return target_power

        return None

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def current_phase(self) -> WorkoutPhase:
        if self.current_segment_index < 0:
            return WorkoutPhase.NOT_STARTED
        if self.current_segment_index >= len(self.segments):
            return WorkoutPhase.COMPLETED
        return self.segments[self.current_segment_index].phase

    @property
    def current_segment_name(self) -> str:
        if self.current_segment_index < 0 or self.current_segment_index >= len(self.segments):
            return ""
        return self.segments[self.current_segment_index].name

    @property
    def target_power(self) -> int:
        return self._last_target_power

    @property
    def total_duration_seconds(self) -> int:
        return sum(s.duration_seconds for s in self.segments)

    @property
    def elapsed_seconds(self) -> float:
        if self.workout_start_time is None:
            return 0.0
        return time.time() - self.workout_start_time

    @property
    def remaining_seconds(self) -> float:
        return max(0, self.total_duration_seconds - self.elapsed_seconds)

    @property
    def segment_elapsed_seconds(self) -> float:
        if self.segment_start_time is None:
            return 0.0
        return time.time() - self.segment_start_time

    @property
    def segment_remaining_seconds(self) -> float:
        if self.current_segment_index < 0 or self.current_segment_index >= len(self.segments):
            return 0.0
        segment = self.segments[self.current_segment_index]
        return max(0, segment.duration_seconds - self.segment_elapsed_seconds)

    def get_workout_summary(self) -> dict:
        """Get a summary of the workout structure."""
        return {
            "ftp": self.config.ftp,
            "zone2_power": self.config.zone2_power,
            "zone2_range": f"{self.config.zone2_low}-{self.config.zone2_high}W",
            "total_duration_minutes": self.total_duration_seconds // 60,
            "hr_target": self.config.hr_target,
            "hr_zone2_low": self.config.hr_zone2_low,
            "hr_zone2_high": self.config.hr_zone2_high,
            "hr_target_mode": self._current_workout_type == "zone2",
            "segments": [
                {
                    "name": s.name,
                    "duration_minutes": s.duration_seconds // 60,
                    "power_start": s.target_power_start,
                    "power_end": s.target_power_end,
                    "phase": s.phase.value
                }
                for s in self.segments
            ]
        }
