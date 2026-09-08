"""
Voice Pill — Custom Context Menu
===================================
Premium dark-themed right-click menu with fade-in animation,
staggered item reveal, smooth hover highlights, and submenus.

Matches the pill aesthetic: near-black background, white text,
rounded corners, smooth transitions.
"""

from PyQt6.QtWidgets import QWidget, QGraphicsOpacityEffect, QApplication
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QRectF, QPointF,
    pyqtSignal, QEasingCurve, QSize, QPoint,
)
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont, QFontMetrics,
    QPainterPath, QCursor,
)

from config import (
    CONTEXT_MENU_BG, CONTEXT_MENU_HOVER, CONTEXT_MENU_TEXT,
    CONTEXT_MENU_TEXT_DIM, CONTEXT_MENU_SEPARATOR,
    CONTEXT_MENU_BORDER, CONTEXT_MENU_RADIUS,
    FADE_DURATION_MS, ANIMATION_PRESETS,
)


# ──────────────────────────────────────────────
# Menu Item Types
# ──────────────────────────────────────────────

class MenuItemType:
    ACTION = "action"
    SUBMENU = "submenu"
    SEPARATOR = "separator"
    HEADER = "header"      # Non-interactive title + subtitle (e.g. app name + active device)
    LABEL = "label"        # Non-interactive dim section label


class MenuItem:
    """Represents a single menu item."""

    def __init__(self, text: str = "", icon: str = "",
                 item_type: str = MenuItemType.ACTION,
                 callback=None, submenu_items: list = None,
                 is_checked: bool = False, is_enabled: bool = True,
                 item_id: str = "", subtitle: str = "", shortcut: str = "",
                 is_danger: bool = False):
        self.text = text
        self.icon = icon
        self.item_type = item_type
        self.callback = callback
        self.submenu_items = submenu_items or []
        self.is_checked = is_checked
        self.is_enabled = is_enabled
        self.item_id = item_id or text
        self.subtitle = subtitle        # Secondary line (HEADER) / inline value (ACTION)
        self.shortcut = shortcut        # Right-aligned hint, e.g. "Ctrl+Space"
        self.is_danger = is_danger      # Render in red (e.g. Quit)

    @property
    def is_interactive(self) -> bool:
        return self.item_type in (MenuItemType.ACTION, MenuItemType.SUBMENU) and self.is_enabled


# ──────────────────────────────────────────────
# Context Menu Widget
# ──────────────────────────────────────────────

