---
name: mc-visual-debug
description: >
  Minecraft plugin development with tiered visual in-game debugging. Use this skill
  whenever the user wants to build, test, or debug a Minecraft plugin (Bukkit/Spigot/
  Paper/Folia). This skill lets Claude Code compile the plugin, deploy it to a local
  server managed by MCSS (or standalone), launch the Minecraft client via MultiMC/Prism
  Launcher, and observe in-game results through a tiered observation system that
  minimizes token waste: server logs (free), chat OCR (cheap), smart-diff screenshots
  (moderate), burst capture (rich), and short video keyframe extraction (richest).
  Claude Code explicitly picks the observation tier based on what it's testing.
  Trigger on any mention of: Minecraft plugin, Bukkit, Spigot, Paper, Folia, MCSS,
  in-game testing, visual debugging, or "test it in Minecraft."
---

# Minecraft Visual Debug Skill

## Overview

This skill enables a full visual debug loop for Minecraft plugin development with
a **tiered observation system** that balances information richness against token cost.

### The Observation Tiers

Claude Code **explicitly selects** the tier based on what it's testing:

| Tier | Name | When to use | Token cost |
|------|------|-------------|------------|
| 0 | Server logs | Always. First check after every action. | ~0 (text) |
| 1 | Chat OCR | After sending a /command — reads the chat response. | ~50 (text) |
| 2 | Smart screenshot | When you need to see the screen but suspect no change. | ~250 (image, only if changed) |
| 3 | Burst capture | Transient visuals: particles, entities, animations. | ~500 (1-2 images) |
| 4 | Video keyframes | Complex motion or multi-step visual sequences. | ~1000 (2-4 images) |

**Rule of thumb**: Start at Tier 0. Escalate only when lower tiers don't answer
the question. Most plugin test iterations never need to go past Tier 1.

## First-Time Setup

Before using this skill, the user must configure `config.yaml` in this skill's
directory. Read it first:

```bash
cat <skill-directory>/config.yaml
```

If it hasn't been customized yet, walk the user through filling it in.

Then run the setup script to install Python dependencies:

```bash
python <skill-directory>/scripts/setup.py
```

## Helper Scripts

There are two main scripts:

- **`mc_helper.py`** — Server and client management (build, deploy, restart, launch,
  send input, etc.)
- **`observe.py`** — The observation engine (tiered capture + analysis)

Both live in `<skill-directory>/scripts/`.

## Core Workflow

### Step 1: Build the Plugin

```bash
python <skill-directory>/scripts/mc_helper.py build
```

Finds the output JAR in `target/` (Maven) or `build/libs/` (Gradle).

### Step 2: Deploy

```bash
python <skill-directory>/scripts/mc_helper.py deploy
```

Copies the JAR to the server's `plugins/` folder, removing old versions.

### Step 3: Restart the Server

```bash
python <skill-directory>/scripts/mc_helper.py server-restart
python <skill-directory>/scripts/mc_helper.py server-wait-ready
```

Uses MCSS API if configured, otherwise direct process management.

### Step 4: Launch the Client (if not already running)

```bash
python <skill-directory>/scripts/mc_helper.py client-launch
```

First launch requires the user to manually connect to `localhost`.

### Step 5: Interact In-Game

```bash
# Send a chat command
python <skill-directory>/scripts/mc_helper.py chat "/my-command arg1"

# Press a key
python <skill-directory>/scripts/mc_helper.py key "w" --duration 2.0

# Click at coordinates
python <skill-directory>/scripts/mc_helper.py click 400 300

# Press escape
python <skill-directory>/scripts/mc_helper.py key "escape"
```

### Step 6: Observe — Pick the Right Tier

This is the critical step. **Choose the observation tier based on what you're
testing:**

#### Tier 0 — Server Logs (always do this first)

```bash
python <skill-directory>/scripts/observe.py logs --tail 30
```

Parses the latest server log. Highlights errors, warnings, plugin messages.
Returns structured text. **Do this after every single action.**

#### Tier 1 — Chat OCR

```bash
python <skill-directory>/scripts/observe.py chat-ocr
```

Captures the Minecraft window, crops the chat region (bottom ~20% of the screen),
runs OCR, and returns the chat text. Perfect for verifying `/command` responses.
Costs ~50 text tokens instead of ~250 image tokens.

#### Tier 2 — Smart Diff Screenshot

```bash
python <skill-directory>/scripts/observe.py screenshot
```

