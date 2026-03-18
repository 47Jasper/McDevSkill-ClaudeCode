#!/usr/bin/env python3
"""
mc_helper.py — Minecraft Server & Client Management
====================================================
Handles build, deploy, server lifecycle, client launch, and in-game input.
For observation/capture, use observe.py instead.

Usage:
    python mc_helper.py build
    python mc_helper.py deploy
    python mc_helper.py server-restart
    python mc_helper.py server-stop
    python mc_helper.py server-start
    python mc_helper.py server-wait-ready [--timeout N]
    python mc_helper.py server-command <cmd>
    python mc_helper.py client-launch
    python mc_helper.py chat "<message>"
    python mc_helper.py key "<key>" [--duration N]
    python mc_helper.py click <x> <y>
    python mc_helper.py status
    python mc_helper.py full-cycle
"""

import argparse
import glob
import os
import platform
import shutil
import subprocess
import sys
import time

import yaml

# ── Load Config ──────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SKILL_DIR, "config.yaml")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


CFG = load_config()
PLATFORM = CFG.get("platform", platform.system().lower())
if PLATFORM == "darwin":
    PLATFORM = "mac"


def run(cmd, cwd=None, shell=True, timeout=120):
    try:
        r = subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"


def ok(msg):
    print(f"[OK] {msg}")


def err(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)


def info(msg):
    print(f"[INFO] {msg}")


# ── Build ────────────────────────────────────────────────────

def find_plugin_jar(project, tool):
    explicit = CFG.get("jar_name")
    search_dir = os.path.join(project, "target" if tool == "maven" else os.path.join("build", "libs"))
    if explicit:
        path = os.path.join(search_dir, explicit)
        return path if os.path.isfile(path) else None
    if not os.path.isdir(search_dir):
        return None
    jars = [f for f in glob.glob(os.path.join(search_dir, "*.jar"))
            if not f.endswith(("-sources.jar", "-javadoc.jar", "-original.jar"))]
    if not jars:
        return None
    jars.sort(key=os.path.getmtime, reverse=True)
    return jars[0]


def cmd_build():
    project = CFG["project_dir"]
    tool = CFG.get("build_tool", "maven")
    if tool == "maven":
        build_cmd = "mvn clean package -q -DskipTests"
    elif tool == "gradle":
        wrapper = "gradlew.bat" if PLATFORM == "windows" else "./gradlew"
        build_cmd = f"{wrapper} build -q -x test"
    else:
        err(f"Unknown build tool: {tool}")
        return False

    info(f"Building with {tool}...")
    code, out, stderr = run(build_cmd, cwd=project, timeout=300)
    if code != 0:
        err(f"Build failed:\n{stderr}\n{out}")
        return False

    jar = find_plugin_jar(project, tool)
    if jar:
        ok(f"Built: {jar}")
        return True
    err("Build succeeded but couldn't find output JAR.")
    return False


# ── Deploy ───────────────────────────────────────────────────

def cmd_deploy():
    project = CFG["project_dir"]
    tool = CFG.get("build_tool", "maven")
    plugins_dir = CFG["server"]["plugins_dir"]
    jar = find_plugin_jar(project, tool)
    if not jar:
        err("No JAR found. Run 'build' first.")
        return False

    os.makedirs(plugins_dir, exist_ok=True)
    base_name = os.path.basename(jar).rsplit("-", 1)[0]
    for old in glob.glob(os.path.join(plugins_dir, f"{base_name}*.jar")):
        os.remove(old)
        info(f"Removed old: {os.path.basename(old)}")

    dest = os.path.join(plugins_dir, os.path.basename(jar))
    shutil.copy2(jar, dest)
    ok(f"Deployed {os.path.basename(jar)} → {plugins_dir}")
    return True


# ── Server Management ────────────────────────────────────────

def use_mcss():
    return bool(CFG["server"].get("mcss_path") and CFG["server"].get("mcss_server_name"))


def mcss_api(endpoint, method="GET", data=None):
    import requests
    port = CFG["server"].get("mcss_api_port", 25560)
    key = CFG["server"].get("mcss_api_key", "")
    base = f"http://localhost:{port}/api/v1"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = f"{base}/{endpoint}"
    if method == "GET":
        return requests.get(url, headers=headers, timeout=10)
    elif method == "POST":
        return requests.post(url, json=data or {}, headers=headers, timeout=10)
    elif method == "PUT":
        return requests.put(url, json=data or {}, headers=headers, timeout=10)
    return requests.request(method, url, json=data, headers=headers, timeout=10)


def get_mcss_server_id():
    r = mcss_api("servers")
    if r.status_code == 200:
        for srv in r.json():
            if srv.get("name", "").lower() == CFG["server"]["mcss_server_name"].lower():
                return srv["id"]
    return None


