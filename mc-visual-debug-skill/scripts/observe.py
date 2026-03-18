#!/usr/bin/env python3
"""
observe.py — Tiered Observation Engine for Minecraft Visual Debugging
=====================================================================
Claude Code calls this to observe the game state at the appropriate tier.

Usage:
    python observe.py logs [--tail N]                    # Tier 0: Server logs
    python observe.py chat-ocr                           # Tier 1: Chat region OCR
    python observe.py screenshot [--force]               # Tier 2: Smart diff screenshot
    python observe.py burst [--frames N] [--duration S]  # Tier 3: Burst capture
    python observe.py record [--duration S]              # Tier 4: Video keyframes
    python observe.py buffer [--last N]                  # View observation buffer
    python observe.py clear                              # Clear buffer + frames

Each command outputs a structured observation report to stdout.
"""

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from PIL import Image

# ── Load Config ──────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
SKILL_DIR = SCRIPT_DIR.parent
CONFIG_PATH = SKILL_DIR / "config.yaml"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


CFG = load_config()
PLATFORM = CFG.get("platform", platform.system().lower())
if PLATFORM == "darwin":
    PLATFORM = "mac"

OBS_CFG = CFG.get("observation", {})

# Observation directory
_obs_dir = OBS_CFG.get("obs_dir", "")
if not _obs_dir:
    if PLATFORM == "windows":
        _obs_dir = os.path.join(os.environ.get("TEMP", "C:/Temp"), "mc_obs")
    else:
        _obs_dir = "/tmp/mc_obs"
OBS_DIR = Path(_obs_dir)
OBS_DIR.mkdir(parents=True, exist_ok=True)

BUFFER_PATH = OBS_DIR / "buffer.json"
FRAME_COUNTER_PATH = OBS_DIR / ".frame_counter"
LAST_HASH_PATH = OBS_DIR / ".last_phash"


# ── Utilities ────────────────────────────────────────────────

def frame_counter():
    """Monotonically increasing frame counter."""
    if FRAME_COUNTER_PATH.exists():
        n = int(FRAME_COUNTER_PATH.read_text().strip())
    else:
        n = 0
    n += 1
    FRAME_COUNTER_PATH.write_text(str(n))
    return n


def frame_path(n):
    return OBS_DIR / f"frame_{n:04d}.png"


