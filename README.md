# slack-linear-digest

Daily Slack DM with:
- Linear issues assigned to you, due today or overdue
- Slack DMs/MPIMs where the last message isn't from you
- Channel mentions of you that you haven't reacted to or replied to

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # or rely on ~/.env.shared
```

### Slack app scopes

Create a Slack app (or reuse one) with:
- **User token** (`xoxp-…`): `users:read`, `users:read.email`, `im:history`, `im:read`, `mpim:history`, `mpim:read`, `channels:history`, `groups:history`, `search:read`
- **Bot token** (`xoxb-…`): `chat:write`, `im:write`

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