def cmd_server_restart():
    if use_mcss():
        sid = get_mcss_server_id()
        if not sid:
            err(f"MCSS server '{CFG['server']['mcss_server_name']}' not found.")
            return False
        r = mcss_api(f"servers/{sid}/action/restart", method="POST")
        if r.status_code in (200, 204):
            ok("MCSS server restarting...")
            return True
        err(f"MCSS restart failed: {r.status_code} {r.text}")
        return False
    else:
        cmd_server_stop()
        time.sleep(2)
        return cmd_server_start()


def cmd_server_stop():
    if use_mcss():
        sid = get_mcss_server_id()
        if sid:
            mcss_api(f"servers/{sid}/action/stop", method="POST")
            ok("MCSS server stopping...")
            return True
        return False

    session = CFG["server"].get("session_name", "mcserver")
    mgr = CFG["server"].get("session_manager", "subprocess")

    if PLATFORM == "windows" or mgr == "subprocess":
        jar_name = os.path.basename(CFG["server"].get("jar_path", "paper.jar"))
        run(f'wmic process where "commandline like \'%{jar_name}%\'" call terminate')
    elif mgr == "tmux":
        run(f"tmux send-keys -t {session} 'stop' Enter")
        time.sleep(5)
        run(f"tmux kill-session -t {session}")
    elif mgr == "screen":
        run(f"screen -S {session} -X stuff 'stop\n'")
        time.sleep(5)
        run(f"screen -S {session} -X quit")

    ok("Server stopped.")
    return True


def cmd_server_start():
    if use_mcss():
        sid = get_mcss_server_id()
        if sid:
            mcss_api(f"servers/{sid}/action/start", method="POST")
            ok("MCSS server starting...")
            return True
        return False

    server_dir = CFG["server"]["server_dir"]
    jar = CFG["server"].get("jar_path", "paper.jar")
    jvm = CFG["server"].get("jvm_args", "-Xmx4G -Xms2G")
    session = CFG["server"].get("session_name", "mcserver")
    mgr = CFG["server"].get("session_manager", "subprocess")
    jar_basename = os.path.basename(jar)
    start_cmd = f"java {jvm} -jar {jar_basename} --nogui"

    if PLATFORM == "windows" or mgr == "subprocess":
        run(f'start "mcserver" /D "{server_dir}" cmd /c "{start_cmd}"', shell=True)
    elif mgr == "tmux":
        run(f'tmux new-session -d -s {session} -c "{server_dir}" "{start_cmd}"')
    elif mgr == "screen":
        run(f'screen -dmS {session} bash -c "cd {server_dir} && {start_cmd}"')

    ok("Server starting...")
    return True


