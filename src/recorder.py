"""
Voice Pill — Audio Recorder
=============================
Manages audio input: device enumeration, stream creation,
real-time capture with amplitude calculation, WAV generation.

Uses sounddevice for cross-platform audio capture with
real-time callback for amplitude streaming to UI.
"""

import io
import time
import threading
import numpy as np
import sounddevice as sd
import scipy.io.wavfile

from PyQt6.QtCore import QObject, pyqtSignal

from config import (
    AUDIO_SAMPLE_RATE,
    AUDIO_CHANNELS,
    AUDIO_CHUNK_SIZE,
    AUDIO_DTYPE,
    DEFAULT_DEVICE,
    AMPLITUDE_NORMALIZATION_CEILING,
    AMPLITUDE_SMOOTHING,
    MIN_RECORDING_DURATION,
    TRIM_TRAILING_SILENCE,
    TRIM_SILENCE_PADDING_MS,
    TRIM_NOISE_FLOOR_MULTIPLIER,
    SILENCE_DETECTION_ENABLED,
)
from silence import SilenceDetector


class AudioDevice:
    """Represents an audio input device."""

    def __init__(self, device_id: int, name: str, channels: int,
                 sample_rate: float, is_default: bool):
        self.device_id = device_id
        self.name = name
        self.channels = channels
        self.sample_rate = sample_rate
        self.is_default = is_default

    def __repr__(self):
        default_marker = " (default)" if self.is_default else ""
        return f"AudioDevice({self.device_id}: {self.name}{default_marker})"


