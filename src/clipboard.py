"""
Voice Pill — Clipboard & Paste Utility
========================================
Copies transcribed text to the system clipboard and simulates
Ctrl+V to paste it into whatever application has focus.
"""

import sys
import time
import pyperclip


def _ensure_linux_xhost_access() -> None:
    """Ensure DISPLAY, XAUTHORITY, and local X11 authorization for pynput key simulation."""
    if sys.platform == "linux":
        import os
        import glob
        import subprocess

        if "DISPLAY" not in os.environ:
            os.environ["DISPLAY"] = ":0"

        if "XAUTHORITY" not in os.environ or not os.path.exists(os.environ.get("XAUTHORITY", "")):
            uid = os.getuid()
            matches = glob.glob(f"/run/user/{uid}/.mutter-Xwaylandauth.*")
            if matches:
                os.environ["XAUTHORITY"] = matches[0]
            elif os.path.exists(os.path.expanduser("~/.Xauthority")):
                os.environ["XAUTHORITY"] = os.path.expanduser("~/.Xauthority")

        try:
            subprocess.run(["xhost", "+local:"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


_GLOBAL_PYNPUT_KB = None
_GLOBAL_XLIB_DISPLAY = None


def _get_pynput_kb():
    global _GLOBAL_PYNPUT_KB
    if _GLOBAL_PYNPUT_KB is None:
        from pynput.keyboard import Controller
        _GLOBAL_PYNPUT_KB = Controller()
    return _GLOBAL_PYNPUT_KB


def is_active_window_terminal() -> bool:
    """Detect if the focused window is a standalone terminal application."""
    if sys.platform != "linux":
        return False
    terminal_names = {
        "gnome-terminal", "gnome-terminal-server", "gnome-console", "kgx",
        "konsole", "alacritty", "kitty", "xterm", "terminator", "tilix",
        "wezterm", "foot", "guake", "yakuake", "tilda", "rio", "ghostty",
        "tabby", "hyper", "warp", "st", "rxvt", "uxterm", "mate-terminal",
        "xfce4-terminal"
    }
    try:
        import subprocess
        # Check xdotool
        try:
            out = subprocess.check_output(["xdotool", "getactivewindow", "getwindowclassname"], stderr=subprocess.DEVNULL).decode().strip().lower()
            if any(t in out for t in terminal_names):
                return True
        except Exception:
            pass
        # Check xprop
        try:
            out = subprocess.check_output(["xprop", "-root", "_NET_ACTIVE_WINDOW"], stderr=subprocess.DEVNULL).decode()
            win_id = out.strip().split()[-1]
            if win_id and win_id != "0x0":
                out_class = subprocess.check_output(["xprop", "-id", win_id, "WM_CLASS"], stderr=subprocess.DEVNULL).decode().lower()
                if any(t in out_class for t in terminal_names):
                    return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def _send_paste_shortcut(mode: str = "auto") -> None:
    """
    Simulate paste keystroke at cursor.
    Modes:
        'auto': Uses Shift+Insert on Linux (universal paste for both terminals and GUI/Electron apps)
        'shift_insert': Simulates Shift+Insert
        'ctrl_shift_v': Simulates Ctrl+Shift+V
        'ctrl_v': Simulates Ctrl+V
    """
    time.sleep(0.08)

    # Determine effective target shortcut
    effective = mode
    if effective == "auto":
        # On Linux, Shift+Insert is universal across GTK, Qt, Electron (Antigravity/VS Code),
        # xterm.js (integrated terminal), and standalone terminals.
        effective = "shift_insert" if sys.platform == "linux" else "ctrl_v"

    # 1. Linux Primary: evdev UInput kernel virtual keyboard
    if sys.platform == "linux":
        try:
            import evdev
            from evdev import UInput, ecodes as e
            ui = UInput({e.EV_KEY: [e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_V, e.KEY_INSERT, e.KEY_LEFTALT, e.KEY_SPACE]})
            time.sleep(0.02)
            # Release modifier keys in case physical keys are still held down by the user
            for keycode in (e.KEY_SPACE, e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT, e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL, e.KEY_LEFTALT, e.KEY_RIGHTALT):
                ui.write(e.EV_KEY, keycode, 0)
            ui.syn()
            time.sleep(0.01)

            if effective == "shift_insert":
                ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
                ui.write(e.EV_KEY, e.KEY_INSERT, 1)
                ui.syn()
                time.sleep(0.03)
                ui.write(e.EV_KEY, e.KEY_INSERT, 0)
                ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
                ui.syn()
            elif effective == "ctrl_shift_v":
                ui.write(e.EV_KEY, e.KEY_LEFTCTRL, 1)
                ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
                ui.write(e.EV_KEY, e.KEY_V, 1)
                ui.syn()
                time.sleep(0.03)
                ui.write(e.EV_KEY, e.KEY_V, 0)
                ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
                ui.write(e.EV_KEY, e.KEY_LEFTCTRL, 0)
                ui.syn()
            else: # ctrl_v
                ui.write(e.EV_KEY, e.KEY_LEFTCTRL, 1)
                ui.write(e.EV_KEY, e.KEY_V, 1)
                ui.syn()
                time.sleep(0.03)
                ui.write(e.EV_KEY, e.KEY_V, 0)
                ui.write(e.EV_KEY, e.KEY_LEFTCTRL, 0)
                ui.syn()

            ui.close()
            return
        except Exception as e:
            print(f"[Clipboard] evdev paste notice: {e}")

    # 2. Linux Fallback: ydotool daemon if installed
    if sys.platform == "linux":
        try:
            import subprocess
            if effective == "shift_insert":
                # Shift (42) + Insert (110)
                res = subprocess.run(["ydotool", "key", "42:1", "110:1", "110:0", "42:0"], capture_output=True)
            elif effective == "ctrl_shift_v":
                # Ctrl (29) + Shift (42) + V (47)
                res = subprocess.run(["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"], capture_output=True)
            else:
                # Ctrl (29) + V (47)
                res = subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], capture_output=True)
            if res.returncode == 0:
                return
        except Exception as e:
            print(f"[Clipboard] ydotool paste notice: {e}")

    # 3. Windows Primary: keyboard module
    if sys.platform == "win32":
        try:
            import keyboard
            shortcut = "shift+insert" if effective == "shift_insert" else ("ctrl+shift+v" if effective == "ctrl_shift_v" else "ctrl+v")
            keyboard.send(shortcut)
            return
        except Exception as e:
            print(f"[Clipboard] Warning: keyboard simulation failed: {e}")

    # 4. Fallback pynput Controller instance
    try:
        from pynput.keyboard import Key
        kb = _get_pynput_kb()
        for k in (Key.space, Key.shift, Key.ctrl, Key.alt):
            try:
                kb.release(k)
            except Exception:
                pass
        time.sleep(0.02)
        if effective == "shift_insert":
            kb.press(Key.shift)
            kb.press(Key.insert)
            time.sleep(0.04)
            kb.release(Key.insert)
            kb.release(Key.shift)
        elif effective == "ctrl_shift_v":
            kb.press(Key.ctrl)
            kb.press(Key.shift)
            kb.press('v')
            time.sleep(0.04)
            kb.release('v')
            kb.release(Key.shift)
            kb.release(Key.ctrl)
        else:
            kb.press(Key.ctrl)
            kb.press('v')
            time.sleep(0.04)
            kb.release('v')
            kb.release(Key.ctrl)
        return
    except Exception as e:
        print(f"[Clipboard] pynput paste notice: {e}")


