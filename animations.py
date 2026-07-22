"""
Voice Pill — Animation Engine
================================
All animation math: bar presets, particle system, heartbeat,
easing functions, and morph interpolation.

Separated from widget.py so presets can be swapped without
touching rendering code.
"""

import math

from config import (
    BAR_COUNT,
    BAR_MIN_HEIGHT,
    BAR_MAX_HEIGHT,
    BAR_ANIMATION_SMOOTHING,
    PARTICLE_MIN_RADIUS,
    PARTICLE_MAX_RADIUS,
    PARTICLE_OUTER_COUNT,
    PARTICLE_INNER_COUNT,
    PARTICLE_OUTER_SPEED,
    PARTICLE_INNER_SPEED,
    PARTICLE_OUTER_RADIUS_FRAC,
    PARTICLE_INNER_RADIUS_FRAC,
    PARTICLE_DEPTH_SCALE,
    PARTICLE_DEPTH_OPACITY,
    PARTICLE_CENTER_RADIUS,
    PARTICLE_DISSOLVE_MS,
    HEARTBEAT_BPM,
    HEARTBEAT_SCALE,
    CIRCLE_DIAMETER,
    ANIMATION_FPS,
    DEFAULT_ANIMATION_PRESET,
)


# ──────────────────────────────────────────────
# Easing Functions
# ──────────────────────────────────────────────

def ease_out_cubic(t: float) -> float:
    """Fast start, smooth deceleration. t ∈ [0, 1]"""
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    """Smooth start and end. Good for looping animations."""
    if t < 0.5:
        return 4.0 * t ** 3
    else:
        return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def ease_out_elastic(t: float) -> float:
    """Overshoot and bounce. Used for bar 'wake up' animation."""
    if t == 0.0 or t == 1.0:
        return t
    return 2.0 ** (-10.0 * t) * math.sin((t * 10.0 - 0.75) * (2.0 * math.pi / 3.0)) + 1.0


def ease_out_back(t: float) -> float:
    """Slight overshoot. Used for pill expansion."""
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2


def ease_out_quart(t: float) -> float:
    """Quartic ease out — snappy."""
    return 1.0 - (1.0 - t) ** 4


# ──────────────────────────────────────────────
# Particle (Processing State — Orbital Ring System)
# ──────────────────────────────────────────────

# Tilt of the orbital plane (0 = edge-on line, 1 = flat circle facing viewer).
# ~0.45 reads as a disc viewed at an angle — the premium "galaxy / orbit" look.
_ORBIT_TILT = 0.45


class Particle:
    """
    A single particle belonging to one of the concentric orbital rings.

    The processing animation is intentionally choreographed (not random):
      - Outer and inner rings orbit the SAME direction (coherent, calm).
      - A √2 speed ratio means the two rings never re-sync — always fresh.
      - A tilted plane + depth-based size/opacity creates a 3D illusion.
      - On spawn, particles travel from their bar position out to orbit (dissolve).
    """

    def __init__(self, ring: str, index: int, count: int,
                 orbit_radius: float, orbit_speed: float, base_radius: float,
                 spawn_x: float = 0.0, spawn_y: float = 0.0):
        self.ring = ring                       # 'outer' | 'inner' | 'center'
        self.orbit_radius = orbit_radius
        self.orbit_speed = orbit_speed
        self.base_radius = base_radius
        # Evenly distribute particles around the ring
        self.base_angle = (2.0 * math.pi / count) * index if count > 0 else 0.0

        # Dissolve origin (where the bar was) → travels out to orbit
        self.spawn_x = spawn_x
        self.spawn_y = spawn_y

        # Rendered state (read by the widget)
        self.x = spawn_x
        self.y = spawn_y
        self.radius = base_radius
        self.opacity = 0.0

    def update(self, time_val: float, dissolve_t: float):
        """
        Update rendered position/size/opacity for one frame.

        Args:
            time_val: Engine time in seconds.
            dissolve_t: 0..1 progress of the bars→orbit dissolve (1 = settled).
        """
        # Fade-in as the system materializes
        fade = min(1.0, dissolve_t * 1.6)

        if self.ring == "center":
            self.x = 0.0
            self.y = 0.0
            self.radius = self.base_radius
            self.opacity = fade
            return

        # Orbital target position on a tilted plane
        angle = self.base_angle + time_val * self.orbit_speed
        orbit_x = math.cos(angle) * self.orbit_radius
        orbit_y = math.sin(angle) * self.orbit_radius * _ORBIT_TILT

        # Depth: front of the orbit (+1) is larger/brighter, back (-1) smaller/dimmer
        depth = math.sin(angle)

        # Blend from spawn position to orbit position (the dissolve)
        e = ease_out_cubic(dissolve_t)
        self.x = self.spawn_x * (1.0 - e) + orbit_x * e
        self.y = self.spawn_y * (1.0 - e) + orbit_y * e

        # Depth-driven size and opacity
        self.radius = self.base_radius * (1.0 + PARTICLE_DEPTH_SCALE * depth)
        depth_opacity = (1.0 - PARTICLE_DEPTH_OPACITY) + PARTICLE_DEPTH_OPACITY * (depth + 1.0) / 2.0
        self.opacity = depth_opacity * fade


