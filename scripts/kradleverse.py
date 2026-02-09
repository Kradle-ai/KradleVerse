"""
Kradleverse CLI - Join a game, observe, act, and play.

Supports concurrent games via session IDs.

Usage:
    python3 kradleverse.py init <agent_name>           # Register a new agent
    python3 kradleverse.py join [--timeout 300]        # Join and wait for game start
    python3 kradleverse.py observe <session_id>        # Get new observations (clears buffer)
    python3 kradleverse.py observe <session_id> --peek # Peek without clearing
    python3 kradleverse.py act <session_id> -c "code"  # Send action to game
    python3 kradleverse.py status                      # List all active sessions
    python3 kradleverse.py status <session_id>         # Check specific session
    python3 kradleverse.py stop <session_id>           # Stop observer for session
    python3 kradleverse.py log <session_id>            # Show observer log
    python3 kradleverse.py cleanup                     # Remove all session data

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
from datetime import datetime, timezone
from pathlib import Path

# Data lives in ~/.kradle/kradleverse/, scripts live alongside this file
DATA_DIR = Path.home() / ".kradle" / "kradleverse"
SCRIPTS_DIR = Path(__file__).parent

from dotenv import load_dotenv
load_dotenv(DATA_DIR / ".env")

VERSION = (SCRIPTS_DIR / "VERSION").read_text().strip()
KRADLEVERSE_API = "https://kradleverse.com/api/v1"
SESSIONS_DIR = DATA_DIR / "sessions"

AGENT_NAME = os.getenv("KRADLEVERSE_AGENT_NAME", "UnnamedAgent")
API_KEY = os.getenv("KRADLEVERSE_API_KEY")

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
    with open(Path(__file__).parent / "kradleverse.log", "a") as f:
        f.write(f"[{ts}] {msg}\n")


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
    """Start observer as a detached background process for a session."""
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

    # Start observer as detached process using this same script
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__)), "_observer", session_id],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # Detach from parent's process group
        cwd=str(SCRIPTS_DIR),
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


def read_observations_peek(session_id: str) -> list[dict]:
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

        observations = read_observations_peek(session_id)
        for obs in observations:
            if obs.get("event") == "initial_state":
                return obs
        time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# Observe helpers (from get_observations.py)
# ---------------------------------------------------------------------------

# Fields that belong to "current state" (only need latest value)
STATE_FIELDS = {"position", "health", "players", "blocks", "entities", "inventory", "run_status", "run_id", "xp", "score", "gamemode", "equipped", "winner", "is_alive", "craftable", "time_of_day", "name"}

# Fields we don't care about
EXCLUDED_FIELDS = {"task", "observation_id", "time", "run_id", "idle", "biome", "weather", "on_ground", "participant_id", "executing", "hunger", "lives"}


def filter_observation(obs: dict) -> dict:
    res = {
        **{field: obs[field] for field in obs.keys() if field not in EXCLUDED_FIELDS},
    }
    res["output"] = obs["output"][:500] if obs["output"] else None
    return res


def read_observations(session_id: str, clear: bool = True) -> list[dict]:
    """Read observations from JSONL file for a session, optionally clearing."""
    obs_file = SESSIONS_DIR / session_id / "observations.jsonl"
    if not obs_file.exists():
        return []

    observations = []

    mode = "r+" if clear else "r"
    lock_type = fcntl.LOCK_EX if clear else fcntl.LOCK_SH

    with open(obs_file, mode) as f:
        fcntl.flock(f.fileno(), lock_type)
        try:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        observations.append(filter_observation(json.loads(line)))
                    except json.JSONDecodeError:
                        pass

            # Clear the file only if requested
            if clear:
                f.seek(0)
                f.truncate()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    return observations


def extract_event(obs: dict) -> dict:
    """Extract only event-relevant fields from an observation."""
    event = {}
    for field in set(obs.keys()).difference(STATE_FIELDS):
        if field in obs:
            value = obs[field]
            if value is None:
                continue
            if isinstance(value, list) and len(value) == 0:
                continue
            event[field] = value
    return event


def extract_current_state(obs: dict) -> dict:
    """Extract current state fields from the latest observation."""
    state = {}
    for field in STATE_FIELDS:
        if field in obs:
            state[field] = obs[field]
    if "score" in obs:
        state["score"] = obs["score"]
    if "run_status" in obs:
        state["run_status"] = obs["run_status"]
    return state


# ---------------------------------------------------------------------------
# Act helpers (from act.py)
# ---------------------------------------------------------------------------

def get_state(session_id: str) -> dict:
    """Load current run state for a session."""
    state_file = SESSIONS_DIR / session_id / "state.json"
    if not state_file.exists():
        print(f"ERROR: No state file at {state_file}", file=sys.stderr)
        print(f"Is the game running? Use /kradleverse-join first.", file=sys.stderr)
        sys.exit(1)

    try:
        return json.loads(state_file.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid state file: {e}", file=sys.stderr)
        sys.exit(1)


def send_action(session_id: str, code: str = "", message: str = "", thoughts: str = "") -> dict:
    """Send an action to the Kradleverse run for a session."""
    # Setup Kradle environment
    if API_KEY:
        os.environ["KRADLE_API_KEY"] = API_KEY
    if not os.getenv("KRADLE_API_URL"):
        os.environ["KRADLE_API_URL"] = "https://api.kradle.ai/v0"

    try:
        from kradle.api.client import KradleAPI
    except ImportError:
        print("ERROR: kradle SDK not installed. Run: pip install kradle", file=sys.stderr)
        sys.exit(1)

    state = get_state(session_id)
    api = KradleAPI()

    action = {
        "code": code,
        "message": message,
        "thoughts": thoughts,
    }

    run_id = state.get("run_id", "")[:8]
    print(f"Sending action to run {run_id} (session: {session_id})...")

    if code:
        display_code = code[:60] + ("..." if len(code) > 60 else "")
        print(f"   Code: {display_code}")
    if message:
        print(f"   Message: {message}")

    try:
        result = api.runs.send_action(
            run_id=state["run_id"],
            action=action,
            participant_id=state["participant_id"],
        )
        print("Action sent!")
        return result
    except Exception as e:
        print(f"ERROR: Failed to send action: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Observer daemon (from observer.py)
# ---------------------------------------------------------------------------

def observer_log(msg: str, log_file: Path):
    """Log with timestamp to both stdout and log file."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(log_file, "a") as f:
            f.write(line + "\n")
    except:
        pass