def _set_clipboard_text(text: str) -> None:
    """Set system clipboard using pyperclip, Qt clipboard, and wl-copy on Wayland."""
    try:
        pyperclip.copy(text)
    except Exception:
        pass

    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            cb = app.clipboard()
            if cb:
                cb.setText(text)
    except Exception:
        pass

    if sys.platform == "linux":
        try:
            import subprocess
            subprocess.run(["wl-copy", text], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def copy_and_paste(text: str, mode: str = "auto") -> bool:
    """
    Copy text to clipboard and simulate paste shortcut at cursor.

    Flow:
        1. Copy text to system clipboard via pyperclip, Qt, and wl-copy
        2. 30ms delay — ensures clipboard is updated
        3. Simulate paste keystroke according to mode (auto / shift_insert / ctrl_shift_v / ctrl_v)
        4. 30ms delay — ensures paste is processed

    Args:
        text: The text to paste.
        mode: Paste mode ('auto', 'shift_insert', 'ctrl_shift_v', 'ctrl_v', 'copy_only').

    Returns:
        True if successful, False on error.
    """
    if not text or not text.strip():
        return False

    try:
        # Step 1: Copy to clipboard
        _set_clipboard_text(text)

        if mode == "copy_only":
            return True

        # Step 2: Brief pause for clipboard sync
        time.sleep(0.03)

        # Step 3: Simulate paste keystroke
        _send_paste_shortcut(mode)

        # Step 4: Brief pause to let target app process paste
        time.sleep(0.03)

        return True

    except Exception as e:
        print(f"[Clipboard] Error during copy_and_paste: {e}")
        return False



def copy_only(text: str) -> bool:
    """
    Copy text to clipboard WITHOUT simulating Ctrl+V.
    Fallback method if paste simulation causes issues.

    Args:
        text: The text to copy.

    Returns:
        True if successful, False on error.
    """
    if not text or not text.strip():
        return False

    try:
        pyperclip.copy(text)
        return True
    except Exception:
        return False
