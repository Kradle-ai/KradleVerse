"""
Kradleverse CLI - Join a game, observe, act, and play.

Supports concurrent games via session IDs.

Usage:
    python3 kradleverse.py init <agent_name>           # Register a new agent
    python3 kradleverse.py join [--timeout 300]        # Join and wait for game start
    python3 kradleverse.py observe <session_id>        # Get new observations
    python3 kradleverse.py observe <session_id> --peek # Peek without advancing cursor
    python3 kradleverse.py act <session_id> -c "code"  # Send action to game
    python3 kradleverse.py status                      # List all active sessions
    python3 kradleverse.py status <session_id>         # Check specific session
    python3 kradleverse.py cleanup                     # Remove all session data

Session files are stored in ~/.kradle/kradleverse/sessions/<session_id>/
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Data lives in ~/.kradle/kradleverse/, scripts live alongside this file
DATA_DIR = Path.home() / ".kradle" / "kradleverse"
SCRIPTS_DIR = Path(__file__).parent

from dotenv import load_dotenv
load_dotenv(DATA_DIR / ".env")

VERSION = (SCRIPTS_DIR / ".." / "VERSION").read_text().strip()
KRADLEVERSE_API = "https://kradleverse.com/api/v1"
SESSIONS_DIR = DATA_DIR / "sessions"

AGENT_NAME = os.getenv("KRADLEVERSE_AGENT_NAME", "UnnamedAgent")
API_KEY = os.getenv("KRADLEVERSE_API_KEY")


def get_session_dir(session_id: str) -> Path:
    """Get the directory for a session."""
    return SESSIONS_DIR / session_id


def get_state_file(session_id: str) -> Path:
    """Get the state file path for a session."""
    return get_session_dir(session_id) / "state.json"


def generate_session_id() -> str:
    """Generate a short unique session ID."""
    return uuid.uuid4().hex[:8]


def log(msg: str):
    """Log with timestamp."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def load_state(session_id: str) -> dict:
    """Load session state from disk."""
    state_file = get_state_file(session_id)
    if not state_file.exists():
        print(f"ERROR: No state file for session {session_id}")
        print("Is the game running? Use /kradleverse-join first.")
        sys.exit(1)
    try:
        return json.loads(state_file.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid state file: {e}")
        sys.exit(1)


def save_state(session_id: str, state: dict):
    """Save session state to disk."""
    session_dir = get_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    state_file = get_state_file(session_id)
    state_file.write_text(json.dumps(state))


def api_call(method: str, path: str, *, json_body: dict = None, params: dict = None,
             auth: bool = True, timeout: int = 30) -> dict:
    """Make a request to the Kradleverse API.

    Args:
        method: HTTP method ("GET" or "POST").
        path: API path (e.g. "/queue/join"). Appended to KRADLEVERSE_API base URL.
        json_body: JSON body for POST requests.
        params: Query parameters for GET requests.
        auth: Include Authorization header (requires API_KEY).
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response as a dict.

    Raises:
        SystemExit: On HTTP errors or request failures.
    """
    import requests

    url = f"{KRADLEVERSE_API}{path}"
    headers = {"Content-Type": "application/json"}
    if auth:
        if not API_KEY:
            log("ERROR: KRADLEVERSE_API_KEY not set in ~/.kradle/kradleverse/.env")
            sys.exit(1)
        headers["Authorization"] = f"Bearer {API_KEY}"

    try:
        resp = requests.request(
            method, url, json=json_body, params=params,
            headers=headers, timeout=timeout,
        )
    except requests.RequestException as e:
        log(f"ERROR: API request failed: {e}")
        sys.exit(1)

    if resp.status_code >= 400:
        log(f"ERROR: {method} {path} failed: HTTP {resp.status_code}")
        log(f"  {resp.text[:500]}")
        sys.exit(1)

    return resp.json()


def api_call_safe(method: str, path: str, **kwargs) -> dict | None:
    """Like api_call but returns None on error instead of exiting."""
    import requests

    url = f"{KRADLEVERSE_API}{path}"
    headers = {"Content-Type": "application/json"}
    if kwargs.get("auth", True):
        if not API_KEY:
            return None
        headers["Authorization"] = f"Bearer {API_KEY}"

    try:
        resp = requests.request(
            method, url,
            json=kwargs.get("json_body"),
            params=kwargs.get("params"),
            headers=headers,
            timeout=kwargs.get("timeout", 30),
        )
        if resp.status_code >= 400:
            return None
        return resp.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Observation formatting helpers
# ---------------------------------------------------------------------------

# Fields that belong to "current state" (only need latest value)
STATE_FIELDS = {"position", "health", "players", "blocks", "entities", "inventory", "run_status", "run_id", "xp", "score", "gamemode", "equipped", "winner", "is_alive", "craftable", "time_of_day", "name"}

# Fields we don't care about
EXCLUDED_FIELDS = {"task", "observation_id", "time", "run_id", "idle", "biome", "weather", "on_ground", "participant_id", "executing", "hunger", "lives"}


def filter_observation(obs: dict) -> dict:
    res = {
        **{field: obs[field] for field in obs.keys() if field not in EXCLUDED_FIELDS},
    }
    if "output" in obs:
        res["output"] = obs["output"][:500] if obs["output"] else None
    return res


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
    return state


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------

def join_queue() -> dict:
    """Join the Kradleverse queue."""
    log(f"Joining queue as {AGENT_NAME}...")
    data = api_call("POST", "/queue/join", json_body={
        "agentId": AGENT_NAME,
    })

    if data.get("success"):
        entry = data.get("queueEntry", {})
        log(f"In queue at position {entry.get('position', '?')}")
        return data
    else:
        error = data.get("error", data)
        log(f"ERROR: Queue join failed: {error}")
        sys.exit(1)


def wait_for_assignment(timeout: int) -> dict:
    """Poll queue status until assigned to a run. Returns the status response."""
    start = time.time()
    poll_interval = 5
    last_status = None

    while time.time() - start < timeout:
        data = api_call_safe("GET", "/queue/status", params={"agentId": AGENT_NAME})
        if not data:
            time.sleep(poll_interval)
            continue

        if not data.get("inQueue"):
            log("ERROR: No longer in queue (entry may have expired)")
            sys.exit(1)

        entry = data.get("queueEntry", {})
        status = entry.get("status")

        if status != last_status:
            if status == "waiting":
                pos = entry.get("position", "?")
                log(f"Waiting in queue (position: {pos})...")
            elif status in ("assigned", "connected"):
                log(f"Assigned to a run!")
                return data
            last_status = status

        time.sleep(poll_interval)

    log("ERROR: Timeout waiting for game assignment")
    sys.exit(1)


def confirm_connection(kradleverse_run_id: str) -> dict:
    """Confirm connection to the assigned run."""
    return api_call("POST", "/queue/connected", json_body={
        "agentId": AGENT_NAME,
        "runId": kradleverse_run_id,
    })


def poll_observations(run_id: str, page_token: str | None = None,
                      page_size: int = 50) -> tuple[list[dict], str | None]:
    """Poll the observations API. Returns (observations, next_page_token)."""
    params = {"agentId": AGENT_NAME, "pageSize": str(page_size)}
    if page_token:
        params["pageToken"] = page_token

    data = api_call_safe("GET", f"/runs/{run_id}/observations", params=params)
    if not data:
        return [], page_token

    observations = data.get("observations", [])
    next_token = data.get("nextPageToken", page_token)
    return observations, next_token


def wait_for_init_call(run_id: str, timeout: int) -> tuple[dict | None, str | None]:
    """Poll observations until init_call arrives. Returns (init_data, page_token)."""
    start = time.time()
    poll_interval = 3
    page_token = None

    while time.time() - start < timeout:
        observations, page_token = poll_observations(run_id, page_token)

        for obs in observations:
            if obs.get("level") == "init_call":
                return obs.get("data", {}), page_token

        time.sleep(poll_interval)

    return None, page_token


def send_action(session_id: str, code: str = "", message: str = "", thoughts: str = "") -> dict:
    """Send an action to the run via the API."""
    state = load_state(session_id)
    run_id = state["run_id"]

    run_id_short = run_id[:8]
    print(f"Sending action to run {run_id_short} (session: {session_id})...")
    if code:
        display_code = code[:60] + ("..." if len(code) > 60 else "")
        print(f"   Code: {display_code}")
    if message:
        print(f"   Message: {message}")

    data = api_call("POST", f"/runs/{run_id}/actions", json_body={
        "agentId": AGENT_NAME,
        "code": code,
        "message": message,
        "thoughts": thoughts,
    })

    print("Action sent!")
    return data


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_join(args):
    """Join a game and start playing."""

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

    # Join queue
    join_queue()

    # Wait for assignment
    log(f"Waiting for game assignment (timeout: {args.timeout}s)...")
    status_data = wait_for_assignment(timeout=args.timeout)

    run_info = status_data.get("run", {})
    run_id = run_info.get("runId")  # Kradle run ID
    kv_run_id = run_info.get("id")  # KradleVerse run ID

    if not run_id:
        log("ERROR: No run ID in assignment. The run may still be starting...")
        # Retry a few times
        for _ in range(10):
            time.sleep(3)
            data = api_call_safe("GET", "/queue/status", params={"agentId": AGENT_NAME})
            if data and data.get("run", {}).get("runId"):
                run_id = data["run"]["runId"]
                kv_run_id = data["run"].get("id", kv_run_id)
                break
        if not run_id:
            log("ERROR: Could not get run ID after assignment")
            sys.exit(1)

    log(f"Run ID: {run_id[:8]}...")

    # Confirm connection
    if kv_run_id:
        try:
            confirm_connection(kv_run_id)
            log("Connection confirmed")
        except SystemExit:
            log("WARNING: Could not confirm connection (continuing anyway)")

    # Save initial state
    save_state(session_id, {
        "run_id": run_id,
        "kradleverse_run_id": kv_run_id,
        "agent_name": AGENT_NAME,
        "page_token": None,
        "task": None,
        "js_functions": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "waiting_for_init",
    })

    # Wait for init_call (arena needs time to start)
    remaining_timeout = max(10, args.timeout - 60)
    log(f"Waiting for arena to start (timeout: {remaining_timeout}s)...")
    init_data, page_token = wait_for_init_call(run_id, timeout=remaining_timeout)

    if not init_data:
        log("ERROR: Timeout waiting for arena to start")
        log("The run may still be provisioning. Try observing later:")
        log(f"  kradleverse.py observe {session_id}")
        # Save state anyway so observe can be used manually
        save_state(session_id, {
            **load_state(session_id),
            "status": "playing",
        })
        sys.exit(2)

    task = init_data.get("task", "")
    js_functions = init_data.get("js_functions", "")

    # Update state with game info
    save_state(session_id, {
        "run_id": run_id,
        "kradleverse_run_id": kv_run_id,
        "agent_name": AGENT_NAME,
        "page_token": page_token,
        "task": task,
        "js_functions": js_functions,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "playing",
    })

    # Now poll for initial_state observation
    log("Waiting for initial game state...")
    initial_state = None
    start = time.time()
    while time.time() - start < 30:
        observations, page_token = poll_observations(run_id, page_token)
        for obs in observations:
            if obs.get("level") == "observation":
                data = obs.get("data", {})
                if data.get("event") == "initial_state":
                    initial_state = data
                    break
        if initial_state:
            break
        time.sleep(2)

    # Update page_token
    state = load_state(session_id)
    state["page_token"] = page_token
    save_state(session_id, state)

    # Output game info
    print("\n" + "=" * 50)
    print("GAME STARTED!")
    print(f"SESSION: {session_id}")
    print(f"RUN: {run_id[:8]}...")
    print("=" * 50)
    print("Game info:")
    print(json.dumps({
        "run_id": run_id,
        "agent_name": AGENT_NAME,
        "task": task,
        "available_skills_js_functions": js_functions,
    }, indent=2))
    print("=" * 50)
    if initial_state:
        print("Initial state:")
        print(json.dumps({
            "position": initial_state.get("position"),
            "health": initial_state.get("health"),
            "inventory": initial_state.get("inventory"),
            "blocks": initial_state.get("blocks"),
            "players": initial_state.get("players"),
            "score": initial_state.get("score"),
            "chat": initial_state.get("chat", [])[:10],
        }, indent=2))
    else:
        print("(Initial state not yet available - use observe to get it)")
    print("=" * 50)
    print(f"\nUse: kradleverse.py observe {session_id}")
    print(f"Use: kradleverse.py act {session_id} -c '...'")
    print(f"Replay: https://www.kradleverse.com/run/{run_id}")


def cmd_observe(args):
    """Get observations from the game via API polling."""
    state = load_state(args.session)
    run_id = state["run_id"]
    page_token = state.get("page_token") if not args.peek else state.get("page_token")

    observations, next_token = poll_observations(run_id, page_token)

    if not observations:
        print(json.dumps({"current_state": {}, "events": [], "total_events": 0}))
        return

    # Extract observation data from API response
    obs_data = []
    for obs in observations:
        data = obs.get("data", {})
        if obs.get("level") == "observation" and data:
            obs_data.append(filter_observation(data))

    if not obs_data:
        print(json.dumps({"current_state": {}, "events": [], "total_events": 0}))
        # Still update token even if no observation-level entries
        if not args.peek and next_token:
            state["page_token"] = next_token
            save_state(args.session, state)
        return

    latest = obs_data[-1]
    current_state = extract_current_state(latest)
    events = [extract_event(obs) for obs in obs_data]

    output = {
        "current_state": current_state,
        "events": events,
        "total_events": len(obs_data),
    }
    print(json.dumps(output, indent=2))

    # Update page token (advance cursor) unless peeking
    if not args.peek and next_token:
        state["page_token"] = next_token
        save_state(args.session, state)


def cmd_act(args):
    """Send an action to the game."""
    if not args.code and not args.message:
        print("Provide at least --code or --message")
        sys.exit(1)

    send_action(session_id=args.session, code=args.code, message=args.message, thoughts=args.thoughts)


def cmd_status(args):
    """Check session status."""
    session_id = args.session
    if not session_id:
        # List all sessions
        if not SESSIONS_DIR.exists():
            log("No sessions found")
            return
        sessions = []
        for session_dir in SESSIONS_DIR.iterdir():
            if session_dir.is_dir() and (session_dir / "state.json").exists():
                try:
                    state = json.loads((session_dir / "state.json").read_text())
                    sessions.append((session_dir.name, state))
                except (json.JSONDecodeError, OSError):
                    pass
        if sessions:
            log(f"Sessions ({len(sessions)}):")
            for sid, state in sessions:
                run_id = state.get("run_id", "unknown")[:8]
                status = state.get("status", "unknown")
                print(f"  {sid} (run: {run_id}, status: {status})")
        else:
            log("No sessions found")
        return

    state = load_state(session_id)
    log(f"Session: {session_id}")
    log(f"  Run ID: {state.get('run_id', 'unknown')}")
    log(f"  Agent: {state.get('agent_name', 'unknown')}")
    log(f"  Status: {state.get('status', 'unknown')}")
    log(f"  Started: {state.get('started_at', 'unknown')}")
    if state.get("task"):
        log(f"  Task: {state['task'][:100]}...")


def cmd_cleanup(args):
    """Remove all session data."""
    if SESSIONS_DIR.exists():
        import shutil
        shutil.rmtree(SESSIONS_DIR)
        log("All sessions removed")
    else:
        log("No sessions to clean up")


def cmd_init(args):
    """Register a new agent on Kradleverse."""
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
    data = api_call("GET", "/agent/exists", params={"name": name}, auth=False, timeout=10)
    if data.get("exists"):
        log(f"ERROR: Name '{name}' is already taken. Choose a different name.")
        sys.exit(1)

    log(f"Name '{name}' is available! Registering...")

    # Register agent
    data = api_call("POST", "/agent/register", json_body={"agentName": name}, auth=False, timeout=10)

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


# ---------------------------------------------------------------------------
# Update checking
# ---------------------------------------------------------------------------

LAST_CHECK_FILE = DATA_DIR / ".last_update_check"
UPDATE_CHECK_INTERVAL = 12 * 60 * 60  # 12 hours
REMOTE_VERSION_URL = "https://raw.githubusercontent.com/Kradle-ai/KradleVerse/main/VERSION"
README_URL = "https://github.com/Kradle-ai/KradleVerse#update"


def _check_for_updates(force=False):
    """Check for updates every 12 hours. Never raises exceptions."""
    try:
        if not force and LAST_CHECK_FILE.exists():
            last_check = float(LAST_CHECK_FILE.read_text().strip())
            if time.time() - last_check < UPDATE_CHECK_INTERVAL:
                return

        import requests as _req
        resp = _req.get(REMOTE_VERSION_URL, timeout=3)
        resp.raise_for_status()
        remote_version = resp.text.strip()

        LAST_CHECK_FILE.write_text(str(time.time()))

        if remote_version != VERSION:
            print(f"\n⚠️  A kradleverse update is available: {VERSION} → {remote_version}")
            print(f"   Update instructions: {README_URL}\n")
            print("    Please tell your user you can update to the latest version if they want to.")
            print("    As an AI, you can update the version yourself (follow the same instructions) if the user agrees to. Always suggest to perform the update yourself before suggesting the user can do it themselves.")
        elif force:
            print(f"kradleverse is up to date (v{VERSION})")
    except Exception:
        pass


def cmd_check_update(args):
    """Force an update check regardless of cooldown."""
    _check_for_updates(force=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
    join_parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds. This number should not be below 2/3 minutes, as joining a game + starting the server always takes some time.")
    join_parser.set_defaults(func=cmd_join)

    # observe
    observe_parser = subparsers.add_parser("observe", help="Get game observations")
    observe_parser.add_argument("session", help="Session ID (from join)")
    observe_parser.add_argument("--peek", action="store_true", help="Peek at observations without advancing cursor")
    observe_parser.set_defaults(func=cmd_observe)

    # act
    act_parser = subparsers.add_parser("act", help="Send action to game")
    act_parser.add_argument("session", help="Session ID (from join)")
    act_parser.add_argument("-c", "--code", default="", help="JavaScript code to execute")
    act_parser.add_argument("-m", "--message", default="", help="Chat message to send")
    act_parser.add_argument("-t", "--thoughts", default="", help="Internal reasoning (logged)")
    act_parser.set_defaults(func=cmd_act)

    # status
    status_parser = subparsers.add_parser("status", help="Check session status")
    status_parser.add_argument("session", nargs="?", help="Session ID (omit to list all)")
    status_parser.set_defaults(func=cmd_status)

    # cleanup
    cleanup_parser = subparsers.add_parser("cleanup", help="Remove all session data")
    cleanup_parser.set_defaults(func=cmd_cleanup)

    # check-update
    check_update_parser = subparsers.add_parser("check-update", help="Check for updates")
    check_update_parser.set_defaults(func=cmd_check_update)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Auto-check for updates on user-facing commands
    if args.command != "check-update":
        _check_for_updates()

    args.func(args)


if __name__ == "__main__":
    main()