class AudioRecorder(QObject):
    """
    Audio capture manager with real-time amplitude streaming.

    Signals:
        amplitude_updated(float): Emitted with normalized amplitude (0.0-1.0)
                                  every audio chunk (~64ms). Thread-safe via Qt signal.
        recording_error(str): Emitted when an audio error occurs.

    Usage:
        recorder = AudioRecorder()
        recorder.amplitude_updated.connect(widget.update_bars)
        recorder.start_recording()
        # ... recording ...
        wav_bytes = recorder.stop_recording()
    """

    amplitude_updated = pyqtSignal(float)
    recording_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._stream: sd.InputStream | None = None
        self._audio_buffer: list[np.ndarray] = []
        self._buffer_lock = threading.Lock()
        self._is_recording = False
        self._record_start_time: float = 0.0
        self._selected_device: int | None = DEFAULT_DEVICE
        self._smoothed_amplitude: float = 0.0
        self._last_audio_duration: float = 0.0  # Duration (s) of last trimmed recording
        self._noise_calibration_samples: list[float] = []
        self._calibrated_noise_threshold: float = 350.0

        # Silence detector
        self.silence_detector = SilenceDetector()

        # Device cache
        self._devices: list[AudioDevice] = []
        self.refresh_devices()

    # ──────────────────────────────────────────
    # Device Management
    # ──────────────────────────────────────────

    def refresh_devices(self) -> list[AudioDevice]:
        """
        Re-enumerate all audio input devices.
        Call when user plugs in USB/Bluetooth mic or clicks "Refresh".

        Returns:
            List of AudioDevice objects.
        """
        self._devices = []

        # Keywords that indicate output/loopback/virtual devices (not real microphones)
        _OUTPUT_KEYWORDS = (
            'speaker', 'speakers', 'output', 'headphone', 'headphones',
            'hdmi', 'display audio', 'digital audio', 'realtek digital',
            'stereo mix', 'loopback', 'what u hear', 'wave out',
            'spdif', 'optical', 'mapper', 'primary sound', 'sound mapper',
        )

        # Host-API preference: WASAPI gives the cleanest modern capture on Windows,
        # ALSA/Pulse/PipeWire on Linux. Lower = better rank.
        _HOSTAPI_PREFERENCE = {
            'windows wasapi': 0,
            'mme': 1,
            'windows directsound': 2,
            'windows wdm-ks': 3,
            'alsa': 0,
            'pulseaudio': 0,
            'pulse': 0,
            'pipewire': 0,
            'jack audio connection kit': 1,
            'jack': 1,
            'core audio': 0,
        }


        def _clean_name(raw: str) -> str:
            """Normalize a device name for dedup (lowercase + collapse whitespace)."""
            return " ".join(raw.strip().lower().split())

        def _is_junk_name(raw: str) -> bool:
            """
            True for WDM-KS / kernel-streaming artifact names that aren't real,
            user-facing devices, e.g. 'Input (@System32\\drivers\\bthhfenum.sys,...)'
            or empty-parens duplicates like 'Microphone Array 1 ()'.
            """
            n = raw.strip()
            low = n.lower()
            if n.startswith("@") or "(@" in n or "\\drivers\\" in low:
                return True
            if n.endswith("()") or n.endswith("( )"):
                return True
            return False

        try:
            all_devices = sd.query_devices()
            try:
                hostapis = sd.query_hostapis()
            except Exception:
                hostapis = []
            default_input = sd.default.device[0]  # Default input device index

            # The default index may point to a host-API variant we dedup away, so
            # remember the default device's NAME and match by name instead of index.
            default_norm = ""
            try:
                if isinstance(default_input, int) and 0 <= default_input < len(all_devices):
                    default_norm = _clean_name(all_devices[default_input]['name'])
            except Exception:
                default_norm = ""

            # Build candidate list (filtered), tagged with host-API rank
            candidates = []
            for i, dev in enumerate(all_devices):
                if dev.get('max_input_channels', 0) <= 0:
                    continue

                name = dev['name']
                name_lower = name.lower()

                # Skip output/loopback/virtual devices masquerading as inputs
                if any(kw in name_lower for kw in _OUTPUT_KEYWORDS):
                    continue
                # Skip kernel-streaming junk / empty-parens duplicates
                if _is_junk_name(name):
                    continue

                hostapi_idx = dev.get('hostapi', -1)
                hostapi_name = ""
                if 0 <= hostapi_idx < len(hostapis):
                    hostapi_name = hostapis[hostapi_idx].get('name', '').lower()
                rank = _HOSTAPI_PREFERENCE.get(hostapi_name, 99)

                candidates.append({
                    'index': i,
                    'name': name,
                    'norm': _clean_name(name),
                    'channels': dev['max_input_channels'],
                    'sample_rate': dev['default_samplerate'],
                    'rank': rank,
                })

            # Best host API first (WASAPI gives FULL names; MME truncates to 31 chars).
            candidates.sort(key=lambda c: c['rank'])

            # Prefix-aware dedup: two names are the same device if identical, OR one
            # is a prefix of the other AND the short one is near MME's 31-char limit
            # (i.e. it's a truncated version of the full name). Because WASAPI comes
            # first, the FULL name is accepted and the truncated MME copy is dropped.
            accepted = []  # list of normalized names already kept

            for cand in candidates:
                nk = cand['norm']
                is_dup = False
                for ak in accepted:
                    short, long = (nk, ak) if len(nk) <= len(ak) else (ak, nk)
                    if short == long:
                        is_dup = True
                        break
                    if len(short) >= 28 and long.startswith(short):
                        is_dup = True  # MME truncation of the same device
                        break
                if is_dup:
                    continue

                # Validate the device can actually be opened before committing
                if not self._is_device_openable(cand['index'], cand['sample_rate']):
                    continue

                accepted.append(nk)

                # Default match by name (prefix-aware), since the system default may
                # be a different host-API variant of this same device.
                is_default = False
                if default_norm:
                    s, l = (nk, default_norm) if len(nk) <= len(default_norm) else (default_norm, nk)
                    if s == l or (len(s) >= 28 and l.startswith(s)):
                        is_default = True

                self._devices.append(AudioDevice(
                    device_id=cand['index'],
                    name=self._friendly_name(cand['name']),
                    channels=cand['channels'],
                    sample_rate=cand['sample_rate'],
                    is_default=is_default,
                ))

            # Stable, friendly ordering: default first, then alphabetical
            self._devices.sort(key=lambda d: (not d.is_default, d.name.lower()))

        except Exception as e:
            self.recording_error.emit(f"Device enumeration failed: {e}")

        return self._devices

    @staticmethod
    def _friendly_name(raw: str) -> str:
        """Tidy a device name for display (trim length, strip leading index tokens)."""
        n = raw.strip()
        if len(n) > 40:
            n = n[:39].rstrip() + "…"
        return n

    def _is_device_openable(self, device_index: int, sample_rate: float) -> bool:
        """
        Confirm a device can actually be opened for input. Filters out phantom/
        disconnected devices that Windows still lists. Cheap check_settings call.
        """
        try:
            sd.check_input_settings(
                device=device_index,
                channels=AUDIO_CHANNELS,
                dtype=AUDIO_DTYPE,
                samplerate=AUDIO_SAMPLE_RATE,
            )
            return True
        except Exception:
            # Fall back to the device's own default sample rate before rejecting
            try:
                sd.check_input_settings(
                    device=device_index,
                    channels=AUDIO_CHANNELS,
                    dtype=AUDIO_DTYPE,
                )
                return True
            except Exception:
                return False

    def get_devices(self) -> list[AudioDevice]:
        """Get cached list of audio input devices."""
        return self._devices

    def select_device(self, device_id: int) -> None:
        """
        Select a specific audio input device.
        Takes effect on next start_recording() call.

        Args:
            device_id: The device index from AudioDevice.device_id.
        """
        self._selected_device = device_id

    def get_selected_device(self) -> int | None:
        """Get currently selected device ID (None = system default)."""
        return self._selected_device

    def get_selected_device_name(self) -> str:
        """Get the name of the currently selected device."""
        if self._selected_device is None:
            # Find default device
            for dev in self._devices:
                if dev.is_default:
                    return dev.name
            return "System Default"

        for dev in self._devices:
            if dev.device_id == self._selected_device:
                return dev.name

        return "Unknown Device"

    # ──────────────────────────────────────────
    # Recording Control
    # ──────────────────────────────────────────

    def start_recording(self) -> bool:
        """
        Start audio capture.

        Opens a sounddevice InputStream and begins writing
        audio chunks to the buffer. Amplitude is emitted
        via the amplitude_updated signal for UI animation.

        Returns:
            True if recording started, False on error.
        """
        if self._is_recording:
            return False

        # Reset state
        with self._buffer_lock:
            self._audio_buffer = []
        self._smoothed_amplitude = 0.0
        self._noise_calibration_samples = []
        self._calibrated_noise_threshold = 600.0

        try:
            self._stream = sd.InputStream(
                samplerate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                dtype=AUDIO_DTYPE,
                blocksize=AUDIO_CHUNK_SIZE,
                device=self._selected_device,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._is_recording = True
            self._record_start_time = time.monotonic()

            # Start silence detection
            if SILENCE_DETECTION_ENABLED:
                self.silence_detector.start()

            return True

        except sd.PortAudioError as e:
            self.recording_error.emit(f"Microphone error: {e}")
            return False
        except Exception as e:
            self.recording_error.emit(f"Recording failed: {e}")
            return False

    def stop_recording(self) -> bytes | None:
        """
        Stop audio capture and return WAV bytes.

        Returns:
            WAV file as bytes, or None if recording was too short
            or an error occurred.
        """
        if not self._is_recording:
            return None

        self._is_recording = False
        if SILENCE_DETECTION_ENABLED:
            self.silence_detector.stop()

        # Stop and close stream
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception:
            self._stream = None

        # Check minimum duration
        duration = time.monotonic() - self._record_start_time
        if duration < MIN_RECORDING_DURATION:
            return None  # Too short — accidental activation

        # No audio captured
        with self._buffer_lock:
            captured_chunks = self._audio_buffer
            self._audio_buffer = []

        if not captured_chunks:
            return None

        # Build WAV bytes
        try:
            audio_data = np.concatenate(captured_chunks, axis=0)

            # Trim leading/trailing silence — the single biggest anti-hallucination
            # win for long recordings. Whisper invents text to fill trailing silence.
            if TRIM_TRAILING_SILENCE:
                audio_data = self._trim_silence(audio_data)

            if audio_data is None or len(audio_data) == 0:
                self._last_audio_duration = 0.0
                return None

            # Record the post-trim duration for downstream plausibility checks
            self._last_audio_duration = len(audio_data) / float(AUDIO_SAMPLE_RATE)

            wav_buffer = io.BytesIO()
            scipy.io.wavfile.write(wav_buffer, AUDIO_SAMPLE_RATE, audio_data)
            wav_buffer.seek(0)
            return wav_buffer.read()
        except Exception as e:
            self.recording_error.emit(f"WAV generation failed: {e}")
            return None

    def slice_chunk_buffer(self) -> tuple[bytes | None, float]:
        """
        Atomically pop accumulated audio buffer into WAV bytes for streaming chunk.
        Continuous recording remains active.

        Returns:
            Tuple of (wav_bytes, duration_seconds)
        """
        if not self._is_recording:
            return None, 0.0

        with self._buffer_lock:
            if not self._audio_buffer:
                return None, 0.0
            chunks = self._audio_buffer
            self._audio_buffer = []

        try:
            audio_data = np.concatenate(chunks, axis=0)
            raw_duration = len(audio_data) / float(AUDIO_SAMPLE_RATE)

            if raw_duration < 0.4:
                return None, 0.0

            if TRIM_TRAILING_SILENCE:
                trimmed_data = self._trim_silence(audio_data)
                if trimmed_data is not None and len(trimmed_data) > 0:
                    audio_data = trimmed_data

            chunk_duration = len(audio_data) / float(AUDIO_SAMPLE_RATE)
            wav_buffer = io.BytesIO()
            scipy.io.wavfile.write(wav_buffer, AUDIO_SAMPLE_RATE, audio_data)
            wav_buffer.seek(0)
            return wav_buffer.read(), chunk_duration
        except Exception as e:
            self.recording_error.emit(f"Streaming chunk slice failed: {e}")
            return None, 0.0

    @property
    def last_audio_duration(self) -> float:
        """Duration (seconds) of the most recent recording AFTER silence trimming."""
        return self._last_audio_duration

    def _trim_silence(self, audio_data: np.ndarray) -> np.ndarray:
        """
        Trim leading and trailing silence from the captured audio.

        Uses frame-wise RMS with an adaptive noise floor. Keeps a short padding
        of audio after the last detected speech so natural sentence tails aren't cut.

        Args:
            audio_data: int16 numpy array (mono), shape (N,) or (N, 1).

        Returns:
            Trimmed int16 numpy array. Falls back to the original on any issue.
        """
        try:
            samples = audio_data.reshape(-1).astype(np.float32)
            total = len(samples)
            if total == 0:
                return audio_data

            # 20ms analysis frames
            frame_len = max(1, int(AUDIO_SAMPLE_RATE * 0.02))
            n_frames = total // frame_len
            if n_frames < 2:
                return audio_data  # Too short to meaningfully trim

            trimmed_len = n_frames * frame_len
            frames = samples[:trimmed_len].reshape(n_frames, frame_len)
            # Per-frame RMS
            rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-9)

            # Adaptive noise floor: 10th percentile of frame energy is "quiet".
            # We trim CONSERVATIVELY — only clearly-silent regions — because losing
            # the user's actual words is far worse than leaving a little silence.
            noise_floor = float(np.percentile(rms, 10))
            speech_threshold = max(
                noise_floor * TRIM_NOISE_FLOOR_MULTIPLIER,
                AMPLITUDE_NORMALIZATION_CEILING * 0.005,  # low absolute floor vs digital noise
            )

            speech_frames = np.where(rms > speech_threshold)[0]
            if len(speech_frames) == 0:
                # Could not confidently locate speech — DO NOT discard the audio.
                # Send the original so Whisper still gets a chance. Recall first.
                return audio_data

            first_frame = int(speech_frames[0])
            last_frame = int(speech_frames[-1])

            pad_samples = int(AUDIO_SAMPLE_RATE * (TRIM_SILENCE_PADDING_MS / 1000.0))
            start = max(0, first_frame * frame_len - pad_samples)
            end = min(total, (last_frame + 1) * frame_len + pad_samples)

            if end <= start:
                return audio_data

            trimmed = audio_data.reshape(-1)[start:end]
            # Safety: never return a near-empty clip — fall back to original
            if len(trimmed) < frame_len:
                return audio_data
            # Preserve original 2D shape if it had one
            if audio_data.ndim == 2:
                trimmed = trimmed.reshape(-1, 1)
            return trimmed
        except Exception:
            # Never let trimming break the pipeline — fall back to original audio
            return audio_data



    @property
    def is_recording(self) -> bool:
        """Whether audio is currently being captured."""
        return self._is_recording

    @property
    def recording_duration(self) -> float:
        """Current recording duration in seconds."""
        if not self._is_recording:
            return 0.0
        return time.monotonic() - self._record_start_time

    # ──────────────────────────────────────────
    # Audio Callback (runs on sounddevice thread)
    # ──────────────────────────────────────────

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status):
        """
        Called by sounddevice for every audio chunk.
        Runs on sounddevice's dedicated thread — NOT the Qt main thread.

        IMPORTANT: We must NOT call Qt widget methods directly here.
        Use pyqtSignal (amplitude_updated) for thread-safe communication.
        """
        if status:
            # Stream error (input overflow, device disconnect, etc.)
            if status.input_overflow:
                pass  # Minor — some samples lost, not critical
            else:
                # Serious error — device may be disconnected
                self.recording_error.emit(f"Audio stream error: {status}")
                return

        if not self._is_recording:
            return

        # 1. Append raw audio to buffer (copy! indata is reused by sounddevice)
        with self._buffer_lock:
            self._audio_buffer.append(indata.copy())

        # 2. Calculate RMS amplitude
        audio_float = indata.astype(np.float32)
        rms = float(np.sqrt(np.mean(audio_float ** 2)))

        # 3. Dynamic background noise floor calibration (samples initial 0.5s of recording)
        elapsed = time.monotonic() - self._record_start_time
        if elapsed <= 0.5:
            self._noise_calibration_samples.append(rms)
            ambient_median = float(np.median(self._noise_calibration_samples))
            self._calibrated_noise_threshold = max(ambient_median * 2.0, 600.0)

        # Recognize human speech vs ambient room noise:
        # Anything <= _calibrated_noise_threshold is ambient noise (0.0 -> dots)
        # Anything > _calibrated_noise_threshold is human speech (1.0 -> full bar animation)
        if rms <= self._calibrated_noise_threshold:
            normalized = 0.0
        else:
            normalized = 1.0

        # 4. Apply exponential moving average (EMA) smoothing
        self._smoothed_amplitude = (
            self._smoothed_amplitude * (1.0 - AMPLITUDE_SMOOTHING)
            + normalized * AMPLITUDE_SMOOTHING
        )

        # 5. Emit smoothed amplitude for UI (thread-safe Qt signal)
        self.amplitude_updated.emit(self._smoothed_amplitude)

        # 6. Feed raw amplitude to silence detector
        if SILENCE_DETECTION_ENABLED:
            self.silence_detector.feed(normalized)

    # ──────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────

    def cleanup(self):
        """Release all audio resources. Called on app exit."""
        self._is_recording = False
        self.silence_detector.stop()
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception:
            self._stream = None
