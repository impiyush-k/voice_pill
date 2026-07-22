"""
Voice Pill — Silence Detection
================================
Adaptive silence detector that calibrates to ambient noise
and auto-stops recording after 3 seconds of continuous silence.

Algorithm:
  1. Calibration phase (first 500ms): sample ambient noise → compute noise floor
  2. Detection phase: compare amplitude to threshold, track silence duration
  3. Only auto-stop if user has actually spoken (has_spoken guard)
"""

import time
import numpy as np
from config import (
    SILENCE_THRESHOLD_MULTIPLIER,
    SILENCE_DURATION,
    NOISE_CALIBRATION_DURATION,
    AUDIO_SAMPLE_RATE,
    AUDIO_CHUNK_SIZE,
    VAD_SPEECH_FRAMES,
    VAD_SILENCE_FRAMES,
)


class SilenceDetector:
    """
    Adaptive silence detector with noise floor calibration.

    Usage:
        detector = SilenceDetector(on_silence_callback=my_callback)
        detector.start()  # Begin calibration phase

        # Feed amplitude values from audio callback:
        detector.feed(amplitude)  # 0.0 - 1.0

        # When silence is detected, on_silence_callback() is called.
        detector.stop()  # Reset for next recording
    """

    def __init__(self, on_silence_callback=None):
        """
        Args:
            on_silence_callback: Called when silence is detected.
                                 No arguments. Called from the thread
                                 that calls feed().
        """
        self._on_silence = on_silence_callback
        self._active = False
        self.reset()

    def reset(self):
        """Reset all state for a new recording session."""
        self._calibration_samples: list[float] = []
        self._calibrating = False
        self._noise_floor = 0.0
        self._speech_threshold = 0.0
        self._silence_start_time: float | None = None
        self._has_spoken = False
        self._active = False
        self._calibration_start_time: float = 0.0
        # Consecutive-frame counters (noise robustness)
        self._speech_run = 0     # consecutive frames currently above threshold
        self._silence_run = 0    # consecutive frames currently below threshold

    def start(self):
        """Begin a new detection session (starts calibration phase)."""
        self.reset()
        self._active = True
        self._calibrating = True
        self._calibration_start_time = time.monotonic()

    def stop(self):
        """Stop detection."""
        self._active = False

    def feed(self, amplitude: float):
        """
        Feed a new amplitude sample to the detector.
        Called from the audio callback thread.

        Args:
            amplitude: Normalized amplitude value (0.0 - 1.0)
        """
        if not self._active:
            return

        if self._calibrating:
            self._handle_calibration(amplitude)
            return

        self._handle_detection(amplitude)

    def _handle_calibration(self, amplitude: float):
        """Collect samples during calibration phase."""
        self._calibration_samples.append(amplitude)

        elapsed = time.monotonic() - self._calibration_start_time
        if elapsed >= NOISE_CALIBRATION_DURATION:
            self._finish_calibration()

    def _finish_calibration(self):
        """Compute noise floor and speech threshold from calibration data."""
        if self._calibration_samples:
            # Use median (not mean) — robust to outlier spikes (coughs, bumps)
            self._noise_floor = float(np.median(self._calibration_samples))
        else:
            self._noise_floor = 0.0

        # Speech threshold: noise floor × multiplier
        # Clamp minimum to avoid triggering on digital noise in silent rooms
        self._speech_threshold = max(
            self._noise_floor * SILENCE_THRESHOLD_MULTIPLIER,
            0.01  # Absolute minimum threshold
        )

        self._calibrating = False
        self._silence_start_time = None
        self._has_spoken = False

    def _handle_detection(self, amplitude: float):
        """
        Check if amplitude indicates speech or silence.

        Uses consecutive-frame confirmation so a single noise spike does not
        register as speech, and a single quiet frame does not register as silence.
        This makes auto-stop robust in noisy rooms.
        """
        now = time.monotonic()

        if amplitude > self._speech_threshold:
            # Above threshold — count toward a speech run
            self._speech_run += 1
            self._silence_run = 0

            if self._speech_run >= VAD_SPEECH_FRAMES:
                # Confirmed speech
                self._has_spoken = True
                self._silence_start_time = None  # Reset silence timer
        else:
            # Below threshold — count toward a silence run
            self._silence_run += 1
            self._speech_run = 0

            # Only begin timing silence once we've seen enough consecutive quiet frames
            if self._silence_run >= VAD_SILENCE_FRAMES:
                if self._silence_start_time is None:
                    self._silence_start_time = now

                silence_duration = now - self._silence_start_time

                # Only auto-stop if:
                # 1. User has spoken and the silence duration has passed, OR
                # 2. User NEVER spoke, but a longer timeout passed (accidental activation).
                timeout_duration = (
                    SILENCE_DURATION if self._has_spoken else (SILENCE_DURATION * 2.5)
                )

                if silence_duration >= timeout_duration:
                    self._active = False  # Prevent re-triggering
                    if self._on_silence:
                        self._on_silence()

    @property
    def is_calibrating(self) -> bool:
        """Whether we're still in the calibration phase."""
        return self._calibrating

    @property
    def is_active(self) -> bool:
        """Whether the detector is currently running."""
        return self._active

    @property
    def noise_floor(self) -> float:
        """The computed noise floor amplitude."""
        return self._noise_floor

    @property
    def speech_threshold(self) -> float:
        """The computed speech threshold amplitude."""
        return self._speech_threshold

    @property
    def has_spoken(self) -> bool:
        """Whether speech has been detected during this session."""
        return self._has_spoken

    @property
    def is_silent(self) -> bool:
        """Whether current audio is below speech threshold or in a quiet run."""
        return self._silence_run >= VAD_SILENCE_FRAMES or (self._silence_start_time is not None)
