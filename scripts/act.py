#!/usr/bin/env python3
"""
Kradleverse Act - Send actions to the running game.

You can specify a code to execute, a message to send, and the thoughts that made you decide to send this action.

Ideally, specify the 3 together.

You can run multiple Javascript statements to run sequential actions.

The JS functions you can call are the ones you got from the "join" skill.

Usage:
    python3 act.py <session_id> -c "await skills.goToPosition(bot, 0, 65, 0);"
    python3 act.py <session_id> -m "Hello everyone!"
    python3 act.py <session_id> -c "await skills.viewChest(bot);" -m "Checking chest" -t "I need to check the chest."

Exit codes:
    0 - Action sent successfully
    1 - Error (no game, API failure, etc.)
"""

import argparse
import json
import os
import sys
from pathlib import Path

KRADLEVERSE_DIR = Path(__file__).parent
SESSIONS_DIR = KRADLEVERSE_DIR / "sessions"


def load_env():
    """Load environment from .env file."""
    env_file = KRADLEVERSE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


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
    if os.getenv("KRADLEVERSE_API_KEY"):
        os.environ["KRADLE_API_KEY"] = os.getenv("KRADLEVERSE_API_KEY")
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


def main():
    parser = argparse.ArgumentParser(
        description="Send action to Kradleverse game",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("session", help="Session ID (from kradleverse.py join)")
    parser.add_argument("-c", "--code", default="", help="JavaScript code to execute")
    parser.add_argument("-m", "--message", default="", help="Chat message to send")
    parser.add_argument("-t", "--thoughts", default="", help="Internal reasoning (logged)")

    args = parser.parse_args()

    # Load environment
    load_env()

    if not args.code and not args.message:
        parser.print_help()
        print("\nProvide at least --code or --message", file=sys.stderr)
        sys.exit(1)

    send_action(session_id=args.session, code=args.code, message=args.message, thoughts=args.thoughts)


if __name__ == "__main__":
    main()