def cmd_server_wait_ready(timeout=90):
    log_file = CFG["server"].get("log_file", "")
    if not log_file:
        log_file = os.path.join(CFG["server"]["server_dir"], "logs", "latest.log")
    info(f"Waiting for server to be ready (timeout: {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        if os.path.isfile(log_file):
            with open(log_file, "r", errors="replace") as f:
                content = f.read()
                if "Done" in content and "For help" in content:
                    ok("Server is ready!")
                    return True
        time.sleep(2)
    err("Timed out waiting for server to be ready.")
    return False


def cmd_server_command(command):
    if use_mcss():
        sid = get_mcss_server_id()
        if sid:
            r = mcss_api(f"servers/{sid}/execute", method="POST", data={"command": command})
            if r.status_code in (200, 204):
                ok(f"Sent: {command}")
                return True
            err(f"MCSS command failed: {r.text}")
            return False
    session = CFG["server"].get("session_name", "mcserver")
    mgr = CFG["server"].get("session_manager", "subprocess")
    if mgr == "tmux":
        run(f"tmux send-keys -t {session} '{command}' Enter")
    elif mgr == "screen":
        run(f"screen -S {session} -X stuff '{command}\n'")
    else:
        err("Cannot send console commands in 'subprocess' mode without MCSS.")
        return False
    ok(f"Sent: {command}")
    return True


# ── Client ───────────────────────────────────────────────────

def cmd_client_launch():
    launcher = CFG["client"]["launcher"]
    launcher_path = CFG["client"]["launcher_path"]
    instance = CFG["client"].get("instance_name", "")
    if not os.path.isfile(launcher_path):
        err(f"Launcher not found: {launcher_path}")
        return False
    if launcher in ("multimc", "prism"):
        cmd = f'"{launcher_path}" --launch "{instance}"'
    else:
        cmd = f'"{launcher_path}"'
    info(f"Launching {launcher} instance '{instance}'...")
    if PLATFORM == "windows":
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    ok("Client launch initiated.")
    return True


# ── Input ────────────────────────────────────────────────────

def _focus():
    """Import and use observe.py's focus_window via subprocess to keep things simple."""
    # Quick inline focus
    title = CFG["client"].get("window_title", "Minecraft")
    if PLATFORM == "windows":
        run(f'powershell -c "(New-Object -ComObject WScript.Shell).AppActivate(\'{title}\')"')
    elif PLATFORM == "linux":
        code, hwnd, _ = run(f'xdotool search --name "{title}" | head -1')
        if hwnd:
            run(f"xdotool windowactivate {hwnd}")
    elif PLATFORM == "mac":
        run(f'osascript -e \'tell application "System Events" to set frontmost of '
            f'(first process whose name contains "{title}") to true\'')
    time.sleep(0.3)


def cmd_chat(message):
    _focus()
    import pyautogui
    pyautogui.PAUSE = 0.05
    if message.startswith("/"):
        pyautogui.press("slash")
        message = message[1:]
    else:
        pyautogui.press("t")
    time.sleep(0.3)
    try:
        pyautogui.typewrite(message, interval=0.02)
    except Exception:
        import pyperclip
        pyperclip.copy(message)
        pyautogui.hotkey("ctrl", "v")
    time.sleep(0.1)
    pyautogui.press("enter")
    ok(f"Chat sent: {'/' if message else ''}{message}")
    return True


def cmd_key(key, duration=0):
    _focus()
    import pyautogui
    if duration > 0:
        pyautogui.keyDown(key)
        time.sleep(duration)
        pyautogui.keyUp(key)
        ok(f"Held '{key}' for {duration}s")
    else:
        pyautogui.press(key)
        ok(f"Pressed '{key}'")
    return True


def cmd_click(x, y):
    _focus()
    import pyautogui
    pyautogui.click(int(x), int(y))
    ok(f"Clicked at ({x}, {y})")
    return True


# ── Status ───────────────────────────────────────────────────

def cmd_status():
    title = CFG["client"].get("window_title", "Minecraft")
    if PLATFORM == "windows":
        code, out, _ = run(f'powershell -c "(Get-Process | Where-Object '
                           f'{{$_.MainWindowTitle -like \'*{title}*\'}}).Id"')
        client_running = bool(out and out != "0")
    elif PLATFORM == "linux":
        code, out, _ = run(f'xdotool search --name "{title}" | head -1')
        client_running = bool(out)
    else:
        client_running = None

    print(f"Client: {'RUNNING' if client_running else 'NOT FOUND' if client_running is not None else 'UNKNOWN'}")

    if use_mcss():
        try:
            sid = get_mcss_server_id()
            if sid:
                r = mcss_api(f"servers/{sid}")
                if r.status_code == 200:
                    print(f"Server (MCSS): {r.json().get('status', 'UNKNOWN')}")
                else:
                    print("Server (MCSS): QUERY FAILED")
            else:
                print("Server (MCSS): NOT FOUND BY NAME")
        except Exception as e:
            print(f"Server (MCSS): ERROR ({e})")
    else:
        print("Server: Direct management (check process)")

    return True


# ── Full Cycle ───────────────────────────────────────────────

def cmd_full_cycle():
    steps = [
        ("Building plugin...", cmd_build),
        ("Deploying JAR...", cmd_deploy),
        ("Restarting server...", cmd_server_restart),
        ("Waiting for server...", lambda: cmd_server_wait_ready(timeout=90)),
    ]
    for label, fn in steps:
        info(label)
        if not fn():
            err(f"Full cycle aborted at: {label}")
            return False
        time.sleep(1)

    time.sleep(3)
    info("Running Tier 0 observation (logs)...")
    # Call observe.py for logs
    observe_script = os.path.join(SCRIPT_DIR, "observe.py")
    subprocess.run([sys.executable, observe_script, "logs", "--tail", "30"])

    ok("Full cycle complete! Pick a higher observation tier if needed.")
    return True


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MC Server & Client Helper")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("build")
    sub.add_parser("deploy")
    sub.add_parser("server-restart")
    sub.add_parser("server-stop")
    sub.add_parser("server-start")
    wait_p = sub.add_parser("server-wait-ready")
    wait_p.add_argument("--timeout", type=int, default=90)
    sc_p = sub.add_parser("server-command")
    sc_p.add_argument("cmd", nargs="+")
    sub.add_parser("client-launch")
    chat_p = sub.add_parser("chat")
    chat_p.add_argument("message")
    key_p = sub.add_parser("key")
    key_p.add_argument("keyname")
    key_p.add_argument("--duration", type=float, default=0)
    click_p = sub.add_parser("click")
    click_p.add_argument("x", type=int)
    click_p.add_argument("y", type=int)
    sub.add_parser("status")
    sub.add_parser("full-cycle")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "build": cmd_build,
        "deploy": cmd_deploy,
        "server-restart": cmd_server_restart,
        "server-stop": cmd_server_stop,
        "server-start": cmd_server_start,
        "server-wait-ready": lambda: cmd_server_wait_ready(args.timeout),
        "server-command": lambda: cmd_server_command(" ".join(args.cmd)),
        "client-launch": cmd_client_launch,
        "chat": lambda: cmd_chat(args.message),
        "key": lambda: cmd_key(args.keyname, args.duration),
        "click": lambda: cmd_click(args.x, args.y),
        "status": cmd_status,
        "full-cycle": cmd_full_cycle,
    }

    fn = dispatch.get(args.command)
    if fn:
        success = fn()
        sys.exit(0 if success else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