def report(tier, tier_name, lines, frames=None):
    """Print a structured observation report."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'═' * 45}")
    print(f" OBSERVATION REPORT — Tier {tier} ({tier_name})")
    print(f" Timestamp: {ts}")
    print(f"{'─' * 45}")
    for line in lines:
        print(f" {line}")
    if frames:
        print(f"{'─' * 45}")
        for f in frames:
            print(f" Frame: {f}")
    print(f"{'─' * 45}")

    # Buffer entry
    entry = {
        "tier": tier,
        "tier_name": tier_name,
        "timestamp": ts,
        "summary": "; ".join(lines[:3]),
        "frames": [str(f) for f in (frames or [])],
    }
    buffer_append(entry)

    buf_count = buffer_count()
    print(f" Buffer: {buf_count} entries")
    print(f"{'═' * 45}\n")


def buffer_append(entry):
    """Append to the rolling observation buffer."""
    max_entries = OBS_CFG.get("buffer_max_entries", 50)
    buf = buffer_load()
    buf.append(entry)
    if len(buf) > max_entries:
        buf = buf[-max_entries:]
    with open(BUFFER_PATH, "w") as f:
        json.dump(buf, f, indent=2)


def buffer_load():
    if BUFFER_PATH.exists():
        try:
            with open(BUFFER_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def buffer_count():
    return len(buffer_load())


# ── Window + Capture Helpers ─────────────────────────────────

def find_mc_window():
    """Find the Minecraft window handle/id."""
    title = CFG.get("client", {}).get("window_title", "Minecraft")

    if PLATFORM == "windows":
        try:
            import win32gui
            result = []

            def callback(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    t = win32gui.GetWindowText(hwnd)
                    if title.lower() in t.lower():
                        result.append(hwnd)

            win32gui.EnumWindows(callback, None)
            return result[0] if result else None
        except ImportError:
            code, out, _ = _run(
                f'powershell -c "(Get-Process | Where-Object '
                f'{{$_.MainWindowTitle -like \'*{title}*\'}}).MainWindowHandle"'
            )
            return int(out) if out and out != "0" else None

    elif PLATFORM == "linux":
        code, out, _ = _run(f'xdotool search --name "{title}" | head -1')
        return out if code == 0 and out else None

    elif PLATFORM == "mac":
        return title

    return None


def focus_window():
    """Bring Minecraft to the foreground."""
    hwnd = find_mc_window()
    if not hwnd:
        return False

    if PLATFORM == "windows":
        try:
            import win32gui
            import win32con
            win32gui.ShowWindow(int(hwnd), win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(int(hwnd))
        except Exception:
            _run('powershell -c "(New-Object -ComObject WScript.Shell)'
                 ".AppActivate('Minecraft')\"")
    elif PLATFORM == "linux":
        _run(f"xdotool windowactivate {hwnd}")
    elif PLATFORM == "mac":
        _run(f'osascript -e \'tell application "System Events" to set frontmost of '
             f'(first process whose name contains "{hwnd}") to true\'')

    time.sleep(0.3)
    return True


def capture_screenshot():
    """Capture the Minecraft window. Returns a PIL Image or None."""
    delay = OBS_CFG.get("capture_delay", 0.5)
    focus_window()
    time.sleep(delay)

    n = frame_counter()
    out = frame_path(n)

    if PLATFORM == "windows":
        _capture_windows(str(out))
    elif PLATFORM == "mac":
        _capture_mac(str(out))
    elif PLATFORM == "linux":
        _capture_linux(str(out))

    if out.exists() and out.stat().st_size > 0:
        return Image.open(str(out)), out
    return None, None


def _capture_windows(out_path):
    ps = f'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen
$bmp = New-Object System.Drawing.Bitmap($screen.Bounds.Width, $screen.Bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($screen.Bounds.Location, [System.Drawing.Point]::Empty, $screen.Bounds.Size)
$bmp.Save("{out_path}")
$g.Dispose(); $bmp.Dispose()
'''
    _run(f'powershell -c "{ps.strip()}"')


def _capture_mac(out_path):
    hwnd = find_mc_window()
    if hwnd:
        _run(f'screencapture -l $(osascript -e \'tell application "System Events" '
             f'to unix id of (first process whose name contains "{hwnd}")\') '
             f'"{out_path}"')
    else:
        _run(f'screencapture "{out_path}"')


def _capture_linux(out_path):
    hwnd = find_mc_window()
    if hwnd:
        _run(f'import -window {hwnd} "{out_path}"')
    else:
        _run(f'scrot "{out_path}"')


def _run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timed out"


# ── Perceptual Hashing ──────────────────────────────────────

def compute_phash(img):
    """Compute perceptual hash of a PIL Image."""
    import imagehash
    return imagehash.phash(img, hash_size=16)


def load_last_hash():
    """Load the previous frame's perceptual hash."""
    if LAST_HASH_PATH.exists():
        import imagehash
        hex_str = LAST_HASH_PATH.read_text().strip()
        if hex_str:
            return imagehash.hex_to_hash(hex_str)
    return None


def save_hash(h):
    """Persist the current frame hash."""
    LAST_HASH_PATH.write_text(str(h))


def hash_diff_pct(h1, h2):
    """Percentage difference between two perceptual hashes (0-100)."""
    # phash with hash_size=16 gives 256-bit hashes
    max_bits = h1.hash.size
    diff_bits = h1 - h2  # Hamming distance
    return (diff_bits / max_bits) * 100


# ── OCR ──────────────────────────────────────────────────────

_ocr_reader = None


def get_ocr_reader():
    """Lazy-init EasyOCR reader (first call downloads models)."""
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        langs = OBS_CFG.get("ocr_languages", ["en"])
        _ocr_reader = easyocr.Reader(langs, verbose=False)
    return _ocr_reader


def ocr_image(img):
    """Run OCR on a PIL Image. Returns list of text strings."""
    import numpy as np
    reader = get_ocr_reader()
    arr = np.array(img)
    results = reader.readtext(arr, detail=0, paragraph=True)
    return results


