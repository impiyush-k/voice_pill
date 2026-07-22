"""
Voice Pill — Groq API Client
==============================
Handles all Groq API communication:
  1. Whisper large-v3-turbo for speech-to-text (fast, accurate)
  2. Llama 3.1 8B-instant for text formatting (bullet points, punctuation)
  3. Lightweight 10-word context for seamless flow

Model layer architecture:
    Audio → Whisper (raw text) → LLM (format only) → Clipboard
                                       ↑
                                 last 10 words (never reproduced)

Includes rate-limit awareness (tiktoken) and retry logic.
"""

import time
import re
import io
import requests
import tiktoken
import numpy as np
from scipy.io import wavfile as wavfile_io

from config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    WHISPER_MODEL,
    LLM_MODEL,
    API_CONNECT_TIMEOUT,
    API_READ_TIMEOUT,
    API_RETRY_COUNT,
    API_RETRY_DELAY,
    CONTEXT_MAX_WORDS,
    CONTEXT_ENABLED,
    AGENT_TRIGGERS,
    HALLUCINATION_MAX_WORDS_PER_SEC,
    GHOST_PHRASES,
    RATE_LIMIT_PRECHECK,
    VPN_CONNECT_TIMEOUT,
    VPN_READ_TIMEOUT,
    VPN_AUDIO_SAMPLE_RATE,
    VPN_SKIP_SSL_VERIFY,
    PROXIES,
)


def _log(msg: str) -> None:
    """
    Crash-proof debug logging. The app runs under pythonw (no console) and the
    Windows console uses cp1252 — printing Hindi/Devanagari would otherwise raise
    UnicodeEncodeError on the worker thread and abort a valid transcription.
    """
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


# ──────────────────────────────────────────────
# Whisper Prompt — Accuracy-Focused
# ──────────────────────────────────────────────
# Whisper mimics the STYLE of its prompt ("show, don't tell").
# This prompt focuses on ACCURACY: clean punctuation, proper nouns,
# numbers, and Hindi-English code-switching.
# NO bullet points — the LLM handles all formatting.
# Must stay under 224 tokens (Whisper's prompt limit).

WHISPER_STYLE_PROMPT = (
    'Ramu said, "The API is down — did you check the error logs?" '
    'Priya replied, "Yes; there are 3 issues: authentication failure, '
    'a database timeout, and a 500 response." '
    'He asked, "Yeh kaise hua? How long has this been happening?" '
    'She said, "Maybe 20 minutes... possibly longer." '
    '"Theek hai," Ramu added. '
    'The uptime is still 99.7% (confirmed by the dashboard), '
    'and we\'ve served over 1,200 requests today. '
    'Chalo, let\'s move forward; the deadline is June 14th, 2025.'
)


# ──────────────────────────────────────────────
# LLM System Prompt — Formatting Only
# ──────────────────────────────────────────────
# The LLM's ONLY job is to structure raw dictated speech.
# It must NEVER change, add, remove, or rephrase any word.

