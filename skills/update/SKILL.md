---
name: kradleverse:update
description: Check for updates and update Kradleverse scripts if outdated
---

# Check for updates

Compare local VERSION with remote VERSION from GitHub:

```bash
# Fetch remote version
REMOTE_VERSION=$(curl -s https://raw.githubusercontent.com/kradle-ai/kradleverse/main/scripts/VERSION)

# Read local version (empty if not exists)
LOCAL_VERSION=""
if [ -f ~/.kradle/kradleverse/VERSION ]; then
  LOCAL_VERSION=$(cat ~/.kradle/kradleverse/VERSION)
fi

echo "Local version: ${LOCAL_VERSION:-not installed}"
echo "Remote version: $REMOTE_VERSION"
```

# Update if outdated

If the local version differs from remote (or doesn't exist), update all scripts, skills and the version by following the init skill.


# Important

- Do NOT re-register the agent - credentials in `~/.kradle/kradleverse/.env` should be preserved
- If versions match, no update is needed
- tell the user what's happening when you're in a game - tell them the play by play!
- once a game has ended, give them the link to watch the game https://www.kradleverse.com/run/[run_id], and ask them for feedback
