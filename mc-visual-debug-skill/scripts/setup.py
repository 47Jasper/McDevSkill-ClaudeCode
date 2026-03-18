#!/usr/bin/env python3
"""
Setup script — installs all Python packages for the MC Visual Debug skill.
Run once:  python setup.py
"""

import subprocess
import sys
import platform
import shutil


def pip_install(packages):
    for pkg in packages:
        print(f"  Installing {pkg}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pkg, "--quiet"],
            stdout=subprocess.DEVNULL,
        )


def main():
    os_name = platform.system().lower()
    print("=" * 50)
    print("MC Visual Debug — Setup")
    print("=" * 50)

    # ── Core (all platforms) ──
    print("\n[1/4] Core dependencies...")
    pip_install(["pyautogui", "Pillow", "pyyaml", "requests"])

    # ── Observation engine ──
    print("\n[2/4] Observation engine...")
    pip_install([
        "imagehash",       # Perceptual hashing for frame diff (Tier 2+)
        "easyocr",         # OCR for chat text extraction (Tier 1)
        "numpy",           # Required by imagehash and easyocr
    ])

    # ── Platform-specific ──
    print("\n[3/4] Platform-specific...")
    if os_name == "windows":
        pip_install(["pywin32"])
        print("  [Windows] pywin32 installed for window management.")
    elif os_name == "darwin":
        print("  [Mac] Install cliclick for input: brew install cliclick")
    elif os_name == "linux":
        print("  [Linux] Install these if missing:")
        print("    sudo apt install scrot xdotool")

    # ── ffmpeg check (Tier 4) ──
    print("\n[4/4] Checking ffmpeg (optional, needed for Tier 4)...")
    if shutil.which("ffmpeg"):
        print("  ffmpeg found!")
    else:
        print("  ffmpeg NOT found. Tier 4 (video keyframes) won't work.")
        if os_name == "windows":
            print("  Install: choco install ffmpeg  OR  winget install ffmpeg")
        elif os_name == "darwin":
            print("  Install: brew install ffmpeg")
        elif os_name == "linux":
            print("  Install: sudo apt install ffmpeg")

    # ── EasyOCR model download hint ──
    print("\n[NOTE] EasyOCR will download language models (~100MB) on first use.")
    print("       This is a one-time download and is cached locally.")

    print("\n" + "=" * 50)
    print("Setup complete!")
    print("Next: Edit config.yaml with your paths.")
    print("=" * 50)


if __name__ == "__main__":
    main()