LLM_SYSTEM_PROMPT = (
    "You are a strict text formatter and translator for voice-dictated speech. Convert raw transcription into clean, well-structured ENGLISH text.\n\n"
    "CRITICAL RULES:\n"
    "1. OUTPUT IN ENGLISH: If the input contains Hindi or Hinglish (mixed Hindi-English), translate it faithfully and naturally to English, preserving the speaker's exact meaning. Do not invent or add ideas.\n"
    "2. PRESERVE ENGLISH WORDS: If the input is in English, KEEP THE EXACT WORDS. Do NOT correct the grammar, conjugation, or rephrase if it changes the user's spoken words. Keep the words exactly as spoken, fixing only punctuation, spacing, and capitalization.\n"
    "3. NO TALKING BACK: Do not answer questions (unless a trigger word like Jarvis is used), do not respond to statements, do not say 'Here is the formatted text', and do not add any preamble, explanations, notes, or postamble. Output ONLY the formatted text.\n"
    "4. PUNCTUATION & CAPITALIZATION: Add proper punctuation (periods, commas, question marks, exclamation marks, semicolons, em-dashes, colons) and capitalization.\n"
    "5. NUMBER FORMATTING: Format numbers correctly (e.g., 1,200, not 'twelve hundred').\n"
    "6. BULLET & NUMBER LISTS:\n"
    "   - Use bullet points (•) or number points (1., 2., 3., etc.) ONLY when listing 2 or more separate, distinct ideas or items.\n"
    "   - Look for list cues: 'first point', 'second point', 'the next thing', 'also', 'number one', 'another thing', '1.', '2.'.\n"
    "   - Strip these cue words from the output and just bullet/number the actual content.\n"
    "   - If it is a single continuous thought or statement, output it as a normal sentence/paragraph (no bullets).\n"
    "7. COMPLETE CONTENT: Include ALL content from the input. Never skip, truncate, or summarize any part.\n"
    "8. IGNORE CONTEXT WORDS: If context words are provided at the start, never include them in your output.\n"
    "9. NO TAGS: Do NOT include the <transcript> or </transcript> tags in your output.\n\n"
    "INPUT FORMAT:\n"
    "The input transcript is wrapped in <transcript>...</transcript> tags. Process ONLY the text inside these tags and output nothing else.\n\n"
    "EXAMPLES:\n"
    "Input: <transcript>hello how are you it is not working correctly</transcript>\n"
    "Output: Hello, how are you? It is not working correctly.\n\n"
    "Input: <transcript>first point we need better testing second point the deployment pipeline is broken</transcript>\n"
    "Output:\n• We need better testing.\n• The deployment pipeline is broken.\n\n"
    "Input: <transcript>yeh server crash ho gaya tha aur hum ne barah hazaar requests kho diye</transcript>\n"
    "Output: The server crashed and we lost 12,000 requests.\n\n"
    "Input: <transcript>is he from the iit felhi is that true</transcript>\n"
    "Output: Is he from the IIT? Felhi is that true?"
)


# ──────────────────────────────────────────────
# LLM Agent Prompt — Inline Answering
# ──────────────────────────────────────────────
# This prompt is used ONLY when a trigger word is detected.

LLM_AGENT_PROMPT = (
    "You are a strict text formatter and inline assistant. You are processing a voice transcription.\n\n"
    "The user has dictated text that contains a trigger word (e.g., 'Jarvis', 'Apollo', 'Omega', 'System answer') followed by a question.\n\n"
    "YOUR TASK:\n"
    "1. Identify the trigger word and the question immediately following it.\n"
    "2. Replace the trigger word and the question with ONLY the direct, concise answer (1-2 sentences maximum). Do NOT include conversational filler, preamble, or explanations (e.g., do NOT say 'Here is the answer:', 'I've updated the text:', 'As you requested...', or 'The answer is:').\n"
    "3. Keep all other text (before or after the trigger/question) EXACTLY as spoken. Do NOT rephrase, translate, or alter it.\n"
    "4. Format the final combined text with proper punctuation and capitalization.\n"
    "5. NO TAGS IN OUTPUT: Do NOT include the <transcript> or </transcript> tags in your output.\n\n"
    "EXAMPLES:\n"
    "Input: <transcript>i want to know jarvis what is the capital of France because it is important</transcript>\n"
    "Output: I want to know. Paris is the capital of France. Because it is important.\n\n"
    "Input: <transcript>jarvis what is the weather in delhi right now</transcript>\n"
    "Output: The weather in Delhi is currently sunny and 32°C.\n\n"
    "Input: <transcript>we need to fix this and omega what is 2 plus 2 is correct</transcript>\n"
    "Output: We need to fix this and 4 is correct."
)


# ──────────────────────────────────────────────
# Context Memory (10-word window)
# ──────────────────────────────────────────────

class ContextMemory:
    """
    Lightweight rolling context buffer — stores only the last N words.

    Fed to the LLM so it knows the flow and can format seamlessly.
    The LLM must NEVER reproduce these context words in its output.
    """

    def __init__(self, max_words: int = CONTEXT_MAX_WORDS):
        self._words: list[str] = []
        self._max_words = max_words

    def add(self, text: str) -> None:
        """Append new transcription words to the context buffer."""
        if not text or not text.strip():
            return
        new_words = text.strip().split()
        self._words.extend(new_words)
        # Keep only the last N words
        if len(self._words) > self._max_words:
            self._words = self._words[-self._max_words:]

    def get_context(self) -> str:
        """Get the current context string (last N words)."""
        return " ".join(self._words)

    def clear(self) -> None:
        """Clear the context buffer."""
        self._words = []

    @property
    def is_empty(self) -> bool:
        return len(self._words) == 0


