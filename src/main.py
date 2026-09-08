"""
Voice Pill — Main Entry Point
================================
Orchestrates all subsystems: UI, audio, API, hotkeys, tray.
Wires signals/slots, manages the state machine, handles errors.

Run this file to start Voice Pill:
    py main.py
    pythonw main.py   (no console window)
"""

import sys
import os
import ctypes
import threading
import time
import queue
import socket

if sys.platform == "linux":
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    if "DISPLAY" not in os.environ:
        os.environ["DISPLAY"] = ":0"
    if "XAUTHORITY" not in os.environ or not os.path.exists(os.environ.get("XAUTHORITY", "")):
        import glob
        matches = glob.glob(f"/run/user/{os.getuid()}/.mutter-Xwaylandauth.*")
        if matches:
            os.environ["XAUTHORITY"] = matches[0]
        elif os.path.exists(os.path.expanduser("~/.Xauthority")):
            os.environ["XAUTHORITY"] = os.path.expanduser("~/.Xauthority")
    try:
        import subprocess
        subprocess.run(["xhost", "+local:"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

_script_dir = os.path.dirname(os.path.abspath(__file__))
_proj_dir = os.path.dirname(_script_dir)
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# Auto re-exec with virtualenv if PyQt6 is missing in system python
try:
    import PyQt6
except ImportError:
    _venv_python = os.path.join(_proj_dir, "venv", "bin", "python")
    if os.path.exists(_venv_python) and sys.executable != _venv_python:
        os.execv(_venv_python, [_venv_python] + sys.argv)
    else:
        sys.stderr.write("Error: PyQt6 is not installed. Run via ./start.sh or install requirements.txt.\n")
        sys.exit(1)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal, pyqtSlot

from config import (
    GROQ_API_KEY, HOTKEY_ACTIVATE, HOTKEY_STREAM_ACTIVATE, HOTKEY_STOP,
    validate_config, SILENCE_DETECTION_ENABLED,
    load_vpn_mode, save_vpn_mode, is_position_visible,
    STREAM_CHUNK_MIN_SEC, STREAM_CHUNK_MAX_SEC, STREAM_PAUSE_SLICE_SEC,
)
from autostart import is_autostart_enabled, enable_autostart, disable_autostart


def _short_error(msg: str, max_len: int = 100) -> str:
    """Trim long exception messages for tray notification display."""
    if not msg:
        return "Unknown error"
    msg_lower = msg.lower()
    # HTTP status codes
    if "403" in msg or "forbidden" in msg_lower:
        return "403 Forbidden — VPN is blocking Groq. Try: disable VPN SSL inspection, or use a VPN split-tunnel."
    if "401" in msg or "unauthorized" in msg_lower:
        return "401 Unauthorized — API key rejected. Check GROQ_API_KEY in .env"
    if "timed out" in msg_lower or "timeout" in msg_lower:
        return "Request timed out — VPN connection too slow. Try increasing VPN Read Timeout."
    if "connection refused" in msg_lower:
        return "Connection refused — Groq unreachable on VPN"
    if "ssl" in msg_lower or "certificate" in msg_lower:
        return "SSL error — VPN is intercepting HTTPS. Enable 'Ignore SSL' in VPN Mode settings."
    # Long requests error messages
    for prefix in ("HTTPSConnectionPool", "Max retries exceeded", "Failed to establish"):
        if msg.startswith(prefix):
            return "Network error — cannot reach Groq API on this connection"
    return msg[:max_len] + ("…" if len(msg) > max_len else "")


class _WorkerSignals(QObject):
    """Signals to safely marshal events from background threads to Qt main thread."""
    activate = pyqtSignal()
    stream_activate = pyqtSignal()
    stop = pyqtSignal()
    transcription_done = pyqtSignal(str)
    transcription_error = pyqtSignal(str)
    chunk_processing_started = pyqtSignal()
    chunk_transcription_done = pyqtSignal(str)
    quit_app = pyqtSignal()
    show_widget = pyqtSignal()
    reset_position = pyqtSignal()

# ──────────────────────────────────────────────
# Single Instance Enforcement & IPC Command Server
# ──────────────────────────────────────────────

IPC_PORT = 47823

def send_ipc_command(cmd: str) -> bool:
    """Send a command string (e.g. 'activate', 'stream_activate', 'stop', 'show') to a running Voice Pill process."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(("127.0.0.1", IPC_PORT))
        s.sendall(f"{cmd}\n".encode("utf-8"))
        s.close()
        return True
    except Exception:
        return False


def start_ipc_server(signals: _WorkerSignals) -> socket.socket | None:
    """Start localhost socket server to listen for hotkey IPC commands from secondary processes."""
    try:
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", IPC_PORT))
        server_sock.listen(5)

        def _listen_loop():
            while True:
                try:
                    conn, _ = server_sock.accept()
                    data = conn.recv(1024).decode("utf-8", errors="ignore").strip()
                    conn.close()
                    if not data:
                        continue

                    cmd = data.lower()
                    if cmd in ("activate", "toggle"):
                        signals.activate.emit()
                    elif cmd in ("stream_activate", "stream"):
                        signals.stream_activate.emit()
                    elif cmd == "stop":
                        signals.stop.emit()
                    elif cmd in ("show", "idle"):
                        signals.show_widget.emit()
                    elif cmd in ("reset", "reset_pos", "recenter"):
                        signals.reset_position.emit()
                    elif cmd == "quit":
                        signals.quit_app.emit()
                except Exception:
                    break

        t = threading.Thread(target=_listen_loop, daemon=True)
        t.start()
        return server_sock
    except Exception as e:
        print(f"[IPC] Failed to start IPC server: {e}")
        return None




# ──────────────────────────────────────────────
# Main Application Controller
# ──────────────────────────────────────────────

class VoicePillApp:
    """
    Application controller — wires all subsystems together.
    """

    def __init__(self):
        # ── Core Components ──
        # High DPI MUST be set BEFORE QApplication is created
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

        self.app = QApplication(sys.argv)
        self.app.setApplicationName("Voice Pill")
        self.app.setQuitOnLastWindowClosed(False)

        # ── Lazy imports (after QApplication exists) ──
        from widget import VoicePillWidget, State
        from context_menu import VoicePillContextMenu
        from tray import SystemTrayManager
        from recorder import AudioRecorder
        from groq_client import GroqClient
        from hotkeys import HotkeyManager
        from clipboard import copy_and_paste

        # Store State enum for use in callbacks
        self.State = State
        self._copy_and_paste = copy_and_paste

        # ── Subsystems ──
        self.hotkey_manager = HotkeyManager()
        self.audio_recorder = AudioRecorder()
        self.groq_client = GroqClient()
        self.widget = VoicePillWidget()
        self.context_menu = VoicePillContextMenu()
        self.tray = SystemTrayManager(self.app)

        # ── Safe Signals ──
        self.signals = _WorkerSignals()
        self.signals.activate.connect(self._on_activate)
        self.signals.stream_activate.connect(self._on_stream_activate)
        self.signals.stop.connect(self._on_stop)
        self.signals.transcription_done.connect(self._on_transcription_done)
        self.signals.transcription_error.connect(self._on_transcription_error)
        self.signals.chunk_processing_started.connect(self._on_chunk_processing_started)
        self.signals.chunk_transcription_done.connect(self._on_chunk_transcription_done)
        self.signals.quit_app.connect(self._quit)

        # ── Localhost IPC Server ──
        self._ipc_server = start_ipc_server(self.signals)

        # ── State ──
        self._space_registered = False
        self._is_streaming = False
        self._is_chunk_worker_active = False
        self._last_chunk_slice_time = 0.0
        self._last_activate_time = 0.0

        # ── Streaming Dictation Queue & Timer ──
        self._chunk_queue = queue.Queue()
        self._chunk_timer = QTimer()
        self._chunk_timer.setInterval(200)  # Check pause slice every 200ms
        self._chunk_timer.timeout.connect(self._check_stream_slice)
        self._start_queue_worker()

        # ── Setup ──
        self._connect_signals()
        self._register_hotkeys()
        self._setup_tray()
        self._validate_startup()

        # ── Keep-Alive Timer ──
        # Windows 11 aggressively suspends/kills background GUI processes that hit 0.00% CPU for hours.
        # This 15-second tick ensures the OS considers the process active.
        self._keep_alive_timer = QTimer()
        self._keep_alive_timer.setInterval(15000)
        self._keep_alive_timer.timeout.connect(lambda: None)
        self._keep_alive_timer.start()

        # ── VPN Mode (persisted) ──
        self._vpn_mode_enabled = load_vpn_mode()
        self.groq_client.set_vpn_mode(self._vpn_mode_enabled)

    # ──────────────────────────────────────────
    # Signal Connections
    # ──────────────────────────────────────────

    def _connect_signals(self):
        """Wire all signals and slots."""

        # Audio → UI: amplitude for bar animation
        self.audio_recorder.amplitude_updated.connect(
            self.widget.update_amplitude,
            Qt.ConnectionType.QueuedConnection  # Thread-safe: queued to main thread
        )

        # Audio error
        self.audio_recorder.recording_error.connect(
            self._on_recording_error,
            Qt.ConnectionType.QueuedConnection
        )

        # Silence detection → stop recording
        if SILENCE_DETECTION_ENABLED:
            self.audio_recorder.silence_detector._on_silence = self._on_silence_detected

        # Internal signals
        self.signals.activate.connect(self._on_activate)
        self.signals.stream_activate.connect(self._on_stream_activate)
        self.signals.stop.connect(self._on_stop)
        self.signals.quit_app.connect(self._quit)
        self.signals.show_widget.connect(self._on_show_widget)
        self.signals.reset_position.connect(self._on_reset_position)

        # Widget interactions
        self.widget.clicked.connect(self._on_activate)
        self.widget.right_clicked.connect(self._show_context_menu)

        # Context menu actions
        self.context_menu.action_triggered.connect(self._on_menu_action)
        self.context_menu.device_selected.connect(self._on_device_selected)
        self.context_menu.preset_selected.connect(self._on_preset_selected)
        self.context_menu.vpn_mode_toggled.connect(self._on_vpn_mode_toggled)
        self.context_menu.pill_scale_selected.connect(self._on_pill_scale_selected)
        self.context_menu.autostart_toggled.connect(self._on_autostart_toggled)

        # ── System Auto-Start ──
        if not is_autostart_enabled():
            enable_autostart()

    def _on_show_widget(self):
        """Bring Voice Pill widget to front without starting recording."""
        self.widget.show()
        self.widget.raise_()
        self.widget.activateWindow()
        if not is_position_visible(self.widget.x(), self.widget.y(), self.widget.width(), self.widget.height()):
            self.widget.reset_to_default_position()
        if self.widget.current_state not in (self.State.RECORDING, self.State.PROCESSING):
            self.widget.transition_to(self.State.IDLE)

    def _on_reset_position(self):
        """Reset Voice Pill widget position to bottom-center of primary screen."""
        self.widget.reset_to_default_position()
        self.widget.show()
        self.widget.raise_()
        self.widget.activateWindow()
        if self.widget.current_state not in (self.State.RECORDING, self.State.PROCESSING):
            self.widget.transition_to(self.State.IDLE)
        self.tray.show_notification("Voice Pill", "Pill position reset to bottom-center")


    # ──────────────────────────────────────────
    # Hotkey Registration
    # ──────────────────────────────────────────

    def _register_hotkeys(self):
        """Register global hotkeys."""
        self.hotkey_manager.install_crash_handlers()

        # On GNOME Linux, shortcuts are handled natively by GNOME gsettings -> start.sh IPC.
        # Bypass pynput listener on GNOME to eliminate duplicate event firing.
        if sys.platform == "linux" and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
            return

        # Windows / non-GNOME fallbacks
        self.hotkey_manager.register("activate", HOTKEY_ACTIVATE, self._on_activate_from_thread)
        self.hotkey_manager.register("stream_activate", HOTKEY_STREAM_ACTIVATE, self._on_stream_activate_from_thread)

    def _register_stop_hotkey(self):
        """Register Space as stop hotkey (only during recording)."""
        if not self._space_registered:
            if sys.platform == "linux" and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
                def _worker():
                    try:
                        import subprocess
                        script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "start.sh")
                        path = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicepillstop/'
                        rel_path = f'org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{path}'
                        
                        # Add voicepillstop to GNOME custom keybindings list if not already present
                        res = subprocess.run(['gsettings', 'get', 'org.gnome.settings-daemon.plugins.media-keys', 'custom-keybindings'], capture_output=True, text=True)
                        out = res.stdout.strip()
                        import ast
                        existing = []
                        if out and out != '@as []':
                            try:
                                existing = ast.literal_eval(out)
                            except Exception:
                                existing = []
                        if path not in existing:
                            existing.append(path)
                            subprocess.run(['gsettings', 'set', 'org.gnome.settings-daemon.plugins.media-keys', 'custom-keybindings', str(existing)])

                        subprocess.run(['gsettings', 'set', rel_path, 'name', 'Voice Pill Stop Recording'])
                        subprocess.run(['gsettings', 'set', rel_path, 'command', f'bash "{script_path}" --stop'])
                        subprocess.run(['gsettings', 'set', rel_path, 'binding', 'space'])
                    except Exception as e:
                        print(f"[HotkeyManager] GNOME stop shortcut registration notice: {e}")
                threading.Thread(target=_worker, daemon=True).start()
            else:
                self.hotkey_manager.register("stop", HOTKEY_STOP, self._on_stop_from_thread)
            self._space_registered = True

    def _unregister_stop_hotkey(self):
        """Unregister Space hotkey (when recording stops)."""
        if self._space_registered:
            if sys.platform == "linux" and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
                def _worker():
                    try:
                        import subprocess
                        path = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicepillstop/'
                        rel_path = f'org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{path}'
                        subprocess.run(['gsettings', 'set', rel_path, 'binding', ''])
                    except Exception as e:
                        print(f"[HotkeyManager] GNOME stop shortcut unregistration notice: {e}")
                threading.Thread(target=_worker, daemon=True).start()
            else:
                self.hotkey_manager.unregister("stop")
            self._space_registered = False

    # ──────────────────────────────────────────
    # Thread-Safe Hotkey Callbacks
    # ──────────────────────────────────────────
    # keyboard library fires callbacks on its OWN thread.
    # We MUST marshal to Qt's main thread via signals.

    def _on_activate_from_thread(self):
        """Called from keyboard thread — marshal to main thread."""
        self.signals.activate.emit()

    def _on_stream_activate_from_thread(self):
        """Called from keyboard thread — marshal to main thread."""
        self.signals.stream_activate.emit()

    def _on_stop_from_thread(self):
        """Called from keyboard thread — marshal to main thread."""
        self.signals.stop.emit()

    def _on_activate(self):
        """
        Ctrl+Space pressed (runs on main thread).
        Toggle recording: if IDLE/READY -> start recording. If RECORDING -> stop recording.
        """
        now = time.monotonic()
        if now - self._last_activate_time < 0.5:
            return
        self._last_activate_time = now

        state = self.widget.current_state

        if self._is_streaming or state == self.State.RECORDING:
            self._on_stop()
            return

        if state in (self.State.IDLE, self.State.READY):
            success = self.audio_recorder.start_recording()
            if success:
                self._is_streaming = False
                self.widget.transition_to(self.State.RECORDING)
                self._register_stop_hotkey()
            else:
                self.widget.show_error_flash()
                self.tray.update_last_error("Failed to start recording")

    def _on_stream_activate(self):
        """
        Ctrl+Shift+Space pressed (runs on main thread).
        If IDLE or READY → start streaming dictation mode.
        If already streaming → stop streaming.
        """
        now = time.monotonic()
        if now - self._last_activate_time < 0.5:
            return
        self._last_activate_time = now

        if self._is_streaming:
            self._on_stop()
            return

        state = self.widget.current_state

        if state in (self.State.IDLE, self.State.READY):
            success = self.audio_recorder.start_recording()
            if success:
                self._is_streaming = True
                self._last_chunk_slice_time = time.monotonic()
                self.widget.transition_to(self.State.RECORDING)
                self._register_stop_hotkey()
                self._chunk_timer.start()
            else:
                self.widget.show_error_flash()
                self.tray.update_last_error("Failed to start streaming recording")

    def _check_stream_slice(self):
        """Timer callback (every 200ms) to slice streaming chunks on natural pauses or 15s timeout."""
        if not self._is_streaming or not self.audio_recorder.is_recording:
            return

        try:
            dur = time.monotonic() - self._last_chunk_slice_time
            is_silent = False
            try:
                is_silent = self.audio_recorder.silence_detector.is_silent
            except Exception:
                is_silent = False

            # Slice if:
            # 1. Spoken >= 10.0s and natural pause occurred, OR
            # 2. Reached max 15.0s (even if continuous speech without pause)
            if (dur >= STREAM_CHUNK_MIN_SEC and is_silent) or (dur >= STREAM_CHUNK_MAX_SEC):
                wav_bytes, chunk_dur = self.audio_recorder.slice_chunk_buffer()
                if wav_bytes:
                    self._last_chunk_slice_time = time.monotonic()
                    self._chunk_queue.put((wav_bytes, chunk_dur))
        except Exception as e:
            print(f"\n[STREAM SLICE ERROR] {e}")

    def _on_stop(self):
        """
        Space pressed during recording (runs on main thread).
        Stop recording → start processing.
        """
        state = self.widget.current_state

        if state != self.State.RECORDING and not self._is_streaming:
            return

        # IMMEDIATELY unregister Space — critical for user's keyboard
        self._unregister_stop_hotkey()

        if self._is_streaming:
            self._is_streaming = False
            self._chunk_timer.stop()

            # Immediately stop listening & slice remaining final buffer
            final_wav, final_dur = self.audio_recorder.slice_chunk_buffer()
            self.audio_recorder.stop_recording()

            if final_wav:
                self._chunk_queue.put((final_wav, final_dur))

            # Turn off glowing border and morph to PROCESSING state (particle galaxy) if queue has work
            self.widget.set_chunk_processing(False)
            if not self._chunk_queue.empty() or self._is_chunk_worker_active:
                self.widget.transition_to(self.State.PROCESSING)
            else:
                self.widget.transition_to(self.State.IDLE)
            return

        # Batch Mode stop
        wav_bytes = self.audio_recorder.stop_recording()

        if wav_bytes is None:
            # Too short or error — go back to idle
            self.widget.transition_to(self.State.IDLE)
            return

        # Transition to processing state
        self.widget.transition_to(self.State.PROCESSING)

        # Process audio on background thread
        self._process_audio_async(wav_bytes)

    def _start_queue_worker(self):
        """Background thread worker for processing streaming chunks sequentially."""
        def _worker():
            while True:
                item = self._chunk_queue.get()
                if item is None:
                    break
                self._is_chunk_worker_active = True
                wav_bytes, chunk_dur = item
                try:
                    self.signals.chunk_processing_started.emit()
                    text = self.groq_client.process_audio(wav_bytes, audio_duration=chunk_dur)
                    if text:
                        self.signals.chunk_transcription_done.emit(text)
                    else:
                        self.signals.chunk_transcription_done.emit("")
                except Exception as e:
                    print(f"\n[STREAM CHUNK ERROR] {e}")
                    self.signals.chunk_transcription_done.emit("")
                finally:
                    self._chunk_queue.task_done()
                    self._is_chunk_worker_active = False

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _on_chunk_processing_started(self):
        """Main thread slot: enable widget glowing border if streaming, or morph to PROCESSING if stopped."""
        if self._is_streaming:
            self.widget.set_chunk_processing(True)
        else:
            self.widget.set_chunk_processing(False)
            self.widget.transition_to(self.State.PROCESSING)

    def _on_chunk_transcription_done(self, text: str):
        """Main thread slot: paste text, shut off glowing border, and return to IDLE when done."""
        if text:
            try:
                print(f"\n[STREAM CHUNK TRANSCRIPTION] {text}")
            except UnicodeEncodeError:
                safe_text = text.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding)
                print(f"\n[STREAM CHUNK TRANSCRIPTION] {safe_text}")

            def _paste_worker():
                time.sleep(0.05)
                self._copy_and_paste(text)

            threading.Thread(target=_paste_worker, daemon=True).start()

        self.widget.set_chunk_processing(False)

        # Main thread check: if streaming stopped and queue is empty, transition to IDLE
        if not self._is_streaming and self._chunk_queue.empty() and not self._is_chunk_worker_active:
            self.widget.transition_to(self.State.IDLE)

    def _on_silence_detected(self):
        """Called by silence detector from audio thread."""
        self.signals.stop.emit()

    # ──────────────────────────────────────────
    # API Processing
    # ──────────────────────────────────────────

    def _process_audio_async(self, wav_bytes: bytes):
        """Process audio on a background thread."""

        audio_duration = self.audio_recorder.last_audio_duration

        def _worker():
            try:
                text = self.groq_client.process_audio(wav_bytes, audio_duration=audio_duration)
                if text:
                    self.signals.transcription_done.emit(text)
                else:
                    # Groq returned None — surface the actual error reason
                    err = self.groq_client.get_last_error() or "No speech detected or API returned empty result"
                    self.signals.transcription_error.emit(err)
            except Exception as e:
                self.signals.transcription_error.emit(str(e))

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def _on_transcription_done(self, text: str):
        """Called on main thread when transcription is complete."""
        try:
            print(f"\n[TRANSCRIPTION SUCCESS] {text}")
        except UnicodeEncodeError:
            safe_text = text.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding)
            print(f"\n[TRANSCRIPTION SUCCESS] {safe_text}")

        # Transition widget to IDLE state first
        self.widget.transition_to(self.State.IDLE)

        # Dispatch copy and paste on a background thread so Qt main event loop stays unblocked
        def _paste_worker():
            time.sleep(0.05)
            self._copy_and_paste(text)

        threading.Thread(target=_paste_worker, daemon=True).start()

    def _on_transcription_error(self, error: str):
        """Called on main thread when transcription fails."""
        print(f"\n[TRANSCRIPTION ERROR] {error}")
        self.tray.update_last_error(error)
        self.tray.show_notification("Voice Pill — Error", _short_error(error))
        self.widget.show_error_flash()
        QTimer.singleShot(800, lambda: self.widget.transition_to(self.State.IDLE))

    # ──────────────────────────────────────────
    # Recording Error
    # ──────────────────────────────────────────

    def _on_recording_error(self, error: str):
        """Handle audio recording errors."""
        self._unregister_stop_hotkey()
        self.tray.update_last_error(error)
        self.widget.show_error_flash()
        QTimer.singleShot(800, lambda: self.widget.transition_to(self.State.IDLE))

    # ──────────────────────────────────────────
    # Context Menu
    # ──────────────────────────────────────────

    def _show_context_menu(self, pos):
        """Show the right-click context menu."""
        devices = self.audio_recorder.get_devices()
        current_device = self.audio_recorder.get_selected_device()
        current_preset = self.widget.anim_engine.current_preset

        self.context_menu.build_default_menu(
            devices=devices,
            current_device_id=current_device,
            current_preset=current_preset,
            vpn_mode_enabled=self._vpn_mode_enabled,
            current_scale_mode=self.widget.scale_mode,
            autostart_enabled=is_autostart_enabled(),
        )
        self.context_menu.show_at(pos)

    def _on_menu_action(self, action_id: str):
        """Handle context menu action."""
        if action_id == "refresh":
            self.audio_recorder.refresh_devices()
            self.tray.show_notification("Voice Pill", "Audio devices refreshed.")

        elif action_id == "reset_position":
            self._on_reset_position()

        elif action_id == "start_recording":
            self._on_activate()

        elif action_id == "clear_context":
            self.groq_client.clear_context()
            self.tray.show_notification("Voice Pill", "Context memory cleared.")

        elif action_id == "test_connection":
            self._test_connection_async()

        elif action_id == "quit":
            self._quit()

    def _test_connection_async(self):
        """Quickly test if Groq API is reachable and report via tray notification."""
        self.tray.show_notification("Voice Pill", "Testing connection to Groq…")

        def _worker():
            try:
                import requests as _req
                from config import GROQ_API_KEY, GROQ_BASE_URL, VPN_SKIP_SSL_VERIFY, PROXIES
                verify = not (self._vpn_mode_enabled and VPN_SKIP_SSL_VERIFY)
                resp = _req.get(
                    f"{GROQ_BASE_URL}/models",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    timeout=(5, 10),
                    verify=verify,
                    proxies=PROXIES,
                )
                if resp.status_code == 200:
                    return "✓ Groq is reachable — connection OK"
                elif resp.status_code == 401:
                    return "✗ 401 Unauthorized — check your API key in .env"
                elif resp.status_code == 403:
                    msg = resp.json().get("error", {}).get("message", "Forbidden")
                    if "network" in msg.lower():
                        return ("✗ 403 Forbidden — Groq is blocking this network.\n"
                                "Fix: use VPN split-tunnel to exclude api.groq.com from VPN routing.")
                    return f"✗ 403 Forbidden — {msg}"
                else:
                    return f"✗ HTTP {resp.status_code} — {resp.text[:80]}"
            except Exception as e:
                err = str(e)
                if "timed out" in err.lower():
                    return "✗ Timed out — Groq unreachable on this network"
                if "ssl" in err.lower() or "certificate" in err.lower():
                    return "✗ SSL error — VPN is intercepting HTTPS"
                return f"✗ Error: {err[:80]}"

        def _on_done(result: str):
            self.tray.show_notification("Voice Pill — Connection Test", result)

        def _run():
            result = _worker()
            # Marshal back to main thread via a single-shot timer
            QTimer.singleShot(0, lambda: _on_done(result))

        threading.Thread(target=_run, daemon=True).start()

    def _on_device_selected(self, device_id):
        """Handle audio device selection from context menu."""
        if device_id == "auto":
            self.audio_recorder.select_device(None)
            self.tray.show_notification("Voice Pill", "Input device: Auto-Switch")
        else:
            self.audio_recorder.select_device(device_id)
            device_name = self.audio_recorder.get_selected_device_name()
            self.tray.show_notification("Voice Pill", f"Input device: {device_name}")

    def _on_preset_selected(self, preset: str):
        """Handle animation preset selection."""
        self.widget.set_animation_preset(preset)

    def _on_pill_scale_selected(self, mode: str, scale_factor: float):
        """Handle pill size scale selection."""
        self.widget.set_pill_scale(mode, scale_factor)
        display_str = f"Pill Size: {mode.capitalize()}" if mode != "auto" else "Pill Size: Auto (Display-based)"
        self.tray.show_notification("Voice Pill", display_str)

    def _on_autostart_toggled(self, enabled: bool):
        """Handle Start with Windows toggle."""
        if enabled:
            success = enable_autostart()
            if success:
                self.tray.show_notification("Voice Pill", "Auto-start enabled — will run when Windows boots")
            else:
                self.tray.show_notification("Voice Pill — Warning", "Failed to create Windows startup shortcut")
        else:
            success = disable_autostart()
            if success:
                self.tray.show_notification("Voice Pill", "Auto-start disabled")

    def _on_vpn_mode_toggled(self, enabled: bool):
        """Handle VPN Mode toggle from the context menu."""
        self._vpn_mode_enabled = enabled
        self.groq_client.set_vpn_mode(enabled)
        save_vpn_mode(enabled)
        status = "ON — 8 kHz audio, extended timeout" if enabled else "OFF — standard mode"
        self.tray.show_notification("Voice Pill", f"VPN Mode: {status}")


    # ──────────────────────────────────────────
    # Tray Setup
    # ──────────────────────────────────────────

    def _setup_tray(self):
        """Configure system tray icon and callbacks."""
        self.tray.set_callbacks(
            on_show=self._on_tray_show,
            on_reset_position=self._on_reset_position,
            on_start_recording=self._on_activate,
            on_quit=self._quit,
        )
        self.tray.show()

    def _on_tray_show(self):
        """Show the widget (from tray menu or double-click)."""
        self.widget.show()
        self.widget.raise_()
        self.widget.activateWindow()

    # ──────────────────────────────────────────
    # Startup Validation
    # ──────────────────────────────────────────

    def _validate_startup(self):
        """Run startup checks and show warnings."""
        warnings = validate_config()

        for warning in warnings:
            print(f"[Warning] {warning}")

        if not self.groq_client.validate_api_key():
            self.tray.show_notification(
                "Voice Pill — Warning",
                "API key is missing or invalid. Add GROQ_API_KEY to .env file.",
            )

        devices = self.audio_recorder.get_devices()
        if not devices:
            self.tray.show_notification(
                "Voice Pill — Warning",
                "No audio input devices found. Connect a microphone.",
            )

    # ──────────────────────────────────────────
    # Shutdown
    # ──────────────────────────────────────────

    def _quit(self):
        """Graceful shutdown."""
        self._unregister_stop_hotkey()
        self.hotkey_manager.unregister_all()

        if self.audio_recorder.is_recording:
            self.audio_recorder.stop_recording()

        self.audio_recorder.cleanup()
        self.groq_client.cleanup()
        self.tray.cleanup()

        self.app.quit()

    # ──────────────────────────────────────────
    # Run
    # ──────────────────────────────────────────

    def run(self) -> int:
        """Start the application. Returns exit code."""
        self.widget.show()
        return self.app.exec()


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

def main():
    """Application entry point."""

    # Check CLI arguments for IPC trigger flags
    cli_action = None
    for arg in sys.argv[1:]:
        a = arg.lower().lstrip("-")
        if a in ("activate", "toggle", "stream", "stream_activate", "stop", "quit"):
            cli_action = "stream_activate" if a == "stream" else a
            break

    # If explicit CLI sub-command provided (e.g. --activate, --stop) and primary instance is running, send command and exit
    if cli_action and cli_action != "quit" and send_ipc_command(cli_action):
        sys.exit(0)

    # Standard launch: terminate any previous running Voice Pill process to prevent duplicate instances
    if send_ipc_command("quit"):
        print("[Voice Pill] Terminating existing Voice Pill background process...")
        time.sleep(0.35)

    # Create and run primary instance app
    voice_pill = VoicePillApp()

    # If started with a CLI action, trigger it after app starts
    if cli_action:
        if cli_action in ("activate", "toggle"):
            QTimer.singleShot(200, voice_pill._on_activate)
        elif cli_action == "stream_activate":
            QTimer.singleShot(200, voice_pill._on_stream_activate)

    try:
        exit_code = voice_pill.run()
    except KeyboardInterrupt:
        voice_pill._quit()
        exit_code = 0
    except Exception as e:
        import traceback
        with open("crash.log", "a", encoding="utf-8") as f:
            f.write("\n--- FATAL CRASH ---\n")
            traceback.print_exc(file=f)
        voice_pill.hotkey_manager.unregister_all()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
