"""
Voice Pill — Windows Auto-Start Manager
=========================================
Manages adding/removing Voice Pill from the Windows Startup folder
so it runs automatically whenever the user logs in or turns on their laptop.
"""

import sys
import os
import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STARTUP_SHORTCUT_NAME = "VoicePill.lnk"


def get_startup_shortcut_path() -> Path | None:
    """Get the full path to the VoicePill shortcut in the Windows Startup folder."""
    appdata = os.getenv("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / _STARTUP_SHORTCUT_NAME


def is_autostart_enabled() -> bool:
    """Check if Voice Pill is currently configured to start automatically on boot/login."""
    if sys.platform == "win32":
        shortcut = get_startup_shortcut_path()
        return shortcut.exists() if shortcut else False
    elif sys.platform == "linux":
        autostart_file = Path.home() / ".config" / "autostart" / "voicepill.desktop"
        return autostart_file.exists()
    return False


def create_desktop_shortcuts() -> None:
    """Create double-clickable .desktop launcher shortcuts on Linux Desktop, App Menu, and project folder."""
    if sys.platform != "linux":
        return

    try:
        script_path = (_PROJECT_ROOT / "start.sh").resolve()
        content = (
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=Voice Pill\n"
            "Comment=Voice Pill Speech-to-Text Dictation Service\n"
            f'Exec=bash "{script_path}"\n'
            f"Path={_PROJECT_ROOT}\n"
            "Icon=audio-input-microphone\n"
            "Terminal=false\n"
            "Categories=Utility;Audio;\n"
            "StartupNotify=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )

        # 1. GNOME Applications Menu (~/.local/share/applications/voicepill.desktop)
        app_dir = Path.home() / ".local" / "share" / "applications"
        app_dir.mkdir(parents=True, exist_ok=True)
        app_desktop = app_dir / "voicepill.desktop"
        app_desktop.write_text(content, encoding="utf-8")
        os.chmod(app_desktop, 0o755)

        # 2. User Desktop Folder (~/Desktop/VoicePill.desktop)
        desktop_folder = Path.home() / "Desktop"
        if desktop_folder.exists():
            user_desktop = desktop_folder / "VoicePill.desktop"
            user_desktop.write_text(content, encoding="utf-8")
            os.chmod(user_desktop, 0o755)
            subprocess.run(["gio", "set", str(user_desktop), "metadata::trusted", "true"], check=False)

        # 3. Project Directory (/home/piyush/project/voice_pill/VoicePill.desktop)
        proj_desktop = _PROJECT_ROOT / "VoicePill.desktop"
        proj_desktop.write_text(content, encoding="utf-8")
        os.chmod(proj_desktop, 0o755)
    except Exception as e:
        print(f"[Autostart Error] Failed to create desktop shortcuts: {e}")


def enable_autostart() -> bool:
    """Create autostart entry for Windows (Startup folder) or Linux (~/.config/autostart)."""
    if sys.platform == "win32":
        shortcut_path = get_startup_shortcut_path()
        if not shortcut_path:
            return False
        target_script = _PROJECT_ROOT / "start_silent.vbs"
        if not target_script.exists():
            target_script = _PROJECT_ROOT / "start.bat"
        ps_cmd = (
            f"$WshShell = New-Object -comObject WScript.Shell; "
            f"$Shortcut = $WshShell.CreateShortcut('{shortcut_path}'); "
            f"$Shortcut.TargetPath = '{target_script}'; "
            f"$Shortcut.WorkingDirectory = '{_PROJECT_ROOT}'; "
            f"$Shortcut.WindowStyle = 7; "
            f"$Shortcut.Save()"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            return shortcut_path.exists()
        except Exception as e:
            print(f"[Autostart Error] Failed to create Windows shortcut: {e}")
            return False

    elif sys.platform == "linux":
        try:
            create_desktop_shortcuts()
            autostart_dir = Path.home() / ".config" / "autostart"
            autostart_dir.mkdir(parents=True, exist_ok=True)
            desktop_file = autostart_dir / "voicepill.desktop"
            script_path = (_PROJECT_ROOT / "start.sh").resolve()
            content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Voice Pill\n"
                "Comment=Voice Pill Speech-to-Text Dictation Service\n"
                f'Exec=bash "{script_path}"\n'
                "Terminal=false\n"
                "Categories=Utility;Audio;\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
            desktop_file.write_text(content, encoding="utf-8")
            return desktop_file.exists()
        except Exception as e:
            print(f"[Autostart Error] Failed to create Linux desktop entry: {e}")
            return False

    return False


def disable_autostart() -> bool:
    """Remove the Voice Pill autostart entry on Windows or Linux."""
    if sys.platform == "win32":
        shortcut_path = get_startup_shortcut_path()
        if shortcut_path and shortcut_path.exists():
            try:
                shortcut_path.unlink()
                return True
            except OSError as e:
                print(f"[Autostart Error] Failed to delete Windows shortcut: {e}")
                return False
    elif sys.platform == "linux":
        desktop_file = Path.home() / ".config" / "autostart" / "voicepill.desktop"
        if desktop_file.exists():
            try:
                desktop_file.unlink()
                return True
            except OSError as e:
                print(f"[Autostart Error] Failed to delete Linux desktop entry: {e}")
                return False
    return True
