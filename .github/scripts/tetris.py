"""Render a GitHub contribution graph as an animated Tetris SVG.

Contribution cells are grouped into tetromino-sized pieces that drop from
the top of the board, slide into place and stack up into the real graph.

Usage: GITHUB_TOKEN=... python tetris.py <user> <out_dir>
"""
import json
import os
import sys
import urllib.request
from collections import deque

CELL = 12
GAP = 3
PITCH = CELL + GAP
PAD_X = 16
TOP = 44          # space above the board where pieces spawn
PAD_BOTTOM = 14
FALL = 0.55       # seconds a piece takes to fall
STAGGER = 0.22    # seconds between piece spawns
HOLD = 3.0        # seconds the finished board is shown
MAX_CYCLE = 40.0

THEMES = {
    "dark": {
        "bg": "#0B1E3A",
        "empty": "#123A5E",
        "levels": ["#123A5E", "#1F6FA8", "#3FA9F5", "#2EE6A6", "#B9FFE4"],
        "text": "#A9C7E8",
        "accent": "#2EE6A6",
    },
    "light": {
        "bg": "#F4F9FF",
        "empty": "#DDEAF6",
        "levels": ["#DDEAF6", "#9FCDF0", "#3FA9F5", "#1F6FA8", "#0B3D6B"],
        "text": "#355A80",
        "accent": "#1F6FA8",
    },
}

LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { weekday contributionLevel } }
      }
    }
  }
}
"""


def fetch_calendar(user, token):
    body = json.dumps({"query": QUERY, "variables": {"login": user}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    if "errors" in data:
        raise SystemExit(f"GraphQL error: {data['errors']}")
    return data["data"]["user"]["contributionsCollection"]["contributionCalendar"]


def build_grid(calendar):
    """Return {(week, weekday): level} for every day in the calendar."""
    grid = {}
    for x, week in enumerate(calendar["weeks"]):
        for day in week["contributionDays"]:
            grid[(x, day["weekday"])] = LEVELS.get(day["contributionLevel"], 0)
    return grid


def make_pieces(grid):
    """Group active cells into connected pieces of at most four cells."""
    active = {pos for pos, level in grid.items() if level > 0}
    pieces = []
    # Fill from the bottom row up, left to right, like a real stack.
    for start in sorted(active, key=lambda p: (-p[1], p[0])):
        if start not in active:
            continue
        piece, queue = [], deque([start])
        active.discard(start)
        while queue and len(piece) < 4:
            x, y = queue.popleft()
            piece.append((x, y))
            for nb in ((x + 1, y), (x, y - 1), (x - 1, y), (x, y + 1)):
                if nb in active and len(piece) + len(queue) < 4:
                    active.discard(nb)
                    queue.append(nb)
        active.update(queue)  # cells we queued but did not use
        pieces.append(piece)
    # Drop order: pieces resting lower land first.
    pieces.sort(key=lambda pc: (-max(y for _, y in pc), min(x for x, _ in pc)))
    return pieces


def render(calendar, theme):
    grid = build_grid(calendar)
    weeks = len(calendar["weeks"])
    width = PAD_X * 2 + weeks * PITCH - GAP
    height = TOP + 7 * PITCH - GAP + PAD_BOTTOM
    pieces = make_pieces(grid)
    t = THEMES[theme]

    stagger = STAGGER
    if pieces and len(pieces) * stagger + FALL + HOLD > MAX_CYCLE:
        stagger = (MAX_CYCLE - FALL - HOLD) / len(pieces)
    cycle = max(len(pieces) * stagger + FALL + HOLD, 4.0)
    pct = lambda s: round(min(s / cycle * 100, 100), 3)

    def cell_xy(x, y):
        return PAD_X + x * PITCH, TOP + y * PITCH

    spawn_x = PAD_X + (weeks // 2) * PITCH
    css, shapes = [], []
    for i, piece in enumerate(pieces):
        start = i * stagger
        land = start + FALL
        px = min(cell_xy(x, y)[0] for x, y in piece)
        py = min(cell_xy(x, y)[1] for x, y in piece)
        dx = spawn_x - px
        dy = -(py + PITCH * 2)
        css.append(
            f"@keyframes p{i}{{"
            f"0%,{pct(start)}%{{transform:translate({dx}px,{dy}px);opacity:0}}"
            f"{pct(start + 0.01)}%{{opacity:1}}"
            f"{pct(start + FALL * 0.45)}%{{transform:translate(0px,{round(dy * 0.4)}px)}}"
            f"{pct(land)}%,{pct(cycle - 0.6)}%{{transform:translate(0px,0px);opacity:1}}"
            f"100%{{transform:translate(0px,0px);opacity:0}}}}"
            f".p{i}{{animation:p{i} {cycle:.2f}s linear infinite}}"
        )
        rects = "".join(
            f'<rect x="{cx}" y="{cy}" width="{CELL}" height="{CELL}" rx="2" fill="{t["levels"][grid[(x, y)]]}"/>'
            for x, y in piece
            for cx, cy in [cell_xy(x, y)]
        )
        shapes.append(f'<g class="p{i}">{rects}</g>')

    flash_at = pct(cycle - HOLD + 0.3)
    css.append(
        f"@keyframes flash{{0%,{flash_at}%{{opacity:0}}{pct(cycle - HOLD + 0.5)}%{{opacity:.55}}"
        f"{pct(cycle - HOLD + 1.0)}%,100%{{opacity:0}}}}"
        f".flash{{animation:flash {cycle:.2f}s linear infinite}}"
    )

    empty = "".join(
        f'<rect x="{cx}" y="{cy}" width="{CELL}" height="{CELL}" rx="2"/>'
        for (x, y) in grid
        for cx, cy in [cell_xy(x, y)]
    )
    flash = "".join(
        f'<rect x="{cx}" y="{cy}" width="{CELL}" height="{CELL}" rx="2"/>'
        for (x, y), level in grid.items()
        if level > 0
        for cx, cy in [cell_xy(x, y)]
    )
    font = "font-family=\"'Fira Code','Consolas',monospace\" font-weight=\"700\""
    total = calendar["totalContributions"]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
        f"<style>{''.join(css)}</style>"
        f'<rect width="{width}" height="{height}" rx="10" fill="{t["bg"]}"/>'
        f'<text x="{PAD_X}" y="24" {font} font-size="13" fill="{t["accent"]}">CONTRIBUTION TETRIS</text>'
        f'<text x="{width - PAD_X}" y="24" {font} font-size="13" fill="{t["text"]}" text-anchor="end">SCORE {total:,}</text>'
        f'<g fill="{t["empty"]}">{empty}</g>'
        f"{''.join(shapes)}"
        f'<g class="flash" fill="#FFFFFF">{flash}</g>'
        f"</svg>"
    )


def main():
    user, out_dir = sys.argv[1], sys.argv[2]
    calendar = fetch_calendar(user, os.environ["GITHUB_TOKEN"])
    os.makedirs(out_dir, exist_ok=True)
    for theme in THEMES:
        with open(os.path.join(out_dir, f"tetris-{theme}.svg"), "w", encoding="utf-8") as f:
            f.write(render(calendar, theme))
    print(f"Rendered {calendar['totalContributions']} contributions for {user}")


if __name__ == "__main__":
    main()
