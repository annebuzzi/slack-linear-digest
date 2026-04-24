"""Daily digest: Linear tasks due today + Slack messages I haven't replied to.

Sends a single DM to the user on Slack.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path.home() / ".env.shared")
load_dotenv()  # local .env overrides

LINEAR_API_KEY = os.environ["LINEAR_API_KEY"]
SLACK_USER_TOKEN = os.environ.get("SLACK_USER_TOKEN") or os.environ["SLACK_SUPPORTBOT_USER_TOKEN"]
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN") or os.environ["SLACK_SUPPORTBOT_BOT_TOKEN"]
MY_EMAIL = os.environ.get("MY_EMAIL", "anne.buzzi@archive.com")
MY_SLACK_USER_ID = os.environ.get("MY_SLACK_USER_ID", "U06SZT6KZ7C")
DIGEST_CHANNEL = os.environ.get("DIGEST_CHANNEL", "anne")
# For these senders, an emoji reaction from me does NOT count as a response.
REACTION_NOT_A_REPLY_USERS = {
    "U09LN7NC479",  # Luiza
    "U014H03PG4C",  # Kylie
}
IGNORED_LOOKBACK_HOURS = int(os.environ.get("IGNORED_LOOKBACK_HOURS", "48"))

LINEAR_URL = "https://api.linear.app/graphql"
SLACK_URL = "https://slack.com/api"


# ---------- Linear ----------

def linear_query(query: str, variables: dict | None = None) -> dict:
    r = requests.post(
        LINEAR_URL,
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": LINEAR_API_KEY, "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"Linear error: {data['errors']}")
    return data["data"]


def fetch_due_today() -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    query = """
    query DueToday($today: TimelessDateOrDuration!) {
      viewer {
        assignedIssues(
          filter: {
            dueDate: { lte: $today }
            state: { type: { nin: ["completed", "canceled"] } }
          }
          first: 50
        ) {
          nodes {
            identifier
            title
            url
            dueDate
            priority
            state { name }
          }
        }
      }
    }
    """
    data = linear_query(query, {"today": today})
    issues = data["viewer"]["assignedIssues"]["nodes"]
    issues.sort(key=lambda i: (i.get("dueDate") or "", -i.get("priority", 0)))
    return issues


# ---------- Slack ----------

def slack_call(method: str, token: str, **params) -> dict:
    r = requests.get(
        f"{SLACK_URL}/{method}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method} error: {data.get('error')}")
    return data


def slack_post(method: str, token: str, **payload) -> dict:
    r = requests.post(
        f"{SLACK_URL}/{method}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method} error: {data.get('error')}")
    return data


def resolve_user_id(email: str) -> str:
    if MY_SLACK_USER_ID:
        return MY_SLACK_USER_ID
    return slack_call("users.lookupByEmail", SLACK_USER_TOKEN, email=email)["user"]["id"]


def fetch_ignored_dms(my_id: str, oldest_ts: float) -> list[dict]:
    """DMs/MPIMs where the most recent message is from someone else (I haven't replied)."""
    ignored = []
    cursor = None
    while True:
        params = {"types": "im,mpim", "limit": 100, "exclude_archived": True}
        if cursor:
            params["cursor"] = cursor
        data = slack_call("conversations.list", SLACK_USER_TOKEN, **params)
        for conv in data.get("channels", []):
            try:
                hist = slack_call(
                    "conversations.history",
                    SLACK_USER_TOKEN,
                    channel=conv["id"],
                    limit=5,
                    oldest=str(oldest_ts),
                )
            except RuntimeError:
                continue
            msgs = [m for m in hist.get("messages", []) if m.get("type") == "message" and not m.get("subtype")]
            if not msgs:
                continue
            latest = msgs[0]
            if latest.get("user") == my_id or latest.get("bot_id"):
                continue
            sender = latest.get("user", "unknown")
            ignored.append({
                "channel_id": conv["id"],
                "is_mpim": conv.get("is_mpim", False),
                "user_id": sender,
                "text": latest.get("text", "")[:200],
                "ts": latest.get("ts"),
            })
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return ignored


def fetch_my_usergroup_ids(my_id: str) -> list[str]:
    """User group IDs (Sxxxx) that include me. Needs usergroups:read scope."""
    try:
        groups = slack_call("usergroups.list", SLACK_USER_TOKEN).get("usergroups", [])
    except RuntimeError as e:
        print(f"[warn] can't list usergroups: {e}", file=sys.stderr)
        return []
    mine = []
    for g in groups:
        if g.get("date_delete"):
            continue
        try:
            members = slack_call(
                "usergroups.users.list", SLACK_USER_TOKEN, usergroup=g["id"]
            ).get("users", [])
        except RuntimeError:
            continue
        if my_id in members:
            mine.append(g["id"])
    return mine


def fetch_ignored_mentions(my_id: str, oldest_ts: float) -> list[dict]:
    """Channel mentions of me (direct or via a user group) where I didn't react or reply in-thread."""
    group_ids = fetch_my_usergroup_ids(my_id)
    queries = [f"<@{my_id}>"] + [f"<!subteam^{gid}>" for gid in group_ids]
    matches_by_ts = {}
    for q in queries:
        try:
            data = slack_call(
                "search.messages",
                SLACK_USER_TOKEN,
                query=q,
                sort="timestamp",
                sort_dir="desc",
                count=50,
            )
        except RuntimeError as e:
            print(f"[warn] search '{q}': {e}", file=sys.stderr)
            continue
        for m in data.get("messages", {}).get("matches", []):
            ts = m.get("ts")
            if ts and ts not in matches_by_ts:
                matches_by_ts[ts] = m
    ignored = []
    for msg in matches_by_ts.values():
        try:
            ts = float(msg.get("ts", 0))
        except (TypeError, ValueError):
            continue
        if ts < oldest_ts:
            continue
        if msg.get("user") == my_id:
            continue
        if msg.get("bot_id") or msg.get("app_id") or msg.get("subtype") == "bot_message":
            continue  # ignore automation pings
        channel = msg.get("channel", {})
        if channel.get("is_im") or channel.get("is_mpim"):
            continue  # handled by DM pass
        # Check replies + reactions — if I participated, skip.
        if _i_responded(msg, my_id):
            continue
        ignored.append({
            "channel_id": channel.get("id"),
            "channel_name": channel.get("name"),
            "user_id": msg.get("user"),
            "text": (msg.get("text") or "")[:200],
            "ts": msg.get("ts"),
            "permalink": msg.get("permalink"),
        })
    return ignored


def _i_responded(msg: dict, my_id: str) -> bool:
    sender = msg.get("user")
    reaction_counts_for_sender = sender not in REACTION_NOT_A_REPLY_USERS

    def check_reactions(m: dict) -> bool:
        if not reaction_counts_for_sender:
            return False
        for r in m.get("reactions", []) or []:
            if my_id in (r.get("users") or []):
                return True
        return False

    if check_reactions(msg):
        return True
    if msg.get("reply_users") and my_id in msg["reply_users"]:
        return True

    channel_id = (msg.get("channel") or {}).get("id")
    ts = msg.get("ts")
    if not channel_id or not ts:
        return False

    # Fetch canonical message — gives reliable reactions + thread_ts.
    canon = None
    try:
        hist = slack_call(
            "conversations.history",
            SLACK_USER_TOKEN,
            channel=channel_id,
            oldest=ts,
            latest=ts,
            inclusive="true",
            limit=1,
        )
        canon = (hist.get("messages") or [{}])[0]
    except RuntimeError:
        pass

    if canon:
        if check_reactions(canon):
            return True
        if canon.get("reply_users") and my_id in canon["reply_users"]:
            return True

    thread_ts = (canon or {}).get("thread_ts") or msg.get("thread_ts") or ts
    try:
        thread = slack_call(
            "conversations.replies",
            SLACK_USER_TOKEN,
            channel=channel_id,
            ts=thread_ts,
            limit=200,
        )
    except RuntimeError:
        # Can't verify (likely scope issue on a private channel). Be conservative:
        # if the canonical message shows the thread has replies, assume I might have
        # responded and skip flagging to avoid noisy false positives.
        if canon and canon.get("reply_count", 0) > 0:
            return True
        return False

    replies = thread.get("messages", [])
    if len(replies) <= 1 and thread_ts == ts:
        return False
    for reply in replies:
        if reply.get("ts") == ts:
            continue
        if reply.get("user") == my_id:
            return True
    return False


# ---------- Formatting ----------

PRIORITY = {0: "—", 1: "🔴 Urgent", 2: "🟠 High", 3: "🟡 Med", 4: "🟢 Low"}


def build_message(issues: list[dict], ignored_dms: list[dict], ignored_mentions: list[dict]) -> str:
    today = datetime.now().strftime("%A, %b %d")
    lines = [f"*Daily digest — {today}*", ""]

    lines.append(f"*📋 Linear — due today or overdue ({len(issues)})*")
    if not issues:
        lines.append("_Nothing due. 🎉_")
    else:
        for i in issues:
            prio = PRIORITY.get(i.get("priority", 0), "—")
            due = i.get("dueDate") or "—"
            lines.append(f"• <{i['url']}|{i['identifier']}> {i['title']} · {prio} · due {due} · _{i['state']['name']}_")
    lines.append("")

    lines.append(f"*💬 Slack DMs waiting on you ({len(ignored_dms)})*")
    if not ignored_dms:
        lines.append("_Inbox zero on DMs. 🙌_")
    else:
        for d in ignored_dms[:15]:
            who = f"<@{d['user_id']}>"
            preview = d["text"].replace("\n", " ")
            lines.append(f"• {who}: {preview}")
    lines.append("")

    lines.append(f"*🔔 Mentions you haven't responded to ({len(ignored_mentions)})*")
    if not ignored_mentions:
        lines.append("_All caught up._")
    else:
        for m in ignored_mentions[:15]:
            link = m.get("permalink") or ""
            ch = f"#{m['channel_name']}" if m.get("channel_name") else ""
            preview = m["text"].replace("\n", " ")
            if link:
                lines.append(f"• <{link}|{ch}> <@{m['user_id']}>: {preview}")
            else:
                lines.append(f"• {ch} <@{m['user_id']}>: {preview}")

    return "\n".join(lines)


# ---------- Main ----------

def main() -> int:
    my_id = resolve_user_id(MY_EMAIL)
    oldest_ts = (datetime.now(timezone.utc) - timedelta(hours=IGNORED_LOOKBACK_HOURS)).timestamp()

    issues = fetch_due_today()
    try:
        ignored_dms = fetch_ignored_dms(my_id, oldest_ts)
    except RuntimeError as e:
        print(f"[warn] skipping DMs: {e}", file=sys.stderr)
        ignored_dms = []
    try:
        ignored_mentions = fetch_ignored_mentions(my_id, oldest_ts)
    except RuntimeError as e:
        print(f"[warn] skipping mentions: {e}", file=sys.stderr)
        ignored_mentions = []

    text = build_message(issues, ignored_dms, ignored_mentions)
    slack_post("chat.postMessage", SLACK_BOT_TOKEN, channel=DIGEST_CHANNEL, text=text, unfurl_links=False, unfurl_media=False)
    print(f"Sent digest: {len(issues)} issues, {len(ignored_dms)} DMs, {len(ignored_mentions)} mentions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
