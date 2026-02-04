#!/usr/bin/env python3
"""
Kradleverse Observer - Background daemon that receives game events.

This script runs as a detached background process, started by kradleverse.py.
It connects to Kradle, receives observations, and writes them to observations.jsonl.
Auto-stops on gameover or 5-minute inactivity.

Usage:
    python3 observer.py <session_id>
"""

import argparse
import dataclasses
import fcntl
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Load environment
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from kradle import Context, Kradle
from kradle.models import ChallengeInfo, MinecraftEvent, Observation

KRADLEVERSE_DIR = Path(__file__).parent
SESSIONS_DIR = KRADLEVERSE_DIR / "sessions"

AGENT_NAME = os.getenv("KRADLEVERSE_AGENT_NAME", "UnnamedAgent")
INACTIVITY_TIMEOUT = 300  # 5 minutes

# Session-specific paths (set by main)
SESSION_ID = None
OBSERVATIONS_FILE = None
STATE_FILE = None
PID_FILE = None
LOG_FILE = None


def init_session(session_id: str):
    """Initialize session-specific file paths."""
    global SESSION_ID, OBSERVATIONS_FILE, STATE_FILE, PID_FILE, LOG_FILE
    SESSION_ID = session_id
    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    OBSERVATIONS_FILE = session_dir / "observations.jsonl"
    STATE_FILE = session_dir / "state.json"
    PID_FILE = session_dir / "observer.pid"
    LOG_FILE = session_dir / "observer.log"


def log(msg: str):
    """Log with timestamp to both stdout and log file."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except:
        pass


def save_state(run_id: str, participant_id: str, task: str, js_functions: str):
    """Save current run state for act.py."""
    state = {
        "run_id": run_id,
        "participant_id": participant_id,
        "available_skills_js_functions": js_functions,
        "task": task,
        "agent_name": AGENT_NAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    STATE_FILE.write_text(json.dumps(state))


def log_observation(obs_dict: dict):
    """Append observation to JSONL file with file locking."""
    with open(OBSERVATIONS_FILE, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(obs_dict) + "\n")
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def run_observer():
    """Run the Kradle observer."""

    # Write PID file
    PID_FILE.write_text(str(os.getpid()))
    log(f"Observer started (PID: {os.getpid()})")

    # Set Kradle env vars
    if os.getenv("KRADLEVERSE_API_KEY"):
        os.environ["KRADLE_API_KEY"] = os.getenv("KRADLEVERSE_API_KEY")
    if not os.getenv("KRADLE_API_URL"):
        os.environ["KRADLE_API_URL"] = "https://api.kradle.ai/v0"

    last_observation_time = time.time()
    game_over = threading.Event()

    def check_inactivity():
        """Background thread to check for inactivity timeout."""
        while not game_over.is_set():
            time.sleep(30)
            if time.time() - last_observation_time > INACTIVITY_TIMEOUT:
                log("Inactivity timeout (5 minutes). Stopping observer.")
                cleanup_and_exit(0)

    def cleanup_and_exit(code: int):
        """Clean up and exit."""
        PID_FILE.unlink(missing_ok=True)
        os._exit(code)

    # Start inactivity checker
    inactivity_thread = threading.Thread(target=check_inactivity, daemon=True)
    inactivity_thread.start()

    # Clear old observations
    if OBSERVATIONS_FILE.exists():
        OBSERVATIONS_FILE.unlink()

    kradle = Kradle(create_public_url=True)
    agent = kradle.agent(
        name=f"kradleverse:{AGENT_NAME}",
        display_name=AGENT_NAME,
        description="Kradleverse observer",
    )

    @agent.init
    def on_init(challenge: ChallengeInfo, context: Context):
        """Called when agent joins a session."""
        nonlocal last_observation_time
        last_observation_time = time.time()

        context["task"] = challenge.task
        context["run_id"] = challenge.run_id
        context["participant_id"] = challenge.participant_id
        context["js_functions"] = challenge.js_functions

        # Save state for act.py
        save_state(challenge.run_id, challenge.participant_id, challenge.task, challenge.js_functions)
        log(f"Joined run: {challenge.run_id}")

    @agent.event(
        MinecraftEvent.INITIAL_STATE,
        MinecraftEvent.IDLE,
        MinecraftEvent.COMMAND_EXECUTED,
        MinecraftEvent.COMMAND_PROGRESS,
        MinecraftEvent.CHAT,
        MinecraftEvent.MESSAGE,
        MinecraftEvent.DEATH,
        MinecraftEvent.GAMEOVER,
    )
    def on_event(obs: Observation, context: Context):
        """Log observation."""
        nonlocal last_observation_time
        last_observation_time = time.time()

        # Build observation dict
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **dataclasses.asdict(obs),
        }

        log_observation(entry)
        log(f"Event: {obs.event} (score: {obs.score})")

        # Exit on game over
        if obs.event in ("gameover", "game_over"):
            log(f"Game over! Final score: {obs.score}")
            game_over.set()
            # Give time to log, then exit
            threading.Timer(2.0, lambda: cleanup_and_exit(0)).start()

        return {
            "code": "",
            "message": "",
            "thoughts": "",
            "delay": 0,
        }

    log("Starting Kradle agent...")
    agent.serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kradleverse Observer")
    parser.add_argument("session", help="Session ID")
    args = parser.parse_args()

    init_session(args.session)

    try:
        run_observer()
    except KeyboardInterrupt:
        log("Observer stopped by user")
        PID_FILE.unlink(missing_ok=True)
    except Exception as e:
        log(f"Observer error: {e}")
        PID_FILE.unlink(missing_ok=True)
        sys.exit(1)
