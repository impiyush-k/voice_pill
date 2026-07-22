import keyboard
import time

def test():
    print("TEST!")

keyboard.add_hotkey("ctrl+space", test, suppress=False)
print("Listening...")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
