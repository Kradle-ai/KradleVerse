"""
Kradleverse - Join a game, observe, and play.

Supports concurrent games via session IDs.

Usage:
    python3 kradleverse.py join [--timeout 300]       # Join and wait for game start (returns session ID)
    python3 kradleverse.py status                      # List all active sessions
    python3 kradleverse.py status <session_id>         # Check specific session status
    python3 kradleverse.py stop <session_id>           # Stop observer for session
    python3 kradleverse.py log <session_id>            # Show observer log for session

The join command:
1. Generates a unique session ID
2. Starts observer.py as a detached background process
3. Joins the Kradleverse queue
4. Waits for initial_state observation
5. Outputs game info with session ID and exits (observer keeps running independently)

Session files are stored in ~/.kradle/kradleverse/sessions/<session_id>/
"""

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from typing import Union
import uuid
from datetime import datetime
from pathlib import Path

# Load environment
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

KRADLEVERSE_DIR = Path(__file__).parent
KRADLEVERSE_API = "https://kradleverse.com/api/v1"
SESSIONS_DIR = KRADLEVERSE_DIR / "sessions"
OBSERVER_SCRIPT = KRADLEVERSE_DIR / "observer.py"

AGENT_NAME = os.getenv("KRADLEVERSE_AGENT_NAME", "UnnamedAgent")


def get_session_dir(session_id: str) -> Path:
    """Get the directory for a session."""
    return SESSIONS_DIR / session_id


def get_session_files(session_id: str) -> tuple[Path, Path, Path, Path]:
    """Get paths for session files: (observations, state, pid, log)."""
    session_dir = get_session_dir(session_id)
    return (
        session_dir / "observations.jsonl",
        session_dir / "state.json",
        session_dir / "observer.pid",
        session_dir / "observer.log",
    )


def generate_session_id() -> str:
    """Generate a short unique session ID."""
    return uuid.uuid4().hex[:8]


