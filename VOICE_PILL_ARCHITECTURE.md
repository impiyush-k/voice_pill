# Voice Pill — Comprehensive System Architecture & Animation Engine Specification

> **Document Purpose**: This reference manual provides an exhaustive technical analysis of the **Voice Pill** desktop application. It breaks down the complete system lifecycle from hotkey activation, audio streaming, real-time amplitude math, widget rendering, state morphing, particle system physics, silence detection, to API transcription, LLM formatting, and clipboard auto-pasting.
>
> *Use this document to understand the codebase logic in depth or feed it to another AI agent to replicate or extend the application.*

---

## 1. System Overview & Core Technology Stack

**Voice Pill** is a minimalist, floating Windows desktop voice assistant written in Python 3 using **PyQt6** for graphics, **sounddevice** & **numpy** for audio capture, **keyboard** for global hotkeys, and **Groq Cloud APIs** (Whisper + Llama 3.1) for instant speech-to-text and formatting.

### Key Technologies
- **UI Framework**: PyQt6 (`QWidget`, `QPainter`, `QPropertyAnimation`, `QParallelAnimationGroup`, `QTimer` with `PreciseTimer`).
- **Audio Capture**: `sounddevice` (WASAPI/MME input streams, 16 kHz 16-bit PCM mono), `scipy.io.wavfile`, `scipy.signal`.
- **Audio Processing**: NumPy (RMS calculation, EMA smoothing, polyphase 8 kHz resampling for VPN mode).
- **Global Hotkeys**: `keyboard` library (Ctrl+Space global trigger, dynamic Space stop hook).
- **AI/LLM Stack**: Groq API via `requests` (`whisper-large-v3-turbo` for speech recognition + `llama-3.1-8b-instant` for structural formatting and inline agent queries).
- **OS Integration**: Windows Named Mutex for single-instance enforcement, System Tray (`QSystemTrayIcon`), `pyperclip` + simulated `Ctrl+V` pasting.

---

## 2. Codebase Structure & Module Map

