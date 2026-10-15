#!/usr/bin/env python3
"""
Self-hosted GitHub profile graphs — no third-party image services.

Same README concept as before (activity graph + dev quote), but the pixels
are rendered by our own CI instead of vercel-hosted APIs:

  assets/activity.svg - contribution area graph (bg #0d1117, line #5BCDEC)
  assets/quote.svg    - daily dev quote card (same #0d1117 / #5BCDEC theme)

Data comes from the GitHub GraphQL API. Token priority:
  PROFILE_PAT (your personal access token — includes private contributions)
  PROFILE_TOKEN / GITHUB_TOKEN / GH_TOKEN (public contributions only)
Pure stdlib, no deps.
CI never fails on API errors — it renders a graceful fallback in the same
layout, so the README never shows a broken image.

Usage:
  PROFILE_PAT=... PROFILE_LOGIN=auraflaa python scripts/generate.py
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
LOGIN_DEFAULT = "auraflaa"

# Matches the old embed params: bg_color=0d1117, hide_border=true,
# line=5BCDEC, point=false, area=true, area_color=5BCDEC, color=ffffff
BG = "#0d1117"
GRID = "#21262d"
TEXT = "#ffffff"
MUTED = "#8b949e"
ACCENT = "#5BCDEC"
FONT = "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"

# Quote card reuses the exact profile theme so both cards match:
# bg #0d1117, hairline #21262d, text white / muted grey, accent #5BCDEC.
Q_BG = "#0d1117"
Q_BORDER = "#21262d"
Q_TEXT = "#ffffff"
Q_MUTED = "#8b949e"
Q_ACCENT = "#5BCDEC"

QUOTES = [
    ("Talk is cheap. Show me the code.", "Linus Torvalds"),
    ("First, solve the problem. Then, write the code.", "John Johnson"),
    ("Any fool can write code that a computer can understand. Good programmers write code that humans can understand.", "Martin Fowler"),
    ("Code is like humor. When you have to explain it, it's bad.", "Cory House"),
    ("Make it work, make it right, make it fast.", "Kent Beck"),
    ("Simplicity is prerequisite for reliability.", "Edsger W. Dijkstra"),
    ("The best error message is the one that never shows up.", "Thomas Fuchs"),
    ("Fix the cause, not the symptom.", "Steve Maguire"),
    ("Testing leads to failure, and failure leads to understanding.", "Burt Rutan"),
    ("Programs must be written for people to read, and only incidentally for machines to execute.", "Harold Abelson"),
]

GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays { date contributionCount }
        }
      }
    }
  }
}
"""


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def fetch_calendar(login: str, token: str | None) -> dict:
    """Return {login, total, days, error}. Never raises."""
    fallback = {"login": login, "total": 0, "days": [], "error": ""}
    if not token:
        fallback["error"] = "no token - placeholder until CI runs with PROFILE_PAT/GITHUB_TOKEN"
        return fallback
    try:
        body = json.dumps(
            {"query": GRAPHQL_QUERY, "variables": {"login": login}}
        ).encode()
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "auraflaa-profile-graphs",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
        if "errors" in payload:
            fallback["error"] = "; ".join(
                e.get("message", "graphql error") for e in payload["errors"][:2]
            )
            return fallback
        user = payload["data"]["user"]
        days: list[dict] = []
        for w in user["contributionsCollection"]["contributionCalendar"]["weeks"]:
            days.extend(w.get("contributionDays", []))
        return {
            "login": user["login"],
            "total": user["contributionsCollection"]["contributionCalendar"][
                "totalContributions"
            ],
            "days": days,
            "error": "",
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, ValueError) as e:  # noqa: BLE001
        fallback["error"] = f"{type(e).__name__}: {e}"[:160]
        return fallback


def updated_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ------------------------------------------------------------ activity