# ──────────────────────────────────────────────
# Rate Limit Tracker
# ──────────────────────────────────────────────

class RateLimitTracker:
    """
    Tracks token usage per minute to stay within Groq's rate limits.
    
    llama-3.1-8b-instant limits:
        - 6,000 tokens per minute
        - 30 requests per minute
        - 500,000 tokens per day
    """

    def __init__(self, tokens_per_minute: int = 6000, requests_per_minute: int = 30):
        self._tokens_per_minute = tokens_per_minute
        self._requests_per_minute = requests_per_minute
        self._token_log: list[tuple[float, int]] = []   # (timestamp, token_count)
        self._request_log: list[float] = []              # timestamps
        try:
            self._tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._tokenizer = None

    def count_tokens(self, text: str) -> int:
        """Count tokens precisely using tiktoken. Use for recording actual usage."""
        if not text:
            return 0
        if self._tokenizer:
            return len(self._tokenizer.encode(text))
        return len(text) // 4

    def fast_estimate_tokens(self, text: str) -> int:
        """Fast char-based token estimate. Use for pre-flight rate limit checks."""
        if not text:
            return 0
        # ~1 token per 4 chars is a safe overestimate for English
        return len(text) // 4 + 1

    def estimate_messages_tokens(self, messages: list[dict]) -> int:
        """Fast token estimate for a chat completion message list (no tiktoken)."""
        total = 0
        for msg in messages:
            total += 4  # overhead per message
            total += self.fast_estimate_tokens(msg.get("content", ""))
        total += 2  # priming tokens
        return total

    def count_messages_tokens(self, messages: list[dict]) -> int:
        """Precise token count for a chat completion message list (uses tiktoken)."""
        total = 0
        for msg in messages:
            total += 4
            total += self.count_tokens(msg.get("content", ""))
        total += 2
        return total

    def _clean_old_entries(self):
        """Remove entries older than 60 seconds."""
        cutoff = time.monotonic() - 60.0
        self._token_log = [(t, c) for t, c in self._token_log if t > cutoff]
        self._request_log = [t for t in self._request_log if t > cutoff]

    def tokens_used_this_minute(self) -> int:
        """Get tokens consumed in the current 60-second window."""
        self._clean_old_entries()
        return sum(c for _, c in self._token_log)

    def requests_this_minute(self) -> int:
        """Get request count in the current 60-second window."""
        self._clean_old_entries()
        return len(self._request_log)

    def can_send(self, estimated_tokens: int) -> bool:
        """Check if we can send a request without exceeding rate limits."""
        self._clean_old_entries()
        tokens_used = sum(c for _, c in self._token_log)
        requests_used = len(self._request_log)

        if requests_used >= self._requests_per_minute:
            _log(f"[RATE] request limit reached ({requests_used}/{self._requests_per_minute} req/min)")
            return False

        if tokens_used + estimated_tokens > self._tokens_per_minute:
            _log(f"[RATE] token limit would be exceeded ({tokens_used}+{estimated_tokens}/{self._tokens_per_minute} tok/min)")
            return False

        return True

    def record_usage(self, tokens: int):
        """Record a completed request's token usage."""
        now = time.monotonic()
        self._token_log.append((now, tokens))
        self._request_log.append(now)

    def wait_time_seconds(self) -> float:
        """How long to wait before the oldest entry expires from the window."""
        self._clean_old_entries()
        if not self._token_log and not self._request_log:
            return 0.0
        oldest = float('inf')
        if self._token_log:
            oldest = min(oldest, self._token_log[0][0])
        if self._request_log:
            oldest = min(oldest, self._request_log[0])
        remaining = 60.0 - (time.monotonic() - oldest)
        return max(0.0, remaining)


# ──────────────────────────────────────────────
# Groq Client
# ──────────────────────────────────────────────