| File | Primary Responsibility | Key Classes & Functions |
| :--- | :--- | :--- |
| [`main.py`](file:///e:/project/june/real_things/voice_pill/main.py) | Application orchestrator, state machine, thread-safe signal marshaling, hotkeys, tray. | `VoicePillApp`, `_WorkerSignals`, `ensure_single_instance()` |
| [`widget.py`](file:///e:/project/june/real_things/voice_pill/widget.py) | Frameless, transparent floating UI widget. Handles state morphing, mouse events, rendering. | `VoicePillWidget`, `State` (IDLE, READY, RECORDING, PROCESSING) |
| [`animations.py`](file:///e:/project/june/real_things/voice_pill/animations.py) | Animation math engine: bar presets, 3D particle orbital ring system, heartbeat, easing equations. | `AnimationEngine`, `Particle`, `ease_out_cubic`, `ease_out_back` |
| [`recorder.py`](file:///e:/project/june/real_things/voice_pill/recorder.py) | Audio device enumeration, input stream management, real-time RMS amplitude calculation, WAV creation. | `AudioRecorder`, `AudioDevice` |
| [`silence.py`](file:///e:/project/june/real_things/voice_pill/silence.py) | Adaptive silence detection and Voice Activity Detection (VAD) with ambient noise calibration. | `SilenceDetector` |
| [`groq_client.py`](file:///e:/project/june/real_things/voice_pill/groq_client.py) | Whisper STT pipeline, ghost phrase filtering, LLM formatting, agentic query handling, VPN resampling. | `GroqClient`, `ContextMemory`, `RateLimitTracker` |
| [`hotkeys.py`](file:///e:/project/june/real_things/voice_pill/hotkeys.py) | Thread-safe hotkey registration with 3-layer crash-proof cleanup hooks (`atexit`, `signal`, `excepthook`). | `HotkeyManager` |
| [`config.py`](file:///e:/project/june/real_things/voice_pill/config.py) | Central source of truth for all constants, dimensions, colors, threshold tunings, and persistence paths. | Dimensions, RGBA colors, API keys, presets |
| [`clipboard.py`](file:///e:/project/june/real_things/voice_pill/clipboard.py) | Clipboard text copying and keyboard macro simulation (`Ctrl+V`). | `copy_and_paste()`, `copy_only()` |
| [`context_menu.py`](file:///e:/project/june/real_things/voice_pill/context_menu.py)| Dark glassmorphic right-click popup menu with device switcher, presets, VPN mode toggle. | `VoicePillContextMenu`, `MenuItem` |
| [`tray.py`](file:///e:/project/june/real_things/voice_pill/tray.py) | System tray icon manager and native Windows toast notifications. | `SystemTrayManager` |
| [`language.py`](file:///e:/project/june/real_things/voice_pill/language.py) | Devanagari (Hindi) script & Romanized Hinglish detection for smart translation routing. | `contains_hindi()`, `needs_translation()` |

---

## 3. End-to-End System State Machine & Workflow

```
[ IDLE ]  --- (Hover Mouse) --->  [ READY ]
   |                                 |
   +-------- Press Ctrl+Space -------+
   |
   v
[ RECORDING ]  --- (Audio Stream + Amplitude updates UI)
   |
   +-- Press Space / Silence Auto-Stop --> Stop stream & WAV created
   |
   v
[ PROCESSING ] --- (Background Thread: Whisper STT -> LLM Format)
   |
   +-- Success --> Copy to Clipboard + Simulate Ctrl+V --> [ IDLE ]
   +-- Failure --> Show Red Flash Overlay --------------> [ IDLE ]
```

### State Definitions
1. **`IDLE`**: Compact 3D silver metallic dash ($27 \times 6\text{ px}$). Minimal footprint on screen.
2. **`READY`**: Morphed pill capsule ($80 \times 33\text{ px}$) displaying static alternating bars/dots when mouse hovers over the widget.
3. **`RECORDING`**: Morphed pill capsule displaying 9 dynamic vertical bars reacting to voice amplitude in real time.
4. **`PROCESSING`**: Morphed circular widget ($60 \times 60\text{ px}$) displaying an orbiting 3D particle galaxy with a central pulsing heartbeat dot while network requests run in the background.

---

## 4. Deep-Dive: Animation Engine & UI Mechanics

### 4.1 Widget Morphing & Smooth Dimensions
The main widget [`VoicePillWidget`](file:///e:/project/june/real_things/voice_pill/widget.py#L61) inherits from `QWidget` with translucent background, frameless flags, and always-on-top window hints.

When transitioning states, `_morph_to()` runs a `QParallelAnimationGroup` containing two `QPropertyAnimation` instances targeting `pill_width` and `pill_height`:
- Duration: `MORPH_DURATION_MS = 150` ms.
- Easing Curve: `QEasingCurve.Type.OutCubic` ($f(t) = 1 - (1-t)^3$).
- The widget anchors its growth to a **fixed bottom edge**, ensuring it expands upwards gracefully without jumpiness:
  $$\text{cy} = \text{bottom\_y} - \frac{h}{2}$$

### 4.2 The "Dots to Lines" Geometry & Rendering Logic
Inside [`widget.py`](file:///e:/project/june/real_things/voice_pill/widget.py#L462) (`_draw_bars`), 9 visualization bars (`BAR_COUNT = 9`) are drawn.

#### Dimensions & Specifications:
- **Bar Width**: `BAR_WIDTH = 3` px.
- **Bar Gap**: `BAR_GAP = 4` px.
- **Min Height**: `BAR_MIN_HEIGHT = 3` px.
- **Max Height**: `BAR_MAX_HEIGHT = 21` px.
- **Corner Radius**: `BAR_CORNER_RADIUS = 1.5` px.

#### Why Dots Become Lines:
Because `BAR_CORNER_RADIUS` is exactly half of `BAR_WIDTH` ($1.5 = 3 / 2$), when a bar's height equals its width ($3\text{ px} \times 3\text{ px}$), the rounded rectangle renders as a **perfect circular dot**. As audio amplitude increases, the height expands vertically up to $21\text{ px}$, causing the dot to morph seamlessly into a **vertical rounded capsule line**.

### 4.3 Amplitude Pipeline (Audio Callback to Animation Engine)

```
[ Microphone Input ]
        │
        ▼  sounddevice audio callback (audio thread)
[ Raw Audio Chunk (1024 samples @ 16kHz) ]
        │
        ▼  RMS Calculation: rms = sqrt(mean(audio_float^2))
[ Raw RMS ]
        │
        ▼  Normalize: norm = min(1.0, rms / 150.0)
[ Normalized Amplitude (0.0 - 1.0) ]
        │
        ▼  Exponential Moving Average (EMA):
        │  smoothed = smoothed * (1 - 0.15) + norm * 0.15
[ Smoothed Amplitude ]
        │
        ▼  Qt Signal Emission: amplitude_updated.emit(smoothed)
[ Qt Main Thread Queue ]
        │
        ▼  Widget receives update_amplitude(amp)
[ AnimationEngine ] target_amplitude = amp
        │
        ▼  30 FPS Timer Tick: smooth interpolation
        │  current_amplitude += (target_amplitude - current_amplitude) * (0.35 * (60 / FPS))
[ Bar Heights (in Pixels) Computed & Repainted ]
```

### 4.4 Animation Presets & Bar Phase Dynamics
Calculated in [`animations.py`](file:///e:/project/june/real_things/voice_pill/animations.py#L257):

1. **Classic Preset (`_preset_classic`)**:
   - **Idle/Silence**: Alternates every bar: Even bars have height $0$, Odd bars have height $0.5$ (Dot - Line - Dot - Line pattern).
   - **Recording with Voice**: Adjacent bars swap heights out-of-phase using a sine wave:
     $$\text{phase} = \begin{cases} \pi & \text{if } i \text{ is even} \\ 0 & \text{if } i \text{ is odd} \end{cases}$$
     $$\text{raw} = \sin(\text{time} \times 9.5 + \text{phase})$$
     $$\text{factor} = 0.2 + 0.8 \times (0.5 + 0.5 \times \text{raw})$$
     $$\text{height}[i] = \min(1.0, \text{amplitude} \times \text{factor})$$
2. **Wave Preset (`_preset_wave`)**:
   - Sine wave sweeps left to right across the 9 bars: $\sin(\text{time} \times 3.0 + i \times \frac{\pi}{9})$.
3. **Pulse Preset (`_preset_pulse`)**:
   - All 9 bars expand and contract uniformly with a breathing motion.
4. **Processing Preset (`_preset_processing`)**:
   - High-speed ($18\text{ rad/s}$) alternating dot-to-line transformation indicating active computing.

### 4.5 The Processing Phase: 3D Particle Orbital Ring System
When recording stops and processing begins, the widget morphs to a circle (`CIRCLE_DIAMETER = 60` px), and `start_particles()` initializes an orbital system:

1. **Bar Dissolution Effect**: Initial particle spawn points $(x, y)$ are set to the exact locations of the previous 9 bars. Over `PARTICLE_DISSOLVE_MS = 220` ms, cubic easing transitions particles from the linear bar grid outward into their orbital paths.
2. **Dual Concentric Orbital Rings**:
   - **Outer Ring**: 8 particles (`PARTICLE_OUTER_COUNT = 8`), orbit radius $= 0.34 \times \text{Diameter}$, speed $= 1.6\text{ rad/s}$.
   - **Inner Ring**: 5 particles (`PARTICLE_INNER_COUNT = 5`), orbit radius $= 0.19 \times \text{Diameter}$, speed $= 2.26\text{ rad/s}$.
   - *Non-repeating ratio*: The speed ratio $\frac{2.26}{1.6} \approx \sqrt{2} \approx 1.414$ ensures the inner and outer ring patterns **never align or repeat**, producing a hypnotic, organic space-galaxy feel.
3. **3D Tilt Plane & Depth Shading**:
   - The orbital plane is tilted at $\text{ORBIT\_TILT} = 0.45$:
     $$y_{\text{orbit}} = \sin(\text{angle}) \times r \times 0.45$$
   - Depth factor $d = \sin(\text{angle})$ modulates particle radius and opacity to simulate 3D perspective:
     $$\text{radius} = \text{base\_radius} \times (1.0 + 0.45 \times d)$$
4. **Heartbeat Pulse**:
   - A central particle dot pulses at 60 BPM. Its scale follows a $\sin^4$ curve, producing a sharp physiological heartbeat tick followed by a rest:
     $$\text{raw} = \sin(\text{time} \times 2\pi \times \frac{60}{60}), \quad \text{pulse} = \max(0, \text{raw})^4$$
     $$\text{scale} = 1.0 + 0.08 \times \text{pulse}$$

---

## 5. Audio Capture & Silence Detection Engine

### 5.1 Input Device Enumeration & WASAPI Prioritization
In [`recorder.py`](file:///e:/project/june/real_things/voice_pill/recorder.py#L95), `refresh_devices()` queries system microphones via `sounddevice`.
- Filters out loopbacks, output devices, speaker mappers, kernel-streaming artifacts (`@System32\drivers...`).
- Ranks host APIs: **WASAPI** (rank 0, cleanest names & lowest latency) $\rightarrow$ **MME** (rank 1) $\rightarrow$ **DirectSound** (rank 2).
- Performs prefix-aware deduplication to eliminate duplicate MME truncated device entries.

### 5.2 Real-Time Silence Detection & Noise Floor Calibration
In [`silence.py`](file:///e:/project/june/real_things/voice_pill/silence.py):
1. **Calibration Phase (First 500 ms)**: Measures ambient room noise while silent, establishing `_noise_floor` using the median (spike-resistant) of initial samples.
2. **Speech Threshold**: Set to $\max(\text{noise\_floor} \times 2.5, 0.01)$.
3. **Consecutive-Frame VAD Guard**:
   - Needs `VAD_SPEECH_FRAMES = 2` consecutive audio chunks above threshold to confirm speech start.
   - Needs `VAD_SILENCE_FRAMES = 2` consecutive quiet chunks to confirm silence.
4. **Auto-Stop Trigger**: If continuous silence exceeds `SILENCE_DURATION = 4.0` seconds (after speech was detected), `on_silence()` automatically triggers recording stop.

---

## 6. Cloud API Pipeline & LLM Processing Engine

### 6.1 Whisper Speech-to-Text Pipeline
- **Model**: `whisper-large-v3-turbo` via Groq API endpoint `/v1/audio/transcriptions`.
- **Prompt Engineering**: Uses a curated style prompt demonstrating punctuation, numbers, and Hinglish code-switching to enforce clean output formatting without extra decoder overhead.

### 6.2 Multi-Stage Hallucination & Ghost Phrase Rejection
To prevent Whisper from inventing text during silence or background noise:
1. **Words-Per-Second Check**: Rejects transcripts with $> 10.0$ words/sec as impossible hallucinations.
2. **Ghost Phrase Matcher**: [`_is_ghost_phrase()`](file:///e:/project/june/real_things/voice_pill/groq_client.py#L397) normalizes the full text and compares it against known subtitle/YouTube artifacts (e.g., *"thank you for watching"*, *"please subscribe to my channel"*, *"subtitles by the amara.org community"*).

### 6.3 LLM Structural Formatting Engine
- **Model**: `llama-3.1-8b-instant` (speed $\approx 560\text{ tokens/sec}$).
- **Role**: Structures dictated speech into bullet points ($\bullet$), numbered lists, proper capitalization, and punctuation.
- **Strict Constraint**: *Must never change, delete, or add spoken words.*
- **Truncation Resilience**: If LLM output hits `max_tokens` (`finish_reason == "length"`), [`_format_in_two_parts()`](file:///e:/project/june/real_things/voice_pill/groq_client.py#L519) automatically splits the input transcript at a sentence boundary and processes the halves in parallel.

### 6.4 Agentic Inline Triggers
If dictation contains trigger words (`"jarvis"`, `"apollo"`, `"omega"`, `"system answer"`), [`groq_client.py`](file:///e:/project/june/real_things/voice_pill/groq_client.py#L575) dynamically switches the system prompt to `LLM_AGENT_PROMPT`. The LLM computes the direct answer to the question inline and replaces the trigger phrase while keeping all surrounding spoken dictation intact.

### 6.5 VPN Optimization Mode
Toggled via context menu or `vpn_mode.json`:
1. **Audio Resampling**: Polyphase resamples 16 kHz audio down to 8 kHz prior to upload using `scipy.signal.resample_poly`, reducing WAV payload size by **50%**.
2. **Timeout Extension**: Connect timeout extended to 10s, Read timeout extended to 60s.
3. **SSL Bypass**: Skips SSL cert verification (`verify=False`) to bypass corporate VPN MITM SSL inspection while keeping TLS encryption.

---

## 7. Global Hotkeys & OS Integration

### 7.1 Dynamic Hotkey Lifecycle & Anti-Intercept Guard
Implemented in [`hotkeys.py`](file:///e:/project/june/real_things/voice_pill/hotkeys.py):
- **Activation Hotkey**: `Ctrl+Space` is registered permanently on app launch (`suppress=False`).
- **Stop Hotkey**: `Space` is registered **ONLY** when recording starts.
- **CRITICAL SAFETY RULE**: The instant recording ends or fails, `Space` is unregistered immediately (`_unregister_stop_hotkey()`). This guarantees that spacebar presses during typing are never intercepted or suppressed globally.

### 7.2 Thread Safety & PyQt Signal Marshaling
Low-level hooks (`keyboard` thread, `sounddevice` audio thread) fire callbacks off the main thread. To prevent Qt GUI crashes:
- `_WorkerSignals` (`QObject`) marshals events safely:
  - `signals.activate` $\rightarrow$ `_on_activate`
  - `signals.stop` $\rightarrow$ `_on_stop`
  - `signals.transcription_done` $\rightarrow$ `_on_transcription_done`
  - `signals.transcription_error` $\rightarrow$ `_on_transcription_error`

### 7.3 Auto-Paste Sequence
In [`clipboard.py`](file:///e:/project/june/real_things/voice_pill/clipboard.py):
1. `pyperclip.copy(formatted_text)` writes to Windows system clipboard.
2. Sleep 20 ms for clipboard sync.
3. `keyboard.send("ctrl+v")` simulates physical keystrokes into the active target application.
4. Sleep 20 ms to finish paste event.

---

## 8. Specification Guide for Rebuilding in External Agents

When prompting another AI agent to recreate or modify this app, provide the following summary instructions:

```markdown
Build a floating desktop voice assistant using PyQt6 and Python sounddevice.

1. State Machine:
   - IDLE: 27x6px silver dash.
   - READY: 80x33px black pill capsule with static bars.
   - RECORDING: 80x33px pill capsule with 9 animated vertical bars.
   - PROCESSING: 60x60px circle with orbiting particle galaxy.

2. Animation Logic:
   - 9 vertical bars (width 3px, gap 4px, corner radius 1.5px).
   - Min height 3px (perfect circular dots), Max height 21px (lines).
   - Bar height = min_h + amplitude * (max_h - min_h).
   - Alternating phase shift: even bars use phase pi, odd bars phase 0.
   - Processing mode: 2 concentric orbital particle rings tilted 0.45 in 3D, ratio sqrt(2) speed (1.6 & 2.26 rad/s), with 60 BPM sin^4 heartbeat pulse.

3. Audio & Control:
   - sounddevice WASAPI input stream at 16kHz mono 16-bit PCM.
   - Real-time RMS calculation + EMA smoothing (alpha=0.15) emitted to UI via PyQt signal.
   - Global hotkey Ctrl+Space starts recording; dynamic Space key stops recording. Unhook Space immediately on stop!
   - Send WAV to Groq API (whisper-large-v3-turbo for STT, llama-3.1-8b-instant for formatting).
   - Copy output to clipboard and auto-paste via Ctrl+V macro.
```
