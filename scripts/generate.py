#!/usr/bin/env python3
"""
Self-hosted GitHub profile graphs — no third-party image services.

Fetches profile data via GitHub GraphQL API (GITHUB_TOKEN) and renders
four dark-theme SVGs into ./assets/ :

  assets/stats.svg      - stars / commits / PRs / issues / repos / followers
  assets/languages.svg  - top languages by bytes across owned repos
  assets/activity.svg   - full-year contribution heatmap + 30d pulse
  assets/principle.svg  - daily rotating build principle (no API needed)

Design: bg #0d1117, accent #5BCDEC, muted #8b949e. Pure stdlib, no deps.
CI never fails the workflow on API errors — it renders a graceful
fallback SVG with the error noted, so the README never shows a broken image.

Usage:
  GITHUB_TOKEN=... PROFILE_LOGIN=auraflaa python scripts/generate.py
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

BG = "#0d1117"
PANEL = "#0d1117"
BORDER = "#21262d"
TEXT = "#ffffff"
MUTED = "#8b949e"
ACCENT = "#5BCDEC"
ACCENT_DIM = "rgba(91,205,236,0.18)"
FONT = "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"

# GitHub contribution green scale (dark mode)
GREENS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]

PRINCIPLES = [
    ("Research + Application", "thought without action is daydreaming; action without direction is noise."),
    ("Ship small, learn fast", "a thin slice in production beats a perfect plan in a notebook."),
    ("Calibrate before you trust", "a model without uncertainty is just a confident guess."),
    ("Retrieval is memory", "good RAG is a knowledge system, not a search box."),
    ("Data beats opinion", "measure first, argue later."),
    ("Simple scales", "boring pipelines survive; clever ones page you at 3am."),
    ("Eval everything", "if it isn't measured, it isn't improving."),
]

GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    followers { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes {
        stargazerCount
        forkCount
        primaryLanguage { name color }
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalRepositoryContributions
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays { date contributionCount color }
        }
      }
    }
  }
}
"""


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def fetch_profile(login: str, token: str | None) -> dict:
    """Return normalized profile dict. Never raises — returns fallback on error."""
    fallback = {
        "login": login,
        "followers": 0,
        "repo_count": 0,
        "stars": 0,
        "forks": 0,
        "commits": 0,
        "prs": 0,
        "issues": 0,
        "total_contributions": 0,
        "languages": [],
        "weeks": [],
        "error": "no token" if not token else "fetch failed",
    }
    if not token:
        return fallback
    try:
        req_body = json.dumps(
            {"query": GRAPHQL_QUERY, "variables": {"login": login}}
        ).encode()
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=req_body,
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
        repos = user["repositories"]["nodes"]
        stars = sum(r["stargazerCount"] or 0 for r in repos)
        forks = sum(r["forkCount"] or 0 for r in repos)
        lang_bytes: dict[str, dict] = {}
        for r in repos:
            for edge in (r.get("languages") or {}).get("edges", []):
                node = edge["node"]
                name = node["name"]
                entry = lang_bytes.setdefault(
                    name, {"name": name, "bytes": 0, "color": node.get("color") or ACCENT}
                )
                entry["bytes"] += edge["size"] or 0
        langs = sorted(lang_bytes.values(), key=lambda x: -x["bytes"])[:8]
        cc = user["contributionsCollection"]
        cal = cc["contributionCalendar"]
        return {
            "login": user["login"],
            "followers": user["followers"]["totalCount"],
            "repo_count": user["repositories"]["totalCount"],
            "stars": stars,
            "forks": forks,
            "commits": cc["totalCommitContributions"],
            "prs": cc["totalPullRequestContributions"],
            "issues": cc["totalIssueContributions"],
            "total_contributions": cal["totalContributions"],
            "languages": langs,
            "weeks": cal["weeks"],
            "error": "",
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, ValueError) as e:  # noqa: BLE001
        fallback["error"] = f"{type(e).__name__}: {e}"[:160]
        return fallback


def flatten_days(weeks: list) -> list[dict]:
    days: list[dict] = []
    for w in weeks:
        days.extend(w.get("contributionDays", []))
    return days


def current_streak(days: list[dict]) -> int:
    if not days:
        return 0
    # If today has 0 contributions, streak counts back from yesterday.
    idx = len(days) - 1
    if days[idx]["contributionCount"] == 0:
        idx -= 1
    streak = 0
    while idx >= 0 and days[idx]["contributionCount"] > 0:
        streak += 1
        idx -= 1
    return streak