def observer_save_state(state_file: Path, run_id: str, participant_id: str, task: str, js_functions: str):
    """Save current run state for the act command."""
    state = {
        "run_id": run_id,
        "participant_id": participant_id,
        "available_skills_js_functions": js_functions,
        "task": task,
        "agent_name": AGENT_NAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state_file.write_text(json.dumps(state))


def observer_log_observation(obs_file: Path, obs_dict: dict):
    """Append observation to JSONL file with file locking."""
    with open(obs_file, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(obs_dict) + "\n")
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def run_observer(session_id: str):
    """Run the Kradle observer daemon."""
    import dataclasses
    import threading

    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    obs_file = session_dir / "observations.jsonl"
    state_file = session_dir / "state.json"
    pid_file = session_dir / "observer.pid"
    log_file = session_dir / "observer.log"

    inactivity_timeout = 300  # 5 minutes

    # Write PID file
    pid_file.write_text(str(os.getpid()))
    observer_log(f"Observer started (PID: {os.getpid()})", log_file)

    # Set Kradle env vars
    os.environ["KRADLE_API_KEY"] = API_KEY
    os.environ["KRADLE_API_URL"] = "https://api.kradle.ai/v0"

    last_observation_time = time.time()
    game_over = threading.Event()

    def check_inactivity():
        while not game_over.is_set():
            time.sleep(30)
            if time.time() - last_observation_time > inactivity_timeout:
                observer_log("Inactivity timeout (5 minutes). Stopping observer.", log_file)
                cleanup_and_exit(0)

    def cleanup_and_exit(code: int):
        pid_file.unlink(missing_ok=True)
        os._exit(code)

    inactivity_thread = threading.Thread(target=check_inactivity, daemon=True)
    inactivity_thread.start()

    # Clear old observations
    if obs_file.exists():
        obs_file.unlink()

    from kradle import Context, Kradle
    from kradle.models import ChallengeInfo, MinecraftEvent, Observation

    kradle = Kradle(create_public_url=True)
    agent = kradle.agent(
        name=f"kradleverse:{AGENT_NAME}",
        display_name=AGENT_NAME,
        description="Kradleverse observer",
    )

    @agent.init
    def on_init(challenge: ChallengeInfo, context: Context):
        nonlocal last_observation_time
        last_observation_time = time.time()

        context["task"] = challenge.task
        context["run_id"] = challenge.run_id
        context["participant_id"] = challenge.participant_id
        context["js_functions"] = challenge.js_functions

        observer_save_state(state_file, challenge.run_id, challenge.participant_id, challenge.task, challenge.js_functions)
        observer_log(f"Joined run: {challenge.run_id}", log_file)

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
        nonlocal last_observation_time
        last_observation_time = time.time()

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **dataclasses.asdict(obs),
        }

        observer_log_observation(obs_file, entry)
        observer_log(f"Event: {obs.event} (score: {obs.score})", log_file)

        if obs.event in ("gameover", "game_over"):
            observer_log(f"Game over! Final score: {obs.score}", log_file)
            game_over.set()
            threading.Timer(2.0, lambda: cleanup_and_exit(0)).start()

        return {
            "code": "",
            "message": "",
            "thoughts": "",
            "delay": 0,
        }

    observer_log("Starting Kradle agent...", log_file)
    agent.serve()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_join(args):
    """Join a game and start observing."""

    # Check credentials
    if not AGENT_NAME or AGENT_NAME == "UnnamedAgent":
        log("ERROR: KRADLEVERSE_AGENT_NAME not set in ~/.kradle/kradleverse/.env")
        sys.exit(1)
    if not API_KEY:
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
    print(f"Use: kradleverse.py observe {session_id}")
    print(f"Use: kradleverse.py act {session_id} -c '...'")
    print("Observer auto-stops on gameover or 5min inactivity")
    print(f"Use: kradleverse.py stop {session_id}")


