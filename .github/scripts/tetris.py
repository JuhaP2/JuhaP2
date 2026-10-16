"""Render an animated game of Tetris as an SVG for a GitHub profile.

A small bot plays a real game on a 10x20 board: tetrominoes spawn at the
top, slide into position, drop row by row, and full lines flash and clear.
The score climbs as lines clear and finishes at the user's contribution
count for the last year.

Usage: GITHUB_TOKEN=... python tetris.py <user> <out_dir>
"""
import datetime
import json
import os
import random
import sys
import urllib.request

COLS, ROWS = 10, 20
S = 16                 # cell size in px
BX, BY = 16, 16        # board origin
PANEL_X = BX + COLS * S + 16
WIDTH = PANEL_X + 104
HEIGHT = BY + ROWS * S + 16

PIECES = 70            # pieces per game
TICK = 0.035           # seconds per move/drop step
LOCK = 0.08            # pause before a piece locks
CLEAR = 0.3            # line clear flash time
HOLD = 2.5             # seconds the final board stays up

BASE = {
    "I": [(0, 1), (1, 1), (2, 1), (3, 1)],
    "O": [(0, 0), (1, 0), (0, 1), (1, 1)],
    "T": [(1, 0), (0, 1), (1, 1), (2, 1)],
    "S": [(1, 0), (2, 0), (0, 1), (1, 1)],
    "Z": [(0, 0), (1, 0), (1, 1), (2, 1)],
    "J": [(0, 0), (0, 1), (1, 1), (2, 1)],
    "L": [(2, 0), (0, 1), (1, 1), (2, 1)],
}

