# Minecraft Visual Debug Skill for Claude Code

Let Claude Code build your Minecraft plugins, deploy them, launch the real game
client on your account, **observe in-game results through a tiered perception
system**, and iterate — all autonomously while minimizing wasted tokens.

## The Problem with Screenshots

Naive screenshot-based debugging sends a full image to the vision model after
every single action. Each image costs ~250+ tokens. Most of the time, the answer
was already in the server logs (free) or the chat text (~50 tokens via OCR).

## The Solution: Tiered Observation

This skill gives Claude Code **5 observation tiers** of increasing richness and
cost. Claude explicitly picks the tier based on what it's testing:

```
Tier 0 — Server logs         ~10 tokens   Always. 'Free.' Catches 60%+ of issues.
Tier 1 — Chat OCR            ~50 tokens   Reads chat text after /commands.
Tier 2 — Smart screenshot    ~250 tokens  Only sends image if screen changed.
Tier 3 — Burst capture       ~500 tokens  Multi-frame, picks most-different pair.
Tier 4 — Video keyframes     ~1000 tokens Records video, extracts scene changes.
```

Most plugin debug iterations never leave Tiers 0-1. When they do need visuals,
the perceptual hashing in Tier 2 prevents sending duplicate frames. Tier 3's
burst mode catches transient effects (particles, spawns). Tier 4's ffmpeg
recording handles complex animations.

## Quick Start

### 1. Install the skill

```bash
cp -r mc-visual-debug-skill ~/mc-visual-debug-skill
```

### 2. Install dependencies

```bash
python ~/mc-visual-debug-skill/scripts/setup.py
```

Installs: pyautogui, Pillow, pyyaml, requests, imagehash, easyocr, numpy,
and platform-specific extras.

**Platform extras:**
- **Windows**: pywin32 (auto-installed)
- **Mac**: `brew install cliclick`
- **Linux**: `sudo apt install scrot xdotool`
- **All (Tier 4)**: ffmpeg (`choco`/`brew`/`apt install ffmpeg`)

### 3. Edit config.yaml

```yaml
platform: "windows"

project_dir: "C:/Users/YOU/my-plugin"
build_tool: "maven"

server:
  server_dir: "C:/mc-server"
  plugins_dir: "C:/mc-server/plugins"
  # MCSS users:
  mcss_path: "C:/MCSS"
  mcss_server_name: "MyDevServer"
  mcss_api_port: 25560
  mcss_api_key: "your-key"

client:
  launcher: "prism"
  launcher_path: "C:/Program Files/PrismLauncher/prismlauncher.exe"
  instance_name: "DevTest"

observation:
  diff_threshold: 10       # % change needed to trigger Tier 2
  chat_crop_ratio: 0.22    # Bottom 22% of screen = chat area
```

### 4. MCSS API setup

1. Open MCSS → Settings → API
2. Enable the API
3. Generate an API key
4. Note the port (default 25560)
5. Fill these into `config.yaml`

### 5. Launcher instance

1. Create a "DevTest" instance in Prism/MultiMC
2. Match the Minecraft version to your plugin's target
3. Log into your account
4. Add `localhost` to the server list inside Minecraft

### 6. Add CLAUDE.md to your project

```bash
cp ~/mc-visual-debug-skill/CLAUDE.md.template ~/my-plugin/CLAUDE.md
```

### 7. Go!

Tell Claude Code what to build. Example prompt:

> "Build a /heal command that restores HP and shows heart particles.
> Deploy it, restart the server, and verify it works in-game."

Claude Code will:
1. Write the Java code
2. `mc_helper.py build` → compile
3. `mc_helper.py deploy` → copy JAR
4. `mc_helper.py server-restart` + `server-wait-ready`
5. `mc_helper.py client-launch` (if needed)
6. `mc_helper.py chat "/heal"` → send the command
7. `observe.py logs` → check for errors (Tier 0)
8. `observe.py chat-ocr` → read chat response (Tier 1)
9. `observe.py burst` → check for particles (Tier 3, only if needed)
10. Iterate if anything is wrong

## Architecture

```
mc-visual-debug-skill/
├── SKILL.md                # Skill instructions (Claude Code reads this)
├── config.yaml             # YOUR paths and settings
├── CLAUDE.md.template      # Copy into your project root
├── README.md               # You are here
└── scripts/
    ├── setup.py            # One-time dependency installer
    ├── mc_helper.py        # Server/client/input management
    └── observe.py          # Tiered observation engine
```

## Command Reference

### mc_helper.py — Server & Client

| Command | What it does |
|---|---|
| `build` | Compile the plugin (Maven/Gradle) |
| `deploy` | Copy JAR to server plugins folder |
| `server-restart` | Restart via MCSS API or process management |
| `server-stop` / `server-start` | Individual lifecycle control |
| `server-wait-ready` | Block until server boot completes |
| `server-command <cmd>` | Send a console command |
| `client-launch` | Launch Prism/MultiMC instance |
| `chat "<msg>"` | Type in-game chat or /commands |
| `key "<key>" [--duration N]` | Press or hold a key |
| `click <x> <y>` | Click at screen coordinates |
| `status` | Check client + server state |
| `full-cycle` | Build → Deploy → Restart → Wait → Tier 0 |

### observe.py — Observation Engine

| Command | Tier | What it does |
|---|---|---|
| `logs [--tail N]` | 0 | Parse server logs, highlight errors |
| `chat-ocr` | 1 | Crop chat region, OCR to text |
| `screenshot [--force]` | 2 | Capture + perceptual hash diff |
| `burst [--frames N] [--duration S]` | 3 | Multi-frame, select keyframes |
| `record [--duration S]` | 4 | ffmpeg video + scene-change extraction |
| `buffer [--last N]` | — | Review recent observations |
| `clear` | — | Clear all frames and buffer |

## How the Smart Diff Works (Tier 2)

Every screenshot is hashed using perceptual hashing (pHash). This produces a
compact fingerprint that's similar for visually similar images. When a new frame
comes in, its hash is compared to the previous frame's hash. If the difference
is below the threshold (default 10%), the observation reports "no significant
change" and doesn't return an image — saving ~250 tokens. The threshold is
configurable for different environments (raise it outdoors with weather/mobs).

## How Burst Capture Works (Tier 3)

Takes N screenshots evenly spaced over a duration. Compares all pairs using
perceptual hashing. Selects the 1-2 frames with the maximum visual difference
from each other. This naturally catches "before and after" for transient
effects like particles, entity spawns, or GUI popups that might be visible
for only a fraction of a second.

## How Video Keyframes Work (Tier 4)

Records a short clip using ffmpeg (gdigrab/avfoundation/x11grab depending on
platform). Runs ffmpeg's scene-change detection filter to extract only frames
where the visual content changed significantly. If no scene changes are found,
falls back to evenly-spaced frame sampling. If too many keyframes are found,
uses farthest-point sampling on perceptual hashes to pick the most diverse set
(max 4 frames).

## Tips

- **GUI scale 3-4** in Minecraft for better OCR accuracy
- **Low render distance** (4-6 chunks) for faster loading
- **Allocate enough RAM** for both server and client
- **Raise diff_threshold to 15-20** if testing outdoors with weather
- **Wait 1-2 seconds** after input before observing
- **F1 hides HUD**, F3 shows debug — useful for specific observations