# ──────────────────────────────────────────────
# Animation Engine
# ──────────────────────────────────────────────

class AnimationEngine:
    """
    Central animation controller.

    Manages:
      - Bar heights (3 presets: classic, wave, pulse)
      - Particle system (processing state)
      - Heartbeat pulse (processing state)
      - Time tracking
    """

    def __init__(self):
        # Time (increments every frame, ~60fps)
        self._time: float = 0.0
        self._frame_count: int = 0

        # Bar state
        self._bar_heights: list[float] = [0.0] * BAR_COUNT  # 0.0 - 1.0
        self._target_amplitude: float = 0.0
        self._current_amplitude: float = 0.0
        self._preset: str = DEFAULT_ANIMATION_PRESET

        # Bar appearance animation (stagger fade-in)
        self._bar_appear_progress: list[float] = [0.0] * BAR_COUNT
        self._bars_appearing: bool = False
        self._bar_appear_start_frame: int = 0
        self._is_processing: bool = False
        self._is_recording: bool = False

        # Particles
        self._particles: list[Particle] = []
        self._particles_active: bool = False
        self._dissolve_start_time: float = 0.0  # When bars→orbit dissolve began

        # Heartbeat
        self._heartbeat_scale: float = 1.0

        # Preset-specific phases for bar variation
        self._bar_frequencies = [2.0 + i * 0.5 for i in range(BAR_COUNT)]
        self._bar_phases = [i * (2 * math.pi / BAR_COUNT) for i in range(BAR_COUNT)]

    # ──────────────────────────────────────────
    # Time
    # ──────────────────────────────────────────

    def tick(self):
        """Advance animation by one frame."""
        self._time += 1.0 / ANIMATION_FPS
        self._frame_count += 1

        # Smooth amplitude tracking (scaled to remain consistent regardless of FPS)
        fps_ratio = 60.0 / ANIMATION_FPS
        smooth_factor = min(1.0, BAR_ANIMATION_SMOOTHING * fps_ratio)

        self._current_amplitude += (
            (self._target_amplitude - self._current_amplitude) * smooth_factor
        )

        # Update bar appearance animation
        if self._bars_appearing:
            self._update_bar_appear()

        # Update particles
        if self._particles_active:
            self._update_particles(fps_ratio)

        # Update heartbeat
        self._update_heartbeat()

    @property
    def time(self) -> float:
        return self._time

    # ──────────────────────────────────────────
    # Bar Animation
    # ──────────────────────────────────────────

    def set_amplitude(self, amplitude: float):
        """Set the target audio amplitude (0.0 - 1.0)."""
        self._target_amplitude = max(0.0, min(1.0, amplitude))

    def get_bar_heights_px(self) -> list[float]:
        """
        Get bar heights in pixels, based on current preset and amplitude.

        Returns:
            List of BAR_COUNT floats — pixel heights for each bar.
        """
        raw_heights = self._compute_bar_preset()

        # Apply appearance animation (stagger fade-in)
        result = []
        for i, h in enumerate(raw_heights):
            appear = self._bar_appear_progress[i]
            px = BAR_MIN_HEIGHT + h * (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT)
            # During appear, scale from 0 to full height
            px *= appear
            result.append(max(BAR_MIN_HEIGHT * appear, px))

        return result

    def _compute_bar_preset(self) -> list[float]:
        """Compute raw bar heights (0.0 - 1.0) based on active preset."""
        amp = self._current_amplitude

        if self._is_processing:
            return self._preset_processing()

        if self._preset == "classic":
            return self._preset_classic(amp)
        elif self._preset == "wave":
            return self._preset_wave(amp)
        elif self._preset == "pulse":
            return self._preset_pulse(amp)
        else:
            return self._preset_classic(amp)

    def set_recording(self, is_recording: bool):
        """Enable or disable recording state for animation presets."""
        self._is_recording = is_recording

    def _preset_classic(self, amp: float) -> list[float]:
        """
        Classic bars: smooth alternating pattern, scaled by amplitude.
        Idle: static alternating 'big and dot'.
        """
        heights = []
        is_idle = amp < 0.05
        
        for i in range(BAR_COUNT):
            if is_idle:
                if self._is_recording:
                    # All dots when silently recording
                    h = 0.0
                else:
                    # Static alternating pattern (Dot and Big) when just hovering (ready)
                    h = 0.0 if i % 2 == 0 else 0.5
                heights.append(h)
            else:
                # Alternating bars swapping smoothly, scaled by amplitude
                phase = math.pi if i % 2 == 0 else 0.0
                raw = math.sin(self._time * 9.5 + phase) # Slower, more elegant fixed animation speed
                
                # factor ranges from ~0.2 (dot) to 1.0 (full bar)
                factor = 0.2 + 0.8 * (0.5 + 0.5 * raw)
                
                # Height depends on amplitude! Smoothly decay to 0.0
                h = min(1.0, amp * factor)
                heights.append(h)
        return heights

    def _preset_processing(self) -> list[float]:
        """
        Processing state: Fast, sharp alternating pattern.
        'dot becomes log bar, bar becomes dot' repeating.
        """
        heights = []
        for i in range(BAR_COUNT):
            phase = math.pi if i % 2 == 0 else 0.0
            # Faster, wider oscillation for processing
            raw = math.sin(self._time * 18.0 + phase)
            h = 0.5 + 0.4 * raw
            heights.append(h)
        return heights

    def _preset_wave(self, amp: float) -> list[float]:
        """
        Wave: sine wave flows left to right through the bars.
        Modulated by audio amplitude.
        """
        heights = []
        for i in range(BAR_COUNT):
            wave = math.sin(self._time * 3.0 + i * (math.pi / BAR_COUNT))
            h = amp * (0.5 + 0.5 * wave)
            heights.append(max(0.0, min(1.0, h)))
        return heights

    def _preset_pulse(self, amp: float) -> list[float]:
        """
        Pulse: all bars pulse together with a breathing effect.
        """
        breathe = math.sin(self._time * 2.0) * 0.1
        h = max(0.0, min(1.0, amp + breathe))
        return [h] * BAR_COUNT

    def set_preset(self, preset: str):
        """Change the active animation preset."""
        if preset in ("classic", "wave", "pulse"):
            self._preset = preset

    @property
    def current_preset(self) -> str:
        return self._preset

    # ──────────────────────────────────────────
    # Bar Appear / Disappear
    # ──────────────────────────────────────────

    def start_bar_appear(self):
        """
        Start bar appearance (instant, as requested by user).
        """
        self.set_bars_visible()

    def _update_bar_appear(self):
        """Update staggered bar appearance."""
        stagger_frames = 2  # ~30ms between bars at 60fps
        all_done = True

        for i in range(BAR_COUNT):
            bar_start_frame = self._bar_appear_start_frame + i * stagger_frames
            elapsed_frames = self._frame_count - bar_start_frame

            if elapsed_frames < 0:
                self._bar_appear_progress[i] = 0.0
                all_done = False
            elif elapsed_frames >= 12:  # ~200ms per bar animation
                self._bar_appear_progress[i] = 1.0
            else:
                t = elapsed_frames / 12.0
                self._bar_appear_progress[i] = ease_out_back(t)
                all_done = False

        if all_done:
            self._bars_appearing = False

    def reset_bar_appear(self):
        """Reset bars to invisible (for transition back to IDLE)."""
        self._bar_appear_progress = [0.0] * BAR_COUNT
        self._bars_appearing = False

    def set_bars_visible(self):
        """Instantly make all bars fully visible."""
        self._bar_appear_progress = [1.0] * BAR_COUNT
        self._bars_appearing = False

    # ──────────────────────────────────────────
    # Particle System
    # ──────────────────────────────────────────

    def start_particles(self, bar_positions: list[tuple[float, float]] | None = None):
        """
        Initialize and start the particle system.
        Called when transitioning RECORDING → PROCESSING.

        Args:
            bar_positions: Optional list of (x, y) positions of bars
                          to spawn initial particles from (dissolution effect).
        """
        self._particles = []
        self._dissolve_start_time = self._time

        outer_r = CIRCLE_DIAMETER * PARTICLE_OUTER_RADIUS_FRAC
        inner_r = CIRCLE_DIAMETER * PARTICLE_INNER_RADIUS_FRAC

        positions = bar_positions or []

        def spawn_for(i: int) -> tuple[float, float]:
            # Particles dissolve outward from the bar positions; if none, from center.
            if positions:
                return positions[i % len(positions)]
            return (0.0, 0.0)

        # Outer ring — larger particles, slower orbit
        for i in range(PARTICLE_OUTER_COUNT):
            sx, sy = spawn_for(i)
            self._particles.append(Particle(
                ring="outer", index=i, count=PARTICLE_OUTER_COUNT,
                orbit_radius=outer_r, orbit_speed=PARTICLE_OUTER_SPEED,
                base_radius=PARTICLE_MAX_RADIUS, spawn_x=sx, spawn_y=sy,
            ))

        # Inner ring — slightly smaller particles, faster orbit (√2 ratio)
        for i in range(PARTICLE_INNER_COUNT):
            sx, sy = spawn_for(i + PARTICLE_OUTER_COUNT)
            self._particles.append(Particle(
                ring="inner", index=i, count=PARTICLE_INNER_COUNT,
                orbit_radius=inner_r, orbit_speed=PARTICLE_INNER_SPEED,
                base_radius=max(PARTICLE_MIN_RADIUS, PARTICLE_MAX_RADIUS * 0.85),
                spawn_x=sx, spawn_y=sy,
            ))

        # Center — a single pulsing dot (pulses via heartbeat scale)
        self._particles.append(Particle(
            ring="center", index=0, count=1,
            orbit_radius=0.0, orbit_speed=0.0,
            base_radius=PARTICLE_CENTER_RADIUS, spawn_x=0.0, spawn_y=0.0,
        ))

        self._particles_active = True

    def stop_particles(self):
        """Stop the particle system."""
        self._particles_active = False
        self._particles = []

    def set_processing(self, is_processing: bool):
        """Enable or disable the processing animation mode for bars."""
        self._is_processing = is_processing

    def _update_particles(self, fps_ratio: float = 1.0):
        """Update all particle positions for one frame."""
        dissolve_dur = PARTICLE_DISSOLVE_MS / 1000.0
        if dissolve_dur > 0:
            dissolve_t = min(1.0, (self._time - self._dissolve_start_time) / dissolve_dur)
        else:
            dissolve_t = 1.0
        for p in self._particles:
            p.update(self._time, dissolve_t)

    def get_particles(self) -> list[Particle]:
        """Get current particle list for rendering."""
        return self._particles

    @property
    def particles_active(self) -> bool:
        return self._particles_active

    # ──────────────────────────────────────────
    # Heartbeat
    # ──────────────────────────────────────────

    def _update_heartbeat(self):
        """
        Compute heartbeat scale factor.
        sin^4 creates a sharp pulse followed by rest — like a real heartbeat.
        """
        if not self._particles_active:
            self._heartbeat_scale = 1.0
            return

        beat_freq = 2.0 * math.pi * HEARTBEAT_BPM / 60.0
        raw = math.sin(self._time * beat_freq)
        # sin^4 for sharp pulse shape
        pulse = raw ** 4 if raw > 0 else 0.0
        self._heartbeat_scale = 1.0 + HEARTBEAT_SCALE * pulse

    @property
    def heartbeat_scale(self) -> float:
        """Current heartbeat scale factor (1.0 = normal, ~1.08 = peak)."""
        return self._heartbeat_scale

    # ──────────────────────────────────────────
    # Error Flash
    # ──────────────────────────────────────────

    def get_error_flash_opacity(self, flash_start_time: float,
                                 flash_count: int = 2,
                                 flash_duration: float = 0.3) -> float:
        """
        Compute error flash overlay opacity.

        Returns opacity (0.0 - 1.0) for the red error overlay.
        Returns -1.0 when the flash animation is complete.
        """
        elapsed = self._time - flash_start_time
        total_duration = flash_count * flash_duration * 2  # Each flash = up + down

        if elapsed >= total_duration:
            return -1.0  # Animation complete

        # Which phase are we in? (up/down cycle)
        cycle_duration = flash_duration * 2
        cycle_pos = (elapsed % cycle_duration) / cycle_duration

        # Triangle wave: 0 → 1 → 0
        if cycle_pos < 0.5:
            return ease_out_cubic(cycle_pos * 2.0)
        else:
            return ease_out_cubic((1.0 - cycle_pos) * 2.0)

    # ──────────────────────────────────────────
    # Reset
    # ──────────────────────────────────────────

    def reset(self):
        """Full reset — return to clean state."""
        self._target_amplitude = 0.0
        self._current_amplitude = 0.0
        self._bar_heights = [0.0] * BAR_COUNT
        self.reset_bar_appear()
        self.stop_particles()
        self.set_processing(False)
        self._heartbeat_scale = 1.0