# ── TIER 0: Server Logs ─────────────────────────────────────

def cmd_logs(tail=30):
    """Tier 0: Parse and return recent server log lines."""
    log_file = CFG["server"].get("log_file", "")
    if not log_file:
        log_file = os.path.join(CFG["server"]["server_dir"], "logs", "latest.log")

    if not os.path.isfile(log_file):
        report(0, "Server Logs", [f"Log file not found: {log_file}"])
        return

    with open(log_file, "r", errors="replace") as f:
        lines = f.readlines()

    recent = [l.rstrip() for l in lines[-tail:]]

    # Categorize
    errors = [l for l in recent if re.search(r'\b(ERROR|SEVERE|Exception|Caused by)\b', l, re.I)]
    warnings = [l for l in recent if re.search(r'\bWARN\b', l, re.I)]
    plugin_lines = [l for l in recent if re.search(r'\[.*plugin.*\]|\[.*Plugin.*\]', l, re.I)
                    or "plugin" in l.lower()]

    summary = []
    summary.append(f"Lines read: {len(recent)} (last {tail})")
    summary.append(f"Errors: {len(errors)}")
    summary.append(f"Warnings: {len(warnings)}")
    summary.append(f"Plugin mentions: {len(plugin_lines)}")

    if errors:
        summary.append("")
        summary.append("── ERRORS ──")
        for e in errors[-5:]:
            summary.append(f"  {e[:120]}")

    if warnings:
        summary.append("")
        summary.append("── WARNINGS ──")
        for w in warnings[-3:]:
            summary.append(f"  {w[:120]}")

    if plugin_lines:
        summary.append("")
        summary.append("── PLUGIN OUTPUT ──")
        for p in plugin_lines[-5:]:
            summary.append(f"  {p[:120]}")

    if not errors and not warnings:
        summary.append("")
        summary.append("No errors or warnings detected.")

    # Also print the raw tail for Claude to read
    summary.append("")
    summary.append("── RAW LOG TAIL ──")
    for l in recent[-15:]:
        summary.append(f"  {l[:150]}")

    report(0, "Server Logs", summary)


# ── TIER 1: Chat OCR ────────────────────────────────────────

def cmd_chat_ocr():
    """Tier 1: Capture screen, crop chat region, OCR it."""
    img, fpath = capture_screenshot()
    if img is None:
        report(1, "Chat OCR", ["Failed to capture screenshot."])
        return

    # Crop the chat region (bottom portion of screen)
    ratio = OBS_CFG.get("chat_crop_ratio", 0.22)
    w, h = img.size
    top = int(h * (1.0 - ratio))
    chat_region = img.crop((0, top, w, h))

    # Save the cropped region for debugging
    chat_path = OBS_DIR / "chat_crop_latest.png"
    chat_region.save(str(chat_path))

    # OCR
    text_lines = ocr_image(chat_region)

    summary = []
    if text_lines:
        summary.append(f"Chat text extracted ({len(text_lines)} segments):")
        summary.append("")
        for line in text_lines:
            cleaned = line.strip()
            if cleaned:
                summary.append(f"  > {cleaned}")
    else:
        summary.append("No text detected in chat region.")
        summary.append("(Chat may be empty, or GUI scale may need adjusting)")

    summary.append("")
    summary.append(f"Chat crop saved: {chat_path}")

    report(1, "Chat OCR", summary, frames=[fpath] if fpath else [])


# ── TIER 2: Smart Diff Screenshot ───────────────────────────

