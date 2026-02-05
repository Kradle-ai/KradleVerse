"""
Kradleverse Observe - Get latest observations (since last call).

Reads observations from the observer and outputs JSON with:
- current_state: position, health, inventory, nearby blocks, nearby entities, nearby players...
- events: timestamped stream of chat, commands, score changes etc...

Usage:
    python3 get_observations.py <session_id>           # Get all new observations (clears buffer)
    python3 get_observations.py <session_id> --peek    # Peek without clearing
"""

import argparse
import fcntl
import json
import sys
from pathlib import Path

DATA_DIR = Path.home() / ".kradle" / "kradleverse"
SESSIONS_DIR = DATA_DIR / "sessions"

# Fields that belong to "current state" (only need latest value)
STATE_FIELDS = {"position", "health", "players", "blocks", "entities", "inventory", "run_status", "run_id", "xp", "score", "gamemode", "equipped", "winner", "is_alive", "craftable", "time_of_day",  "name"}

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
            # Skip empty values
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
    # Also include score and run_status in state
    if "score" in obs:
        state["score"] = obs["score"]
    if "run_status" in obs:
        state["run_status"] = obs["run_status"]
    return state


def main():
    parser = argparse.ArgumentParser(
        description="Get Kradleverse game observations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("session", help="Session ID (from kradleverse.py join)")
    parser.add_argument("--peek", action="store_true",
                        help="Peek at observations without clearing the buffer")

    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
