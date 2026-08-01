#!/usr/bin/env python3
"""
Morning Digest v2 — goal-biased curation, 6 daily drips + Sunday check-in.

Usage:
    python digest.py --slot morning_7am
    python digest.py --slot weekly_checkin
    python digest.py --slot arvo_3pm --dry-run

Environment variables (GitHub Actions secrets):
    ANTHROPIC_API_KEY   Anthropic API key
    NTFY_TOPIC          ntfy.sh topic name
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo

import feedparser
import yaml

PAGE_PATH = "docs/index.html"


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- goals

def goal_context(config, today):
    lines = []
    for g in config.get("goals", []):
        try:
            deadline = date.fromisoformat(g["deadline"])
        except Exception:  # noqa: BLE001
            continue
        days_left = (deadline - today).days
        urgency = "PAST DUE" if days_left < 0 else f"{days_left} days away"
        lines.append(f"- {g['name']} (deadline {g['deadline']}, {urgency}): {g.get('note', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------- fetch

def extract_image(entry):
    """Pull a thumbnail/image URL out of an RSS entry, trying the common
    places feeds put them. Returns "" if nothing usable is found."""
    media = entry.get("media_thumbnail") or entry.get("media_content")
    if media:
        url = media[0].get("url", "")
        if url:
            return url
    for link in entry.get("links", []):
        if str(link.get("type", "")).startswith("image/"):
            return link.get("href", "")
    if entry.get("enclosures"):
        for enc in entry["enclosures"]:
            if str(enc.get("type", "")).startswith("image/"):
                return enc.get("href", "") or enc.get("url", "")
    # last resort: grab the first <img src="..."> from the raw summary HTML
    raw = entry.get("summary", "") or ""
    m = re.search(r'<img[^>]+src="([^"]+)"', raw)
    return m.group(1) if m else ""


def fetch_candidates(config, slot_cfg):
    wanted_modes = {"core", "stretch", "humour", "fact_check"}
    if slot_cfg.get("items_core", 0) == 0:
        wanted_modes.discard("core")
    if slot_cfg.get("items_stretch", 0) == 0:
        wanted_modes.discard("stretch")
    if slot_cfg.get("items_humour", 0) == 0:
        wanted_modes.discard("humour")

    max_per_feed = config["ai"].get("max_candidates_per_feed", 10)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

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
                "image": extract_image(entry),
            })
    return candidates


# ---------------------------------------------------------------- curate

def reattach_images(items, candidates):
    """Claude's curated JSON doesn't carry the image field through, so
    match each curated item back to its source candidate by link (falling
    back to title) and copy the image URL across."""
    by_link = {c["link"]: c.get("image", "") for c in candidates if c.get("link")}
    by_title = {c["title"]: c.get("image", "") for c in candidates}
    for it in items:
        it["image"] = by_link.get(it.get("link", "")) or by_title.get(it.get("title", ""), "")
    return items


def curate(config, slot_cfg, candidates, goals_text, today_str):
    counts = {k: slot_cfg.get(k, 0) for k in ("items_core", "items_stretch", "items_humour")}
    fact_check_note = (
        "Any item sourced from a 'fact_check' mode source must include a short "
        "caution note in its summary, e.g. '(unverified - worth double-checking).'"
        if config["guardrails"].get("label_fact_check_items", True) else ""
    )
    prompt = f"""You are curating a personal news digest slot called "{slot_cfg['label']}".
Today's date: {today_str}

Reader's interest profile, in priority order:
{json.dumps(config['interests'], indent=1)}

Excluded topics/handling rules: {json.dumps(config['exclusions'])}

Reader's active goals and deadlines (use this to weight coverage - the
closer a deadline, the more that topic should be prioritised):
{goals_text}

Slot theme: {slot_cfg['theme']}
{fact_check_note}

From the candidate items below, select up to:
- {counts.get('items_core', 0)} item(s) tagged mode "core" or "fact_check"
- {counts.get('items_stretch', 0)} item(s) tagged mode "stretch"
- {counts.get('items_humour', 0)} item(s) tagged mode "humour"
(If a category has too few good candidates, select fewer rather than padding.)

For each selected item write a fresh 2-3 sentence summary in your own words.
For stretch items, add one sentence on what assumption it challenges.

Respond ONLY with a JSON array, no markdown fences, of objects:
{{"title": str, "source": str, "mode": str, "link": str, "summary": str}}

