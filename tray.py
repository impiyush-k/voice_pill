"""
Voice Pill — System Tray Integration
=======================================
Manages the system tray icon, tray menu, and toast notifications.

Provides a persistent presence in the Windows notification area
so the user can access settings, see errors, and quit the app
without finding the floating widget.
"""

from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QPen, QAction
from PyQt6.QtCore import Qt, pyqtSignal, QSize


class SystemTrayManager:
    """
    System tray icon and menu manager.

    Signals are emitted via callbacks since this isn't a QObject subclass
    for simplicity — the main.py orchestrator connects actions directly.
    """

    def __init__(self, app: QApplication):
        self._app = app
        self._last_error = "None"

        # Create tray icon
        self._tray = QSystemTrayIcon(app)
        self._tray.setIcon(self._create_pill_icon())
        self._tray.setToolTip("Voice Pill — Ctrl+Space to record")

        # Callbacks
        self._on_show: callable = None
        self._on_start_recording: callable = None
        self._on_quit: callable = None

        # Build menu
        self._menu = QMenu()
        self._build_menu()
        self._tray.setContextMenu(self._menu)

        # Double-click on tray icon → show widget
        self._tray.activated.connect(self._on_tray_activated)

    def _create_pill_icon(self) -> QIcon:
        """
        Generate a small pill/capsule icon for the tray.
        Drawn programmatically — no external file needed.
        """
        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw a silver capsule
        margin = 8
        pill_w = size - margin * 2
        pill_h = size // 3
        pill_y = (size - pill_h) // 2

        # Silver gradient
        from PyQt6.QtGui import QLinearGradient
        gradient = QLinearGradient(margin, pill_y, margin, pill_y + pill_h)
        gradient.setColorAt(0.0, QColor(220, 220, 230))
        gradient.setColorAt(1.0, QColor(140, 140, 155))

        painter.setPen(QPen(QColor(100, 100, 115), 1.5))
        painter.setBrush(QBrush(gradient))
        radius = pill_h // 2
        painter.drawRoundedRect(margin, pill_y, pill_w, pill_h, radius, radius)

        # Draw 3 small white bars in the center
        bar_w = 3
        bar_gap = 4
        bar_count = 3
        total_bars_w = bar_count * bar_w + (bar_count - 1) * bar_gap
        bar_start_x = (size - total_bars_w) // 2
        bar_heights = [pill_h * 0.4, pill_h * 0.7, pill_h * 0.5]

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 200)))

        for i in range(bar_count):
            bx = bar_start_x + i * (bar_w + bar_gap)
            bh = bar_heights[i]
            by = pill_y + (pill_h - bh) / 2
            painter.drawRoundedRect(int(bx), int(by), bar_w, int(bh), 1, 1)

        painter.end()
        return QIcon(pixmap)

    def _build_menu(self):
        """Build the system tray menu."""
        self._menu.clear()
        self._menu.setStyleSheet("""
            QMenu {
                background-color: #1a1a20;
                color: #e6e6eb;
                border: 1px solid #3c3c46;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #32323c;
            }
            QMenu::separator {
                height: 1px;
                background-color: #3c3c46;
                margin: 4px 12px;
            }
        """)

        # Show Voice Pill
        show_action = self._menu.addAction("Show Voice Pill")
        show_action.triggered.connect(lambda: self._on_show() if self._on_show else None)

        self._menu.addSeparator()

        # Start Recording
        record_action = self._menu.addAction("Start Recording")
        record_action.triggered.connect(
            lambda: self._on_start_recording() if self._on_start_recording else None
        )

        self._menu.addSeparator()

        # Last Error
        self._error_action = self._menu.addAction(f"Last Error: {self._last_error}")
        self._error_action.setEnabled(False)

        self._menu.addSeparator()

        # Quit
        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(
            lambda: self._on_quit() if self._on_quit else None
        )

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        """Handle tray icon activation (double-click)."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self._on_show:
                self._on_show()

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def show(self):
        """Show the tray icon."""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    def hide(self):
        """Hide the tray icon."""
        self._tray.hide()

    def set_callbacks(self, on_show=None, on_start_recording=None, on_quit=None):
        """Set callback functions for tray menu actions."""
        self._on_show = on_show
        self._on_start_recording = on_start_recording
        self._on_quit = on_quit

    def show_notification(self, title: str, message: str,
                          icon=QSystemTrayIcon.MessageIcon.Information,
                          duration_ms: int = 3000):
        """Show a Windows toast notification."""
        if self._tray.isVisible():
            self._tray.showMessage(title, message, icon, duration_ms)

    def update_last_error(self, error: str):
        """Update the 'Last Error' display in the tray menu."""
        self._last_error = error or "None"
        if hasattr(self, '_error_action'):
            self._error_action.setText(f"Last Error: {self._last_error}")

    def cleanup(self):
        """Remove tray icon. Called on app exit."""
        self._tray.hide()