def longest_streak(days: list[dict]) -> int:
    best = cur = 0
    for d in days:
        if d["contributionCount"] > 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def fmt(n: int) -> str:
    if n >= 1000:
        v = n / 1000
        return f"{v:.1f}k".replace(".0k", "k")
    return str(n)


def svg_header(w: int, h: int, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">\n'
        f"<title>{esc(title)}</title>\n"
        f'<rect width="{w}" height="{h}" rx="10" fill="{BG}"/>\n'
    )


def updated_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------- stats

def render_stats(p: dict, login: str) -> str:
    cells = [
        ("Total Stars", fmt(p["stars"]), "★"),
        ("Commits (yr)", fmt(p["commits"]), "◉"),
        ("Pull Requests", fmt(p["prs"]), "⑂"),
        ("Issues", fmt(p["issues"]), "◎"),
        ("Public Repos", fmt(p["repo_count"]), "▤"),
        ("Followers", fmt(p["followers"]), "♥"),
    ]
    W, col_w, row_h = 1200, 200, 96
    H = 64 + row_h * 2 + 34
    s = [svg_header(W, H, f"{login} GitHub stats")]
    s.append(
        f'<text x="28" y="36" font-family="{FONT}" font-size="17" font-weight="700" '
        f'fill="{TEXT}">{esc(login)} <tspan fill="{ACCENT}">/</tspan> '
        f'<tspan fill="{MUTED}" font-weight="400">profile pulse</tspan></text>'
    )
    s.append(
        f'<text x="{W - 28}" y="36" text-anchor="end" font-family="{FONT}" '
        f'font-size="12" fill="{MUTED}">self-hosted · updated {esc(updated_stamp())}</text>'
    )
    for i, (label, value, glyph) in enumerate(cells):
        col, row = i % 3, i // 3
        x = 28 + col * ((W - 56) // 3)
        y = 56 + row * row_h
        cw = (W - 56) // 3 - 14
        s.append(f'<rect x="{x}" y="{y}" width="{cw}" height="82" rx="8" fill="#11161d" stroke="{BORDER}"/>')
        s.append(f'<rect x="{x}" y="{y}" width="3" height="82" rx="1.5" fill="{ACCENT}"/>')
        s.append(
            f'<text x="{x + 20}" y="{y + 30}" font-family="{FONT}" font-size="13" '
            f'fill="{ACCENT}">{esc(glyph)}  {esc(label.upper())}</text>'
        )
        s.append(
            f'<text x="{x + 20}" y="{y + 62}" font-family="{FONT}" font-size="30" '
            f'font-weight="700" fill="{TEXT}">{esc(value)}</text>'
        )
    streak = current_streak(flatten_days(p["weeks"])) if p["weeks"] else 0
    total = p["total_contributions"]
    s.append(
        f'<text x="28" y="{H - 12}" font-family="{FONT}" font-size="12" fill="{MUTED}">'
        f'{fmt(total)} contributions last year · {streak}-day streak · {fmt(p["forks"])} forks'
        + (f' · note: {esc(p["error"])}' if p.get("error") else "")
        + "</text>"
    )
    s.append("</svg>")
    return "".join(s)


# ------------------------------------------------------------ languages

def render_languages(p: dict) -> str:
    W, H = 1200, 300
    langs = p["languages"][:6]
    s = [svg_header(W, H, "Top languages")]
    s.append(
        f'<text x="28" y="36" font-family="{FONT}" font-size="17" font-weight="700" '
        f'fill="{TEXT}">Language fingerprint <tspan fill="{MUTED}" font-weight="400">· by bytes</tspan></text>'
    )
    if not langs:
        s.append(
            f'<text x="28" y="120" font-family="{FONT}" font-size="14" fill="{MUTED}">'
            "No language data yet — push some code and CI will fill this in.</text>"
        )
        s.append("</svg>")
        return "".join(s)
    total = sum(l["bytes"] for l in langs) or 1
    # segmented bar
    bx, bw, y = 28, W - 56, 58
    x = bx
    for l in langs:
        frac = l["bytes"] / total
        wseg = max(frac * bw, 4)
        s.append(
            f'<rect x="{x:.1f}" y="{y}" width="{wseg:.1f}" height="14" rx="7" '
            f'fill="{esc(l["color"])}"/>'
        )
        x += wseg + 2
    # rows (2 columns x 3)
    for i, l in enumerate(langs):
        col, row = i % 2, i // 2
        x0 = 28 + col * ((W - 56) // 2)
        y0 = 108 + row * 56
        pct = l["bytes"] / total * 100
        s.append(f'<circle cx="{x0 + 8}" cy="{y0}" r="7" fill="{esc(l["color"])}"/>')
        s.append(
            f'<text x="{x0 + 24}" y="{y0 + 5}" font-family="{FONT}" font-size="15" '
            f'font-weight="600" fill="{TEXT}">{esc(l["name"])}</text>'
        )
        s.append(
            f'<text x="{x0 + 24}" y="{y0 + 24}" font-family="{FONT}" font-size="12" '
            f'fill="{MUTED}">{pct:.1f}%</text>'
        )
        track_x = x0 + 190
        track_w = (W - 56) // 2 - 210
        s.append(
            f'<rect x="{track_x}" y="{y0 - 9}" width="{track_w}" height="8" rx="4" fill="#1c2330"/>'
        )
        s.append(
            f'<rect x="{track_x}" y="{y0 - 9}" width="{int(track_w * l["bytes"] / langs[0]["bytes"])}" height="8" rx="4" fill="{esc(l["color"])}"/>'
        )
    s.append(
        f'<text x="28" y="{H - 12}" font-family="{FONT}" font-size="12" fill="{MUTED}">'
        f'updated {esc(updated_stamp())} · owned repos only</text>'
    )
    s.append("</svg>")
    return "".join(s)


# ------------------------------------------------------------- activity

def level(count: int, max_c: int) -> int:
    if count <= 0:
        return 0
    if max_c <= 0:
        return 1
    q = count / max_c
    if q < 0.25:
        return 1
    if q < 0.5:
        return 2
    if q < 0.75:
        return 3
    return 4


def render_activity(p: dict, login: str) -> str:
    weeks = p["weeks"][-53:] if p["weeks"] else []
    W, H = 1200, 420
    s = [svg_header(W, H, f"{login} contribution heatmap")]
    total = p["total_contributions"]
    days = flatten_days(weeks)
    streak = current_streak(days)
    best = longest_streak(days)
    s.append(
        f'<text x="28" y="38" font-family="{FONT}" font-size="17" font-weight="700" '
        f'fill="{TEXT}">{fmt(total)} contributions <tspan fill="{MUTED}" font-weight="400">· last 12 months</tspan></text>'
    )
    s.append(
        f'<text x="{W - 28}" y="38" text-anchor="end" font-family="{FONT}" font-size="12" '
        f'fill="{MUTED}">streak {streak}d · best {best}d</text>'
    )
    if not weeks:
        s.append(
            f'<text x="28" y="200" font-family="{FONT}" font-size="14" fill="{MUTED}">'
            "Contribution calendar unavailable for this run (API token missing or rate-limited). "
            "Next scheduled run will retry.</text>"
        )
        s.append("</svg>")
        return "".join(s)

    max_c = max((d["contributionCount"] for d in days), default=1)
    cell, gap = 15, 4
    ox, oy = 56, 78
    # month labels
    seen_month = ""
    for wi, w in enumerate(weeks):
        first = w["contributionDays"][0] if w["contributionDays"] else None
        if first:
            try:
                m = dt.date.fromisoformat(first["date"][:10]).strftime("%b")
                if m != seen_month:
                    seen_month = m
                    s.append(
                        f'<text x="{ox + wi * (cell + gap)}" y="{oy - 10}" '
                        f'font-family="{FONT}" font-size="11" fill="{MUTED}">{m}</text>'
                    )
            except ValueError:
                pass
    for r, lab in (("1", "Mon"), ("3", "Wed"), ("5", "Fri")):
        r = int(r)
        s.append(
            f'<text x="28" y="{oy + r * (cell + gap) + 12}" '
            f'font-family="{FONT}" font-size="11" fill="{MUTED}">{lab}</text>'
        )
    for wi, w in enumerate(weeks):
        for di, d in enumerate(w["contributionDays"][:7]):
            lv = level(d["contributionCount"], max_c)
            x = ox + wi * (cell + gap)
            y = oy + di * (cell + gap)
            s.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3.5" '
                f'fill="{GREENS[lv]}"><title>{esc(d["date"][:10])}: '
                f'{d["contributionCount"]} contributions</title></rect>'
            )
    # legend
    lx = W - 28 - 170
    ly = oy + 7 * (cell + gap) + 26
    s.append(f'<text x="{lx}" y="{ly + 11}" font-family="{FONT}" font-size="12" fill="{MUTED}">Less</text>')
    for i, c in enumerate(GREENS):
        s.append(
            f'<rect x="{lx + 42 + i * 19}" y="{ly}" width="15" height="15" rx="3.5" fill="{c}"/>'
        )
    s.append(
        f'<text x="{lx + 42 + 5 * 19 + 6}" y="{ly + 11}" font-family="{FONT}" '
        f'font-size="12" fill="{MUTED}">More</text>'
    )
    # 30-day pulse sparkline
    last30 = days[-30:] if len(days) >= 30 else days
    pw, ph, px, py = W - 56, 90, 28, H - 118
    s.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="8" fill="#11161d" stroke="{BORDER}"/>')
    s.append(
        f'<text x="{px + 14}" y="{py + 22}" font-family="{FONT}" font-size="12" '
        f'fill="{MUTED}">30-DAY PULSE</text>'
    )
    if last30:
        peak = max(d["contributionCount"] for d in last30) or 1
        pts = []
        for i, d in enumerate(last30):
            x = px + 14 + i * ((pw - 28) / max(len(last30) - 1, 1))
            y = py + ph - 12 - (d["contributionCount"] / peak) * (ph - 40)
            pts.append(f"{x:.1f},{y:.1f}")
        poly = " ".join(pts)
        base_y = py + ph - 12
        s.append(
            f'<polygon points="{px + 14},{base_y} {poly} {px + pw - 14},{base_y}" fill="{ACCENT_DIM}"/>'
        )
        s.append(
            f'<polyline points="{poly}" fill="none" stroke="{ACCENT}" stroke-width="2.5" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for i, d in enumerate(last30):
            if d["contributionCount"] == peak and peak > 0:
                x = px + 14 + i * ((pw - 28) / max(len(last30) - 1, 1))
                y = py + ph - 12 - (d["contributionCount"] / peak) * (ph - 40)
                s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{ACCENT}"/>')
                break
    s.append(
        f'<text x="28" y="{H - 10}" font-family="{FONT}" font-size="12" fill="{MUTED}">'
        f'@{esc(login)} · generated {esc(updated_stamp())} · 100% self-hosted, no third-party pixels</text>'
    )
    s.append("</svg>")
    return "".join(s)


# ------------------------------------------------------------- principle

def render_principle() -> str:
    W, H = 1200, 132
    today = dt.date.today()
    idx = today.timetuple().tm_yday % len(PRINCIPLES)
    title, body = PRINCIPLES[idx]
    s = [svg_header(W, H, "Build principle of the day")]
    s.append(f'<rect x="28" y="22" width="4" height="68" rx="2" fill="{ACCENT}"/>')
    s.append(
        f'<text x="48" y="46" font-family="{FONT}" font-size="12" letter-spacing="2" '
        f'fill="{MUTED}">BUILD PRINCIPLE · {esc(today.strftime("%a %b %d").upper())}</text>'
    )
    s.append(
        f'<text x="48" y="76" font-family="{FONT}" font-size="20" font-weight="700" '
        f'fill="{TEXT}">{esc(title)}</text>'
    )
    s.append(
        f'<text x="48" y="100" font-family="{FONT}" font-size="14" fill="{MUTED}">'
        f'{esc(body)}</text>'
    )
    s.append("</svg>")
    return "".join(s)


def main() -> int:
    login = os.environ.get("PROFILE_LOGIN") or os.environ.get("GITHUB_REPOSITORY_OWNER") or LOGIN_DEFAULT
    # GITHUB_REPOSITORY looks like "owner/repo" — owner is more reliable in CI.
    repo_full = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo_full and not os.environ.get("PROFILE_LOGIN"):
        login = repo_full.split("/")[0] or login
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    os.makedirs(ASSETS, exist_ok=True)
    profile = fetch_profile(login, token)

    outputs = {
        "stats.svg": render_stats(profile, login),
        "languages.svg": render_languages(profile),
        "activity.svg": render_activity(profile, login),
        "principle.svg": render_principle(),
    }
    for name, svg in outputs.items():
        with open(os.path.join(ASSETS, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(svg)
    meta = {
        "login": login,
        "updated": updated_stamp(),
        "total_contributions": profile["total_contributions"],
        "stars": profile["stars"],
        "api_error": profile.get("error", ""),
    }
    with open(os.path.join(ASSETS, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"generated {len(outputs)} SVGs for @{login} (api_error={meta['api_error']!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
