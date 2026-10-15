#!/usr/bin/env python3
"""
Self-hosted GitHub profile graphs — no third-party image services.

Renders the profile's cards into ./assets/ with one consistent theme
(bg #0d1117, hairline #21262d, white text, accent #5BCDEC):

  assets/stats.svg    - stat rows (stars / commits / PRs / issues /
                        contributed-to) + house grade ring
  assets/streak.svg   - total / current-streak / longest-streak card
  assets/activity.svg - minimal contribution area graph
  assets/quote.svg    - daily dev quote card

Data comes from the GitHub GraphQL API. Token priority:
  PROFILE_PAT (personal access token — includes private contributions)
  PROFILE_TOKEN / GITHUB_TOKEN / GH_TOKEN (public contributions only)
Pure stdlib, no deps. CI never fails on API errors — it renders a graceful
fallback in the same layout, so the README never shows a broken image.

Usage:
  PROFILE_PAT=... PROFILE_LOGIN=auraflaa python scripts/generate.py
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import sys
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
LOGIN_DEFAULT = "auraflaa"

BG = "#0d1117"
HAIRLINE = "#21262d"
TEXT = "#ffffff"
MUTED = "#8b949e"
ACCENT = "#5BCDEC"
FONT = "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
SERIF = "Georgia,'Times New Roman',serif"

RING_TRACK = "#2d333b"
RING_STATS = "#e6edf3"
FIRE = "#FB8C00"
FIRE_SOFT = "#FFD54F"

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
    name
    followers { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER) {
      totalCount
      nodes { stargazerCount }
    }
    repositoriesContributedTo(
      first: 1
      contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]
    ) {
      totalCount
    }
    contributionsCollection {
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
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


def fetch_profile(login: str, token: str | None) -> dict:
    """Return normalized profile dict. Never raises — fallback on error."""
    fallback = {
        "login": login,
        "name": "",
        "followers": 0,
        "stars": 0,
        "commits": 0,
        "prs": 0,
        "issues": 0,
        "reviews": 0,
        "contributed_to": 0,
        "total": 0,
        "days": [],
        "error": "",
    }
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
        cc = user["contributionsCollection"]
        days: list[dict] = []
        for w in cc["contributionCalendar"]["weeks"]:
            days.extend(w.get("contributionDays", []))
        repos = user["repositories"]["nodes"]
        return {
            "login": user["login"],
            "name": user.get("name") or user["login"],
            "followers": user["followers"]["totalCount"],
            # Stars across the 100 most-starred owned repos (API page cap).
            "stars": sum((r.get("stargazerCount") or 0) for r in repos),
            "commits": cc["totalCommitContributions"],
            "prs": cc["totalPullRequestContributions"],
            "issues": cc["totalIssueContributions"],
            "reviews": cc["totalPullRequestReviewContributions"],
            # "Contributed to (last year)": repos recently contributed to.
            "contributed_to": user["repositoriesContributedTo"]["totalCount"],
            "total": cc["contributionCalendar"]["totalContributions"],
            "days": days,
            "error": "",
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, ValueError) as e:  # noqa: BLE001
        fallback["error"] = f"{type(e).__name__}: {e}"[:160]
        return fallback


def updated_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_int(n: int) -> str:
    return f"{n:,}"


def fmt_md(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def fmt_mdy(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _day(d: dict) -> dt.date:
    return dt.date.fromisoformat(d["date"][:10])


# ---------------------------------------------------------------- grade
# House grade: transparent heuristic inspired by the well-known exponential
# survival-function approach (each metric normalized against a typical median,
# then combined as a weighted average). Ranks: S, A+, A, A-, B+, B, B-, C+,
# C, C-. This is our own formula, not affiliated with any ranking service.

def _survival(x: int, median: float) -> float:
    return 1.0 - math.exp(-x / median) if median > 0 else 0.0


GRADE_INPUTS = (
    ("commits", 250.0, 2.0),
    ("prs", 12.0, 3.0),
    ("issues", 8.0, 1.0),
    ("reviews", 3.0, 1.0),
    ("stars", 10.0, 4.0),
    ("followers", 8.0, 1.0),
)

GRADE_BANDS = (
    (85.0, "S"),
    (75.0, "A+"),
    (68.0, "A"),
    (61.0, "A-"),
    (54.0, "B+"),
    (48.0, "B"),
    (42.0, "B-"),
    (30.0, "C+"),
    (22.0, "C"),
    (14.0, "C-"),
)


def house_grade(p: dict) -> tuple[float, str]:
    total_w = sum(w for _, _, w in GRADE_INPUTS)
    score = (
        100.0
        * sum(w * _survival(p.get(k, 0), m) for k, m, w in GRADE_INPUTS)
        / total_w
    )
    for bound, grade in GRADE_BANDS:
        if score >= bound:
            return score, grade
    return score, "D"


# ---------------------------------------------------------------- streaks

def streak_spans(days: list[dict]) -> dict:
    """Current + longest streak as (count, start, end) date spans."""
    empty = {
        "current": (0, None, None),
        "longest": (0, None, None),
    }
    if not days:
        return empty
    parsed = [(d["date"][:10], d["contributionCount"]) for d in days]

    end = len(parsed) - 1
    if parsed[end][1] == 0:
        end -= 1
    cur = 0
    cur_end = cur_start = None
    i = end
    while i >= 0 and parsed[i][1] > 0:
        cur += 1
        cur_start = parsed[i][0]
        if cur_end is None:
            cur_end = parsed[i][0]
        i -= 1

    best = best_start = best_end = None
    run = run_start = None
    for date_s, count in parsed + [(None, 0)]:
        if count > 0:
            run = (run or 0) + 1
            if run_start is None:
                run_start = date_s
            run_end = date_s
        else:
            if run and (best is None or run > best):
                best, best_start, best_end = run, run_start, run_end
            run = run_start = run_end = None

    def to_date(s: str | None) -> dt.date | None:
        return dt.date.fromisoformat(s) if s else None

    return {
        "current": (cur, to_date(cur_start), to_date(cur_end)),
        "longest": (best or 0, to_date(best_start), to_date(best_end)),
    }


def span_label(span: tuple[int, dt.date | None, dt.date | None]) -> str:
    _, start, end = span
    if not start or not end:
        return "-"
    if start == end:
        return fmt_md(start)
    return f"{fmt_md(start)} - {fmt_md(end)}"


# ----------------------------------------------------------------- stats

def render_stats(p: dict) -> str:
    W, H = 1200, 300
    title = f"{p['name'] or p['login']}'s GitHub Stats"
    rows = [
        ("Total Stars Earned:", fmt_int(p["stars"])),
        ("Total Commits (last year):", fmt_int(p["commits"])),
        ("Total PRs:", fmt_int(p["prs"])),
        ("Total Issues:", fmt_int(p["issues"])),
        ("Contributed to (last year):", fmt_int(p["contributed_to"])),
    ]
    score, grade = house_grade(p)

    s = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}">\n'
        f"<title>{esc(title)}</title>\n"
        f'<rect width="{W}" height="{H}" rx="10" fill="{BG}" stroke="{HAIRLINE}"/>\n'
        f'<text x="48" y="66" font-family="{FONT}" font-size="26" '
        f'font-weight="700" fill="{TEXT}">{esc(title)}</text>\n'
    ]
    y = 118
    for label, value in rows:
        s.append(
            f'<text x="48" y="{y}" font-family="{FONT}" font-size="19" '
            f'fill="{MUTED}">{esc(label)}</text>\n'
            f'<text x="760" y="{y}" text-anchor="end" font-family="{FONT}" '
            f'font-size="19" font-weight="600" fill="{TEXT}">{esc(value)}</text>\n'
        )
        y += 36
    if p.get("error"):
        s.append(
            f'<text x="48" y="{H - 18}" font-family="{FONT}" font-size="12" '
            f'fill="{MUTED}">live data pending ({esc(p["error"])})</text>\n'
        )

    # Grade ring.
    cx, cy, r, sw = 990, 150, 78, 14
    circ = 2 * math.pi * r
    arc = max(score, 2.0) / 100.0 * circ
    s.append(
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="{RING_TRACK}" stroke-width="{sw}"/>\n'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{RING_STATS}" '
        f'stroke-width="{sw}" stroke-linecap="round" '
        f'stroke-dasharray="{arc:.1f} {circ:.1f}" '
        f'transform="rotate(-90 {cx} {cy})">'
        f"<title>House grade {esc(grade)} ({score:.0f}/100)</title></circle>\n"
        f'<text x="{cx}" y="{cy + 15}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="42" font-weight="700" fill="{TEXT}">{esc(grade)}</text>\n'
        "</svg>"
    )
    return "".join(s)


# ---------------------------------------------------------------- streak

def render_streak(p: dict, spans: dict) -> str:
    W, H = 1200, 320
    total = p["total"]
    cur_n, _, _ = spans["current"]
    long_n, _, _ = spans["longest"]
    days = p["days"]

    if days:
        first, last = _day(days[0]), _day(days[-1])
        total_range = (
            f"{fmt_mdy(first)} - Present"
            if last == dt.date.today()
            else f"{fmt_mdy(first)} - {fmt_mdy(last)}"
        )
    else:
        total_range = "-"

    col_w = W // 3
    s = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="Contribution streaks">\n'
        "<title>Contribution streaks</title>\n"
        f'<rect width="{W}" height="{H}" rx="10" fill="{BG}" stroke="{HAIRLINE}"/>\n'
        f'<line x1="{col_w}" y1="44" x2="{col_w}" y2="{H - 44}" stroke="{HAIRLINE}"/>\n'
        f'<line x1="{2 * col_w}" y1="44" x2="{2 * col_w}" y2="{H - 44}" stroke="{HAIRLINE}"/>\n'
        # Total contributions.
        f'<text x="{col_w // 2}" y="128" text-anchor="middle" font-family="{FONT}" '
        f'font-size="58" font-weight="700" fill="{TEXT}">{fmt_int(total)}</text>\n'
        f'<text x="{col_w // 2}" y="176" text-anchor="middle" font-family="{FONT}" '
        f'font-size="21" fill="{TEXT}">Total Contributions</text>\n'
        f'<text x="{col_w // 2}" y="214" text-anchor="middle" font-family="{FONT}" '
        f'font-size="16" fill="{MUTED}">{esc(total_range)}</text>\n'
        # Longest streak.
        f'<text x="{W - col_w // 2}" y="128" text-anchor="middle" font-family="{FONT}" '
        f'font-size="58" font-weight="700" fill="{TEXT}">{fmt_int(long_n)}</text>\n'
        f'<text x="{W - col_w // 2}" y="176" text-anchor="middle" font-family="{FONT}" '
        f'font-size="21" fill="{TEXT}">Longest Streak</text>\n'
        f'<text x="{W - col_w // 2}" y="214" text-anchor="middle" font-family="{FONT}" '
        f'font-size="16" fill="{MUTED}">{esc(span_label(spans["longest"]))}</text>\n'
    ]

    # Current streak: flame-capped ring.
    cx, cy, r, sw = W // 2, 118, 62, 11
    circ = 2 * math.pi * r
    arc = max(min(cur_n, 30) / 30.0, 0.06) * circ if cur_n else 0.06 * circ
    ft = cy - r - 30  # flame tip
    s.append(
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="{RING_TRACK}" stroke-width="{sw}"/>\n'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{FIRE}" '
        f'stroke-width="{sw}" stroke-linecap="round" '
        f'stroke-dasharray="{arc:.1f} {circ:.1f}" '
        f'transform="rotate(-90 {cx} {cy})"/>\n'
        # Flame teardrop overlapping the ring top.
        f'<path d="M {cx} {ft} '
        f"C {cx - 13} {ft + 17} {cx - 15} {ft + 25} {cx - 15} {ft + 31} "
        f"A 15 15 0 0 0 {cx + 15} {ft + 31} "
        f"C {cx + 15} {ft + 25} {cx + 13} {ft + 17} {cx} {ft} Z\" "
        f'fill="{FIRE}"/>\n'
        f'<path d="M {cx} {ft + 16} '
        f"C {cx - 6} {ft + 26} {cx - 7} {ft + 30} {cx - 7} {ft + 33} "
        f"A 7 7 0 0 0 {cx + 7} {ft + 33} "
        f"C {cx + 7} {ft + 30} {cx + 6} {ft + 26} {cx} {ft + 16} Z\" "
        f'fill="{FIRE_SOFT}"/>\n'
        f'<text x="{cx}" y="{cy + 16}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="44" font-weight="700" fill="{TEXT}">{cur_n}</text>\n'
        f'<text x="{cx}" y="238" text-anchor="middle" font-family="{FONT}" '
        f'font-size="22" font-weight="700" fill="{FIRE}">Current Streak</text>\n'
        f'<text x="{cx}" y="268" text-anchor="middle" font-family="{FONT}" '
        f'font-size="16" fill="{MUTED}">{esc(span_label(spans["current"]))}</text>\n'
        "</svg>"
    )
    return "".join(s)


# --------------------------------------------------------------- activity
# Minimal area graph: title + single total line, month ticks, no gridlines,
# no dots, one consistent card frame.

def render_activity(p: dict, login: str) -> str:
    W, H = 1200, 340
    pad_l, pad_r, pad_t, pad_b = 24, 24, 62, 38
    pw, ph = W - pad_l - pad_r, H - pad_t - pad_b

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="{esc(login)} contribution graph">\n'
        f"<title>{esc(login)}'s Contribution Graph</title>\n"
        f'<rect width="{W}" height="{H}" rx="10" fill="{BG}" stroke="{HAIRLINE}"/>\n'
        f'<text x="{pad_l + 24}" y="36" font-family="{FONT}" font-size="20" '
        f'font-weight="700" fill="{TEXT}">{esc(login)}&#39;s Contribution Graph</text>\n'
        f'<text x="{W - pad_r - 24}" y="36" text-anchor="end" font-family="{FONT}" '
        f'font-size="13" fill="{MUTED}">{fmt_int(p["total"])} contributions · last 12 months</text>\n'
    ]

    days = p["days"]
    if not days:
        parts.append(
            f'<text x="{W // 2}" y="{H // 2 + 10}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="15" fill="{MUTED}">'
            f"Contribution data unavailable for this run ({esc(p['error'])}). "
            "Next scheduled run will retry.</text>\n</svg>"
        )
        return "".join(parts)

    counts = [d["contributionCount"] for d in days]
    dates = [d["date"][:10] for d in days]
    peak = max(max(counts), 1)
    n = len(counts)

    def x(i: int) -> float:
        return pad_l + 24 + i * ((pw - 48) / max(n - 1, 1))

    def y(c: int) -> float:
        return pad_t + ph - 6 - (c / peak) * (ph - 12)

    # Month ticks only — no gridlines, no axis labels.
    seen = ""
    for i, ds in enumerate(dates):
        try:
            m = dt.date.fromisoformat(ds).strftime("%b")
        except ValueError:
            continue
        if m != seen:
            seen = m
            parts.append(
                f'<text x="{x(i):.1f}" y="{H - 13}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="11" fill="{MUTED}">{m}</text>\n'
            )

    pts = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, c in enumerate(counts))
    base = pad_t + ph - 6
    parts.append(
        f'<polygon points="{x(0):.1f},{base} {pts} {x(n - 1):.1f},{base}" '
        f'fill="{ACCENT}" fill-opacity="0.16"/>\n'
        f'<polyline points="{pts}" fill="none" stroke="{ACCENT}" '
        f'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>\n'
        "</svg>"
    )
    return "".join(parts)


# ----------------------------------------------------------------- quote

def render_quote() -> str:
    W, H = 1200, 200
    today = dt.date.today()
    quote, author = QUOTES[today.timetuple().tm_yday % len(QUOTES)]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="Dev quote of the day">\n'
        f"<title>Dev Quote</title>\n"
        f'<rect width="{W}" height="{H}" rx="10" fill="{BG}" stroke="{HAIRLINE}"/>\n'
        f'<rect x="28" y="28" width="4" height="{H - 56}" rx="2" fill="{ACCENT}"/>\n'
        f'<text x="52" y="58" font-family="{FONT}" font-size="12" letter-spacing="3" '
        f'fill="{MUTED}">DEV QUOTE · {esc(today.strftime("%A").upper())}</text>\n'
        f'<text x="52" y="112" font-family="{SERIF}" font-size="24" '
        f'font-style="italic" fill="{TEXT}">\u201c{esc(quote)}\u201d</text>\n'
        f'<text x="{W - 52}" y="{H - 40}" text-anchor="end" font-family="{FONT}" '
        f'font-size="15" fill="{ACCENT}">— {esc(author)}</text>\n'
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
    p = fetch_profile(login, token)
    spans = streak_spans(p["days"])
    _, grade = house_grade(p)

    outputs = {
        "stats.svg": render_stats(p),
        "streak.svg": render_streak(p, spans),
        "activity.svg": render_activity(p, login),
        "quote.svg": render_quote(),
    }
    for name, svg in outputs.items():
        with open(os.path.join(ASSETS, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(svg)
    for legacy in ("languages.svg", "principle.svg"):
        path = os.path.join(ASSETS, legacy)
        if os.path.exists(path):
            os.remove(path)
    with open(os.path.join(ASSETS, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "login": login,
                "updated": updated_stamp(),
                "total_contributions": p["total"],
                "stars": p["stars"],
                "commits": p["commits"],
                "grade": grade,
                "current_streak": spans["current"][0],
                "longest_streak": spans["longest"][0],
                "includes_private": includes_private,
                "api_error": p["error"],
            },
            f,
            indent=2,
        )
    print(
        f"generated {len(outputs)} SVGs for @{login} "
        f"(grade={grade}, api_error={p['error']!r})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
