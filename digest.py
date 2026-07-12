#!/usr/bin/env python3
"""
Morning Digest — fetch RSS feeds, curate with the Claude API, publish to
your phone "app" (a GitHub Pages site) and send a push notification.

Usage:
    python digest.py --slot morning_7am
    python digest.py --slot arvo_2pm --dry-run   (prints instead of publishing)

Environment variables (GitHub Actions secrets):
    ANTHROPIC_API_KEY   Anthropic API key
    NTFY_TOPIC          your ntfy.sh topic name (acts like a password - keep it obscure)
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser
import yaml

PAGE_PATH = "docs/index.html"


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- fetch

def fetch_candidates(config, slot_cfg):
    wanted_modes = {
        m for m, key in [("core", "items_core"), ("stretch", "items_stretch"),
                         ("humour", "items_humour")]
        if slot_cfg.get(key, 0) > 0
    }
    max_per_feed = config["ai"].get("max_candidates_per_feed", 10)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=36)

    candidates = []
    for src in config["sources"]:
        if src["mode"] not in wanted_modes:
            continue
        try:
            feed = feedparser.parse(src["url"])
        except Exception as e:  # noqa: BLE001
            print(f"WARN: failed to parse {src['name']}: {e}", file=sys.stderr)
            continue
        for entry in feed.entries[:max_per_feed]:
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                dt = datetime(*published[:6], tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
            candidates.append({
                "source": src["name"],
                "mode": src["mode"],
                "title": entry.get("title", "(untitled)"),
                "summary": re.sub(r"<[^>]+>", "", entry.get("summary", "") or "")[:500],
                "link": entry.get("link", ""),
            })
    return candidates


# ---------------------------------------------------------------- curate

def curate(config, slot_cfg, candidates):
    counts = {k: slot_cfg.get(k, 0) for k in ("items_core", "items_stretch", "items_humour")}
    prompt = f"""You are curating a personal news digest slot called "{slot_cfg['label']}".

Reader's interest profile: {json.dumps(config['interests'], indent=1)}
Excluded topics: {json.dumps(config['exclusions'])}
Slot theme: {slot_cfg['theme']}

From the candidate items below, select exactly:
- {counts['items_core']} item(s) with mode "core"
- {counts['items_stretch']} item(s) with mode "stretch"
- {counts['items_humour']} item(s) with mode "humour"
(If a mode has too few candidates, select fewer rather than substituting.)

For each selected item write a fresh 2-3 sentence summary in your own words.
For stretch items, add one sentence on what assumption or common view it challenges.

Respond ONLY with a JSON array, no markdown fences, of objects:
{{"title": str, "source": str, "mode": str, "link": str, "summary": str}}

Candidates:
{json.dumps(candidates, indent=1)[:60000]}
"""
    body = json.dumps({
        "model": config["ai"]["model"],
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())

    text = "".join(b.get("text", "") for b in data["content"] if b["type"] == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


# ---------------------------------------------------------------- publish (the "app")

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#2E0920">
<link rel="manifest" href="manifest.json">
<title>Daily Digest</title>
<style>
  body {{ font-family: Georgia, serif; background: #FDFBF4; color: #1c1c1c;
         max-width: 620px; margin: 0 auto; padding: 20px 18px 60px; }}
  h1 {{ color: #2E0920; font-size: 26px; margin-bottom: 2px; }}
  .date {{ color: #999; font-size: 13px; margin-bottom: 28px; }}
  .slot {{ margin-bottom: 34px; }}
  .slot h2 {{ color: #8B2D62; font-size: 19px; border-bottom: 2px solid #FFF780;
             padding-bottom: 6px; }}
  .item {{ margin: 16px 0; }}
  .item .t {{ font-weight: 600; font-size: 16px; }}
  .item .t a {{ color: #8B2D62; text-decoration: none; }}
  .item .s {{ color: #888; font-size: 12px; margin: 2px 0 4px; }}
  .item .b {{ font-size: 14.5px; line-height: 1.5; }}
  .stretch {{ border-left: 3px solid #B84483; padding-left: 10px; }}
  .fin {{ color: #aaa; font-size: 12px; text-align: center; margin-top: 40px; }}
</style>
</head>
<body>
<h1>Daily Digest</h1>
<div class="date">{date}</div>
<!--SLOTS-->
{slots}
<div class="fin">That's all for now. Close the app. ☕</div>
</body>
</html>
"""