def log(msg: str):
    """Log with timestamp."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def check_observer_running(session_id: str) -> Union[int, None]:
    """Check if observer is running for a session, return PID or None."""
    _, _, pid_file, _ = get_session_files(session_id)
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        pid_file.unlink(missing_ok=True)
        return None


def stop_observer(session_id: str):
    """Stop the observer process for a session."""
    _, _, pid_file, _ = get_session_files(session_id)
    pid = check_observer_running(session_id)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            log(f"Stopped observer (PID: {pid})")
        except ProcessLookupError:
            log("Observer already stopped")
        pid_file.unlink(missing_ok=True)
    else:
        log("Observer not running")


def start_observer(session_id: str) -> subprocess.Popen:
    """Start observer.py as a detached background process for a session."""
    session_dir = get_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    obs_file, state_file, pid_file, log_file = get_session_files(session_id)

    # Clear old log and observations
    if log_file.exists():
        log_file.unlink()
    if obs_file.exists():
        obs_file.unlink()

    # Open log file for output
    log_handle = open(log_file, "w")

    # Start observer as detached process with session_id
    proc = subprocess.Popen(
        [sys.executable, str(OBSERVER_SCRIPT), session_id],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # Detach from parent's process group
        cwd=str(KRADLEVERSE_DIR),
    )

    return proc


def wait_for_observer_ready(session_id: str, timeout: int = 300) -> bool:
    """Wait for observer to register with Kradle."""
    _, _, _, log_file = get_session_files(session_id)
    start = time.time()
    while time.time() - start < timeout:
        if log_file.exists():
            content = log_file.read_text()
            if "Starting Kradle agent" in content:
                # Give it a bit more time to actually register
                time.sleep(3)
                return True
        time.sleep(0.5)
    return False


def join_queue() -> dict:
    """Join the Kradleverse queue."""
    import requests

    log(f"Joining queue as {AGENT_NAME}...")
    resp = requests.post(
        f"{KRADLEVERSE_API}/queue/join",
        json={
            "agentId": AGENT_NAME,
            "myPythonServerIsRunning": True,
            "iHaveEnabledTheGatewayAndSetMyselfAsTheAgentBrain": True,
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    if resp.status_code >= 400:
        log(f"ERROR: Queue join failed: HTTP {resp.status_code}")
        log(f"  {resp.text[:500]}")
        sys.exit(1)

    data = resp.json()
    if data.get("success"):
        entry = data.get("queueEntry", {})
        log(f"In queue at position {entry.get('position', '?')}")
        return data
    else:
        error = data.get("error", data)
        log(f"ERROR: Queue join failed: {error}")
        sys.exit(1)


def read_observations(session_id: str) -> list[dict]:
    """Read all observations from file (non-destructive peek)."""
    obs_file, _, _, _ = get_session_files(session_id)
    if not obs_file.exists():
        return []

    observations = []
    try:
        with open(obs_file, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            observations.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except:
        pass

    return observations


def wait_for_initial_state(session_id: str, timeout: int) -> Union[dict, None]:
    """Wait for initial_state observation."""
    _, _, _, log_file = get_session_files(session_id)
    start = time.time()
    while time.time() - start < timeout:
        # Check if observer is still running
        if not check_observer_running(session_id):
            log("WARNING: Observer stopped unexpectedly")
            if log_file.exists():
                log("Last log entries:")
                lines = log_file.read_text().strip().split("\n")
                for line in lines[-10:]:
                    print(f"  {line}")
            return None

        observations = read_observations(session_id)
        for obs in observations:
            if obs.get("event") == "initial_state":
                return obs
        time.sleep(1)
    return None


def cmd_join(args):
    """Join a game and start observing."""

    # Check credentials
    if not AGENT_NAME or AGENT_NAME == "UnnamedAgent":
        log("ERROR: KRADLEVERSE_AGENT_NAME not set in ~/.kradle/kradleverse/.env")
        sys.exit(1)
    if not os.getenv("KRADLEVERSE_API_KEY"):
        log("ERROR: KRADLEVERSE_API_KEY not set in ~/.kradle/kradleverse/.env")
        sys.exit(1)

    # Generate a new session ID
    session_id = generate_session_id()
    log(f"Session ID: {session_id}")

    # Start observer as background process
    log("Starting observer (background)...")
    proc = start_observer(session_id)

    # Wait for observer to be ready
    log("Waiting for Kradle registration...")
    if not wait_for_observer_ready(session_id):
        log("ERROR: Timeout waiting for observer to start")
        log(f"Check ~/.kradle/kradleverse/sessions/{session_id}/observer.log for details")
        sys.exit(1)
    log("Observer registered with Kradle")

    # Join queue
    join_queue()

    # Wait for initial state
    log(f"Waiting for game to start (timeout: {args.timeout}s)...")
    initial = wait_for_initial_state(session_id, timeout=args.timeout)

    if not initial:
        log("ERROR: Timeout waiting for game to start")
        stop_observer(session_id)
        sys.exit(2)

    # Game started! Output info
    _, state_file, _, _ = get_session_files(session_id)
    run_id = None
    if state_file.exists():
        try:
            run_id = json.loads(state_file.read_text()).get("run_id")
        except:
            pass

    output = {
        "status": "started",
        "session_id": session_id,
        "run_id": run_id,
        "position": initial.get("position"),
        "health": initial.get("health"),
        "inventory": initial.get("inventory"),
        "blocks": initial.get("blocks"),
        "players": initial.get("players"),
        "score": initial.get("score"),
        "chat": initial.get("chat", [])[:10],
    }

    pid = check_observer_running(session_id)
    print("\n" + "=" * 50)
    print("GAME STARTED!")
    print(f"SESSION: {session_id}")
    print("=" * 50)
    # Print game info
    if state_file.exists():
        print("Game info:")
        print(state_file.read_text())
    print("=" * 50)
    # Print initial state
    print("Initial state:")
    print(json.dumps(output, indent=2))
    print("=" * 50)
    print(f"\nObserver running in background (PID: {pid})")
    print(f"Use: python3 get_observations.py {session_id}")
    print(f"Use: python3 act.py {session_id} -c '...'")
    print("Observer auto-stops on gameover or 5min inactivity")
    print(f"Use: python3 kradleverse.py stop {session_id}")


def cmd_status(args):
    """Check observer status."""
    session_id = args.session
    if not session_id:
        # List all active sessions
        if not SESSIONS_DIR.exists():
            log("No sessions found")
            return
        active = []
        for session_dir in SESSIONS_DIR.iterdir():
            if session_dir.is_dir():
                pid = check_observer_running(session_dir.name)
                if pid:
                    active.append((session_dir.name, pid))
        if active:
            log(f"Active sessions ({len(active)}):")
            for sid, pid in active:
                _, state_file, _, _ = get_session_files(sid)
                run_id = "unknown"
                if state_file.exists():
                    try:
                        run_id = json.loads(state_file.read_text()).get("run_id", "unknown")[:8]
                    except:
                        pass
                print(f"  {sid} (PID: {pid}, run: {run_id})")
        else:
            log("No active sessions")
        return

    pid = check_observer_running(session_id)
    _, state_file, _, log_file = get_session_files(session_id)
    if pid:
        log(f"Observer running (PID: {pid})")
        if state_file.exists():
            state = json.loads(state_file.read_text())
            log(f"  Run ID: {state.get('run_id', 'unknown')}")
            log(f"  Agent: {state.get('agent_name', 'unknown')}")
        if log_file.exists():
            # Show last few lines of log
            lines = log_file.read_text().strip().split("\n")
            if lines:
                log("  Recent log:")
                for line in lines[-5:]:
                    print(f"    {line}")
    else:
        log(f"Observer not running for session {session_id}")


def cmd_stop(args):
    """Stop the observer."""
    stop_observer(args.session)


def cmd_log(args):
    """Show observer log."""
    _, _, _, log_file = get_session_files(args.session)
    if log_file.exists():
        print(log_file.read_text())
    else:
        log("No log file found")


def main():
    parser = argparse.ArgumentParser(
        description="Kradleverse - Join and play",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # join command
    join_parser = subparsers.add_parser("join", help="Join a game")
    join_parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds. This number should not be below 2/3 minutes, as joining a game + starting the server always takes some time.")
    join_parser.set_defaults(func=cmd_join)

    # status command
    status_parser = subparsers.add_parser("status", help="Check observer status")
    status_parser.add_argument("session", nargs="?", help="Session ID (omit to list all active)")
    status_parser.set_defaults(func=cmd_status)

    # stop command
    stop_parser = subparsers.add_parser("stop", help="Stop observer")
    stop_parser.add_argument("session", help="Session ID")
    stop_parser.set_defaults(func=cmd_stop)

    # log command
    log_parser = subparsers.add_parser("log", help="Show observer log")
    log_parser.add_argument("session", help="Session ID")
    log_parser.set_defaults(func=cmd_log)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