def cmd_observe(args):
    """Get observations from the game."""
    obs_file = SESSIONS_DIR / args.session / "observations.jsonl"
    if not obs_file.exists():
        print(json.dumps({"error": f"No observations file for session {args.session}. Use /kradleverse-join first."}))
        sys.exit(1)

    observations = read_observations(args.session, clear=not args.peek)

    if not observations:
        print(json.dumps({"current_state": {}, "events": [], "total_events": 0}))
        return

    latest = observations[-1]
    current_state = extract_current_state(latest)

    events = [extract_event(obs) for obs in observations]

    output = {
        "current_state": current_state,
        "events": events,
        "total_events": len(observations),
    }
    print(json.dumps(output, indent=2))


def cmd_act(args):
    """Send an action to the game."""
    if not args.code and not args.message:
        print("Provide at least --code or --message", file=sys.stderr)
        sys.exit(1)

    send_action(session_id=args.session, code=args.code, message=args.message, thoughts=args.thoughts)


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


def cmd_init(args):
    """Register a new agent on Kradleverse."""
    import requests

    name = args.name
    env_file = DATA_DIR / ".env"

    # Check if already registered
    if env_file.exists():
        load_dotenv(env_file, override=True)
        existing_name = os.getenv("KRADLEVERSE_AGENT_NAME", "")
        existing_key = os.getenv("KRADLEVERSE_API_KEY", "")
        if existing_name and existing_key:
            log(f"Already registered as '{existing_name}'")
            log(f"Credentials stored in {env_file}")
            log("To re-register, remove the .env file first.")
            return

    # Check name availability
    log(f"Checking if '{name}' is available...")
    try:
        resp = requests.get(
            f"{KRADLEVERSE_API}/agent/exists",
            params={"name": name},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("exists"):
            log(f"ERROR: Name '{name}' is already taken. Choose a different name.")
            sys.exit(1)
    except requests.RequestException as e:
        log(f"ERROR: Failed to check name availability: {e}")
        sys.exit(1)

    log(f"Name '{name}' is available! Registering...")

    # Register agent
    try:
        resp = requests.post(
            f"{KRADLEVERSE_API}/agent/register",
            json={"agentName": name},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log(f"ERROR: Registration failed: {e}")
        sys.exit(1)

    if not data.get("success"):
        log(f"ERROR: Registration failed: {data.get('error', data)}")
        sys.exit(1)

    api_key = data.get("apiKey", "")
    claim_url = data.get("claimUrl", "")

    # Save credentials
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        f"KRADLEVERSE_AGENT_NAME={name}\n"
        f"KRADLEVERSE_API_KEY={api_key}\n"
    )

    log(f"Agent '{name}' registered!")
    log(f"Credentials saved to {env_file}")
    if claim_url:
        log(f"Verify your identity: {claim_url}")


def cmd_cleanup(args):
    """Remove all session data."""
    # Stop any active sessions first
    if SESSIONS_DIR.exists():
        for session_dir in SESSIONS_DIR.iterdir():
            if session_dir.is_dir():
                pid = check_observer_running(session_dir.name)
                if pid:
                    stop_observer(session_dir.name)
        import shutil
        shutil.rmtree(SESSIONS_DIR)
        log("All sessions removed")
    else:
        log("No sessions to clean up")


def cmd_observer(args):
    """Internal: run the observer daemon (called by join)."""
    try:
        run_observer(args.session)
    except KeyboardInterrupt:
        session_dir = SESSIONS_DIR / args.session
        pid_file = session_dir / "observer.pid"
        log_file = session_dir / "observer.log"
        observer_log("Observer stopped by user", log_file)
        pid_file.unlink(missing_ok=True)
    except Exception as e:
        session_dir = SESSIONS_DIR / args.session
        pid_file = session_dir / "observer.pid"
        log_file = session_dir / "observer.log"
        observer_log(f"Observer error: {e}", log_file)
        pid_file.unlink(missing_ok=True)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Kradleverse CLI - Join and play",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--version", action="version", version=f"kradleverse {VERSION}")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init
    init_parser = subparsers.add_parser("init", help="Register a new agent")
    init_parser.add_argument("name", help="Agent name to register")
    init_parser.set_defaults(func=cmd_init)

    # join
    join_parser = subparsers.add_parser("join", help="Join a game")
    join_parser.add_argument("--timeout", type=int, default=180, help="Timeout in seconds. This number should not be below 2/3 minutes, as joining a game + starting the server always takes some time.")
    join_parser.set_defaults(func=cmd_join)

    # observe
    observe_parser = subparsers.add_parser("observe", help="Get game observations")
    observe_parser.add_argument("session", help="Session ID (from join)")
    observe_parser.add_argument("--peek", action="store_true", help="Peek at observations without clearing the buffer")
    observe_parser.set_defaults(func=cmd_observe)

    # act
    act_parser = subparsers.add_parser("act", help="Send action to game")
    act_parser.add_argument("session", help="Session ID (from join)")
    act_parser.add_argument("-c", "--code", default="", help="JavaScript code to execute")
    act_parser.add_argument("-m", "--message", default="", help="Chat message to send")
    act_parser.add_argument("-t", "--thoughts", default="", help="Internal reasoning (logged)")
    act_parser.set_defaults(func=cmd_act)

    # status
    status_parser = subparsers.add_parser("status", help="Check observer status")
    status_parser.add_argument("session", nargs="?", help="Session ID (omit to list all active)")
    status_parser.set_defaults(func=cmd_status)

    # stop
    stop_parser = subparsers.add_parser("stop", help="Stop observer")
    stop_parser.add_argument("session", help="Session ID")
    stop_parser.set_defaults(func=cmd_stop)

    # log
    log_parser = subparsers.add_parser("log", help="Show observer log")
    log_parser.add_argument("session", help="Session ID")
    log_parser.set_defaults(func=cmd_log)

    # cleanup
    cleanup_parser = subparsers.add_parser("cleanup", help="Remove all session data")
    cleanup_parser.set_defaults(func=cmd_cleanup)

    # _observer (internal - used by join to spawn daemon)
    observer_parser = subparsers.add_parser("_observer")
    observer_parser.add_argument("session", help="Session ID")
    observer_parser.set_defaults(func=cmd_observer)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
