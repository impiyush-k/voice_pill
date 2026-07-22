"""
Voice Pill — Global Hotkey Manager
====================================
Manages global hotkey registration and unregistration with
thread safety and triple-failsafe crash cleanup.

Critical: The Space key is only registered during recording
and MUST be unregistered when recording stops to avoid
capturing all spacebar input system-wide.
"""

import sys
import atexit
import signal
import threading
import keyboard


class HotkeyManager:
    """
    Thread-safe global hotkey manager with crash-proof cleanup.

    Usage:
        hk = HotkeyManager()
        hk.register("activate", "ctrl+space", on_activate_callback)
        # ... later, during recording ...
        hk.register("stop", "space", on_stop_callback)
        # ... when recording stops ...
        hk.unregister("stop")  # Space goes back to normal
    """

    def __init__(self):
        self._registered: dict[str, object] = {}  # name → keyboard hotkey handle
        self._lock = threading.Lock()
        self._cleanup_installed = False

    def register(self, name: str, key_combo: str, callback, suppress: bool = False) -> bool:
        """
        Register a global hotkey.

        Args:
            name: Unique identifier for this hotkey (e.g., "activate", "stop").
            key_combo: Key combination string (e.g., "ctrl+space", "space").
            callback: Function to call when hotkey is pressed.
            suppress: If True, the keypress is consumed and not passed
                      to other applications. Set to False by default
                      because True requires Administrator privileges on Windows.

        Returns:
            True if registered successfully, False on error.
        """
        with self._lock:
            try:
                # Unregister existing if re-registering same name
                if name in self._registered:
                    self._unregister_unsafe(name)

                # Use suppress=False to avoid needing admin privileges
                handle = keyboard.add_hotkey(
                    key_combo,
                    callback,
                    suppress=suppress,
                    trigger_on_release=False
                )
                self._registered[name] = handle
                return True

            except Exception as e:
                print(f"[HotkeyManager] Failed to register '{name}' ({key_combo}): {e}")
                return False

    def unregister(self, name: str) -> bool:
        """
        Unregister a specific hotkey by name.

        Args:
            name: The name used when registering.

        Returns:
            True if unregistered, False if not found or error.
        """
        with self._lock:
            return self._unregister_unsafe(name)

    def _unregister_unsafe(self, name: str) -> bool:
        """Internal unregister without lock (caller must hold lock)."""
        if name not in self._registered:
            return False
        try:
            keyboard.remove_hotkey(self._registered[name])
        except (ValueError, KeyError):
            pass  # Already removed or invalid — that's fine
        del self._registered[name]
        return True

    def unregister_all(self) -> None:
        """
        Unregister ALL hotkeys. Called on shutdown and crash.
        Best-effort — won't raise exceptions.
        """
        with self._lock:
            for name in list(self._registered.keys()):
                try:
                    keyboard.remove_hotkey(self._registered[name])
                except Exception:
                    pass  # Best effort
            self._registered.clear()

    def is_registered(self, name: str) -> bool:
        """Check if a hotkey is currently registered."""
        return name in self._registered

    def get_registered_names(self) -> list[str]:
        """Get list of all registered hotkey names."""
        return list(self._registered.keys())

    def install_crash_handlers(self) -> None:
        """
        Install failsafe cleanup handlers for all exit paths.

        Three layers of protection:
        1. atexit — normal Python exit
        2. signal — Ctrl+C and SIGTERM
        3. sys.excepthook — unhandled exceptions

        Call this once at startup.
        """
        if self._cleanup_installed:
            return

        # Layer 1: Normal exit
        atexit.register(self.unregister_all)

        # Layer 2: Signals (Ctrl+C, kill)
        def _signal_handler(sig, frame):
            self.unregister_all()
            sys.exit(0)

        try:
            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)
        except (OSError, ValueError):
            # Can fail if not on main thread — non-critical
            pass

        # Layer 3: Unhandled exceptions
        _original_excepthook = sys.excepthook

        def _exception_handler(exc_type, exc_value, exc_traceback):
            self.unregister_all()
            _original_excepthook(exc_type, exc_value, exc_traceback)

        sys.excepthook = _exception_handler

        self._cleanup_installed = True