def cmd_screenshot(force=False):
    """Tier 2: Capture + perceptual hash diff."""
    img, fpath = capture_screenshot()
    if img is None:
        report(2, "Smart Screenshot", ["Failed to capture screenshot."])
        return

    current_hash = compute_phash(img)
    last_hash = load_last_hash()

    threshold = OBS_CFG.get("diff_threshold", 10)

    summary = []

    if last_hash is not None and not force:
        diff = hash_diff_pct(last_hash, current_hash)
        summary.append(f"Visual diff: {diff:.1f}% change from previous")

        if diff < threshold:
            summary.append(f"Below threshold ({threshold}%) — no significant change.")
            summary.append("Verdict: UNCHANGED")
            summary.append("")
            summary.append("Skipping frame (use --force to override).")
            save_hash(current_hash)
            report(2, "Smart Screenshot", summary)
            return
        else:
            summary.append(f"Above threshold ({threshold}%) — change detected!")
            summary.append("Verdict: CHANGED — new frame saved")
    else:
        if force:
            summary.append("Force capture — skipping diff check.")
        else:
            summary.append("No previous frame to compare (first capture).")
        summary.append("Verdict: NEW FRAME")

    save_hash(current_hash)
    report(2, "Smart Screenshot", summary, frames=[fpath])


# ── TIER 3: Burst Capture ───────────────────────────────────

def cmd_burst(num_frames=5, duration=2.0):
    """Tier 3: Rapid multi-frame capture, select most distinct."""
    interval = duration / max(num_frames - 1, 1)
    frames_data = []

    summary = [f"Capturing {num_frames} frames over {duration}s..."]

    for i in range(num_frames):
        img, fpath = capture_screenshot()
        if img is not None:
            h = compute_phash(img)
            frames_data.append({"img": img, "path": fpath, "hash": h, "idx": i})
        if i < num_frames - 1:
            time.sleep(interval)

    summary.append(f"Captured {len(frames_data)} frames successfully.")

    if len(frames_data) < 2:
        summary.append("Not enough frames for comparison.")
        paths = [fd["path"] for fd in frames_data if fd["path"]]
        report(3, "Burst Capture", summary, frames=paths)
        return

    # Find the pair with maximum visual difference
    max_diff = 0
    best_pair = (0, -1)
    for i in range(len(frames_data)):
        for j in range(i + 1, len(frames_data)):
            d = hash_diff_pct(frames_data[i]["hash"], frames_data[j]["hash"])
            if d > max_diff:
                max_diff = d
                best_pair = (i, j)

    summary.append(f"Max pairwise diff: {max_diff:.1f}% (frames {best_pair[0]} & {best_pair[1]})")

    # Select keyframes: always include first and most-different pair
    selected_indices = set()
    selected_indices.add(0)                    # First frame (baseline)
    selected_indices.add(best_pair[0])
    selected_indices.add(best_pair[1])

    # Deduplicate if pair includes frame 0
    selected = sorted(selected_indices)
    # Cap at 2 frames to keep token cost reasonable
    if len(selected) > 2:
        # Keep the pair that's most different
        selected = sorted(list(best_pair))

    selected_frames = [frames_data[i] for i in selected]

    threshold = OBS_CFG.get("diff_threshold", 10)
    if max_diff < threshold:
        summary.append(f"All frames similar (below {threshold}% threshold).")
        summary.append("Verdict: NO SIGNIFICANT CHANGE during burst.")
        # Still provide one frame
        report(3, "Burst Capture", summary, frames=[frames_data[0]["path"]])
    else:
        summary.append(f"Selected {len(selected_frames)} keyframes.")
        summary.append("Verdict: CHANGE DETECTED during burst.")
        paths = [fd["path"] for fd in selected_frames]
        report(3, "Burst Capture", summary, frames=paths)

    # Update the last hash to the final frame
    save_hash(frames_data[-1]["hash"])


# ── TIER 4: Video Keyframes ─────────────────────────────────

