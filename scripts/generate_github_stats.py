#!/usr/bin/env python3
"""
generate_github_stats.py

Fetches REAL contribution data for a GitHub user from GitHub's GraphQL API
(the same source that powers the contribution calendar shown on a user's
profile page), computes:

  - total contributions (last 12 months, as returned by GitHub)
  - current contribution streak
  - longest contribution streak (within the fetched window)

...and renders two dark-themed SVG cards:

  assets/github-streak.svg        -> current streak / longest streak / total
  assets/github-contributions.svg -> GitHub-style contribution heatmap

No values in this script are hard-coded, random, or fabricated. Every number
written into the generated SVGs comes directly from the GraphQL response for
the configured username.

Environment variables:
  GH_USERNAME   GitHub username to fetch stats for (required)
  GH_TOKEN      A token with access to the GraphQL API (required).
                In GitHub Actions this is the built-in secrets.GITHUB_TOKEN.

Exit codes:
  0  success
  1  missing configuration
  2  GitHub API error
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date

GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Theme (matches assets/hero-heading.svg and assets/tech-stack.svg)
# ---------------------------------------------------------------------------
BG = "#0c1420"
CARD_BORDER = "#16233a"
TEXT_MAIN = "#e6f1ff"
TEXT_MUTED = "#7c8aa5"
CYAN = "#22d3ee"
BLUE = "#3882f6"
PURPLE = "#7c3aed"

FONT_MONO = "'JetBrains Mono','Fira Code','Courier New',monospace"
FONT_SANS = "'Space Grotesk','Poppins','Segoe UI',sans-serif"

HEATMAP_LEVELS = ["#0d1826", "#0b3d52", "#0e7490", "#14b8c9", "#22d3ee"]


def die(msg: str, code: int = 1):
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(code)


def fetch_contributions(login: str, token: str) -> dict:
    payload = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{login}-github-stats-script",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        die(f"GitHub GraphQL API request failed: HTTP {e.code} {e.read().decode()[:500]}", 2)
    except urllib.error.URLError as e:
        die(f"GitHub GraphQL API request failed: {e}", 2)

    if "errors" in body:
        die(f"GitHub GraphQL API returned errors: {body['errors']}", 2)

    user = body.get("data", {}).get("user")
    if not user:
        die(f"No such GitHub user, or user data is not accessible: {login}", 2)

    return user["contributionsCollection"]["contributionCalendar"]


def flatten_days(calendar: dict) -> list[dict]:
    days = []
    for week in calendar["weeks"]:
        for day in week["contributionDays"]:
            days.append(
                {
                    "date": datetime.strptime(day["date"], "%Y-%m-%d").date(),
                    "count": day["contributionCount"],
                }
            )
    days.sort(key=lambda d: d["date"])
    return days


def compute_streaks(days: list[dict]) -> dict:
    """
    Computes current + longest streak from a chronologically sorted list of
    {date, count} entries, following GitHub's own contribution-streak
    convention:

      - A "qualifying" day is any calendar day with count > 0.
      - The longest streak is the longest run of consecutive qualifying days
        anywhere in the fetched window.
      - The current streak counts consecutive qualifying days ending at the
        most recent qualifying day, but is only considered "active" (i.e.
        non-zero) if that most recent qualifying day is today or yesterday.
        This mirrors GitHub's behaviour where a streak isn't broken the
        moment today starts with zero contributions -- it's only broken once
        a full day passes with none. If the most recent qualifying day is
        older than yesterday, the streak has been broken and the current
        streak is 0.
    """
    if not days:
        return {"current": 0, "longest": 0}

    by_date = {d["date"]: d["count"] for d in days}
    # GitHub contribution calendars are date-based. Use the actual UTC date\n    # rather than assuming the last returned array element is today.\n    today = datetime.utcnow().date()

    # Longest streak: scan all days for the longest run of count > 0
    longest = 0
    running = 0
    for d in days:
        if d["count"] > 0:
            running += 1
            longest = max(longest, running)
        else:
            running = 0

    # Current streak: find the most recent qualifying day, then count
    # consecutive qualifying days walking backwards from it.
    most_recent_qualifying = None
    cursor = today
    # search backwards for the most recent day with count > 0 (bounded by data we have)
    earliest = days[0]["date"]
    probe = today
    while probe >= earliest:
        if by_date.get(probe, 0) > 0:
            most_recent_qualifying = probe
            break
        probe -= timedelta(days=1)

    if most_recent_qualifying is None:
        current = 0
    elif (today - most_recent_qualifying).days > 1:
        # Most recent contribution was more than 1 day ago -> streak broken
        current = 0
    else:
        current = 0
        probe = most_recent_qualifying
        while by_date.get(probe, 0) > 0:
            current += 1
            probe -= timedelta(days=1)

    return {"current": current, "longest": longest}


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

def esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_streak_svg(current: int, longest: int, total: int, username: str, generated_at: str) -> str:
    width, height = 840, 220
    cols = [
        {"label": "CURRENT STREAK", "value": current, "unit": "day" if current == 1 else "days", "icon": "STREAK", "accent": CYAN},
        {"label": "LONGEST STREAK", "value": longest, "unit": "day" if longest == 1 else "days", "icon": "BEST", "accent": BLUE},
        {"label": "TOTAL CONTRIBUTIONS", "value": total, "unit": "past year", "icon": "TOTAL", "accent": PURPLE},
    ]

    col_w = width / 3
    parts = []
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="GitHub contribution streak for {esc(username)}: '
        f'{current} day current streak, {longest} day longest streak, {total} total contributions">'
    )
    parts.append(f"<title>GitHub contribution streak for {esc(username)}</title>")
    parts.append(
        "<defs>"
        "<style>"
        "@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600;700&amp;family=Space+Grotesk:wght@600;700&amp;display=swap');"
        f".num {{ font-family: {FONT_SANS}; font-weight: 700; }}"
        f".lbl {{ font-family: {FONT_MONO}; font-weight: 600; letter-spacing: 1px; }}"
        f".meta {{ font-family: {FONT_MONO}; font-weight: 500; }}"
        "</style>"
        f'<linearGradient id="borderGrad" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{CYAN}"/><stop offset="50%" stop-color="{BLUE}"/><stop offset="100%" stop-color="{PURPLE}"/>'
        "</linearGradient>"
        '<filter id="glow" x="-40%" y="-40%" width="180%" height="180%">'
        '<feGaussianBlur stdDeviation="2.4" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
        "</filter>"
        "</defs>"
    )

    # outer card
    parts.append(
        f'<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="16" ry="16" '
        f'fill="{BG}" stroke="url(#borderGrad)" stroke-width="1.4" opacity="0.98"/>'
    )
    parts.append(
        f'<rect x="1" y="1" width="{width-2}" height="3" rx="1.5" fill="url(#borderGrad)" opacity="0.65"/>'
    )

    # header
    parts.append(
        f'<text x="28" y="34" font-size="13" class="lbl" fill="{TEXT_MUTED}">'
        f"GITHUB CONTRIBUTION STREAK &#183; @{esc(username)}</text>"
    )
    parts.append(f'<line x1="28" y1="46" x2="{width-28}" y2="46" stroke="{CARD_BORDER}" stroke-width="1"/>')

    for i, col in enumerate(cols):
        cx = col_w * i + col_w / 2
        if i > 0:
            parts.append(
                f'<line x1="{col_w*i:.1f}" y1="66" x2="{col_w*i:.1f}" y2="{height-40}" '
                f'stroke="{CARD_BORDER}" stroke-width="1"/>'
            )
        parts.append(
            f'<text x="{cx:.1f}" y="96" text-anchor="middle" font-size="10" class="lbl" '
            f'fill="{col["accent"]}" opacity="0.9">{col["icon"]}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="150" text-anchor="middle" font-size="46" class="num" '
            f'fill="{col["accent"]}" filter="url(#glow)">{col["value"]}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="172" text-anchor="middle" font-size="12" class="meta" '
            f'fill="{TEXT_MUTED}">{esc(col["unit"])}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="196" text-anchor="middle" font-size="11.5" class="lbl" '
            f'fill="{TEXT_MAIN}" opacity="0.9">{esc(col["label"])}</text>'
        )

    parts.append(
        f'<text x="{width-28}" y="{height-14}" text-anchor="end" font-size="9.5" class="meta" '
        f'fill="{TEXT_MUTED}" opacity="0.7">updated {esc(generated_at)} UTC &#183; source: github.com/{esc(username)}</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


def render_contributions_svg(days: list[dict], username: str, generated_at: str) -> str:
    # Group flattened days back into weeks (columns), 7 rows each, in order.
    weeks: list[list[dict]] = []
    week: list[dict] = []
    for d in days:
        if d["date"].weekday() == 6 and week:  # Sunday starts a new GitHub week column... 
            pass
        week.append(d)
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        weeks.append(week)

    max_count = max((d["count"] for d in days), default=0)

    def level_color(count: int) -> str:
        if count <= 0 or max_count == 0:
            return HEATMAP_LEVELS[0]
        ratio = count / max_count
        if ratio <= 0.25:
            return HEATMAP_LEVELS[1]
        if ratio <= 0.5:
            return HEATMAP_LEVELS[2]
        if ratio <= 0.75:
            return HEATMAP_LEVELS[3]
        return HEATMAP_LEVELS[4]

    cell = 11
    gap = 3
    left_pad = 34
    top_pad = 42
    right_pad = 20
    bottom_pad = 34

    n_weeks = len(weeks)
    header_text = f"GITHUB CONTRIBUTIONS \u00b7 @{username} \u00b7 last 12 months"
    header_min_width = left_pad + int(len(header_text) * 7.4) + right_pad
    width = left_pad + n_weeks * (cell + gap) + right_pad
    height = top_pad + 7 * (cell + gap) + bottom_pad
    width = max(width, 480, header_min_width)

    parts = []
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="GitHub contribution calendar for {esc(username)}, last 12 months">'
    )
    parts.append(f"<title>GitHub contribution calendar for {esc(username)}</title>")
    parts.append(
        "<defs><style>"
        "@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600&amp;display=swap');"
        f"text {{ font-family: {FONT_MONO}; }}"
        "</style>"
        f'<linearGradient id="borderGrad2" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{CYAN}"/><stop offset="50%" stop-color="{BLUE}"/><stop offset="100%" stop-color="{PURPLE}"/>'
        "</linearGradient></defs>"
    )
    parts.append(
        f'<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="14" ry="14" '
        f'fill="{BG}" stroke="url(#borderGrad2)" stroke-width="1.2" opacity="0.98"/>'
    )
    parts.append(
        f'<text x="{left_pad}" y="24" font-size="12.5" font-weight="600" letter-spacing="0.6" '
        f'fill="{TEXT_MUTED}">GITHUB CONTRIBUTIONS &#183; @{esc(username)} &#183; last 12 months</text>'
    )

    # month labels: detect first day-of-month within each week column
    last_month = None
    for wi, wk in enumerate(weeks):
        for d in wk:
            if d["date"].day <= 7 and d["date"].month != last_month:
                x = left_pad + wi * (cell + gap)
                parts.append(
                    f'<text x="{x}" y="{top_pad-8}" font-size="10" fill="{TEXT_MUTED}">'
                    f'{d["date"].strftime("%b")}</text>'
                )
                last_month = d["date"].month
                break

    # day-of-week labels (Mon/Wed/Fri) — weekday(): Mon=0 ... Sun=6
    dow_labels = {0: "Mon", 2: "Wed", 4: "Fri"}
    for dow, label in dow_labels.items():
        y = top_pad + dow * (cell + gap) + cell - 1
        parts.append(f'<text x="6" y="{y}" font-size="9" fill="{TEXT_MUTED}">{label}</text>')

    for wi, wk in enumerate(weeks):
        for d in wk:
            row = d["date"].weekday()  # Mon=0..Sun=6
            x = left_pad + wi * (cell + gap)
            y = top_pad + row * (cell + gap)
            color = level_color(d["count"])
            title = f'{d["count"]} contribution{"s" if d["count"] != 1 else ""} on {d["date"].isoformat()}'
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.5" ry="2.5" '
                f'fill="{color}" stroke="{CARD_BORDER}" stroke-width="0.6">'
                f'<title>{esc(title)}</title></rect>'
            )

    # legend
    lx = width - right_pad - 5 * (cell + 4) - 40
    ly = height - bottom_pad + 20
    parts.append(f'<text x="{lx-28}" y="{ly+cell-1}" font-size="9.5" fill="{TEXT_MUTED}">Less</text>')
    for i, color in enumerate(HEATMAP_LEVELS):
        x = lx + i * (cell + 4)
        parts.append(
            f'<rect x="{x}" y="{ly}" width="{cell}" height="{cell}" rx="2.5" ry="2.5" '
            f'fill="{color}" stroke="{CARD_BORDER}" stroke-width="0.6"/>'
        )
    parts.append(
        f'<text x="{lx + 5*(cell+4) + 6}" y="{ly+cell-1}" font-size="9.5" fill="{TEXT_MUTED}">More</text>'
    )
    parts.append(
        f'<text x="{width-right_pad}" y="{height-10}" text-anchor="end" font-size="8.5" '
        f'fill="{TEXT_MUTED}" opacity="0.7">updated {esc(generated_at)} UTC</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


def main():
    username = os.environ.get("GH_USERNAME", "").strip()
    token = os.environ.get("GH_TOKEN", "").strip()
    out_dir = os.environ.get("OUTPUT_DIR", "assets")

    if not username:
        die("GH_USERNAME environment variable is required")
    if not token:
        die("GH_TOKEN environment variable is required")

    calendar = fetch_contributions(username, token)
    days = flatten_days(calendar)
    total = calendar["totalContributions"]
    streaks = compute_streaks(days)

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    os.makedirs(out_dir, exist_ok=True)

    streak_svg = render_streak_svg(
        current=streaks["current"],
        longest=streaks["longest"],
        total=total,
        username=username,
        generated_at=generated_at,
    )
    with open(os.path.join(out_dir, "github-streak.svg"), "w", encoding="utf-8") as f:
        f.write(streak_svg)

    contrib_svg = render_contributions_svg(days, username, generated_at)
    with open(os.path.join(out_dir, "github-contributions.svg"), "w", encoding="utf-8") as f:
        f.write(contrib_svg)

    print(
        f"OK: {username} -> current={streaks['current']} longest={streaks['longest']} "
        f"total={total} (days fetched: {len(days)})"
    )


if __name__ == "__main__":
    main()
