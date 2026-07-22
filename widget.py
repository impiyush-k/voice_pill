"""
Voice Pill — Main Widget
==========================
The core visual component: a floating, frameless, transparent widget
that morphs between 4 states (IDLE, READY, RECORDING, PROCESSING).

Handles all QPainter rendering, state transitions, morphing,
mouse events (hover, drag, right-click), and coordinates with
the animation engine.
"""

import enum
import math

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QParallelAnimationGroup,
    QEasingCurve, QPoint, QPointF, QRectF, pyqtSignal, pyqtProperty,
    QSize,
)
from PyQt6.QtGui import (
    QPainter, QColor, QLinearGradient, QRadialGradient,
    QPen, QBrush, QPainterPath, QFont,
)

from config import (
    DASH_WIDTH, DASH_HEIGHT, PILL_WIDTH, PILL_HEIGHT, CIRCLE_DIAMETER,
    BAR_COUNT, BAR_WIDTH, BAR_GAP, BAR_MIN_HEIGHT, BAR_MAX_HEIGHT,
    BAR_PADDING_HORIZONTAL, BAR_PADDING_VERTICAL, BAR_CORNER_RADIUS,
    WIDGET_PADDING,
    DASH_COLOR_TOP, DASH_COLOR_BOTTOM, DASH_COLOR_EDGE,
    DASH_HIGHLIGHT, DASH_SHADOW,
    PILL_BG_COLOR, PILL_BORDER_COLOR, PILL_BORDER_WIDTH,
    BAR_COLOR, BAR_COLOR_RECORDING,
    CIRCLE_BG_COLOR, CIRCLE_BORDER_COLOR, PARTICLE_COLOR,
    ERROR_COLOR,
    ANIMATION_FPS, ANIMATION_INTERVAL_MS,
    MORPH_DURATION_MS,
    ERROR_FLASH_COUNT, ERROR_FLASH_DURATION_MS,
    ALWAYS_ON_TOP, DEFAULT_POSITION, POSITION_MARGIN_BOTTOM,
    load_saved_position, save_position,
)
from animations import AnimationEngine, ease_out_cubic


# ──────────────────────────────────────────────
# State Enum
# ──────────────────────────────────────────────

class State(enum.Enum):
    IDLE = "idle"           # Small silver dash
    READY = "ready"         # Pill with static bars (hovering)
    RECORDING = "recording" # Pill with animated bars
    PROCESSING = "processing"  # Circle with particles


# ──────────────────────────────────────────────
# Main Widget
# ──────────────────────────────────────────────