def render_activity(cal: dict, login: str) -> str:
    W, H = 1200, 360
    pad_l, pad_r, pad_t, pad_b = 52, 24, 64, 40
    pw, ph = W - pad_l - pad_r, H - pad_t - pad_b

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="{esc(login)} contribution graph">\n'
        f"<title>{esc(login)}'s Contribution Graph</title>\n"
        f'<rect width="{W}" height="{H}" fill="{BG}"/>\n'
        f'<text x="{pad_l}" y="32" font-family="{FONT}" font-size="20" '
        f'font-weight="700" fill="{TEXT}">{esc(login)}&#39;s Contribution Graph</text>\n'
        f'<text x="{W - pad_r}" y="32" text-anchor="end" font-family="{FONT}" '
        f'font-size="12" fill="{MUTED}">self-hosted · updated {esc(updated_stamp())}</text>\n'
    ]

    days = cal["days"]
    if not days:
        parts.append(
            f'<text x="{W // 2}" y="{H // 2}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="15" fill="{MUTED}">'
            f"Contribution data unavailable for this run ({esc(cal['error'])}). "
            "Next scheduled run will retry.</text>\n</svg>"
        )
        return "".join(parts)

    counts = [d["contributionCount"] for d in days]
    dates = [d["date"][:10] for d in days]
    peak = max(max(counts), 1)
    n = len(counts)

    def x(i: int) -> float:
        return pad_l + i * (pw / max(n - 1, 1))

    def y(c: int) -> float:
        return pad_t + ph - (c / peak) * ph

    # y gridlines + labels (0 / half / peak)
    for tick in (0, (peak + 1) // 2, peak):
        yy = y(tick)
        parts.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{W - pad_r}" y2="{yy:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>\n'
            f'<text x="{pad_l - 10}" y="{yy + 4:.1f}" text-anchor="end" '
            f'font-family="{FONT}" font-size="11" fill="{MUTED}">{tick}</text>\n'
        )
    # x labels (~6 month ticks)
    step = max(n // 6, 1)
    for i in range(0, n, step):
        try:
            label = dt.date.fromisoformat(dates[i]).strftime("%b %d")
        except ValueError:
            label = dates[i]
        parts.append(
            f'<text x="{x(i):.1f}" y="{H - 14}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="11" fill="{MUTED}">{esc(label)}</text>\n'
        )

    pts = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, c in enumerate(counts))
    base = pad_t + ph
    parts.append(
        f'<polygon points="{pad_l},{base} {pts} {W - pad_r},{base}" '
        f'fill="{ACCENT}" fill-opacity="0.18"/>\n'
        f'<polyline points="{pts}" fill="none" stroke="{ACCENT}" '
        f'stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>\n'
    )
    parts.append(
        f'<text x="{pad_l}" y="{H - 14 + 0}" font-family="{FONT}" font-size="0"></text>\n'
        "</svg>"
    )
    # NOTE: zero-size text above is a no-op anchor kept for layout stability.
    return "".join(parts)


# --------------------------------------------------------------- quote

def render_quote() -> str:
    W, H = 1200, 200
    today = dt.date.today()
    quote, author = QUOTES[today.timetuple().tm_yday % len(QUOTES)]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="Dev quote of the day">\n'
        f"<title>Dev Quote</title>\n"
        f'<rect width="{W}" height="{H}" rx="10" fill="{Q_BG}" stroke="{Q_BORDER}"/>\n'
        f'<rect x="28" y="28" width="4" height="{H - 56}" rx="2" fill="{Q_ACCENT}"/>\n'
        f'<text x="52" y="58" font-family="{FONT}" font-size="12" letter-spacing="3" '
        f'fill="{Q_MUTED}">DEV QUOTE · {esc(today.strftime("%A").upper())}</text>\n'
        f'<text x="52" y="112" font-family="Georgia,serif" font-size="24" '
        f'font-style="italic" fill="{Q_TEXT}">\u201c{esc(quote)}\u201d</text>\n'
        f'<text x="{W - 52}" y="{H - 40}" text-anchor="end" font-family="{FONT}" '
        f'font-size="15" fill="{Q_ACCENT}">— {esc(author)}</text>\n'
        "</svg>"
    )


def main() -> int:
    login = (
        os.environ.get("PROFILE_LOGIN")
        or os.environ.get("GITHUB_REPOSITORY_OWNER")
        or LOGIN_DEFAULT
    )
    repo_full = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo_full and not os.environ.get("PROFILE_LOGIN"):
        login = repo_full.split("/")[0] or login
    # PROFILE_PAT is a personal access token (repo + read:user scopes) that can
    # see private contributions; the default GITHUB_TOKEN can only see public.
    token = (
        os.environ.get("PROFILE_PAT")
        or os.environ.get("PROFILE_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
    )
    includes_private = bool(
        os.environ.get("PROFILE_PAT") or os.environ.get("PROFILE_TOKEN")
    )

    os.makedirs(ASSETS, exist_ok=True)
    cal = fetch_calendar(login, token)

    with open(os.path.join(ASSETS, "activity.svg"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_activity(cal, login))
    with open(os.path.join(ASSETS, "quote.svg"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_quote())
    # Drop the previous experimental cards — old concept is activity + quote only.
    for legacy in ("stats.svg", "languages.svg", "principle.svg"):
        p = os.path.join(ASSETS, legacy)
        if os.path.exists(p):
            os.remove(p)
    with open(os.path.join(ASSETS, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "login": login,
                "updated": updated_stamp(),
                "total_contributions": cal["total"],
                "includes_private": includes_private,
                "api_error": cal["error"],
            },
            f,
            indent=2,
        )
    print(f"generated activity.svg + quote.svg for @{login} (api_error={cal['error']!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