class VoicePillContextMenu(QWidget):
    """
    Custom dark-themed context menu.

    Signals:
        action_triggered(str): Emitted when a menu item is clicked.
                               Value is the item_id.
        device_selected(int): Emitted when an audio device is selected.
        preset_selected(str): Emitted when an animation preset is selected.
        closed(): Emitted when menu is dismissed.
    """

    action_triggered = pyqtSignal(str)
    device_selected = pyqtSignal(object)  # Accepts int (device id) or str ("auto")
    preset_selected = pyqtSignal(str)
    pill_scale_selected = pyqtSignal(str, float)  # mode ("auto", "100%", etc), factor (1.0, 1.25, etc)
    vpn_mode_toggled = pyqtSignal(bool)   # True = VPN mode just enabled, False = disabled
    autostart_toggled = pyqtSignal(bool)  # True = enable autostart, False = disable
    closed = pyqtSignal()


    # Layout constants
    ITEM_HEIGHT = 34
    ITEM_PADDING_X = 14
    ICON_WIDTH = 26
    ARROW_WIDTH = 18
    MIN_WIDTH = 248
    SEPARATOR_HEIGHT = 11
    HEADER_HEIGHT = 56
    LABEL_HEIGHT = 22
    TOP_PADDING = 7
    BOTTOM_PADDING = 7

    def __init__(self, parent=None):
        super().__init__(parent)

        # Window setup
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Popup  # Auto-dismiss on click outside
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Items
        self._items: list[MenuItem] = []
        self._hover_index: int = -1
        self._hover_progress: dict[int, float] = {}  # Index → hover opacity

        # Appearance animation
        self._appear_progress: float = 0.0
        self._item_appear_progress: list[float] = []

        # Fade animation
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_anim.setDuration(FADE_DURATION_MS)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Stagger timer
        self._stagger_timer = QTimer(self)
        self._stagger_timer.setInterval(30)  # 30ms between items
        self._stagger_timer.timeout.connect(self._on_stagger_tick)
        self._stagger_index = 0

        # Font
        self._font = QFont("Segoe UI", 10)
        self._font_bold = QFont("Segoe UI", 10)
        self._font_bold.setBold(True)
        self._font_title = QFont("Segoe UI Semibold", 11)
        self._font_title.setBold(True)
        self._font_small = QFont("Segoe UI", 8)

        # Submenu
        self._active_submenu: VoicePillContextMenu | None = None

        self.setMouseTracking(True)

    # ──────────────────────────────────────────
    # Build Menu
    # ──────────────────────────────────────────

    def _item_height(self, item: MenuItem) -> int:
        """Pixel height for a given item type."""
        if item.item_type == MenuItemType.SEPARATOR:
            return self.SEPARATOR_HEIGHT
        if item.item_type == MenuItemType.HEADER:
            return self.HEADER_HEIGHT
        if item.item_type == MenuItemType.LABEL:
            return self.LABEL_HEIGHT
        return self.ITEM_HEIGHT

    def set_items(self, items: list[MenuItem]):
        """Set the menu items and compute the menu size."""
        self._items = items
        self._item_appear_progress = [0.0] * len(items)
        self._hover_progress = {}

        total_h = self.TOP_PADDING
        max_w = self.MIN_WIDTH

        fm = QFontMetrics(self._font)
        fm_small = QFontMetrics(self._font_small)
        fm_title = QFontMetrics(self._font_title)

        for item in items:
            total_h += self._item_height(item)

            if item.item_type == MenuItemType.HEADER:
                w_title = fm_title.horizontalAdvance(item.text) + 40
                w_sub = fm_small.horizontalAdvance(item.subtitle) + 56
                max_w = max(max_w, w_title, w_sub)
            elif item.item_type in (MenuItemType.ACTION, MenuItemType.SUBMENU):
                text_w = fm.horizontalAdvance(item.text)
                sub_w = fm_small.horizontalAdvance(item.subtitle) if item.subtitle else 0
                sc_w = fm_small.horizontalAdvance(item.shortcut) if item.shortcut else 0
                item_w = (self.ITEM_PADDING_X * 2 + self.ICON_WIDTH + text_w
                          + max(self.ARROW_WIDTH, sc_w + 12) + sub_w + 24)
                max_w = max(max_w, item_w)

        total_h += self.BOTTOM_PADDING
        self.setFixedSize(int(max_w), int(total_h))

    def build_default_menu(self, devices: list = None,
                           current_device_id: int | None = None,
                           current_preset: str = "classic",
                           vpn_mode_enabled: bool = False,
                           current_scale_mode: str = "auto",
                           autostart_enabled: bool = False):
        """
        Build the default Voice Pill context menu.

        Args:
            devices: List of AudioDevice objects from recorder.
            current_device_id: Currently selected device ID (None = auto/default).
            current_preset: Currently active animation preset key.
            vpn_mode_enabled: Whether VPN mode is active.
            current_scale_mode: Current pill scale mode ('auto', '100%', etc.).
            autostart_enabled: Whether Windows startup shortcut exists.
        """
        devices = devices or []

        # Resolve the human-readable name of the device currently in use
        if current_device_id is None:
            current_name = "System Default"
            for d in devices:
                if getattr(d, "is_default", False):
                    current_name = f"{d.name}  ·  Auto"
                    break
        else:
            current_name = "Unknown device"
            for d in devices:
                if d.device_id == current_device_id:
                    current_name = d.name
                    break

        items = [
            # ── Header: app name + the microphone currently in use ──
            MenuItem("Voice Pill", "", MenuItemType.HEADER,
                     subtitle=f"🎙  {current_name}", item_id="header"),

            MenuItem("", "", MenuItemType.SEPARATOR),

            MenuItem("INPUT", "", MenuItemType.LABEL, item_id="label_input"),
        ]

        # ── Input device submenu ──
        device_subitems = [
            MenuItem(
                text="System Default",
                subtitle="Auto-switch",
                item_type=MenuItemType.ACTION,
                is_checked=(current_device_id is None),
                item_id="device_auto",
            ),
            MenuItem("", "", MenuItemType.SEPARATOR),
        ]
        if devices:
            for dev in devices:
                device_subitems.append(MenuItem(
                    text=dev.name,
                    subtitle="Default" if getattr(dev, "is_default", False) else "",
                    item_type=MenuItemType.ACTION,
                    is_checked=(dev.device_id == current_device_id),
                    item_id=f"device_{dev.device_id}",
                ))
        else:
            device_subitems.append(MenuItem(
                "No microphones found", "", MenuItemType.ACTION,
                is_enabled=False, item_id="no_devices",
            ))

        # Show the active device inline on the submenu row itself
        short_current = current_name.split("  ·  ")[0]
        items.append(MenuItem(
            "Microphone", "🎙", MenuItemType.SUBMENU,
            submenu_items=device_subitems, item_id="input_device",
            subtitle=(short_current[:18] + "…") if len(short_current) > 19 else short_current,
        ))

        items.append(MenuItem("Refresh Devices", "🔄", MenuItemType.ACTION,
                              item_id="refresh"))

        items.append(MenuItem("", "", MenuItemType.SEPARATOR))
        items.append(MenuItem("APPEARANCE", "", MenuItemType.LABEL, item_id="label_appear"))

        # ── Animation style submenu ──
        preset_subitems = []
        active_preset_name = "Classic Bars"
        for name, key in ANIMATION_PRESETS.items():
            if key == current_preset:
                active_preset_name = name
            preset_subitems.append(MenuItem(
                text=name,
                item_type=MenuItemType.ACTION,
                is_checked=(key == current_preset),
                item_id=f"preset_{key}",
            ))

        items.append(MenuItem(
            "Animation", "🎨", MenuItemType.SUBMENU,
            submenu_items=preset_subitems, item_id="animation_style",
            subtitle=active_preset_name,
        ))

        # ── Pill Size submenu ──
        scale_subitems = [
            MenuItem("Auto (Display)", "", MenuItemType.ACTION,
                     is_checked=(current_scale_mode == "auto"), subtitle="Display-based", item_id="scale_auto"),
            MenuItem("100% (Default)", "", MenuItemType.ACTION,
                     is_checked=(current_scale_mode == "100%"), item_id="scale_100"),
            MenuItem("125%", "", MenuItemType.ACTION,
                     is_checked=(current_scale_mode == "125%"), item_id="scale_125"),
            MenuItem("150%", "", MenuItemType.ACTION,
                     is_checked=(current_scale_mode == "150%"), item_id="scale_150"),
            MenuItem("200%", "", MenuItemType.ACTION,
                     is_checked=(current_scale_mode == "200%"), item_id="scale_200"),
        ]

        items.append(MenuItem(
            "Pill Size", "🔍", MenuItemType.SUBMENU,
            submenu_items=scale_subitems, item_id="pill_size",
            subtitle="Auto (Display)" if current_scale_mode == "auto" else current_scale_mode,
        ))
        items.append(MenuItem("Reset Position to Center", "🎯", MenuItemType.ACTION,
                              item_id="reset_position"))

        items.append(MenuItem("", "", MenuItemType.SEPARATOR))
        items.append(MenuItem("SESSION", "", MenuItemType.LABEL, item_id="label_session"))

        items.append(MenuItem("Start Recording", "●", MenuItemType.ACTION,
                              item_id="start_recording", shortcut="Ctrl+Space"))
        items.append(MenuItem("Clear Context Memory", "🧹", MenuItemType.ACTION,
                              item_id="clear_context"))

        items.append(MenuItem("", "", MenuItemType.SEPARATOR))
        items.append(MenuItem("NETWORK", "", MenuItemType.LABEL, item_id="label_network"))

        items.append(MenuItem(
            "VPN Mode", "🔒", MenuItemType.ACTION,
            item_id="toggle_vpn_mode",
            is_checked=vpn_mode_enabled,
            subtitle="8 kHz audio, extended timeout" if vpn_mode_enabled else "Off",
        ))
        items.append(MenuItem(
            "Test Connection", "🌐", MenuItemType.ACTION,
            item_id="test_connection",
            subtitle="Check if Groq is reachable",
        ))

        items.append(MenuItem("", "", MenuItemType.SEPARATOR))
        items.append(MenuItem("SYSTEM", "", MenuItemType.LABEL, item_id="label_system"))
        items.append(MenuItem(
            "Start with Windows", "🚀", MenuItemType.ACTION,
            item_id="toggle_autostart",
            is_checked=autostart_enabled,
            subtitle="Auto-start on boot" if autostart_enabled else "Off",
        ))

        items.append(MenuItem("", "", MenuItemType.SEPARATOR))
        items.append(MenuItem("Quit Voice Pill", "✕", MenuItemType.ACTION,
                              item_id="quit", is_danger=True))

        self.set_items(items)


    # ──────────────────────────────────────────
    # Show / Hide
    # ──────────────────────────────────────────

    def show_at(self, pos: QPoint):
        """Show menu at position with fade-in and stagger animation."""
        # Adjust if too close to screen edge
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = min(pos.x(), geom.right() - self.width() - 10)
            y = min(pos.y(), geom.bottom() - self.height() - 10)
            x = max(x, geom.left() + 10)
            y = max(y, geom.top() + 10)
            self.move(x, y)
        else:
            self.move(pos)

        # Reset appearance state
        self._item_appear_progress = [0.0] * len(self._items)
        self._hover_index = -1

        # Fade in
        self._fade_anim.stop()
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

        # Stagger items
        self._stagger_index = 0
        self._stagger_timer.start()

        self.show()
        self.raise_()

    def dismiss(self):
        """Hide menu with fade-out."""
        if self._active_submenu:
            self._active_submenu.dismiss()
            self._active_submenu = None

        self._stagger_timer.stop()

        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._on_fade_out_done)
        self._fade_anim.start()

    def _on_fade_out_done(self):
        """Called when fade-out completes."""
        self._fade_anim.finished.disconnect(self._on_fade_out_done)
        self.hide()
        self.closed.emit()

    def _on_stagger_tick(self):
        """Advance stagger animation for one item."""
        if self._stagger_index >= len(self._items):
            self._stagger_timer.stop()
            return

        self._item_appear_progress[self._stagger_index] = 1.0
        self._stagger_index += 1
        self.update()

    # ──────────────────────────────────────────
    # Paint
    # ──────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Menu background
        bg_rect = QRectF(0, 0, self.width(), self.height())
        bg_color = QColor(*CONTEXT_MENU_BG)
        border_color = QColor(*CONTEXT_MENU_BORDER)

        path = QPainterPath()
        path.addRoundedRect(bg_rect, CONTEXT_MENU_RADIUS, CONTEXT_MENU_RADIUS)

        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(QBrush(bg_color))
        painter.drawPath(path)

        # Draw items
        y = float(self.TOP_PADDING)
        for i, item in enumerate(self._items):
            appear = self._item_appear_progress[i] if i < len(self._item_appear_progress) else 1.0
            h = self._item_height(item)

            if item.item_type == MenuItemType.SEPARATOR:
                self._draw_separator(painter, y, appear)
            elif item.item_type == MenuItemType.HEADER:
                self._draw_header(painter, item, y, appear)
            elif item.item_type == MenuItemType.LABEL:
                self._draw_label(painter, item, y, appear)
            else:
                is_hovered = (i == self._hover_index and item.is_enabled)
                self._draw_item(painter, item, y, is_hovered, appear)

            y += h

        painter.end()

    def _draw_separator(self, painter: QPainter, y: float, appear: float):
        """Draw a separator line."""
        if appear < 0.5:
            return

        sep_color = QColor(*CONTEXT_MENU_SEPARATOR)
        sep_color.setAlphaF(appear * 0.6)
        painter.setPen(QPen(sep_color, 1.0))

        margin = self.ITEM_PADDING_X
        painter.drawLine(
            QPointF(margin, y + self.SEPARATOR_HEIGHT / 2),
            QPointF(self.width() - margin, y + self.SEPARATOR_HEIGHT / 2),
        )

    def _draw_header(self, painter: QPainter, item: MenuItem, y: float, appear: float):
        """Draw the title + active-device subtitle header."""
        if appear < 0.1:
            return

        # Title
        title_color = QColor(*CONTEXT_MENU_TEXT)
        title_color.setAlphaF(appear)
        painter.setFont(self._font_title)
        painter.setPen(QPen(title_color))
        painter.drawText(QPointF(self.ITEM_PADDING_X, y + 24), item.text)

        # Subtitle (active microphone) — dimmed
        if item.subtitle:
            sub_color = QColor(*CONTEXT_MENU_TEXT_DIM)
            sub_color.setAlphaF(appear)
            painter.setFont(self._font_small)
            painter.setPen(QPen(sub_color))
            fm = QFontMetrics(self._font_small)
            sub = fm.elidedText(item.subtitle, Qt.TextElideMode.ElideRight,
                                self.width() - self.ITEM_PADDING_X * 2)
            painter.drawText(QPointF(self.ITEM_PADDING_X, y + 43), sub)

    def _draw_label(self, painter: QPainter, item: MenuItem, y: float, appear: float):
        """Draw a dim uppercase section label."""
        if appear < 0.4:
            return
        color = QColor(*CONTEXT_MENU_TEXT_DIM)
        color.setAlphaF(appear * 0.75)
        painter.setFont(self._font_small)
        painter.setPen(QPen(color))
        painter.drawText(QPointF(self.ITEM_PADDING_X, y + self.LABEL_HEIGHT - 7), item.text)

    def _draw_item(self, painter: QPainter, item: MenuItem,
                    y: float, is_hovered: bool, appear: float):
        """Draw a single interactive menu item."""
        if appear < 0.1:
            return

        item_rect = QRectF(5, y + 1, self.width() - 10, self.ITEM_HEIGHT - 2)

        # Hover highlight
        if is_hovered:
            hover_color = QColor(*CONTEXT_MENU_HOVER)
            hover_color.setAlphaF(0.85 * appear)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(hover_color))
            painter.drawRoundedRect(item_rect, 7, 7)

        # Base text color (danger = red, disabled = dim)
        if not item.is_enabled:
            text_color = QColor(*CONTEXT_MENU_TEXT_DIM)
        elif item.is_danger:
            text_color = QColor(255, 95, 95, 255)
        else:
            text_color = QColor(*CONTEXT_MENU_TEXT)
        text_color.setAlphaF(appear)

        has_sub = bool(item.subtitle)
        # Vertically center the main text; nudge up if there's a subtitle line
        main_y = y + (self.ITEM_HEIGHT / 2 + 5) - (5 if has_sub else 0)

        # Icon
        painter.setFont(self._font)
        painter.setPen(QPen(text_color))
        if item.icon:
            painter.drawText(QPointF(self.ITEM_PADDING_X, main_y), item.icon)

        # Main label
        text_x = self.ITEM_PADDING_X + self.ICON_WIDTH
        painter.drawText(QPointF(text_x, main_y), item.text)

        # Inline subtitle (e.g. current device under "Microphone")
        if has_sub:
            sub_color = QColor(*CONTEXT_MENU_TEXT_DIM)
            sub_color.setAlphaF(appear * 0.9)
            painter.setFont(self._font_small)
            painter.setPen(QPen(sub_color))
            painter.drawText(QPointF(text_x, main_y + 13), item.subtitle)

        # Right side: check mark, shortcut, or submenu arrow
        right_x = self.width() - self.ITEM_PADDING_X
        center_y = y + self.ITEM_HEIGHT / 2 + 5

        if item.is_checked:
            painter.setFont(self._font)
            painter.setPen(QPen(QColor(120, 200, 255, int(255 * appear))))  # accent tick
            painter.drawText(QPointF(right_x - 12, center_y), "✓")
        elif item.item_type == MenuItemType.SUBMENU:
            painter.setFont(self._font)
            painter.setPen(QPen(text_color))
            painter.drawText(QPointF(right_x - 10, center_y), "›")
        elif item.shortcut:
            sc_color = QColor(*CONTEXT_MENU_TEXT_DIM)
            sc_color.setAlphaF(appear * 0.8)
            painter.setFont(self._font_small)
            painter.setPen(QPen(sc_color))
            fm = QFontMetrics(self._font_small)
            sc_w = fm.horizontalAdvance(item.shortcut)
            painter.drawText(QPointF(right_x - sc_w, center_y), item.shortcut)

    # ──────────────────────────────────────────
    # Mouse Events
    # ──────────────────────────────────────────

    def mouseMoveEvent(self, event):
        """Track which item is hovered."""
        pos = event.position()
        new_hover = self._get_item_at(pos.y())

        if new_hover != self._hover_index:
            self._hover_index = new_hover
            self.update()

    def mousePressEvent(self, event):
        """Handle item click."""
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position()
        index = self._get_item_at(pos.y())

        if 0 <= index < len(self._items):
            item = self._items[index]
            
            if item.item_type == MenuItemType.SUBMENU:
                # Toggle submenu on click instead of hover
                if self._active_submenu:
                    self._active_submenu.dismiss()
                    self._active_submenu = None
                if item.submenu_items:
                    self._show_submenu(item, index)
                    
            elif item.item_type == MenuItemType.ACTION and item.is_enabled:
                # Check if it's a device selection
                if item.item_id.startswith("device_"):
                    if item.item_id == "device_auto":
                        self.device_selected.emit("auto")
                    else:
                        try:
                            dev_id = int(item.item_id.split("_")[1])
                            self.device_selected.emit(dev_id)
                        except (ValueError, IndexError):
                            pass
                # Check if it's a preset selection
                elif item.item_id.startswith("preset_"):
                    preset_key = item.item_id.replace("preset_", "")
                    self.preset_selected.emit(preset_key)
                # Check if it's a scale selection
                elif item.item_id.startswith("scale_"):
                    if item.item_id == "scale_auto":
                        self.pill_scale_selected.emit("auto", 1.0)
                    elif item.item_id == "scale_100":
                        self.pill_scale_selected.emit("100%", 1.0)
                    elif item.item_id == "scale_125":
                        self.pill_scale_selected.emit("125%", 1.25)
                    elif item.item_id == "scale_150":
                        self.pill_scale_selected.emit("150%", 1.50)
                    elif item.item_id == "scale_200":
                        self.pill_scale_selected.emit("200%", 2.00)
                # Check if it's a VPN mode toggle
                elif item.item_id == "toggle_vpn_mode":
                    new_state = not item.is_checked
                    self.vpn_mode_toggled.emit(new_state)
                # Check if it's an autostart toggle
                elif item.item_id == "toggle_autostart":
                    new_state = not item.is_checked
                    self.autostart_toggled.emit(new_state)
                else:
                    self.action_triggered.emit(item.item_id)

                self.dismiss()

    def keyPressEvent(self, event):
        """Dismiss on Escape."""
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()

    def _get_item_at(self, y: float) -> int:
        """Get the index of the INTERACTIVE item at the given y position (-1 if none)."""
        current_y = float(self.TOP_PADDING)
        for i, item in enumerate(self._items):
            item_h = self._item_height(item)
            if current_y <= y < current_y + item_h:
                return i if item.is_interactive else -1
            current_y += item_h
        return -1

    def _show_submenu(self, item: MenuItem, index: int):
        """Show a submenu for the given item."""
        if self._active_submenu:
            self._active_submenu.dismiss()

        submenu = VoicePillContextMenu()
        submenu.set_items(item.submenu_items)
        submenu.action_triggered.connect(self.action_triggered.emit)
        submenu.device_selected.connect(self.device_selected.emit)
        submenu.preset_selected.connect(self.preset_selected.emit)
        submenu.pill_scale_selected.connect(self.pill_scale_selected.emit)
        submenu.autostart_toggled.connect(self.autostart_toggled.emit)
        
        # Ensure parent menu closes when a submenu action is taken
        submenu.action_triggered.connect(lambda _: self.dismiss())
        submenu.device_selected.connect(lambda _: self.dismiss())
        submenu.preset_selected.connect(lambda _: self.dismiss())
        submenu.pill_scale_selected.connect(lambda *_: self.dismiss())
        submenu.autostart_toggled.connect(lambda _: self.dismiss())


        # Position to the right of the parent menu, aligned to the clicked row
        y_offset = self.TOP_PADDING + sum(
            self._item_height(self._items[j]) for j in range(index)
        )
        submenu_pos = self.mapToGlobal(QPoint(self.width() - 6, int(y_offset)))
        submenu.show_at(submenu_pos)

        self._active_submenu = submenu
