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
import os
import atexit
import signal
import threading

# Try importing keyboard; on Linux non-root this will fail on use or import.
_KEYBOARD_AVAILABLE = False
if sys.platform == "win32":
    try:
        import keyboard
        _KEYBOARD_AVAILABLE = True
    except Exception:
        _KEYBOARD_AVAILABLE = False

try:
    from pynput import keyboard as pynput_keyboard
    _PYNPUT_AVAILABLE = True
except Exception:
    _PYNPUT_AVAILABLE = False


def _to_pynput_combo(key_combo: str) -> str:
    """Convert 'ctrl+space' string format to pynput '<ctrl>+<space>' format."""
    parts = [p.strip().lower() for p in key_combo.split("+")]
    modifiers = {"ctrl", "control", "shift", "alt", "cmd", "win"}
    specials = {
        "space", "enter", "tab", "esc", "escape", "backspace",
        "delete", "up", "down", "left", "right"
    }
    converted = []
    for p in parts:
        if p in modifiers or p in specials:
            converted.append(f"<{p}>")
        else:
            converted.append(p)
    return "+".join(converted)


class HotkeyManager:
    """
    Thread-safe global hotkey manager with crash-proof cleanup.
    Supports 'keyboard' library on Windows/root, GNOME gsettings on Linux GNOME Wayland,
    and 'pynput' fallback on Linux X11/other WMs.
    """

    def __init__(self):
        self._registered: dict[str, object] = {}  # name → handle or metadata
        self._pynput_items: dict[str, tuple[str, callable]] = {}  # name → (pynput_combo, callback)
        self._pynput_listener: object | None = None
        self._lock = threading.Lock()
        self._cleanup_installed = False
        self._backend = "keyboard" if (_KEYBOARD_AVAILABLE and sys.platform == "win32") else "pynput"
        self._setup_gnome_shortcuts()

    def _setup_gnome_shortcuts(self):
        """Auto-configure GNOME global keybindings if running on Linux GNOME Desktop."""
        if sys.platform != "linux":
            return
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        if "GNOME" not in desktop:
            return

        def _worker():
            try:
                import subprocess
                import ast
                script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "start.sh")

                res = subprocess.run(
                    ['gsettings', 'get', 'org.gnome.settings-daemon.plugins.media-keys', 'custom-keybindings'],
                    capture_output=True, text=True
                )
                out = res.stdout.strip()
                existing = []
                if out and out != '@as []':
                    try:
                        existing = ast.literal_eval(out)
                    except Exception:
                        existing = []

                bindings_to_add = [
                    {
                        'path': '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicepill0/',
                        'name': 'Voice Pill Dictation',
                        'command': f'bash "{script_path}" --activate',
                        'binding': '<Primary>space'
                    },
                    {
                        'path': '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicepill1/',
                        'name': 'Voice Pill Streaming Dictation',
                        'command': f'bash "{script_path}" --stream',
                        'binding': '<Primary><Shift>space'
                    }
                ]

                new_list = list(existing)
                for b in bindings_to_add:
                    if b['path'] not in new_list:
                        new_list.append(b['path'])
                    rel_path = f'org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{b["path"]}'
                    subprocess.run(['gsettings', 'set', rel_path, 'name', b['name']])
                    subprocess.run(['gsettings', 'set', rel_path, 'command', b['command']])
                    subprocess.run(['gsettings', 'set', rel_path, 'binding', b['binding']])

                subprocess.run(['gsettings', 'set', 'org.gnome.settings-daemon.plugins.media-keys', 'custom-keybindings', str(new_list)])
            except Exception as e:
                print(f"[HotkeyManager] GNOME shortcut auto-registration notice: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def register(self, name: str, key_combo: str, callback, suppress: bool = False) -> bool:
        """
        Register a global hotkey.
        """
        with self._lock:
            # Unregister existing if re-registering same name
            if name in self._registered:
                self._unregister_unsafe(name)

            # Try keyboard backend first if configured for keyboard
            if self._backend == "keyboard":
                try:
                    import keyboard
                    handle = keyboard.add_hotkey(
                        key_combo,
                        callback,
                        suppress=suppress,
                        trigger_on_release=False
                    )
                    self._registered[name] = handle
                    return True
                except Exception as e:
                    print(f"[HotkeyManager] 'keyboard' backend failed for '{name}': {e}. Falling back to 'pynput'.")
                    self._backend = "pynput"

            # Use pynput backend
            if _PYNPUT_AVAILABLE:
                try:
                    pynput_combo = _to_pynput_combo(key_combo)
                    self._pynput_items[name] = (pynput_combo, callback)
                    self._registered[name] = pynput_combo
                    self._rebuild_pynput_listener_unsafe()
                    return True
                except Exception as e:
                    print(f"[HotkeyManager] Failed to register '{name}' ({key_combo}) via pynput: {e}")
                    return False

            print(f"[HotkeyManager] No suitable hotkey backend available for '{name}'.")
            return False

    def unregister(self, name: str) -> bool:
        """Unregister a specific hotkey by name."""
        with self._lock:
            return self._unregister_unsafe(name)

    def _unregister_unsafe(self, name: str) -> bool:
        """Internal unregister without lock (caller must hold lock)."""
        if name not in self._registered:
            return False

        if self._backend == "keyboard":
            try:
                import keyboard
                keyboard.remove_hotkey(self._registered[name])
            except Exception:
                pass
            del self._registered[name]
            return True
        else:
            if name in self._pynput_items:
                del self._pynput_items[name]
            if name in self._registered:
                del self._registered[name]
            self._rebuild_pynput_listener_unsafe()
            return True

    def _rebuild_pynput_listener_unsafe(self) -> None:
        """Restart pynput listener with current registered callbacks."""
        if self._pynput_listener is not None:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None

        if not self._pynput_items or not _PYNPUT_AVAILABLE:
            return

        hotkey_dict = {
            p_combo: cb for (p_combo, cb) in self._pynput_items.values()
        }

        try:
            self._pynput_listener = pynput_keyboard.GlobalHotKeys(hotkey_dict)
            self._pynput_listener.start()
        except Exception as e:
            print(f"[HotkeyManager] Error starting pynput listener: {e}")

    def unregister_all(self) -> None:
        """Unregister ALL hotkeys. Called on shutdown and crash."""
        with self._lock:
            if self._backend == "keyboard":
                try:
                    import keyboard
                    for name in list(self._registered.keys()):
                        try:
                            keyboard.remove_hotkey(self._registered[name])
                        except Exception:
                            pass
                except Exception:
                    pass
            else:
                if self._pynput_listener is not None:
                    try:
                        self._pynput_listener.stop()
                    except Exception:
                        pass
                    self._pynput_listener = None
                self._pynput_items.clear()

            self._registered.clear()

    def is_registered(self, name: str) -> bool:
        """Check if a hotkey is currently registered."""
        return name in self._registered

    def get_registered_names(self) -> list[str]:
        """Get list of all registered hotkey names."""
        return list(self._registered.keys())

    def install_crash_handlers(self) -> None:
        """Install failsafe cleanup handlers for all exit paths."""
        if self._cleanup_installed:
            return

        atexit.register(self.unregister_all)

        def _signal_handler(sig, frame):
            self.unregister_all()
            sys.exit(0)

        try:
            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)
        except (OSError, ValueError):
            pass

        _original_excepthook = sys.excepthook

        def _exception_handler(exc_type, exc_value, exc_traceback):
            self.unregister_all()
            _original_excepthook(exc_type, exc_value, exc_traceback)

        sys.excepthook = _exception_handler
        self._cleanup_installed = True