def render_slot_html(config, slot_id, slot_cfg, items):
    label_stretch = config["guardrails"].get("label_stretch_items", True)
    include_links = config["guardrails"].get("include_links", True)
    parts = [f'<div class="slot" id="{slot_id}"><h2>{html.escape(slot_cfg["label"])}</h2>']
    for it in items:
        cls = "item stretch" if (label_stretch and it["mode"] == "stretch") else "item"
        title = html.escape(it["title"])
        if include_links and it.get("link"):
            title = f'<a href="{html.escape(it["link"])}">{title}</a>'
        marker = " 🔄" if (label_stretch and it["mode"] == "stretch") else ""
        parts.append(
            f'<div class="{cls}"><div class="t">{title}{marker}</div>'
            f'<div class="s">{html.escape(it["source"])}</div>'
            f'<div class="b">{html.escape(it["summary"])}</div></div>'
        )
    parts.append("</div>")
    return "\n".join(parts)


def publish_page(config, slot_id, slot_cfg, items):
    """Append this slot to today's page; start a fresh page on a new day."""
    tz = ZoneInfo(config.get("timezone", "Australia/Melbourne"))
    today = datetime.now(tz).strftime("%A %d %B %Y")
    slot_html = render_slot_html(config, slot_id, slot_cfg, items)

    existing = ""
    if os.path.exists(PAGE_PATH):
        with open(PAGE_PATH, encoding="utf-8") as f:
            existing = f.read()

    if today in existing and "<!--SLOTS-->" in existing:
        # same day: insert this slot after the marker (newest at top? keep order: append)
        updated = existing.replace(
            '<div class="fin">', slot_html + '\n<div class="fin">', 1
        )
    else:
        updated = PAGE_TEMPLATE.format(date=today, slots=slot_html)

    os.makedirs(os.path.dirname(PAGE_PATH), exist_ok=True)
    with open(PAGE_PATH, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"Published {len(items)} items to {PAGE_PATH}")


def send_push(config, slot_cfg, items):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("No NTFY_TOPIC set - skipping push notification.")
        return
    page_url = config["delivery"].get("page_url", "")
    headline = items[0]["title"] if items else "Your digest is ready"
    body = f"{len(items)} new items. Top: {headline}"
    headers = {"Title": slot_cfg["label"], "Priority": "default", "Tags": "newspaper"}
    if page_url:
        headers["Click"] = page_url
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=body.encode(), headers=headers
    )
    urllib.request.urlopen(req, timeout=30)
    print("Push notification sent.")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = load_config()
    slot_cfg = config["slots"].get(args.slot)
    if not slot_cfg:
        sys.exit(f"Unknown slot '{args.slot}'. Options: {list(config['slots'])}")

    # Weekend handling: only the morning slot runs, using the weekend profile
    tz = ZoneInfo(config.get("timezone", "Australia/Melbourne"))
    weekend_cfg = config.get("weekend", {})
    if datetime.now(tz).weekday() >= 5 and weekend_cfg.get("mode") == "morning_only":
        if args.slot != "morning_7am":
            print("Weekend: skipping non-morning slot. Enjoy your day off the feed.")
            return
        slot_cfg = {**slot_cfg, **{k: v for k, v in weekend_cfg.items() if k != "mode"}}

    candidates = fetch_candidates(config, slot_cfg)
    print(f"Fetched {len(candidates)} candidate items.")
    if not candidates:
        sys.exit("No candidates fetched — check feed URLs / network.")

    items = curate(config, slot_cfg, candidates)
    print(f"Curated {len(items)} items.")

    if args.dry_run:
        for it in items:
            print(f"- [{it['mode']}] {it['title']} ({it['source']})\n  {it['summary']}\n")
        return

    publish_page(config, args.slot, slot_cfg, items)
    send_push(config, slot_cfg, items)


if __name__ == "__main__":
    main()
