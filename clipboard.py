"""
Voice Pill — Clipboard & Paste Utility
========================================
Copies transcribed text to the system clipboard and simulates
Ctrl+V to paste it into whatever application has focus.
"""

import time
import pyperclip
import keyboard


def copy_and_paste(text: str) -> bool:
    """
    Copy text to clipboard and simulate Ctrl+V to paste.

    Flow:
        1. Copy text to system clipboard via pyperclip
        2. 50ms delay — ensures clipboard is updated
        3. Simulate Ctrl+V keystroke via keyboard library
        4. 50ms delay — ensures paste is processed

    Args:
        text: The text to paste.

    Returns:
        True if successful, False on error.
    """
    if not text or not text.strip():
        return False

    try:
        # Step 1: Copy to clipboard
        pyperclip.copy(text)

        # Step 2: Brief pause for clipboard sync
        time.sleep(0.02)

        # Step 3: Simulate Ctrl+V
        keyboard.send("ctrl+v")

        # Step 4: Brief pause to let the target app process the paste
        time.sleep(0.02)

        return True

    except Exception:
        # Non-critical — text is still on clipboard even if Ctrl+V fails
        # User can manually paste
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