Candidates:
{json.dumps(candidates, indent=1)[:60000]}
"""
    body = json.dumps({
        "model": config["ai"]["model"],
        "max_tokens": 2500,
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


# ---------------------------------------------------------------- publish

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
  .slot {{ margin-bottom: 22px; }}
  .slot summary {{ color: #8B2D62; font-size: 19px; font-weight: 600;
                   border-bottom: 2px solid #FFF780; padding-bottom: 8px;
                   cursor: pointer; list-style: none; }}
  .slot summary::-webkit-details-marker {{ display: none; }}
  .slot summary::after {{ content: "  ▾"; color: #B84483; font-size: 14px; }}
  .slot[open] summary::after {{ content: "  ▴"; }}
  .item {{ display: flex; gap: 12px; margin: 16px 0; align-items: flex-start; }}
  .item .thumb {{ width: 84px; height: 84px; min-width: 84px; max-width: 84px;
                 border-radius: 8px; overflow: hidden; flex-shrink: 0;
                 background: #eee; box-sizing: border-box; }}
  .item .thumb img {{ width: 100% !important; height: 100% !important;
                      max-width: none !important; object-fit: cover;
                      display: block; }}
  .item .body {{ flex: 1; min-width: 0; }}
  .item .t {{ font-weight: 600; font-size: 16px; }}
  .item .t a {{ color: #8B2D62; text-decoration: none; }}
  .item .s {{ color: #888; font-size: 12px; margin: 2px 0 4px; }}
  .item .b {{ font-size: 14.5px; line-height: 1.5; }}
  .stretch .body {{ border-left: 3px solid #B84483; padding-left: 10px; }}
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
    include_links = config["guardrails"].get("include_links", True)
    parts = [f'<details class="slot" id="{slot_id}" open><summary>{html.escape(slot_cfg["label"])}</summary>']
    for it in items:
        cls = "item stretch" if it["mode"] == "stretch" else "item"
        title = html.escape(it["title"])
        if include_links and it.get("link"):
            title = f'<a href="{html.escape(it["link"])}">{title}</a>'
        img_html = ""
        if it.get("image"):
            img_html = (
                f'<div class="thumb"><img src="{html.escape(it["image"])}" '
                f'alt="" width="84" height="84" loading="lazy"></div>'
            )
        parts.append(
            f'<div class="{cls}">{img_html}'
            f'<div class="body"><div class="t">{title}</div>'
            f'<div class="s">{html.escape(it["source"])}</div>'
            f'<div class="b">{html.escape(it["summary"])}</div></div></div>'
        )
    parts.append("</details>")
    return "\n".join(parts)


def publish_page(config, slot_id, slot_cfg, items, today_label):
    slot_html = render_slot_html(config, slot_id, slot_cfg, items)

    existing = ""
    if os.path.exists(PAGE_PATH):
        with open(PAGE_PATH, encoding="utf-8") as f:
            existing = f.read()

    if today_label in existing and "<!--SLOTS-->" in existing:
        # Collapse earlier slots so the newest one is the only one open
        existing = re.sub(r'(<details class="slot"[^>]*?) open>', r"\1>", existing)
        updated = existing.replace(
            '<div class="fin">', slot_html + '\n<div class="fin">', 1
        )
    else:
        updated = PAGE_TEMPLATE.format(date=today_label, slots=slot_html)

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
    safe_title = slot_cfg["label"].encode("ascii", "ignore").decode().strip() or "Digest"
    headers = {"Title": safe_title, "Priority": "default", "Tags": "newspaper"}
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
    tz = ZoneInfo(config.get("timezone", "Australia/Melbourne"))
    now = datetime.now(tz)
    today = now.date()
    today_str = today.isoformat()
    today_label = now.strftime("%A %d %B %Y")

    # Weekly check-in only fires (and only makes sense) on Sundays
    if args.slot == "weekly_checkin":
        if now.weekday() != 6:
            print("Not Sunday - skipping weekly check-in.")
            return
        wc = config.get("weekly_checkin", {})
        if not wc.get("enabled", False):
            print("Weekly check-in disabled in config.")
            return
        slot_cfg = {
            "label": wc["label"],
            "items_core": wc.get("items_core", 5),
            "items_stretch": 0,
            "items_humour": 0,
            "theme": wc["theme"],
        }
    else:
        slot_cfg = config["slots"].get(args.slot)
        if not slot_cfg:
            sys.exit(f"Unknown slot '{args.slot}'. Options: {list(config['slots'])}")

        # Weekend handling: only the morning slot runs (weekly_checkin still runs separately)
        weekend_cfg = config.get("weekend", {})
        if now.weekday() >= 5 and weekend_cfg.get("mode") == "morning_only":
            if args.slot != "morning_7am":
                print("Weekend: skipping non-morning slot. Enjoy your day off the feed.")
                return
            slot_cfg = {**slot_cfg, **{k: v for k, v in weekend_cfg.items() if k != "mode"}}

    candidates = fetch_candidates(config, slot_cfg)
    print(f"Fetched {len(candidates)} candidate items.")
    if not candidates:
        sys.exit("No candidates fetched - check feed URLs / network.")

    goals_text = goal_context(config, today) or "No active goals configured."
    items = curate(config, slot_cfg, candidates, goals_text, today_str)
    items = reattach_images(items, candidates)
    print(f"Curated {len(items)} items.")

    if args.dry_run:
        for it in items:
            print(f"- [{it['mode']}] {it['title']} ({it['source']})\n  {it['summary']}\n")
        return

    slot_id = args.slot
    publish_page(config, slot_id, slot_cfg, items, today_label)
    send_push(config, slot_cfg, items)


if __name__ == "__main__":
    main()
