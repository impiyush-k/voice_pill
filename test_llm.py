import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, r'e:\project\june\real_things\voice_pill')

from groq_client import GroqClient

client = GroqClient()

tests = [
    "is he from the iit felhi is that true",
    "okay , it is not producing everything."
]

for t in tests:
    print(f"INPUT: {t}")
    result = client.format_text(t)
    print(f"OUTPUT: {result}\n")