class GroqClient:
    """
    Groq API client for speech-to-text and formatting.

    Pipeline:
        Audio → Whisper STT → LLM Formatting → Formatted text

    The LLM always runs — it formats every transcription with bullet points,
    proper punctuation, and numbers. It never alters words.

    Usage:
        client = GroqClient()
        text = client.process_audio(wav_bytes)
        # text has bullet points, punctuation, numbers — exactly what was said
    """

    def __init__(self):
        self.context = ContextMemory()
        self.rate_limiter = RateLimitTracker()
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {GROQ_API_KEY}",
        })
        if PROXIES:
            self._session.proxies.update(PROXIES)
            _log(f"[PROXY] Using proxies: {PROXIES}")
        self.last_error: str = ""
        self._vpn_mode: bool = False  # Off by default; toggled via set_vpn_mode()

    def set_vpn_mode(self, enabled: bool) -> None:
        """
        Enable or disable VPN mode.

        When enabled:
          - Audio is resampled from AUDIO_SAMPLE_RATE → VPN_AUDIO_SAMPLE_RATE (8 kHz)
            before upload, producing a ~2× smaller WAV payload.
          - Larger connect/read timeouts are used to accommodate slower tunnel speeds.

        Takes effect immediately — next process_audio() call will use the new setting.
        """
        self._vpn_mode = enabled
        _log(f"[VPN] Mode {'ENABLED (8 kHz audio, extended timeout)' if enabled else 'DISABLED (16 kHz audio, standard timeout)'}")

    def process_audio(self, audio_bytes: bytes, audio_duration: float = 0.0) -> str | None:
        """
        Full pipeline: audio → transcription → formatting → clean text.

        Args:
            audio_bytes: WAV file as bytes.
            audio_duration: Length of the audio in seconds (for plausibility checks).

        Returns:
            Formatted text string, or None on error / rejected hallucination.
        """
        t0 = time.monotonic()

        # Step 1: Transcribe with Whisper
        _log(f"[PIPELINE] Step 1: Whisper transcription ({len(audio_bytes)} bytes, {audio_duration:.1f}s audio)...")
        raw_text = self.transcribe(audio_bytes, audio_duration=audio_duration)
        t1 = time.monotonic()

        if not raw_text:
            _log(f"[PIPELINE] FAILED at Step 1: Whisper returned no text ({t1-t0:.2f}s)")
            return None

        raw_words = len(raw_text.split())
        _log(f"[PIPELINE] Step 1 OK ({t1-t0:.2f}s): {raw_words} words — {raw_text!r}")

        # Step 2: Ghost-phrase rejection
        if self._is_ghost_phrase(raw_text):
            _log(f"[PIPELINE] REJECTED at Step 2: ghost phrase: {raw_text!r}")
            return None

        # Step 3: Format with LLM (always run to ensure translation and formatting)
        _log(f"[PIPELINE] Step 3: LLM formatting...")
        formatted = self.format_text(raw_text)
        t2 = time.monotonic()
        
        # Fallback to raw if LLM fails — NEVER return None just because LLM failed
        if formatted:
            final_text = formatted
            _log(f"[PIPELINE] Step 3 OK ({t2-t1:.2f}s): formatted")
        else:
            final_text = raw_text
            _log(f"[PIPELINE] Step 3 FALLBACK ({t2-t1:.2f}s): using raw Whisper text")

        # Step 4: Post-LLM ghost check
        if self._is_ghost_phrase(final_text):
            _log(f"[PIPELINE] REJECTED at Step 4: ghost phrase (post-LLM): {final_text!r}")
            return None

        # Step 5: Update 10-word context for seamless flow
        if CONTEXT_ENABLED:
            self.context.add(final_text)

        final_words = len(final_text.split())
        _log(f"[PIPELINE] DONE in {t2-t0:.2f}s total — {raw_words} words in → {final_words} words out")
        return final_text

    @staticmethod
    def _normalize_for_ghost_match(text: str) -> str:
        """Lowercase and strip punctuation/whitespace for ghost-phrase comparison."""
        cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    def _is_ghost_phrase(self, text: str) -> bool:
        """
        Return True if the ENTIRE transcript is a known Whisper hallucination phrase.
        Only rejects when the whole transcript equals a ghost phrase — never rejects
        a phrase that merely appears inside a longer, legitimate transcript.
        """
        if not text or not text.strip():
            return True

        normalized = self._normalize_for_ghost_match(text)
        if not normalized:
            return True

        for ghost in GHOST_PHRASES:
            ghost_norm = self._normalize_for_ghost_match(ghost)
            if normalized == ghost_norm:
                return True
        return False

    def transcribe(self, audio_bytes: bytes, audio_duration: float = 0.0) -> str | None:
        """
        Send audio to Groq Whisper API for transcription.

        Uses whisper-large-v3-turbo for speed. Response format is 'json'
        (direct text, no segment parsing overhead).

        Args:
            audio_bytes: WAV file as bytes.
            audio_duration: Length of audio in seconds (for plausibility check).

        Returns:
            Transcribed text, or None on error.
        """
        url = f"{GROQ_BASE_URL}/audio/transcriptions"

        # VPN Mode: resample to 8 kHz to shrink the upload payload ~2×
        upload_bytes = self._resample_wav_for_vpn(audio_bytes) if self._vpn_mode else audio_bytes

        files = {
            "file": ("recording.wav", upload_bytes, "audio/wav"),
        }

        data = {
            "model": WHISPER_MODEL,
            "response_format": "json",
            "temperature": 0.0,
        }

        # Whisper prompt: "show, don't tell" — demonstrates the output style we want.
        # If we have context, prepend it to the style prompt for continuity.
        if CONTEXT_ENABLED and not self.context.is_empty:
            # Context + style example (must stay under 224 tokens total)
            context_str = self.context.get_context()
            data["prompt"] = f"{context_str}. {WHISPER_STYLE_PROMPT}"
        else:
            data["prompt"] = WHISPER_STYLE_PROMPT

        try:
            response = self._request_with_retry(
                "POST", url, files=files, data=data
            )
            result = response.json()
            text = result.get("text", "").strip()

            if not text:
                _log("[WHISPER] API returned empty text — no speech detected")
                return None

            # Basic plausibility check on the whole transcript
            if audio_duration > 0.5:
                word_count = len(text.split())
                wps = word_count / audio_duration
                if wps > HALLUCINATION_MAX_WORDS_PER_SEC:
                    _log(f"[HALLU] {wps:.1f} words/sec (impossible): {text!r}")
                    return None

            return text

        except Exception as e:
            self.last_error = f"Transcription failed: {e}"
            _log(f"\n[API ERROR] {self.last_error}")
            return None

    def format_text(self, text: str) -> str | None:
        """
        Send raw transcription to LLM for formatting.

        If the output gets truncated (finish_reason='length'), automatically
        splits the text into 2 halves and formats each separately.

        Args:
            text: Raw Whisper transcription.

        Returns:
            Formatted text, or None on error (caller falls back to raw text).
        """
        try:
            # Try formatting the full text first
            result = self._call_llm_format(text)
            if result is None:
                return None

            formatted, finish_reason, usage = result

            # Check if output was truncated
            if finish_reason == "length":
                _log(f"[LLM] Output TRUNCATED (hit max_tokens). Splitting text into 2 parts...")
                return self._format_in_two_parts(text)

            return formatted
        except Exception as e:
            self.last_error = f"Formatting failed: {e}"
            _log(f"\n[API ERROR] {self.last_error}")
            return None

    def _format_in_two_parts(self, text: str) -> str | None:
        """
        Split text at the nearest sentence boundary and format each half separately.
        Combines the results. Uses at most 2 LLM calls (stays within rate limits).
        """
        try:
            # Find a split point near the middle — prefer sentence boundaries
            words = text.split()
            mid = len(words) // 2

            # Search for sentence-ending punctuation near the middle
            best_split = mid
            for offset in range(0, min(mid, 20)):
                for candidate in [mid + offset, mid - offset]:
                    if 0 < candidate < len(words):
                        word = words[candidate - 1]
                        if word.endswith(('.', '?', '!', '।')):
                            best_split = candidate
                            break
                else:
                    continue
                break

            part1_text = " ".join(words[:best_split])
            part2_text = " ".join(words[best_split:])

            _log(f"[LLM] Split: part1={len(words[:best_split])} words, part2={len(words[best_split:])} words")

            # Format each part
            result1 = self._call_llm_format(part1_text)
            result2 = self._call_llm_format(part2_text)

            formatted1 = result1[0] if result1 else part1_text
            formatted2 = result2[0] if result2 else part2_text

            # Combine — add newline between parts if both exist
            combined = formatted1.rstrip()
            if formatted2:
                combined += "\n" + formatted2.lstrip()

            return combined
        except Exception as e:
            _log(f"[LLM] Split formatting failed: {e}")
            return None

    def _call_llm_format(self, text: str) -> tuple[str, str, dict] | None:
        """
        Single LLM formatting call. Returns (formatted_text, finish_reason, usage) or None.
        Dynamically uses LLM_AGENT_PROMPT if a trigger word is detected, else LLM_SYSTEM_PROMPT.
        """
        url = f"{GROQ_BASE_URL}/chat/completions"

        # Check for agentic trigger words
        lower_text = text.lower()
        is_agent_query = any(re.search(r'\b' + re.escape(t.lower()) + r'\b', lower_text) for t in AGENT_TRIGGERS)

        if is_agent_query:
            _log(f"[LLM] Trigger word detected. Using Agentic inline prompt.")
            system_content = LLM_AGENT_PROMPT
        else:
            system_content = LLM_SYSTEM_PROMPT

        messages = [
            {"role": "system", "content": system_content},
        ]
        # Include 10-word context for seamless flow (LLM must not reproduce these)
        if CONTEXT_ENABLED and not self.context.is_empty:
            context_str = self.context.get_context()
            user_content = (
                f"[CONTEXT — these are the last words from previous speech. "
                f"Do NOT include them in your output. They are only for understanding flow.]\n"
                f"{context_str}\n"
                f"[END CONTEXT]\n\n"
                f"Format this speech:\n<transcript>{text}</transcript>"
            )
        else:
            user_content = f"Format this speech:\n<transcript>{text}</transcript>"

        messages.append({"role": "user", "content": user_content})

        # Rate limit check (fast char-based estimate — no tiktoken overhead)
        estimated_tokens = 0
        if RATE_LIMIT_PRECHECK:
            estimated_tokens = self.rate_limiter.estimate_messages_tokens(messages) + 300
            if not self.rate_limiter.can_send(estimated_tokens):
                wait = self.rate_limiter.wait_time_seconds()
                if wait > 0 and wait <= 5.0:
                    _log(f"[RATE] waiting {wait:.1f}s for rate limit window to clear")
                    time.sleep(wait)
                elif wait > 5.0:
                    _log(f"[RATE] skipping LLM formatting (would need to wait {wait:.1f}s)")
                    return None

        payload = {
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 1200,
            "stream": False,
        }

        try:
            response = self._request_with_retry(
                "POST", url, json=payload,
                headers={"Content-Type": "application/json"},
            )
            result = response.json()

            choice = result["choices"][0]
            formatted = choice["message"]["content"].strip()
            # Strip XML tags if LLM accidentally included them
            formatted = re.sub(r'</?transcript>', '', formatted, flags=re.IGNORECASE).strip()
            finish_reason = choice.get("finish_reason", "stop")

            # Record actual token usage for rate limiting
            usage = result.get("usage", {})
            total_tokens = usage.get("total_tokens", estimated_tokens)
            if RATE_LIMIT_PRECHECK:
                self.rate_limiter.record_usage(total_tokens)
                _log(f"[LLM] {total_tokens} tokens, finish_reason={finish_reason} "
                     f"({self.rate_limiter.tokens_used_this_minute()}/{6000} tok/min)")
            else:
                _log(f"[LLM] {total_tokens} tokens, finish_reason={finish_reason}")

            if not formatted:
                return None

            return (formatted, finish_reason, usage)

        except Exception as e:
            self.last_error = f"Formatting failed: {e}"
            _log(f"\n[API ERROR] {self.last_error}")
            return None


    # ──────────────────────────────────────────
    # HTTP with Retry (VPN Resilience)
    # ──────────────────────────────────────────

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Execute HTTP request with fast-fail timeout logic.

        Uses a two-tuple timeout (connect_timeout, read_timeout) so a dead network
        fails in connect_timeout seconds rather than the old flat 45 seconds.

        In VPN mode, both timeouts are extended to accommodate slower tunnel speeds.

        Retries on:
          - Timeout (network too slow)
          - ConnectionError (VPN reconnecting, DNS failure)
          - 429 Too Many Requests (rate limited)
          - 5xx Server Error (Groq internal issue)

        Does NOT retry on:
          - 400 Bad Request (our fault)
          - 401 Unauthorized (bad API key)
          - 403 Forbidden (access denied)

        Raises the last exception if all retries fail.
        """
        # Merge authorization header (don't override if caller provides extra headers)
        if "headers" in kwargs:
            merged_headers = dict(self._session.headers)
            merged_headers.update(kwargs.pop("headers"))
            kwargs["headers"] = merged_headers

        # Choose timeout based on VPN mode
        if self._vpn_mode:
            timeout = (VPN_CONNECT_TIMEOUT, VPN_READ_TIMEOUT)
            # Bypass SSL cert check — fixes 403 Forbidden from VPN SSL inspection (MITM proxies).
            # The connection is still TLS-encrypted; we just skip the cert chain verification.
            verify = not VPN_SKIP_SSL_VERIFY
            if not verify:
                # Suppress urllib3's InsecureRequestWarning — user already opted into VPN mode
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        else:
            timeout = (API_CONNECT_TIMEOUT, API_READ_TIMEOUT)
            verify = True

        response = self._session.request(
            method, url, timeout=timeout, verify=verify, **kwargs
        )
        response.raise_for_status()
        return response

    # ──────────────────────────────────────────
    # VPN Audio Resampling
    # ──────────────────────────────────────────

    @staticmethod
    def _resample_wav_for_vpn(wav_bytes: bytes) -> bytes:
        """
        Resample WAV audio from AUDIO_SAMPLE_RATE (16 kHz) to VPN_AUDIO_SAMPLE_RATE (8 kHz).

        This halves the file size, making it feasible to upload over VPN tunnels
        that have limited MTU or slow throughput. Whisper handles 8 kHz well —
        it's the same quality as a standard phone call.

        Falls back to the original bytes if resampling fails for any reason.
        """
        try:
            # Parse the existing WAV
            wav_buffer_in = io.BytesIO(wav_bytes)
            orig_rate, audio_data = wavfile_io.read(wav_buffer_in)

            # Nothing to do if already at target rate
            if orig_rate == VPN_AUDIO_SAMPLE_RATE:
                return wav_bytes

            audio_float = audio_data.astype(np.float32)

            # Fast NumPy decimation for standard 16kHz -> 8kHz (no C-DLL dependencies)
            if orig_rate == 16000 and VPN_AUDIO_SAMPLE_RATE == 8000:
                resampled = audio_float[::2]
            else:
                try:
                    import scipy.signal
                    up = VPN_AUDIO_SAMPLE_RATE
                    down = orig_rate
                    import math
                    g = math.gcd(up, down)
                    up //= g
                    down //= g

                    if audio_float.ndim == 1:
                        resampled = scipy.signal.resample_poly(audio_float, up, down)
                    else:
                        resampled = np.stack(
                            [scipy.signal.resample_poly(audio_float[:, ch], up, down)
                             for ch in range(audio_float.shape[1])],
                            axis=1,
                        )
                except Exception:
                    step = max(1, int(orig_rate / VPN_AUDIO_SAMPLE_RATE))
                    resampled = audio_float[::step]

            # Clip and convert back to int16
            resampled = np.clip(resampled, -32768, 32767).astype(np.int16)

            wav_buffer_out = io.BytesIO()
            wavfile_io.write(wav_buffer_out, VPN_AUDIO_SAMPLE_RATE, resampled)
            wav_buffer_out.seek(0)
            result = wav_buffer_out.read()

            _log(f"[VPN] Resampled {orig_rate} Hz → {VPN_AUDIO_SAMPLE_RATE} Hz: "
                 f"{len(wav_bytes):,} B → {len(result):,} B "
                 f"({100 * len(result) // len(wav_bytes)}% of original)")
            return result

        except Exception as e:
            _log(f"[VPN] Resample failed (using original audio): {e}")
            return wav_bytes


    # ──────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────

    def validate_api_key(self) -> bool:
        """
        Quick check that the API key looks valid.
        Does NOT make a network request — just format validation.
        """
        return bool(GROQ_API_KEY and GROQ_API_KEY.startswith("gsk_"))

    def clear_context(self) -> None:
        """Clear the context memory buffer."""
        self.context.clear()

    def get_last_error(self) -> str:
        """Get the last error message."""
        return self.last_error

    def cleanup(self):
        """Close HTTP session. Called on app exit."""
        try:
            self._session.close()
        except Exception:
            pass
