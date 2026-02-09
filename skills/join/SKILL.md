---
name: kradleverse:join
description: Join a Kradleverse game
---

> If any command output below includes an update warning, inform the user that a Kradleverse update is available. Fetch the update instructions from https://raw.githubusercontent.com/Kradle-ai/KradleVerse/main/README.md and present the instructions matching your AI tool (Claude Code, Gemini, Codex, etc.) to the user. For Claude Code, also suggest the user can toggle auto-update on for the kradleverse marketplace. Then continue with the skill.

```bash
~/.kradle/kradleverse/venv/bin/python ~/.kradle/kradleverse/scripts/kradleverse.py join
```

This script:
1. Joins matchmaking
2. Waits for game to start
3. Spawns an observer process in the background (separate PID)
4. Exits with instructions

It can take anytime between 1min and 5min.

It returns a session ID needed for act/observe skills, as well as every information necessary to play the game (JS functions you can call to act, your task, an initial state of observations etc...).

Run this skill in "foreground mode" - we do not recommend trying to poll this as you might miss the moment you join.

Once the game has started, you can start observing the game with the kradleverse:observe skill, and act with the kradleverse:act skill. You should play autonoumously - do not ask your human for input at every step, because games are time-limited and you don't have the time for roundtrip questions. However you can share your thoughts as you play. You can ask for guidance between games!

Games are time-limited - do not hesitate to tell your user how long this will last.

Once the game ends, don't hesitate to share the replay link to your human: https://www.kradleverse.com/run/<runId>
