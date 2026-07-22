"""
Voice Pill — Central Configuration
===================================
Every setting, constant, and tunable parameter lives here.
Other modules import from this file — no magic numbers anywhere else.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# Load Environment
# ──────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent
_ENV_PATH = _PROJECT_ROOT / ".env"

load_dotenv(_ENV_PATH, override=False)

# Fallback: try manual parse if dotenv fails (handles weird formats)
def _fallback_load_env():
    """Manual .env parser for edge cases like 'groq api=...'"""
    if not _ENV_PATH.exists():
        return {}
    result = {}
    with open(_ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    return result

_fallback_env = _fallback_load_env()


# ──────────────────────────────────────────────
# API Configuration
# ──────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY") or _fallback_env.get("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
WHISPER_MODEL = "whisper-large-v3-turbo"   # 4 decoder layers (vs 32) — near-identical accuracy, much faster
LLM_MODEL = "llama-3.1-8b-instant"         # ~560 tok/s (vs 70B's ~276 tok/s) — 2x faster, plenty for formatting
API_CONNECT_TIMEOUT = 5   # seconds — time to establish TCP connection; fast-fail if network is dead
API_READ_TIMEOUT = 20     # seconds — time to wait for response body; Whisper+LLM should never need >15s
API_RETRY_COUNT = 0       # NO retries — instant fail, user re-records (faster than waiting)
API_RETRY_DELAY = 0       # unused with 0 retries
RATE_LIMIT_PRECHECK = False # False = bypass local token precheck and let Groq handle 429s reactively

# VPN Mode — larger timeouts + audio resampled to 8 kHz before upload (~2× smaller payload)
# Enabled/disabled by the user via right-click menu. Off by default.
VPN_MODE_ENABLED = False         # master toggle; overridden at runtime via load_vpn_mode()
VPN_CONNECT_TIMEOUT = 10         # seconds — VPN tunnels need more time to establish
VPN_READ_TIMEOUT = 60            # seconds — VPN + Groq may be slow; give it plenty of room
VPN_AUDIO_SAMPLE_RATE = 8000     # Hz — half of normal 16 kHz; Whisper handles 8 kHz perfectly
VPN_SKIP_SSL_VERIFY = True       # Disable SSL cert verification in VPN mode — fixes 403 Forbidden
                                  # caused by corporate VPN SSL inspection (MITM proxy).
                                  # Safe for Groq: we still use TLS encryption, just skip cert check.
VPN_MODE_SAVE_FILE = _PROJECT_ROOT / "vpn_mode.json"

# Proxy settings (e.g. to route Groq traffic around the VPN if you have a local proxy)
# Can be configured in .env as HTTP_PROXY=http://127.0.0.1:1080
HTTP_PROXY = os.getenv("HTTP_PROXY") or _fallback_env.get("HTTP_PROXY", "")
HTTPS_PROXY = os.getenv("HTTPS_PROXY") or _fallback_env.get("HTTPS_PROXY", "")
PROXIES = {}
if HTTP_PROXY:
    PROXIES["http"] = HTTP_PROXY
if HTTPS_PROXY:
    PROXIES["https"] = HTTPS_PROXY


# ──────────────────────────────────────────────
# LLM Formatting (always-on)
# ──────────────────────────────────────────────
# The LLM always runs on every transcription for formatting:
# bullet points, punctuation, numbers, exclamation marks.
# It NEVER changes words — only structures them.

# ──────────────────────────────────────────────
# Agentic Trigger Words
# ──────────────────────────────────────────────
# If any of these words are spoken, the LLM will wake up and answer the question inline,
# replacing the trigger word and question with the answer while preserving surrounding text.
AGENT_TRIGGERS = ["jarvis", "apollo", "omega", "system answer"]

# ──────────────────────────────────────────────
# Context Memory (disabled per user)
# ──────────────────────────────────────────────
CONTEXT_ENABLED = False                    # disabled per user request: fresh start every time, zero context carryover
CONTEXT_MAX_WORDS = 10                     # Last N words for context (unused when disabled)

# ── Segment-level hallucination filters (Whisper verbose_json) ──
# RECALL FIRST: these are intentionally LENIENT. Losing the user's real words is
# worse than an occasional hallucination. no_speech_prob/avg_logprob are treated as
# SOFT signals (see two-tier filter in groq_client.py) — they never drop your only text.
HALLUCINATION_NO_SPEECH_THRESHOLD = 0.6    # Whisper no_speech_prob > this = low-confidence (soft)
HALLUCINATION_LOGPROB_THRESHOLD = -1.0     # Whisper avg_logprob < this = low-confidence (soft)
HALLUCINATION_COMPRESSION_MAX = 2.4        # compression_ratio > this (repetition loops) = HARD reject
HALLUCINATION_COMPRESSION_MIN = 0.3        # compression_ratio < this (garbage) = HARD reject
HALLUCINATION_MAX_WORDS_PER_SEC = 10.0     # word_count/duration > this = impossible = HARD reject

# ── Transcript-level plausibility (kept for reference; not used as a hard drop) ──
HALLUCINATION_MIN_WORDS_PER_10S = 1.0

# ── Known Whisper "ghost" phrases — hallucinated on pure silence/noise ──
# Matched case-insensitively against the FULL assembled transcript. Kept SMALL and
# unambiguous (YouTube/subtitle artifacts) so we never reject genuine dictation.
# Generic single words like "you" / "thank you" are deliberately NOT included.
GHOST_PHRASES = (
    "thank you for watching",
    "thanks for watching",
    "thank you for watching!",
    "please subscribe",
    "please subscribe to my channel",
    "don't forget to subscribe",
    "like and subscribe",
    "subscribe to the channel",
    "see you in the next video",
    "i'll see you in the next video",
    "subtitles by the amara.org community",
    "subtitles by the amara community",
)


# ──────────────────────────────────────────────
# Hotkeys
# ──────────────────────────────────────────────

HOTKEY_ACTIVATE = "ctrl+space"          # Start batch recording (global, always registered)
HOTKEY_STREAM_ACTIVATE = "ctrl+shift+space" # Start streaming recording (global, always registered)
HOTKEY_STOP = "space"                   # Stop recording (registered only during recording)

# Streaming Dictation Timing Constants
STREAM_CHUNK_MIN_SEC = 10.0             # Min audio seconds before slicing on natural pause
STREAM_CHUNK_MAX_SEC = 15.0             # Max audio seconds to slice chunk even if speaking continuously
STREAM_PAUSE_SLICE_SEC = 0.35           # Silence duration (sec) to trigger natural pause chunk slice


# ──────────────────────────────────────────────
# Audio
# ──────────────────────────────────────────────

AUDIO_SAMPLE_RATE = 16000         # 16kHz — standard for speech recognition
AUDIO_CHANNELS = 1                # Mono
AUDIO_CHUNK_SIZE = 1024           # Samples per callback (~64ms at 16kHz)
AUDIO_DTYPE = "int16"             # 16-bit PCM
DEFAULT_DEVICE = None             # None = system default; set to device index or name

SILENCE_THRESHOLD_MULTIPLIER = 2.5  # Speech = amplitude > noise_floor × this (increased to ignore background birds)
SILENCE_DURATION = 4.0              # Seconds of silence before auto-stop (increased to avoid premature cutoff)
SILENCE_DETECTION_ENABLED = True    # Set to False to disable silence auto-stop completely

NOISE_CALIBRATION_DURATION = 0.5    # Seconds to sample ambient noise
AMPLITUDE_SMOOTHING = 0.15          # EMA alpha (0=very smooth, 1=instant)

# ── Improved VAD (consecutive-frame logic, robust to noisy rooms) ──
# A chunk is ~64ms (AUDIO_CHUNK_SIZE / AUDIO_SAMPLE_RATE). These are in chunks.
VAD_SPEECH_FRAMES = 2               # Consecutive frames above threshold to confirm speech (kills noise spikes)
VAD_SILENCE_FRAMES = 2              # Consecutive frames below threshold to confirm silence (avoids cutting pauses)

# ── Trailing-silence trim (applied to finished WAV before upload) ──
# Disabled because the noise floor detection was aggressively cutting off
# quiet speech at the start and end of recordings, causing lost words.
TRIM_TRAILING_SILENCE = False       # Disable aggressive audio trimming
TRIM_SILENCE_PADDING_MS = 300       # Keep this much audio after the last detected speech (natural tail)
TRIM_NOISE_FLOOR_MULTIPLIER = 2.0   # Speech = RMS > rolling_noise_floor × this (for the trim scan)

# Amplitude normalization: RMS values above this are clipped to 1.0
AMPLITUDE_NORMALIZATION_CEILING = 150.0

# Minimum recording duration (seconds) — skip if shorter (accidental activation)
MIN_RECORDING_DURATION = 0.5


# ──────────────────────────────────────────────
# UI — Dimensions
# ──────────────────────────────────────────────

DASH_WIDTH = 27                   # Idle dash width (px) — tiny, just a presence indicator
DASH_HEIGHT = 6                   # Idle dash height (px)
PILL_WIDTH = 80                   # Active pill width (px)
PILL_HEIGHT = 33                  # Active pill height (px)
CIRCLE_DIAMETER = 60              # Processing circle diameter (px) — 0.75x pill width

BAR_COUNT = 9                     # Number of audio visualization bars
BAR_WIDTH = 3                     # Width of each bar (px)
BAR_GAP = 4                       # Gap between bars (px)
BAR_MIN_HEIGHT = 3                # Minimum bar height (px) — matches width for perfect circular dot
BAR_MAX_HEIGHT = 21               # Maximum bar height (px) — full amplitude (increased slightly)
BAR_PADDING_HORIZONTAL = 14       # Padding from pill edge to first/last bar
BAR_PADDING_VERTICAL = 5          # Padding from pill top/bottom to bar tips

# Widget bounding box (includes room for shadows and hover area)
WIDGET_PADDING = 8                # Extra pixels around the drawn content


# ──────────────────────────────────────────────
# UI — Colors (RGBA tuples)
# ──────────────────────────────────────────────

BACKGROUND_COLOR = (0, 0, 0, 0)              # Fully transparent window bg

# 3D Silver Dash
DASH_COLOR_TOP = (220, 220, 230, 255)         # Gradient top — bright silver
DASH_COLOR_BOTTOM = (140, 140, 155, 255)      # Gradient bottom — darker silver
DASH_COLOR_EDGE = (100, 100, 115, 255)        # Edge shadow — depth
DASH_HIGHLIGHT = (255, 255, 255, 100)         # Top highlight — light reflection
DASH_SHADOW = (0, 0, 0, 80)                   # Drop shadow

# Pill
PILL_BG_COLOR = (0, 0, 0, 255)                # Pitch black interior
PILL_BORDER_COLOR = (35, 35, 40, 255)         # Very subtle border — barely visible
PILL_BORDER_WIDTH = 0.8                        # Thin border stroke

# Bars
BAR_COLOR = (255, 255, 255, 255)              # White bars (idle/ready)
BAR_COLOR_RECORDING = (255, 255, 255, 230)    # Slightly translucent (recording)
BAR_CORNER_RADIUS = 1.5                       # Half of BAR_WIDTH to make perfect circular dots

# Processing circle
CIRCLE_BG_COLOR = (0, 0, 0, 255)              # Pitch black
CIRCLE_BORDER_COLOR = (35, 35, 40, 255)       # Very subtle
PARTICLE_COLOR = (255, 255, 255)              # White particles (alpha varies)

# Error
ERROR_COLOR = (255, 60, 60, 120)              # Red overlay for error flash

# Context menu
CONTEXT_MENU_BG = (20, 20, 25, 245)           # Near-black, slightly transparent
CONTEXT_MENU_HOVER = (50, 50, 60, 255)        # Hover highlight
CONTEXT_MENU_TEXT = (230, 230, 235, 255)       # Off-white text
CONTEXT_MENU_TEXT_DIM = (130, 130, 140, 255)   # Dimmed text (disabled items)
CONTEXT_MENU_SEPARATOR = (60, 60, 70, 255)    # Separator line
CONTEXT_MENU_BORDER = (50, 50, 58, 200)       # Menu border
CONTEXT_MENU_RADIUS = 10                       # Corner radius


# ──────────────────────────────────────────────
# UI — Animation Timing
# ──────────────────────────────────────────────

ANIMATION_FPS = 30                     # Target frames per second
ANIMATION_INTERVAL_MS = 1000 // 30     # ~33ms per frame

MORPH_DURATION_MS = 150                # Dash ↔ Pill morph duration (sped up)
BAR_STAGGER_MS = 30                    # Delay between each bar fade-in
BAR_ANIMATION_SMOOTHING = 0.35         # How smoothly bars follow amplitude (decreased for smoother, slower reaction)

PARTICLE_COUNT = 60                    # (legacy — no longer used by the orbital ring system; kept for compatibility)
PARTICLE_ORBIT_SPEED = 12.0            # (legacy — see PARTICLE_RING_* below)
PARTICLE_MIN_RADIUS = 0.5              # Smallest particle size (px)
PARTICLE_MAX_RADIUS = 1.5              # Largest particle size (px)

# ── Premium orbital ring system (PROCESSING state) ──
# Two concentric rings of particles orbiting the SAME direction (galaxy feel),
# plus a central pulsing dot. Speed ratio of ~√2 makes the pattern never repeat.
PARTICLE_OUTER_COUNT = 8               # Particles in the outer ring
PARTICLE_INNER_COUNT = 5               # Particles in the inner ring
PARTICLE_OUTER_SPEED = 1.6             # Outer ring angular speed (rad/s) — slow, elegant
PARTICLE_INNER_SPEED = 2.26            # Inner ring angular speed (rad/s) — ≈ √2 × outer (non-repeating)
PARTICLE_OUTER_RADIUS_FRAC = 0.34      # Outer orbit radius as fraction of CIRCLE_DIAMETER
PARTICLE_INNER_RADIUS_FRAC = 0.19      # Inner orbit radius as fraction of CIRCLE_DIAMETER
PARTICLE_DEPTH_SCALE = 0.45            # How much the depth (z) sine modulates particle size
PARTICLE_DEPTH_OPACITY = 0.55          # How much the depth (z) sine modulates particle opacity
PARTICLE_CENTER_RADIUS = 1.8           # Central pulsing dot base radius (px)
PARTICLE_DISSOLVE_MS = 220             # Time for bars to travel out into orbit on RECORDING→PROCESSING

HEARTBEAT_BPM = 60                     # Heartbeat pulse speed
HEARTBEAT_SCALE = 0.08                 # Scale amplitude (8% size change)

FADE_DURATION_MS = 200                 # Context menu fade duration
ERROR_FLASH_COUNT = 2                  # Number of red flash pulses
ERROR_FLASH_DURATION_MS = 300          # Duration per flash pulse


# ──────────────────────────────────────────────
# Animation Presets
# ──────────────────────────────────────────────

ANIMATION_PRESETS = {
    "Classic Bars": "classic",
    "Wave": "wave",
    "Pulse": "pulse",
}
DEFAULT_ANIMATION_PRESET = "classic"


# ──────────────────────────────────────────────
# Context Memory
# ──────────────────────────────────────────────
# (Settings moved to LLM Formatting section above)


# ──────────────────────────────────────────────
# Window Behavior
# ──────────────────────────────────────────────

ALWAYS_ON_TOP = True                       # Stay above all windows
DEFAULT_POSITION = "bottom-center"         # Default position on screen
POSITION_MARGIN_BOTTOM = 60                # Pixels from bottom edge
POSITION_SAVE_FILE = _PROJECT_ROOT / "position.json"


# ──────────────────────────────────────────────
# Position Persistence
# ──────────────────────────────────────────────

def load_saved_position():
    """Load widget position from disk. Returns (x, y) or None."""
    try:
        if POSITION_SAVE_FILE.exists():
            with open(POSITION_SAVE_FILE, "r") as f:
                data = json.load(f)
                return (data["x"], data["y"])
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        # Corrupted file — delete it, use defaults
        try:
            POSITION_SAVE_FILE.unlink()
        except OSError:
            pass
    return None


def save_position(x: int, y: int):
    """Save widget position to disk."""
    try:
        with open(POSITION_SAVE_FILE, "w") as f:
            json.dump({"x": x, "y": y}, f)
    except OSError:
        pass  # Non-critical — silently fail


# ──────────────────────────────────────────────
# VPN Mode Persistence
# ──────────────────────────────────────────────

def load_vpn_mode() -> bool:
    """Load the VPN mode toggle from disk. Returns True if VPN mode is enabled."""
    try:
        if VPN_MODE_SAVE_FILE.exists():
            with open(VPN_MODE_SAVE_FILE, "r") as f:
                data = json.load(f)
                return bool(data.get("vpn_mode", False))
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        pass
    return False


def save_vpn_mode(enabled: bool):
    """Save the VPN mode toggle to disk."""
    try:
        with open(VPN_MODE_SAVE_FILE, "w") as f:
            json.dump({"vpn_mode": enabled}, f)
    except OSError:
        pass  # Non-critical — silently fail


# ──────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────

def validate_config():
    """
    Validate configuration values. Returns list of warning strings.
    Called at startup — warnings shown in system tray.
    """
    warnings = []

    if not GROQ_API_KEY:
        warnings.append("GROQ_API_KEY is missing. Add it to .env file.")
    elif not GROQ_API_KEY.startswith("gsk_"):
        warnings.append("GROQ_API_KEY doesn't start with 'gsk_'. Verify your API key.")

    if BAR_COUNT < 1:
        warnings.append(f"BAR_COUNT={BAR_COUNT} is invalid. Using 10.")

    if ANIMATION_FPS < 15 or ANIMATION_FPS > 144:
        warnings.append(f"ANIMATION_FPS={ANIMATION_FPS} is unusual. Recommended: 60.")

    if SILENCE_DURATION < 1.0:
        warnings.append(f"SILENCE_DURATION={SILENCE_DURATION}s is very short. May cause premature stop.")

    return warnings