def cmd_record(duration=5):
    """Tier 4: Record short video with ffmpeg, extract keyframes."""
    ffmpeg = OBS_CFG.get("ffmpeg_path", "") or "ffmpeg"
    if not shutil.which(ffmpeg):
        report(4, "Video Keyframes", [
            "ffmpeg not found!",
            "Install ffmpeg to use Tier 4 recording.",
            "Windows: choco install ffmpeg",
            "Mac: brew install ffmpeg",
            "Linux: sudo apt install ffmpeg",
        ])
        return

    fps = OBS_CFG.get("record_fps", 10)
    scene_thresh = OBS_CFG.get("scene_threshold", 0.3)

    video_path = OBS_DIR / "capture.mp4"
    keyframes_dir = OBS_DIR / "keyframes"
    keyframes_dir.mkdir(exist_ok=True)

    # Clean old keyframes
    for old in keyframes_dir.glob("*.png"):
        old.unlink()

    summary = [f"Recording {duration}s at {fps}fps..."]

    # Focus the window first
    focus_window()
    time.sleep(0.3)

    # ── Record ──
    if PLATFORM == "windows":
        # gdigrab captures the whole screen; we'll crop later if needed
        rec_cmd = (
            f'"{ffmpeg}" -y -f gdigrab -framerate {fps} '
            f'-t {duration} -i desktop '
            f'-c:v libx264 -preset ultrafast -crf 28 '
            f'"{video_path}"'
        )
    elif PLATFORM == "mac":
        # avfoundation — device 1 is usually the screen
        rec_cmd = (
            f'"{ffmpeg}" -y -f avfoundation -framerate {fps} '
            f'-t {duration} -i "1:none" '
            f'-c:v libx264 -preset ultrafast -crf 28 '
            f'"{video_path}"'
        )
    elif PLATFORM == "linux":
        display = os.environ.get("DISPLAY", ":0")
        rec_cmd = (
            f'"{ffmpeg}" -y -f x11grab -framerate {fps} '
            f'-t {duration} -i {display} '
            f'-c:v libx264 -preset ultrafast -crf 28 '
            f'"{video_path}"'
        )
    else:
        report(4, "Video Keyframes", [f"Unsupported platform: {PLATFORM}"])
        return

    code, out, err = _run(rec_cmd, timeout=duration + 30)
    if code != 0 or not video_path.exists():
        summary.append(f"Recording failed (exit code {code}).")
        if err:
            summary.append(f"ffmpeg stderr: {err[:200]}")
        report(4, "Video Keyframes", summary)
        return

    file_size = video_path.stat().st_size
    summary.append(f"Recorded: {video_path} ({file_size // 1024} KB)")

    # ── Extract keyframes using scene change detection ──
    summary.append(f"Extracting keyframes (scene threshold={scene_thresh})...")

    extract_cmd = (
        f'"{ffmpeg}" -y -i "{video_path}" '
        f'-vf "select=\'gt(scene,{scene_thresh})\',showinfo" '
        f'-vsync vfr '
        f'"{keyframes_dir}/kf_%03d.png"'
    )
    code, out, err = _run(extract_cmd, timeout=60)

    keyframe_files = sorted(keyframes_dir.glob("kf_*.png"))

    if not keyframe_files:
        # No scene changes detected — fall back to evenly-spaced frames
        summary.append("No scene changes detected. Extracting evenly spaced frames...")
        num_samples = min(4, max(2, int(duration)))
        for i in range(num_samples):
            t = (i / max(num_samples - 1, 1)) * duration
            sample_cmd = (
                f'"{ffmpeg}" -y -ss {t:.2f} -i "{video_path}" '
                f'-frames:v 1 "{keyframes_dir}/kf_{i:03d}.png"'
            )
            _run(sample_cmd, timeout=15)
        keyframe_files = sorted(keyframes_dir.glob("kf_*.png"))

    # If we have too many keyframes, select the most distinct ones
    if len(keyframe_files) > 4:
        keyframe_files = _select_best_keyframes(keyframe_files, max_frames=4)

    summary.append(f"Extracted {len(keyframe_files)} keyframes.")

    # Rename/copy to observation dir with proper numbering
    final_frames = []
    for kf in keyframe_files:
        n = frame_counter()
        dest = frame_path(n)
        shutil.copy2(str(kf), str(dest))
        final_frames.append(dest)

    if final_frames:
        summary.append("Verdict: KEYFRAMES EXTRACTED")
    else:
        summary.append("Verdict: NO KEYFRAMES — recording may have been static")

    report(4, "Video Keyframes", summary, frames=final_frames)


