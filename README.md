# slack-linear-digest

Daily Slack digest in `#anne` with:
- Linear issues assigned to you, due today or overdue
- Channel mentions of you (direct or via user group) that you haven't reacted to or replied to

DMs are intentionally not included — the app is shared with the team and
`im:*`/`mpim:*` scopes would give the bot read access to every team DM.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # or rely on ~/.env.shared
```

### Slack app scopes

- **User token** (`xoxp-…`): `search:read`, `channels:history`, `groups:history`, `usergroups:read`
- **Bot token** (`xoxb-…`): `chat:write`

### Linear

Personal API key from Linear → Settings → API. The `viewer` query resolves to you automatically.

## Run

```bash
python main.py
```

## Schedule (macOS)

Add to crontab (`crontab -e`) to run weekdays at 9am:

```
0 9 * * 1-5 cd ~/slack-linear-digest && .venv/bin/python main.py >> digest.log 2>&1
```