THEMES = {
    "dark": {
        "bg": "#0B1E3A", "board": "#071428", "grid": "#10284A",
        "text": "#A9C7E8", "value": "#EAF6FF", "accent": "#2EE6A6",
        "colors": {"I": "#3FE0F5", "O": "#FFD166", "T": "#9B6CFF", "S": "#2EE6A6",
                   "Z": "#FF5C7A", "J": "#3F7CF5", "L": "#FF9F43"},
    },
    "light": {
        "bg": "#F4F9FF", "board": "#E3EEF9", "grid": "#D3E3F3",
        "text": "#355A80", "value": "#0B1E3A", "accent": "#1F6FA8",
        "colors": {"I": "#1FB8D6", "O": "#E8B33A", "T": "#7B4FE0", "S": "#18B886",
                   "Z": "#E8455F", "J": "#2F62D6", "L": "#E8832A"},
    },
}

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection { contributionCalendar { totalContributions } }
  }
}
"""


def fetch_total(user, token):
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
    return data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]


def rotations(cells):
    out, cur = [], cells
    for _ in range(4):
        min_x = min(x for x, _ in cur)
        min_y = min(y for _, y in cur)
        norm = sorted((x - min_x, y - min_y) for x, y in cur)
        if norm not in out:
            out.append(norm)
        cur = [(-y, x) for x, y in cur]
    return out


ROTS = {k: rotations(v) for k, v in BASE.items()}


# ---------------------------------------------------------------- game logic

def fits(board, shape, px, py):
    for x, y in shape:
        bx, by = px + x, py + y
        if bx < 0 or bx >= COLS or by >= ROWS:
            return False
        if by >= 0 and board[by][bx] is not None:
            return False
    return True


def drop_y(board, shape, px):
    if not fits(board, shape, px, 0):
        return None
    y = 0
    while fits(board, shape, px, y + 1):
        y += 1
    return y


def evaluate(board, shape, px, py):
    """Score a placement with the classic height/holes/bumpiness heuristic."""
    grid = [[c is not None for c in row] for row in board]
    for x, y in shape:
        grid[py + y][px + x] = True
    full = [r for r in range(ROWS) if all(grid[r])]
    grid = [row for r, row in enumerate(grid) if r not in full]
    grid = [[False] * COLS for _ in full] + grid
    heights, holes = [], 0
    for c in range(COLS):
        h, seen = 0, False
        for r in range(ROWS):
            if grid[r][c]:
                if not seen:
                    h, seen = ROWS - r, True
            elif seen:
                holes += 1
        heights.append(h)
    bump = sum(abs(heights[i] - heights[i + 1]) for i in range(COLS - 1))
    return -0.51 * sum(heights) + 0.76 * len(full) - 0.36 * holes - 0.18 * bump


def best_move(board, kind):
    best = None
    for rot, shape in enumerate(ROTS[kind]):
        w = max(x for x, _ in shape) + 1
        for px in range(COLS - w + 1):
            py = drop_y(board, shape, px)
            if py is None:
                continue
            score = evaluate(board, shape, px, py)
            if best is None or score > best[0]:
                best = (score, rot, px, py)
    return best


def row_clear(board, row, x1, x2):
    lo, hi = min(x1, x2), max(x1, x2)
    return all(board[row][x] is None for x in range(lo, hi + 1))


def simulate(seed):
    rng = random.Random(seed)
    bag, seq = [], []
    while len(seq) < PIECES + 1:
        if not bag:
            bag = list(BASE)
            rng.shuffle(bag)
        seq.append(bag.pop())

    board = [[None] * COLS for _ in range(ROWS)]
    cells = {}       # id -> {kind, x, y, t_on, events: [(t, prop, value)]}
    pieces = []      # {kind, shape, path: [(t, x, y)], t_off}
    clears = []      # (t, lines_total)
    t, lines, next_id = 0.4, 0, 0

    for i in range(PIECES):
        kind = seq[i]
        move = best_move(board, kind)
        if move is None:
            break
        _, rot, tx, ty = move
        shape = ROTS[kind][rot]
        w = max(x for x, _ in shape) + 1
        cx = (COLS - w) // 2
        if not fits(board, shape, cx, 0):
            cx = tx
        cy = 0
        path = [(t, cx, cy)]
        while (cx, cy) != (tx, ty):
            t += TICK
            if cx != tx:
                nx = cx + (1 if tx > cx else -1)
                if fits(board, shape, nx, cy):
                    cx = nx
            if cy < ty and fits(board, shape, cx, cy + 1):
                # Only drop if we can still slide over to the target column.
                if cx == tx or all(
                    fits(board, shape, x, cy + 1)
                    for x in range(min(cx, tx), max(cx, tx) + 1)
                ):
                    cy += 1
            path.append((t, cx, cy))
        t += LOCK
        pieces.append({"kind": kind, "shape": shape, "path": path, "t_off": t, "next": seq[i + 1]})

        for x, y in shape:
            cid = next_id
            next_id += 1
            board[ty + y][tx + x] = cid
            cells[cid] = {"kind": kind, "x": tx + x, "y": ty + y, "lock_y": ty + y, "t_on": t, "events": []}

        full = [r for r in range(ROWS) if all(c is not None for c in board[r])]
        if full:
            for r in full:
                for cid in board[r]:
                    cells[cid]["events"].append((t, "fill", "#FFFFFF"))
                    cells[cid]["events"].append((t + CLEAR, "opacity", 0))
            for r in range(ROWS):
                if r in full:
                    continue
                shift = sum(1 for f in full if f > r)
                for cid in board[r]:
                    if cid is not None and shift:
                        cells[cid]["y"] += shift
                        cells[cid]["events"].append((t + CLEAR, "dy", cells[cid]["y"]))
            kept = [row for r, row in enumerate(board) if r not in full]
            board = [[None] * COLS for _ in full] + kept
            lines += len(full)
            t += CLEAR
            clears.append((t, lines))
        t += 0.05

    return pieces, cells, clears, t + HOLD, lines


# ---------------------------------------------------------------- rendering

def render(pieces, cells, clears, cycle, total_lines, total, theme):
    th = THEMES[theme]
    pct = lambda s: f"{min(max(s / cycle * 100, 0), 100):.4f}%"
    css = [
        "*{animation-timing-function:step-end!important}",
        "text{font-family:'Fira Code','Consolas',monospace;font-weight:700}",
    ]
    out = []

    # Board and background grid.
    out.append(f'<rect width="{WIDTH}" height="{HEIGHT}" rx="12" fill="{th["bg"]}"/>')
    out.append(f'<rect x="{BX - 2}" y="{BY - 2}" width="{COLS * S + 4}" height="{ROWS * S + 4}" rx="4" fill="{th["board"]}" stroke="{th["grid"]}" stroke-width="2"/>')
    grid = "".join(
        f'<rect x="{BX + c * S + 1}" y="{BY + r * S + 1}" width="{S - 2}" height="{S - 2}" rx="2"/>'
        for r in range(ROWS) for c in range(COLS)
    )
    out.append(f'<g fill="{th["grid"]}" opacity=".45">{grid}</g>')

    def block(x, y, color):
        return (f'<rect x="{x + 1}" y="{y + 1}" width="{S - 2}" height="{S - 2}" rx="2" fill="{color}"/>'
                f'<rect x="{x + 3}" y="{y + 3}" width="{S - 8}" height="3" rx="1" fill="#FFFFFF" opacity=".35"/>')

    # Locked cells: appear on lock, shift down on clears, flash and vanish when cleared.
    end_fade = cycle - 0.5
    for cid, c in cells.items():
        frames = {0.0: {"opacity": "0"}}
        frames[c["t_on"]] = {"opacity": "1"}
        for t_ev, prop, val in c["events"]:
            f = frames.setdefault(t_ev, {})
            if prop == "fill":
                f["fill"] = val
            elif prop == "opacity":
                f["opacity"] = str(val)
            elif prop == "dy":
                f["transform"] = f"translateY({(val - c['lock_y']) * S}px)"
        if not any(p == "opacity" for _, p, _ in c["events"]):
            frames.setdefault(end_fade, {})["opacity"] = "0"
        frames.setdefault(cycle, {})["opacity"] = "0"
        kf = "".join(f"{pct(tm)}{{{';'.join(f'{k}:{v}' for k, v in props.items())}}}" for tm, props in sorted(frames.items()))
        css.append(f"@keyframes c{cid}{{{kf}}}.c{cid}{{animation:c{cid} {cycle:.3f}s infinite;opacity:0}}")
        x, y = BX + c["x"] * S, BY + c["lock_y"] * S
        out.append(f'<g class="c{cid}" fill="{th["colors"][c["kind"]]}">'
                   f'<rect x="{x + 1}" y="{y + 1}" width="{S - 2}" height="{S - 2}" rx="2"/>'
                   f'<rect x="{x + 3}" y="{y + 3}" width="{S - 8}" height="3" rx="1" fill="#FFFFFF" opacity=".35"/></g>')

    # Falling pieces.
    for i, p in enumerate(pieces):
        frames = [f"0%{{opacity:0;transform:translate({p['path'][0][1] * S}px,{p['path'][0][2] * S}px)}}"]
        for tm, x, y in p["path"]:
            frames.append(f"{pct(tm)}{{opacity:1;transform:translate({x * S}px,{y * S}px)}}")
        frames.append(f"{pct(p['t_off'])}{{opacity:0}}100%{{opacity:0}}")
        css.append(f"@keyframes a{i}{{{''.join(frames)}}}.a{i}{{animation:a{i} {cycle:.3f}s infinite;opacity:0}}")
        blocks = "".join(block(BX + x * S, BY + y * S, th["colors"][p["kind"]]) for x, y in p["shape"])
        out.append(f'<g class="a{i}">{blocks}</g>')

    # Side panel labels.
    px = PANEL_X
    out.append(f'<text x="{px}" y="{BY + 12}" font-size="15" fill="{th["accent"]}">TETRIS</text>')
    out.append(f'<text x="{px}" y="{BY + 44}" font-size="10" fill="{th["text"]}">NEXT</text>')
    out.append(f'<rect x="{px}" y="{BY + 52}" width="80" height="56" rx="6" fill="{th["board"]}" stroke="{th["grid"]}" stroke-width="2"/>')
    out.append(f'<text x="{px}" y="{BY + 140}" font-size="10" fill="{th["text"]}">SCORE</text>')
    out.append(f'<text x="{px}" y="{BY + 196}" font-size="10" fill="{th["text"]}">LINES</text>')
    out.append(f'<text x="{px}" y="{HEIGHT - 22}" font-size="8" fill="{th["text"]}" opacity=".7">score = contributions</text>')

    # Next-piece preview, one group per piece lifetime.
    for i, p in enumerate(pieces):
        shape = ROTS[p["next"]][0]
        w = max(x for x, _ in shape) + 1
        h = max(y for _, y in shape) + 1
        ox = px + 40 - w * 7
        oy = BY + 80 - h * 7
        start = p["path"][0][0]
        end = pieces[i + 1]["path"][0][0] if i + 1 < len(pieces) else cycle - 0.5
        css.append(f"@keyframes n{i}{{0%{{opacity:0}}{pct(start)}{{opacity:1}}{pct(end)}{{opacity:0}}100%{{opacity:0}}}}"
                   f".n{i}{{animation:n{i} {cycle:.3f}s infinite;opacity:0}}")
        rects = "".join(f'<rect x="{ox + x * 14 + 1}" y="{oy + y * 14 + 1}" width="12" height="12" rx="2"/>' for x, y in shape)
        out.append(f'<g class="n{i}" fill="{th["colors"][p["next"]]}">{rects}</g>')

    # Score and lines counters: one text per state, swapped at each clear.
    states = [(0.0, 0)] + clears
    for k, (tm, ln) in enumerate(states):
        score = round(total * ln / total_lines) if total_lines else (total if k else 0)
        if k == len(states) - 1:
            score = total
        end = states[k + 1][0] if k + 1 < len(states) else cycle
        css.append(f"@keyframes s{k}{{0%{{opacity:0}}{pct(tm)}{{opacity:1}}{pct(end)}{{opacity:0}}100%{{opacity:{1 if k == 0 else 0}}}}}"
                   f".s{k}{{animation:s{k} {cycle:.3f}s infinite;opacity:0}}")
        out.append(f'<g class="s{k}" fill="{th["value"]}">'
                   f'<text x="{px}" y="{BY + 166}" font-size="22">{score:,}</text>'
                   f'<text x="{px}" y="{BY + 222}" font-size="22">{ln}</text></g>')
    if not clears:
        out.append(f'<text x="{px}" y="{BY + 166}" font-size="22" fill="{th["value"]}">{total:,}</text>')

    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}">'
            f"<style>{''.join(css)}</style>{''.join(out)}</svg>")


def main():
    user, out_dir = sys.argv[1], sys.argv[2]
    total = fetch_total(user, os.environ["GITHUB_TOKEN"])
    seed = total * 1000 + datetime.date.today().toordinal()
    pieces, cells, clears, cycle, lines = simulate(seed)
    os.makedirs(out_dir, exist_ok=True)
    for theme in THEMES:
        with open(os.path.join(out_dir, f"tetris-{theme}.svg"), "w", encoding="utf-8") as f:
            f.write(render(pieces, cells, clears, cycle, lines, total, theme))
    print(f"{user}: {total} contributions, {len(pieces)} pieces, {lines} lines, {cycle:.1f}s loop")


if __name__ == "__main__":
    main()
