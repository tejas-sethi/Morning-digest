# Daily Digest — Phone App Edition

A drip-fed digest (7am / 10am / 12pm / 2pm Melbourne) that lives as an app
icon on your phone. No email, no Instagram, no algorithm.

## How the "app" works

1. GitHub Actions runs `digest.py` at each drip time.
2. The script fetches your RSS sources, has the Claude API curate them,
   and rewrites `docs/index.html` — your app's screen.
3. GitHub Pages serves that page at a private-ish URL.
4. ntfy sends a push notification to your phone; tapping it (or your
   home-screen icon) opens the freshly updated page.

Each slot APPENDS to the day's page, so by 2pm the app shows the whole
day. At the next morning's run it starts a fresh page.

## Setup

### A. Repo (see chat walkthrough)
Upload: `digest.py`, `config.yaml`, `README.md`, `QUESTIONNAIRE.md`,
`docs/index.html`, `docs/manifest.json`, and create
`.github/workflows/digest.yml` via "Create new file".

### B. Turn on GitHub Pages (this creates the app URL)
Repo → Settings → Pages → under "Build and deployment":
- Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs** → Save

After a minute your app lives at:
`https://YOURUSERNAME.github.io/morning-digest/`
Paste that URL into `config.yaml` under `delivery.page_url`.

Note: GitHub Pages sites are public-by-URL on free accounts. The URL is
unguessable in practice, but don't put anything sensitive in the digest.

### C. Secrets
Repo → Settings → Secrets and variables → Actions → New repository secret:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from console.anthropic.com |
| `NTFY_TOPIC` | your invented topic, e.g. `tj-digest-x7k2q` |

### D. Phone setup (2 minutes)
1. Install the **ntfy** app (App Store / Play Store, free).
2. In ntfy: Subscribe to topic → enter the same topic name as the secret.
3. Open your GitHub Pages URL in your phone browser →
   **Add to Home Screen**. iPhone: Share button → Add to Home Screen.
   Android: browser menu ⋮ → Add to Home screen.

You now have a Digest icon. Notifications arrive at each drip; tap to read.

### E. Test
Actions tab → Daily Digest Drip → Run workflow → pick a slot. Green tick →
your phone pings and the page updates (hard-refresh if cached).

## Changing things later
Edit `config.yaml` in the browser (pencil icon) → commit. Applies from the
next run. To reduce from 4 drips to 2, delete cron lines in the workflow
and the matching `case` entries.