class VoicePillWidget(QWidget):
    """
    The floating pill widget.

    Signals:
        state_changed(str): Emitted when state transitions. Value is State.value.
        right_clicked(QPoint): Emitted on right-click with global position.
        position_saved(int, int): Emitted when widget is dragged to new position.
    """

    state_changed = pyqtSignal(str)
    right_clicked = pyqtSignal(QPoint)
    position_saved = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)

        # ── State ──
        self._state = State.IDLE
        self._previous_state = State.IDLE

        # ── Animated geometry (driven by QPropertyAnimation) ──
        self._pill_width: float = float(DASH_WIDTH)
        self._pill_height: float = float(DASH_HEIGHT)
        self._pill_opacity: float = 1.0

        # ── Mouse ──
        self._drag_offset = QPoint(0, 0)
        self._is_dragging = False
        self._mouse_inside = False

        # ── Animation ──
        self.anim_engine = AnimationEngine()
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(ANIMATION_INTERVAL_MS)
        # PreciseTimer forces Qt to use 1ms timer resolution on Windows
        # (default Windows timer granularity is ~15.6ms, which makes a 33ms
        # interval fire irregularly and looks choppy). This is the key fix
        # that makes 30fps actually look like smooth 30fps.
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.timeout.connect(self._on_animation_frame)

        # ── Morph animations ──
        self._morph_group: QParallelAnimationGroup | None = None

        # ── Error flash & Chunk processing glow ──
        self._error_flash_active = False
        self._error_flash_start_time: float = 0.0
        self._is_chunk_processing = False
        self._glow_phase: float = 0.0

        # ── Window setup ──
        self._setup_window()
        self._set_initial_position()

    # ──────────────────────────────────────────
    # Window Setup
    # ──────────────────────────────────────────

    def _setup_window(self):
        """Configure frameless, transparent, always-on-top window."""
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # Hide from taskbar
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # Fixed size to accommodate largest state (Pill or Circle) + padding for shadows
        max_w = max(PILL_WIDTH, CIRCLE_DIAMETER) + WIDGET_PADDING * 2
        max_h = max(PILL_HEIGHT, CIRCLE_DIAMETER) + WIDGET_PADDING * 2
        self.setFixedSize(int(max_w), int(max_h))

    def _set_initial_position(self):
        """Position widget at bottom-center of screen always (ignores saved positions)."""
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.center().x() - self.width() // 2
            y = geom.bottom() - self.height() - POSITION_MARGIN_BOTTOM
            self.move(x, y)

    # ──────────────────────────────────────────
    # Qt Property Animations
    # ──────────────────────────────────────────

    def _get_pill_width(self) -> float:
        return self._pill_width

    def _set_pill_width(self, value: float):
        self._pill_width = value
        self.update()

    def _get_pill_height(self) -> float:
        return self._pill_height

    def _set_pill_height(self, value: float):
        self._pill_height = value
        self.update()

    # Register as Qt properties for QPropertyAnimation
    pill_width = pyqtProperty(float, _get_pill_width, _set_pill_width)
    pill_height = pyqtProperty(float, _get_pill_height, _set_pill_height)

    # ──────────────────────────────────────────
    # State Management
    # ──────────────────────────────────────────

    @property
    def current_state(self) -> State:
        return self._state

    def transition_to(self, new_state: State):
        """
        Transition to a new state with appropriate animations.
        """
        if new_state == self._state:
            return

        old_state = self._state
        self._previous_state = old_state
        self._state = new_state

        # Stop any running morph
        if self._morph_group and self._morph_group.state() == QParallelAnimationGroup.State.Running:
            self._morph_group.stop()

        # Handle transition
        if new_state == State.IDLE:
            self._enter_idle(old_state)
        elif new_state == State.READY:
            self._enter_ready(old_state)
        elif new_state == State.RECORDING:
            self._enter_recording(old_state)
        elif new_state == State.PROCESSING:
            self._enter_processing(old_state)

        self.state_changed.emit(new_state.value)

    def _enter_idle(self, from_state: State):
        """Transition to IDLE: morph to small dash, stop animations."""
        self._morph_to(DASH_WIDTH, DASH_HEIGHT, MORPH_DURATION_MS)
        self.anim_engine.reset_bar_appear()
        self.anim_engine.stop_particles()
        self.anim_engine.set_processing(False)
        self.anim_engine.set_recording(False)
        # Keep timer running during morph, stop after
        QTimer.singleShot(MORPH_DURATION_MS + 50, self._maybe_stop_timer)

    def _enter_ready(self, from_state: State):
        """Transition to READY: morph to pill, show static bars."""
        self._start_timer()
        self._morph_to(PILL_WIDTH, PILL_HEIGHT, MORPH_DURATION_MS)
        self.anim_engine.set_bars_visible()
        self.anim_engine.set_amplitude(0.0)
        self.anim_engine.set_processing(False)
        self.anim_engine.set_recording(False)

    def _enter_recording(self, from_state: State):
        """Transition to RECORDING: pill with animated bars."""
        self._start_timer()
        self.anim_engine.set_processing(False)
        self.anim_engine.set_recording(True)

        if from_state == State.IDLE:
            # Skipped READY — need to morph AND show bars
            self._morph_to(PILL_WIDTH, PILL_HEIGHT, MORPH_DURATION_MS)
            self.anim_engine.start_bar_appear()
        elif from_state == State.READY:
            # Already pill-shaped, bars already visible
            self.anim_engine.set_bars_visible()

    def _enter_processing(self, from_state: State):
        """Transition to PROCESSING: pill morphs to circle with particles."""
        self._start_timer()

        self.anim_engine.set_processing(True)

        # Gather current bar positions for dissolution effect
        bar_positions = []
        total_bars_w = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        start_x = -total_bars_w / 2.0
        for i in range(BAR_COUNT):
            bar_x = start_x + i * (BAR_WIDTH + BAR_GAP)
            bar_positions.append((bar_x, 0.0))

        self.anim_engine.start_particles(bar_positions)

        # Morph to circle
        self._morph_to(CIRCLE_DIAMETER, CIRCLE_DIAMETER, MORPH_DURATION_MS)

    def _morph_to(self, target_width: float, target_height: float,
                  duration_ms: int):
        """Animate width and height to target values."""
        self._morph_group = QParallelAnimationGroup(self)

        w_anim = QPropertyAnimation(self, b"pill_width")
        w_anim.setDuration(duration_ms)
        w_anim.setStartValue(self._pill_width)
        w_anim.setEndValue(float(target_width))
        w_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        h_anim = QPropertyAnimation(self, b"pill_height")
        h_anim.setDuration(duration_ms)
        h_anim.setStartValue(self._pill_height)
        h_anim.setEndValue(float(target_height))
        h_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._morph_group.addAnimation(w_anim)
        self._morph_group.addAnimation(h_anim)
        self._morph_group.start()

    def _maybe_stop_timer(self):
        """Stop animation timer if in IDLE and no ongoing animations."""
        if self._state == State.IDLE and not self._error_flash_active:
            self._animation_timer.stop()

    def _start_timer(self):
        """Ensure animation timer is running."""
        if not self._animation_timer.isActive():
            self._animation_timer.start()

    # ──────────────────────────────────────────
    # Animation Frame
    # ──────────────────────────────────────────

    def _on_animation_frame(self):
        """Called every ~16ms (60fps). Advance animation and repaint."""
        self.anim_engine.tick()

        if self._is_chunk_processing:
            self._glow_phase = (self._glow_phase + 0.15) % (2.0 * math.pi)

        # Handle error flash completion
        if self._error_flash_active:
            opacity = self.anim_engine.get_error_flash_opacity(
                self._error_flash_start_time,
                ERROR_FLASH_COUNT,
                ERROR_FLASH_DURATION_MS / 1000.0,
            )
            if opacity < 0:
                self._error_flash_active = False
                if self._state == State.IDLE:
                    self._maybe_stop_timer()

        self.update()  # Trigger paintEvent

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def set_chunk_processing(self, enabled: bool):
        """
        Enable or disable glowing border aura during background streaming chunk processing.
        """
        self._is_chunk_processing = enabled
        if enabled:
            self._start_timer()
        self.update()

    def update_amplitude(self, amplitude: float):
        """Update bar animation with new audio amplitude."""
        self.anim_engine.set_amplitude(amplitude)

    def show_error_flash(self):
        """Show error flash animation (red pulses)."""
        self._error_flash_active = True
        self._error_flash_start_time = self.anim_engine.time
        self._start_timer()

    def set_animation_preset(self, preset: str):
        """Change the bar animation preset."""
        self.anim_engine.set_preset(preset)

    # ──────────────────────────────────────────
    # Paint Event — The Heart of the UI
    # ──────────────────────────────────────────

    def paintEvent(self, event):
        """Render the widget based on current state."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Center of widget horizontally
        cx = self.width() / 2.0

        # Current animated dimensions
        w = self._pill_width
        h = self._pill_height

        # Calculate dynamic cy so shapes grow upwards from a fixed bottom edge
        # The fixed bottom edge leaves room for the drop shadow
        bottom_y = self.height() - WIDGET_PADDING
        cy = bottom_y - h / 2.0

        radius = min(w, h) / 2.0  # Corner radius = capsule

        # Calculate morph progress from dash to pill based on width (0.0 to 1.0)
        if self._state == State.PROCESSING:
            progress = 1.0  # Force pill/circle to be fully opaque during processing
        else:
            if PILL_WIDTH > DASH_WIDTH:
                progress = (w - DASH_WIDTH) / (PILL_WIDTH - DASH_WIDTH)
            else:
                progress = 0.0
            progress = max(0.0, min(1.0, progress))

        # Draw Dash (fades out as progress approaches 1.0)
        if progress < 1.0:
            painter.setOpacity(1.0 - progress)
            self._draw_3d_dash(painter, cx, cy, w, h)

        # Draw Pill (fades in as progress approaches 1.0)
        if progress > 0.0:
            painter.setOpacity(progress)
            
            # Apply heartbeat scale if processing (applies only to particles now, background is static)
            is_processing = (self._state == State.PROCESSING)
            hb_scale = 1.0
            if is_processing:
                hb_scale = self.anim_engine.heartbeat_scale

            self._draw_pill(painter, cx, cy, w, h, radius)

            if is_processing and self.anim_engine.particles_active:
                painter.save()
                path = QPainterPath()
                path.addRoundedRect(QRectF(cx - w / 2, cy - h / 2, w, h), radius, radius)
                painter.setClipPath(path)
                self._draw_particles(painter, cx, cy, hb_scale)
                painter.restore()
            else:
                # Only draw bars when pill is wide enough to hold them
                min_bars_width = DASH_WIDTH + 10
                if w > min_bars_width:
                    painter.save()
                    path = QPainterPath()
                    path.addRoundedRect(QRectF(cx - w / 2, cy - h / 2, w, h), radius, radius)
                    painter.setClipPath(path)
                    self._draw_bars(painter, cx, cy, w, h)
                    painter.restore()

        # Reset opacity for error overlay
        painter.setOpacity(1.0)

        # Error flash overlay
        if self._error_flash_active:
            self._draw_error_overlay(painter, cx, cy, w, h, radius)

        painter.end()

    def _draw_3d_dash(self, painter: QPainter, cx: float, cy: float,
                       w: float, h: float):
        """
        Draw the silver 3D dash (IDLE state).

        5-layer rendering for metallic 3D appearance:
        1. Drop shadow
        2. Main body (linear gradient)
        3. Bottom edge
        4. Top highlight
        5. Specular highlight
        """
        rect = QRectF(cx - w / 2, cy - h / 2, w, h)
        radius = h / 2.0  # Perfect capsule

        # Layer 1: Drop shadow (Removed to reduce CPU/GPU rendering load)
        # shadow_rect = rect.translated(0, 1)
        # shadow_color = QColor(*DASH_SHADOW)
        # painter.setPen(Qt.PenStyle.NoPen)
        # painter.setBrush(QBrush(shadow_color))
        # painter.drawRoundedRect(shadow_rect, radius, radius)

        # Layer 2: Main body — linear gradient (silver)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, QColor(*DASH_COLOR_TOP))
        gradient.setColorAt(1.0, QColor(*DASH_COLOR_BOTTOM))
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, radius, radius)

        # Layer 3: Top highlight (light reflection)
        if h > 3:
            highlight_rect = QRectF(rect.left() + 2, rect.top(), rect.width() - 4, max(1, h * 0.4))
            highlight_color = QColor(*DASH_HIGHLIGHT)
            painter.setBrush(QBrush(highlight_color))
            painter.drawRoundedRect(highlight_rect, radius, radius)

        # Layer 4: Specular highlight (point light reflection)
        spec_cx = cx - w * 0.12
        spec_cy = cy - h * 0.15
        spec_gradient = QRadialGradient(spec_cx, spec_cy, w * 0.3)
        spec_gradient.setColorAt(0.0, QColor(255, 255, 255, 50))
        spec_gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(spec_gradient))
        painter.drawRoundedRect(rect, radius, radius)

    def _draw_pill(self, painter: QPainter, cx: float, cy: float,
                    w: float, h: float, radius: float):
        """Draw the pill/capsule shape (READY and RECORDING states)."""
        rect = QRectF(cx - w / 2, cy - h / 2, w, h)

        # Background — pitch black
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(*PILL_BG_COLOR)))
        painter.drawRoundedRect(rect, radius, radius)

        # Border — subtle by default, glowing cyan aura when chunk is processing
        if self._is_chunk_processing:
            alpha = int(170 + 70 * math.sin(self._glow_phase))
            glow_color = QColor(0, 220, 255, alpha)  # Glowing cyan pulse
            pen = QPen(glow_color, 2.0)
        else:
            pen = QPen(QColor(*PILL_BORDER_COLOR), PILL_BORDER_WIDTH)

        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)

    def _draw_bars(self, painter: QPainter, cx: float, cy: float,
                    w: float, h: float):
        """Draw the visualization bars inside the pill."""
        bar_heights = self.anim_engine.get_bar_heights_px()

        # Calculate total bars width
        total_bars_w = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        start_x = cx - total_bars_w / 2.0

        # Choose bar color based on state
        if self._state == State.RECORDING:
            bar_color = QColor(*BAR_COLOR_RECORDING)
        else:
            bar_color = QColor(*BAR_COLOR)

        painter.setPen(Qt.PenStyle.NoPen)

        for i in range(BAR_COUNT):
            bar_h = bar_heights[i]

            # Clamp bar height to stay inside pill with padding
            max_bar_h = h - BAR_PADDING_VERTICAL * 2
            bar_h = max(BAR_MIN_HEIGHT, min(bar_h, max_bar_h))

            bar_x = start_x + i * (BAR_WIDTH + BAR_GAP)
            bar_y = cy - bar_h / 2.0

            bar_rect = QRectF(bar_x, bar_y, BAR_WIDTH, bar_h)
            painter.setBrush(QBrush(bar_color))
            painter.drawRoundedRect(bar_rect, BAR_CORNER_RADIUS, BAR_CORNER_RADIUS)

    def _draw_particles(self, painter: QPainter, cx: float, cy: float, scale: float):
        """Draw the 3D rotating particle system."""
        particles = self.anim_engine.get_particles()

        painter.setPen(Qt.PenStyle.NoPen)
        for p in particles:
            if p.opacity <= 0:
                continue

            color = QColor(*PARTICLE_COLOR)
            color.setAlphaF(p.opacity)
            painter.setBrush(QBrush(color))

            # Draw particle centered at cx+p.x, cy+p.y
            px = cx + p.x * scale
            py = cy + p.y * scale
            pr = p.radius * scale

            painter.drawEllipse(QRectF(px - pr, py - pr, pr * 2, pr * 2))

    def _draw_error_overlay(self, painter: QPainter, cx: float, cy: float,
                              w: float, h: float, radius: float):
        """Draw red error flash overlay on top of current shape."""
        opacity = self.anim_engine.get_error_flash_opacity(
            self._error_flash_start_time,
            ERROR_FLASH_COUNT,
            ERROR_FLASH_DURATION_MS / 1000.0,
        )
        if opacity <= 0:
            return

        rect = QRectF(cx - w / 2, cy - h / 2, w, h)
        error_color = QColor(*ERROR_COLOR)
        error_color.setAlphaF(opacity * 0.5)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(error_color))

        painter.drawRoundedRect(rect, radius, radius)

    # ──────────────────────────────────────────
    # Mouse Events
    # ──────────────────────────────────────────

    def enterEvent(self, event):
        """Mouse enters widget area."""
        self._mouse_inside = True
        if self._state == State.IDLE:
            self.transition_to(State.READY)

    def leaveEvent(self, event):
        """Mouse leaves widget area."""
        self._mouse_inside = False
        if self._state == State.READY:
            self.transition_to(State.IDLE)

    def mousePressEvent(self, event):
        """Handle mouse press — drag (left) or context menu (right)."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        """Handle drag."""
        if self._is_dragging:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)

    def mouseReleaseEvent(self, event):
        """Handle drag end — save position."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            self.position_saved.emit(self.x(), self.y())