Captures the Minecraft window and compares it to the previous capture using
perceptual hashing. If the screen hasn't changed significantly (< threshold),
returns "no significant change" (zero vision tokens). If it has changed, saves
the frame and returns the path for you to view.

**To view the screenshot (only when observe.py reports a change):**
Look at the image file path returned by observe.py.

#### Tier 3 — Burst Capture

```bash
python <skill-directory>/scripts/observe.py burst --frames 5 --duration 2.0
```

Takes N screenshots over a duration, compares them all pairwise, and selects
the 1-2 most visually distinct frames. Ideal for:
- Particle effects (did they appear and disappear?)
- Entity spawning (did a mob spawn?)
- Boss bars, action bars, scoreboards appearing

Returns the paths of selected keyframes.

#### Tier 4 — Video Keyframes

```bash
python <skill-directory>/scripts/observe.py record --duration 5
```

Records a short video of the Minecraft window using ffmpeg, then extracts
scene-change keyframes. Returns 2-4 frames that represent the most significant
visual transitions. Ideal for:
- Animated GUIs or menus
- Movement-based testing (entity AI, pathfinding)
- Multi-step visual sequences

Requires ffmpeg to be installed.

### Step 7: Analyze + Iterate

After observing, combine the information from all used tiers:

1. **Logs say error?** → Fix the code, go to Step 1.
2. **Chat OCR shows wrong response?** → Fix the command handler.
3. **Screenshot shows unexpected visuals?** → Investigate further.
4. **Everything looks correct?** → Move to the next feature.

## Observation Reports

Every `observe.py` call outputs a structured report to stdout:

```
═══ OBSERVATION REPORT ═══
Tier: 2 (Smart Screenshot)
Timestamp: 2025-01-15 14:32:07
─────────────────────────
Visual diff: 34.2% change from previous
Verdict: CHANGED — new frame saved
Frame: /tmp/mc_obs/frame_007.png
Previous: /tmp/mc_obs/frame_006.png
─────────────────────────
Observation buffer: 7 entries (last 5 min)
═══════════════════════════
```

## Observation Buffer

The observe script maintains a rolling buffer of recent observations in
`/tmp/mc_obs/buffer.json`. You can review it:

```bash
python <skill-directory>/scripts/observe.py buffer --last 10
```

This shows the last N observations with their tier, timestamp, and summary.
Useful for understanding the sequence of events.

## Quick Reference

```
Build → Deploy → Restart → Wait Ready → Interact → Observe → Iterate
                                            ↑                    |
                                            └────────────────────┘

Observation tier selection:
  "Did the server error?"           → Tier 0 (logs)
  "Did the command respond?"        → Tier 1 (chat-ocr)
  "Did the GUI/world change?"       → Tier 2 (screenshot)
  "Did particles/entities appear?"  → Tier 3 (burst)
  "Did the animation play out?"     → Tier 4 (record)
```

## Full Cycle Shortcut

```bash
python <skill-directory>/scripts/mc_helper.py full-cycle
```

Runs: Build → Deploy → Restart → Wait Ready → Tier 0 observation.
You then manually pick higher tiers as needed.

## Tips

- **Always start with Tier 0.** Logs catch 60%+ of issues for zero token cost.
- **Tier 1 before Tier 2.** If you sent a /command, OCR the chat first. Don't
  pay for a full screenshot when 50 text tokens will tell you the answer.
- **Wait 1-2 seconds** between sending input and observing. Minecraft needs
  a moment to process and render.
- **Use F1 to hide the HUD** if Tier 2+ screenshots are cluttered.
- **Use F3 for debug screen** if you need coordinates or TPS.
- **Tier 3 burst is your friend for particles** — they're visible for only a
  few frames. A single screenshot often misses them.
- **Tier 4 requires ffmpeg.** Install it if you need video capture.

## Troubleshooting

- **OCR returns garbage**: Chat font might be too small. Increase Minecraft's
  GUI scale to 3 or 4 in video settings.
- **Screenshots are black**: Window is minimized or occluded. Ensure Minecraft
  is visible on screen.
- **Perceptual hash always says "changed"**: If you're in a world with moving
  clouds/mobs, raise the diff threshold in config.yaml.
- **ffmpeg not found**: Install it — `choco install ffmpeg` (Windows),
  `brew install ffmpeg` (Mac), `sudo apt install ffmpeg` (Linux).
- **MCSS server won't restart**: Verify `mcss_path`, `mcss_server_name`, and
  `mcss_api_key` in config.yaml.