def _select_best_keyframes(files, max_frames=4):
    """Given many keyframes, pick the N most visually distinct."""
    import imagehash
    data = []
    for f in files:
        img = Image.open(str(f))
        h = imagehash.phash(img, hash_size=16)
        data.append({"path": f, "hash": h})

    if len(data) <= max_frames:
        return [d["path"] for d in data]

    # Greedy farthest-point sampling
    selected = [0]  # Start with first frame
    for _ in range(max_frames - 1):
        best_idx = -1
        best_min_dist = -1
        for i in range(len(data)):
            if i in selected:
                continue
            min_dist = min(data[i]["hash"] - data[s]["hash"] for s in selected)
            if min_dist > best_min_dist:
                best_min_dist = min_dist
                best_idx = i
        if best_idx >= 0:
            selected.append(best_idx)

    selected.sort()
    return [data[i]["path"] for i in selected]


# ── Buffer View ──────────────────────────────────────────────

def cmd_buffer(last=10):
    """View the recent observation buffer."""
    buf = buffer_load()
    if not buf:
        print("Observation buffer is empty.")
        return

    show = buf[-last:]
    print(f"\n{'═' * 45}")
    print(f" OBSERVATION BUFFER (last {len(show)} of {len(buf)})")
    print(f"{'═' * 45}")
    for i, entry in enumerate(show):
        tier = entry.get("tier", "?")
        name = entry.get("tier_name", "?")
        ts = entry.get("timestamp", "?")
        summary = entry.get("summary", "")
        frames = entry.get("frames", [])
        print(f"\n [{i+1}] Tier {tier} ({name}) @ {ts}")
        print(f"     {summary[:100]}")
        if frames:
            for fp in frames:
                print(f"     Frame: {fp}")
    print(f"\n{'═' * 45}\n")


def cmd_clear():
    """Clear the observation buffer and all frames."""
    count = 0
    for f in OBS_DIR.glob("frame_*.png"):
        f.unlink()
        count += 1
    for f in OBS_DIR.glob("chat_crop_*.png"):
        f.unlink()
    kf_dir = OBS_DIR / "keyframes"
    if kf_dir.exists():
        shutil.rmtree(str(kf_dir))
    cap = OBS_DIR / "capture.mp4"
    if cap.exists():
        cap.unlink()
    if BUFFER_PATH.exists():
        BUFFER_PATH.unlink()
    if FRAME_COUNTER_PATH.exists():
        FRAME_COUNTER_PATH.unlink()
    if LAST_HASH_PATH.exists():
        LAST_HASH_PATH.unlink()

    print(f"Cleared {count} frames and buffer from {OBS_DIR}")


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MC Observation Engine")
    sub = parser.add_subparsers(dest="command")

    # Tier 0
    log_p = sub.add_parser("logs", help="Tier 0: Server logs")
    log_p.add_argument("--tail", type=int, default=30)

    # Tier 1
    sub.add_parser("chat-ocr", help="Tier 1: Chat region OCR")

    # Tier 2
    ss_p = sub.add_parser("screenshot", help="Tier 2: Smart diff screenshot")
    ss_p.add_argument("--force", action="store_true",
                      help="Skip diff check, always capture")

    # Tier 3
    burst_p = sub.add_parser("burst", help="Tier 3: Burst capture")
    burst_p.add_argument("--frames", type=int,
                         default=OBS_CFG.get("burst_default_frames", 5))
    burst_p.add_argument("--duration", type=float,
                         default=OBS_CFG.get("burst_default_duration", 2.0))

    # Tier 4
    rec_p = sub.add_parser("record", help="Tier 4: Video keyframes")
    rec_p.add_argument("--duration", type=float, default=5.0)

    # Buffer
    buf_p = sub.add_parser("buffer", help="View observation buffer")
    buf_p.add_argument("--last", type=int, default=10)

    # Clear
    sub.add_parser("clear", help="Clear buffer and frames")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "logs":
        cmd_logs(args.tail)
    elif args.command == "chat-ocr":
        cmd_chat_ocr()
    elif args.command == "screenshot":
        cmd_screenshot(force=args.force)
    elif args.command == "burst":
        cmd_burst(num_frames=args.frames, duration=args.duration)
    elif args.command == "record":
        cmd_record(duration=args.duration)
    elif args.command == "buffer":
        cmd_buffer(last=args.last)
    elif args.command == "clear":
        cmd_clear()


if __name__ == "__main__":
    main()
