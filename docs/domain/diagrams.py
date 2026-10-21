#!/usr/bin/env python3
"""Draw the domain-model figures as SVG, one function per figure.

The figures are hand-laid-out on purpose: a domain model is an argument about what
belongs together, and an automatic layout cannot make that argument.
"""
import html
import os

BG = "#0b1118"
INK = "#e6eef8"
DIM = "#8ea0b6"
FAINT = "#5a6b80"
LINE = "#233244"

COLOUR = {
    "market": "#4f9bff",
    "intel": "#38d39f",
    "model": "#f5b544",
    "risk": "#ef6b6b",
    "evidence": "#a879ff",
    "control": "#7f8ea3",
}
FILL = {
    "market": "#0f1a28",
    "intel": "#0d1f1a",
    "model": "#221a10",
    "risk": "#251315",
    "evidence": "#1a1528",
    "control": "#151b24",
}
FONT = "-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"



def text_w(text, size):
    """Approximate rendered width, in the same units as the layout.

    Enough to keep a label inside its box: wide glyphs, narrow glyphs and digits
    differ by nearly a factor of two, and guessing one width for all of them is how
    labels end up over the border.
    """
    wide = sum(1 for c in text if c in "MWmw@" or c.isupper())
    narrow = sum(1 for c in text if c in "iljt.,;:'!| ")
    digits = sum(1 for c in text if c.isdigit())
    other = len(text) - wide - narrow - digits
    return size * (0.80 * wide + 0.34 * narrow + 0.58 * digits + 0.52 * other)


def fit(text, size, avail, floor):
    """Largest size at or below `size` that fits `avail`, never below `floor`."""
    avail *= 0.94
    while size > floor and text_w(text, size) > avail:
        size -= 0.5
    return size

class Fig:
    def __init__(self, w, h, title, subtitle=None, corner=None):
        self.w, self.h = w, h
        self.boxes = []
        self.notes = []
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
                      f'viewBox="0 0 {w} {h}" font-family="{FONT}">',
                      f'<rect width="{w}" height="{h}" fill="{BG}"/>']
        for k, c in COLOUR.items():
            self.parts.append(
                f'<marker id="a-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                f'markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{c}"/></marker>')
        t_size = fit(title, 25, w - 260, 15)
        self.parts.append(f'<text x="40" y="50" fill="{INK}" font-size="{t_size}" font-weight="700">'
                          f'{html.escape(title)}</text>')
        if subtitle:
            s_size = fit(subtitle, 15, w - 120, 10)
            self.parts.append(f'<text x="40" y="74" fill="{DIM}" font-size="{s_size}">'
                              f'{html.escape(subtitle)}</text>')
        if corner:
            c_size = fit(corner, 13, 300, 8.5)
            self.parts.append(f'<text x="{w-40}" y="46" fill="{FAINT}" font-size="{c_size}" '
                              f'text-anchor="end">{html.escape(corner)}</text>')

    def box(self, x, y, w, h, title, rows, colour, dashed=False, tag=None):
        c = COLOUR[colour]
        self.boxes.append((x, y, w, h))
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" '
                          f'fill="{FILL[colour]}" stroke="{c}" stroke-opacity="0.8" '
                          f'stroke-width="1.2"{dash}/>')
        title_size = fit(title.upper(), 15.0, w - 26, 9.5)
        self.parts.append(f'<text x="{x+10}" y="{y+22}" fill="{c}" font-size="{title_size}" '
                          f'font-weight="700" letter-spacing="0.06em">{html.escape(title.upper())}</text>')
        if tag:
            ty = y + 20 if len(title) <= 16 else y + h - 10
            self.parts.append(f'<text x="{x+w-12}" y="{ty}" fill="{FAINT}" font-size="8.5" '
                              f'text-anchor="end">{html.escape(tag)}</text>')
        yy = y + 40
        # the tallest size that fits both the width and the number of rows
        by_height = ((h - 46) / max(len(rows), 1)) - 8 if rows else 16
        for row in rows:
            name, kind = row if isinstance(row, tuple) else (row, "")
            bold = kind in ("aggregate", "entity", "state")
            tag_w = text_w(kind.upper(), 11) + 12 if kind else 0
            size = fit(name, min(16.0, by_height), w - 24 - tag_w, 9.5)
            self.parts.append(
                f'<text x="{x+10}" y="{yy}" fill="{INK if bold else DIM}" font-size="{size}" '
                f'font-weight="{600 if bold else 400}">{html.escape(name)}</text>')
            if kind:
                ts = fit(kind.upper(), 11, tag_w - 6, 7.5)
                self.parts.append(f'<text x="{x+w-10}" y="{yy}" fill="{FAINT}" font-size="{ts}" '
                                  f'text-anchor="end" letter-spacing="0.04em">'
                                  f'{html.escape(kind.upper())}</text>')
            yy += size + 8
        return self

    def diamond(self, cx, cy, w, h, text, colour):
        c = COLOUR[colour]
        pts = f"{cx},{cy-h/2} {cx+w/2},{cy} {cx},{cy+h/2} {cx-w/2},{cy}"
        self.parts.append(f'<polygon points="{pts}" fill="{FILL[colour]}" stroke="{c}" '
                          f'stroke-opacity="0.85" stroke-width="1.2"/>')
        words = text.split("\n")
        size = min(fit(wd, 15.5, w * 0.60, 9.5) for wd in words) if words else 12
        for i, word in enumerate(words):
            dy = (i - (len(words) - 1) / 2) * (size + 4) + size * 0.36
            self.parts.append(f'<text x="{cx}" y="{cy+dy}" fill="{INK}" font-size="{size}" '
                              f'text-anchor="middle">{html.escape(word)}</text>')
        return self

    def arrow(self, pts, colour, dashed=False, width=1.4, label=None, lx=0, ly=0, anchor="start"):
        d = "M " + " L ".join(f"{a},{b}" for a, b in pts)
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{COLOUR[colour]}" '
                          f'stroke-opacity="0.8" stroke-width="{width}"{dash} '
                          f'marker-end="url(#a-{colour})"/>')
        if label:
            run = max(abs(pts[-1][0] - pts[0][0]), abs(pts[-1][1] - pts[0][1]), 40)
            size = fit(label, 13, max(run * 0.85, 60), 8.5)
            self.parts.append(f'<text x="{lx}" y="{ly}" fill="{DIM}" font-size="{size}" '
                              f'text-anchor="{anchor}">{html.escape(label)}</text>')
        return self

    def note(self, x, y, text, size=20, colour=DIM, anchor="start", weight="400"):
        room = (self.w - x - 30) if anchor == "start" else (x - 30)
        size = fit(text, min(size, 22), room, 12)
        width = text_w(text, size)
        x0 = x if anchor == "start" else x - width
        h = size + 4

        def blocked(at):
            """Lowest y this note must move to, or None if the line is clear."""
            for bx, by, bw, bh in self.boxes:
                if x0 < bx + bw and x0 + width > bx and at - size < by + bh and at > by:
                    return by + bh + size + 10
            for nx, ny, nw, nh in self.notes:
                if x0 < nx + nw and x0 + width > nx and at - size < ny + nh and at > ny:
                    return ny + nh + 8
            return None

        for _ in range(14):
            nxt = blocked(y)
            if nxt is None:
                break
            y = max(y, nxt)
        self.notes.append((x0, y - size, width, h))
        self.parts.append(f'<text x="{x}" y="{y}" fill="{colour}" font-size="{size}" '
                          f'text-anchor="{anchor}" font-weight="{weight}">'
                          f'{html.escape(text)}</text>')
        return self

    def bar(self, x, y, w, h, ratio, colour, label=None):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="2" fill="#141d28"/>')
        self.parts.append(f'<rect x="{x}" y="{y}" width="{max(2, w*ratio):.0f}" height="{h}" '
                          f'rx="2" fill="{COLOUR[colour]}"/>')
        if label:
            self.note(x + w + 8, y + h - 1, label, size=19)
        return self

    def save(self, path):
        self.parts.append("</svg>")
        with open(path, "w") as fh:
            fh.write("\n".join(self.parts))
        return path


# ---------------------------------------------------------------- 1 · context map
def context_map():
    f = Fig(1660, 760, "Domain model, level 1 — the contexts",
            "what the platform is about, and which way knowledge flows",
            "one envelope contract · one bus · one record")
    f.box(40, 118, 300, 190, "Market data", [
        ("Symbol universe", "value"), ("Kline series", "entity"),
        ("Depth book", "entity"), ("Feed shard", "service")], "market", tag="ingest")
    f.box(40, 470, 300, 170, "Intelligence", [
        ("Signal", "entity"), ("Flow window", "entity"),
        ("TA reading", "entity"), ("Forecast", "entity")], "intel", tag="derived")

    f.box(420, 110, 320, 240, "Books", [
        ("Book", "aggregate"), ("Position", "entity"), ("Trade", "entity"),
        ("Decision", "entity"), ("Refusal", "entity")], "model", tag="decides")
    f.box(420, 450, 320, 190, "Risk & execution", [
        ("Kill switch", "aggregate"), ("Risk gate", "service"),
        ("Sizing rule", "value"), ("Exit policy", "value")], "risk", tag="refuses")

    f.box(820, 110, 320, 200, "Recording", [
        ("Envelope", "aggregate"), ("Recorder", "service"),
        ("Lake segment", "entity"), ("Derived reference", "value")], "evidence", tag="writes")
    f.box(820, 430, 320, 210, "Forensics", [
        ("Case file", "aggregate"), ("Book report", "aggregate"),
        ("Search", "service"), ("Journey", "service")], "evidence", tag="reads")

    f.box(1220, 110, 400, 530, "Control", [
        ("Trading state", "aggregate"), ("Supervisor", "service"),
        ("Heartbeat", "service"), ("Stop flag", "entity"),
        ("Topic registry", "aggregate"), ("Interceptor chain", "service"),
        ("Operator registry", "service"), ("publishes system.control", "value"),
        ("STOP resets books and stats", "value")], "control", dashed=True, tag="may stop")

    f.arrow([(340, 200), (420, 200)], "market", label="market.*", lx=346, ly=190)
    f.arrow([(340, 545), (385, 545), (385, 330), (420, 330)], "intel", width=1.2,
            label="intel.*", lx=346, ly=562)
    f.arrow([(580, 350), (580, 450)], "model")
    f.note(592, 424, "proposed order", size=20)
    f.arrow([(470, 450), (470, 350)], "risk", dashed=True, width=1.2)
    f.note(458, 424, "allowed / refused", size=20, anchor="end")
    f.arrow([(740, 230), (786, 230), (786, 190), (820, 190)], "model",
            label="decision · fill", lx=748, ly=252)
    f.arrow([(340, 92), (820, 92)], "market", dashed=True, width=1.0)
    f.note(352, 84, "every topic is recorded, not only trades", size=19)
    f.arrow([(1140, 310), (1140, 430)], "evidence", label="read-only replay", lx=1150, ly=376)
    f.arrow([(1330, 360), (1140, 360)], "control", dashed=True, width=1.0)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "01-contexts.svg"))


# ---------------------------------------------------------------- 2 · envelope
def envelope():
    f = Fig(1660, 700, "The envelope — the only thing that travels",
            "data · meta · refs, and the hops that prove what happened to it",
            "version 1, one predecessor accepted")
    f.box(60, 120, 420, 300, "data", [
        ("the payload the producer computed", "value"),
        ("shape owned by the topic", "value"),
        ("stored verbatim", "value")], "market", tag="never rewritten")
    f.box(510, 120, 520, 300, "meta", [
        ("version · plane · topic · key", "value"),
        ("producer · generation · sequence", "value"),
        ("cid · computed_at · received_at", "value"),
        ("compute_ms", "value"),
        ("extra — free-form markers", "value")], "evidence", tag="provenance")
    f.box(1060, 120, 520, 300, "refs and hops", [
        ("ref: topic, key, generation, cid", "entity"),
        ("ref: derived, age_ms", "value"),
        ("hop: interceptor, name, outcome", "entity"),
        ("hop: operator, transformer", "entity"),
        ("hop: ms, detail", "value")], "model", tag="the journey")
    f.arrow([(480, 270), (510, 270)], "market", width=1.2)
    f.arrow([(1030, 270), (1060, 270)], "evidence", width=1.2)
    f.note(60, 470, "Unknown top-level member is refused; unknown meta key is ignored; "
                    "version N-1 is accepted so a writer can be rolled forward.", size=19)
    f.note(60, 492, "A reference is declared by a producer or derived by the recorder — "
                    "and either way it carries its age, so staleness is a number, not an opinion.", size=19)
    f.arrow([(300, 560), (1300, 560)], "control", dashed=True, width=1.0)
    f.note(300, 550, "interceptors → operator → transformers, each appending a hop", size=19)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "02-envelope.svg"))


# ---------------------------------------------------------------- 3 · market data
def market_data():
    f = Fig(1660, 700, "Market data — from the exchange socket to a topic",
            "400 symbols, sharded, parsed once, published once",
            "the only process that talks to the exchange")
    f.box(40, 120, 300, 120, "Exchange", [
        ("stream.binance.com", "service"), ("combined streams", "value")], "market")
    f.box(390, 100, 330, 210, "Shards", [
        ("shard 0 — 900 streams", "service"), ("shard 1 — 701 streams", "service"),
        ("reconnect with backoff", "value")], "market", tag="≤1024 each")
    f.box(770, 100, 330, 240, "Parse", [
        ("bookTicker → top of book", "value"), ("depth5 → depth book", "value"),
        ("kline → series, closed flag", "value"), ("miniTicker → 24h stats", "value")], "market")
    f.box(1150, 100, 330, 240, "State", [
        ("Prices", "entity"), ("Books", "entity"), ("Klines", "entity"),
        ("Mini stats", "entity")], "market", tag="in memory")
    f.box(1150, 430, 330, 200, "Topics", [
        ("market.price", "value"), ("market.depth", "value"),
        ("market.candle", "value"), ("ticks · mini24h", "value")], "evidence")
    f.arrow([(340, 180), (390, 180)], "market")
    f.arrow([(720, 200), (770, 200)], "market", label="decode", lx=726, ly=190)
    f.arrow([(1100, 200), (1150, 200)], "market", label="apply", lx=1106, ly=190)
    f.arrow([(1315, 340), (1315, 430)], "market", label="publish", lx=1325, ly=392)
    f.note(40, 320, "Publishing happens on its own goroutine through a bounded queue:", size=19)
    f.note(40, 340, "a blocked broker write must never stop the socket read, or Binance closes it.", size=19)
    f.note(40, 380, "Depth is sampled: under pressure a sample is dropped and counted,", size=19)
    f.note(40, 398, "because a stale price chart is worse than a missing tick.", size=19)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "03-market-data.svg"))


# ---------------------------------------------------------------- 4 · book
def book_aggregate():
    f = Fig(1660, 700, "Book — the aggregate that decides",
            "one equity, a set of positions, and the decisions that produced them",
            "every book is one aggregate")
    f.box(60, 110, 460, 260, "Book", [
        ("equity", "value"), ("start_equity", "value"),
        ("positions: symbol → Position", "entity"),
        ("trades: the closed record", "entity"),
        ("strategy · risk size", "value")], "model", tag="aggregate root")
    f.box(600, 110, 400, 200, "Position", [
        ("symbol · side", "value"), ("entry · notional · qty", "value"),
        ("opened_at · peak", "value")], "model", tag="inside the boundary")
    f.box(600, 380, 400, 210, "Trade (model.fill)", [
        ("gross_pct · net_pct", "value"), ("held_s · reason", "value"),
        ("pnl_usd · entry_ms/exit_ms", "value")], "evidence", tag="published")
    f.box(1080, 110, 400, 200, "Decision", [
        ("engine · symbol · side", "value"),
        ("allowed · reason · confidence", "value")], "model", tag="model.decision")
    f.box(1080, 380, 400, 210, "Refusal", [
        ("the rule that stopped it", "value"),
        ("the number that tripped it", "value"),
        ("the input's age", "value")], "risk", tag="a first-class outcome")
    f.arrow([(520, 180), (600, 180)], "model")
    f.note(560, 170, "holds", size=19, anchor="middle")
    f.arrow([(1080, 300), (1000, 300)], "model")
    f.arrow([], "model") if False else None
    f.note(1040, 352, "authorises", size=13, anchor="middle")
    f.arrow([(560, 370), (560, 520), (600, 520)], "model")
    f.note(474, 552, "closes into", size=19)
    f.arrow([(1006, 520), (1080, 520)], "risk")
    
    f.note(60, 430, "Invariants the aggregate keeps:", size=19, weight="600", colour=INK)
    f.note(60, 458, "· a position is never opened without an allowed decision", size=19)
    f.note(60, 482, "· a trade is only born from a position that existed", size=19)
    f.note(60, 506, "· equity moves only by a closed trade's net, never by opinion", size=19)
    f.note(60, 530, "· a refusal is recorded even though nothing was bought", size=19)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "04-book.svg"))


# ---------------------------------------------------------------- 5 · risk
def risk_gate():
    f = Fig(1660, 660, "The gate — what must pass before money moves",
            "authority, keying, freshness, correlation, then the arithmetic of risk",
            "every refusal names the rule")
    steps = [("authority", "interceptor", "market"), ("keying", "interceptor", "market"),
             ("freshness", "interceptor", "market"), ("correlation", "interceptor", "evidence"),
             ("risk.allow", "operator", "model"), ("size_for", "operator", "model"),
             ("hold_loser / exit policy", "operator", "risk")]
    x = 40
    for i, (name, kind, col) in enumerate(steps):
        f.box(x, 130, 200, 110, name, [(kind, "value")], col, tag=str(i + 1))
        if i:
            f.arrow([(x - 22, 185), (x, 185)], col, width=1.2)
        x += 222
    f.note(40, 300, "Each stage appends a hop. A refusal is a hop with outcome=refuse and the rule "
                    "in its detail — so 'why not' is answered by the same object as 'why'.", size=19)
    f.box(40, 360, 500, 180, "What the gate checks", [
        ("size above zero", "value"), ("forecast age ≤ limit", "value"),
        ("signal age ≤ limit", "value"), ("kill switch clear", "value"),
        ("gross multiple ≤ limit", "value"), ("symbol share ≤ limit", "value")], "risk")
    f.box(580, 360, 500, 180, "What the book then does", [
        ("size = equity · risk / stop", "value"),
        ("cap at a quarter of equity", "value"),
        ("take profit above the cost floor", "value"),
        ("stop, time cap, or target", "value")], "model")
    f.box(1120, 360, 500, 180, "What is never true", [
        ("a losing exit is not refused", "value"),
        ("a stop is not widened", "value"),
        ("a stopped book does not trade", "value"),
        ("a refusal is not silent", "value")], "control", dashed=True)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "05-risk.svg"))


# ---------------------------------------------------------------- 6 · recorder
def recorder():
    f = Fig(1660, 680, "The recorder — the only writer of the record",
            "one consumer per topic, interceptors run one by one, hops recorded as they pass",
            "read-only elsewhere")
    f.box(40, 120, 300, 170, "Bus", [
        ("topic exchange", "value"), ("q.rec.<topic>", "entity")], "evidence")
    f.box(390, 100, 360, 220, "Interceptors", [
        ("authority — pass / refuse", "entity"), ("keying", "entity"),
        ("freshness", "entity"), ("correlation", "entity")], "risk", tag="each hop recorded")
    f.box(800, 100, 340, 220, "Record", [
        ("attach derived refs", "value"),
        ("append to the journal", "entity"),
        ("publish to the live feed", "service")], "evidence")
    f.box(1190, 100, 420, 220, "Lake", [
        ("one line, one envelope", "value"),
        ("state/lake3/*.jsonl", "entity"),
        ("indexed by topic offsets", "value")], "evidence", tag="append only")
    f.arrow([(340, 200), (390, 200)], "evidence", label="consume", lx=346, ly=190)
    f.arrow([(750, 200), (800, 200)], "risk", label="pass", lx=756, ly=190)
    f.arrow([(1140, 200), (1190, 200)], "evidence", label="append", lx=1146, ly=190)
    f.note(40, 340, "A refused envelope is recorded too, with extra.refused = true. "
                    "The record therefore contains what did not happen.", size=19)
    f.box(40, 400, 500, 190, "Derived references", [
        ("newest input per topic, within 5 minutes", "value"),
        ("marked derived: true", "value"),
        ("carries age_ms at decision time", "value")], "evidence")
    f.box(580, 400, 500, 190, "Why derived exists", [
        ("books do not declare their inputs", "value"),
        ("without it, a decision has no parents", "value"),
        ("with it, a chain can be walked", "value")], "model")
    f.box(1120, 400, 490, 190, "What it is not", [
        ("not a claim the producer made", "value"),
        ("not a substitute for declared refs", "value"),
        ("always labelled, never implied", "value")], "control", dashed=True)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "06-recorder.svg"))


# ---------------------------------------------------------------- 7 · lake
def lake():
    f = Fig(1660, 720, "The lake — what is actually on disk",
            "one journal per era, one line per envelope, rotated, compressed, expired",
            "the record is a file")
    f.box(40, 110, 420, 210, "Directory", [
        ("state/lake3/", "entity"), ("envelopes.jsonl", "entity"),
        ("seg-*.jsonl", "entity"), ("*.gz — sealed segments", "entity")], "evidence", tag="append only")
    f.box(520, 110, 460, 210, "Line format", [
        ("one JSON object per line", "value"),
        ("{topic, key, ts, payload}", "value"),
        ("payload is the envelope", "value")], "evidence")
    f.box(1040, 110, 580, 210, "Index", [
        ("topic → byte offsets", "entity"),
        ("built once, extended on append", "value"),
        ("refresh throttled to 30 s", "value"),
        ("cold query reads a handful of lines", "value")], "model", tag="in memory")
    f.arrow([(460, 200), (520, 200)], "evidence")
    f.arrow([(980, 200), (1040, 200)], "model", label="offsets", lx=986, ly=190)

    f.box(40, 380, 300, 190, "Retention", [
        ("rotate at a size", "value"), ("gzip the sealed file", "value"),
        ("expire after a window", "value")], "control")
    f.box(380, 380, 380, 190, "Tiers", [
        ("bronze — raw journal", "state"), ("silver — features", "state"),
        ("gold — datasets", "state"), ("archive — cold", "state")], "intel")
    f.box(800, 380, 380, 190, "Replay", [
        ("replay a window", "service"),
        ("same envelopes, same order", "value"),
        ("rebuild derived state", "value")], "evidence")
    f.box(1220, 380, 400, 190, "Beneath the lake", [
        ("the filesystem, and nothing else", "value"),
        ("no database, no queue, no cache", "value"),
        ("readable with grep and jq", "value")], "control", dashed=True)

    f.note(40, 620, "A lake that cannot be read with grep is not a record; it is a service dependency. "
                    "This one is plain JSONL, so an investigator can read it", size=19)
    f.note(40, 640, "without the platform running — which is precisely what makes it evidence.", size=19)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "07-lake.svg"))


# ---------------------------------------------------------------- 8 · control
def control():
    f = Fig(1660, 660, "Control — what may change behaviour",
            "supervise, stop, and nothing else",
            "no data path may stop the platform")
    f.box(60, 120, 420, 220, "Supervisor", [
        ("engine registry", "entity"), ("restart on exit", "service"),
        ("equity and open counts", "value")], "control")
    f.box(540, 120, 460, 220, "Trading state", [
        ("on / off", "state"), ("stop flag on disk", "entity"),
        ("reset books and stats", "service")], "risk", tag="aggregate")
    f.box(1060, 120, 460, 220, "Heartbeat", [
        ("what is alive now", "value"), ("equity from the books", "value"),
        ("wins / losses", "value")], "control")
    f.arrow([(480, 230), (540, 230)], "control")
    f.arrow([(1000, 230), (1060, 230)], "risk")
    f.note(60, 400, "STOP means stop:", size=19, weight="600", colour=INK)
    for i, line in enumerate(["kills every trading process",
                              "writes the flag every gate reads",
                              "publishes system.control on the bus",
                              "resets books and statistics, archived first"]):
        f.note(60, 422 + i * 18, "· " + line, size=19)
    f.note(60, 520, "and what STOP must never touch:", size=19, weight="600", colour=INK)
    for i, line in enumerate(["market data", "the recorder", "the explorer"]):
        f.note(60, 542 + i * 18, "· " + line, size=19)
    f.box(860, 380, 660, 240, "The invariant", [
        ("no data path can stop the platform", "value"),
        ("no control path can invent data", "value"),
        ("a stopped book is visibly stopped", "value"),
        ("a refused trade is visibly refused", "value")], "control", dashed=True)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "08-control.svg"))



# ---------------------------------------------------------------- 9 · data stores
def stores():
    f = Fig(1660, 700, "Data stores, as they exist on this machine",
            "what is written where, in which format, who writes it and who may read it",
            "60 GB of journal · 105 state files · 8 ledgers")
    top, bottom = 110, 380

    f.box(40, top, 380, 150, "The lake", [
        ("state/lake3/envelopes.jsonl", "entity"),
        ("60.2 GB · one journal", "value"),
        ("JSONL, one envelope per line", "value"),
        ("writer: the recorder", "service")], "evidence", tag="bronze")

    f.box(470, top, 380, 150, "Derived datasets", [
        ("datasets/features.csv", "entity"),
        ("datasets/manifest.json", "entity"),
        ("datasets/model/*.json", "entity"),
        ("writer: the historian", "service")], "intel", tag="silver · gold")

    f.box(900, top, 340, 150, "Live state", [
        ("state/pack_*.json", "entity"),
        ("state/strategy_*.json", "entity"),
        ("105 files, rewritten in place", "value")], "model", tag="not a record")

    f.box(1290, top, 330, 150, "Observability", [
        ("logs/*.log", "entity"), ("104 MB · 115 files", "value"),
        ("bridge/messages.jsonl", "entity")], "control", tag="diagnostic")

    f.box(40, bottom, 380, 150, "Older journals", [
        ("state/lake/", "entity"), ("state/lake2/", "entity"),
        ("state/lake4/", "entity"),
        ("kept for replay comparison", "value")], "evidence", tag="cold")

    f.box(470, bottom, 380, 150, "Ledgers", [
        ("ledger/*.jsonl", "entity"),
        ("one file per engine, written by the engine", "value"),
        ("state/fills.jsonl", "entity")], "model", tag="append only")

    f.box(900, bottom, 340, 150, "Archives", [
        ("state/archive_stop_*", "entity"),
        ("state/archive_*", "entity"),
        ("a copy before every reset", "value")], "control", tag="recoverable")

    f.box(1290, bottom, 330, 150, "In memory", [
        ("topic → byte offsets", "entity"),
        ("the newest 20,000 envelopes", "value"),
        ("disposable, rebuilt on boot", "value")], "evidence", tag="index")

    # only the relationships that actually exist
    f.arrow([(420, 185), (470, 185)], "intel")
    f.note(470, 292, "the historian reads the journal and writes the datasets", size=19)
    f.arrow([(1240, 300), (1240, 380)], "control", label="before a reset", lx=1250, ly=340)

    f.note(40, 590, "The journal is the record. Everything else here is derived from it, a cache of it, "
                    "or a copy taken before something was reset.", size=19)
    f.note(40, 612, "Only the ledgers and the logs are written by more than one process, and both are "
                    "append-only.", size=19)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "09-stores.svg"))


def decision_tree():
    f = Fig(1660, 780, "How the lake chooses — a decision tree",
            "what lands where, how long it stays, and when a segment is sealed",
            "every branch is a rule, not a habit")
    cx = 830

    f.diamond(cx, 120, 420, 84, "an envelope reaches the recorder", "control")
    f.arrow([(cx, 162), (cx, 208)], "control")

    f.diamond(cx, 250, 420, 84, "is it a trading outcome?", "model")
    f.arrow([(cx - 210, 250), (520, 250)], "model", label="yes", lx=566, ly=240)
    f.box(120, 195, 400, 110, "Silver — the trade record", [
        ("ledger/<engine>.jsonl", "entity"), ("state/fills.jsonl", "entity"),
        ("append only, never expired", "value")], "model")

    f.arrow([(cx, 292), (cx, 338)], "evidence", label="no", lx=cx + 14, ly=318)

    f.diamond(cx, 380, 460, 84, "is it a market or intel topic?", "market")
    f.arrow([(cx + 230, 380), (1150, 380)], "market", label="yes", lx=1122, ly=370)
    f.box(1150, 325, 420, 110, "Bronze — the journal", [
        ("one JSONL line per envelope", "value"),
        ("read by the explorer and the historian", "value")], "evidence")

    f.arrow([(cx - 230, 380), (520, 380)], "control", label="no", lx=566, ly=370)
    f.box(120, 325, 400, 110, "Control and health", [
        ("seven-day retention", "value"),
        ("small, high value, short lived", "value")], "control")

    f.arrow([(cx, 422), (cx, 500)], "evidence")
    f.diamond(cx, 545, 420, 84, "is the segment over 1 GB?", "risk")

    f.arrow([(cx + 210, 545), (1150, 545)], "control", label="yes", lx=1122, ly=535)
    f.box(1150, 495, 420, 100, "Seal and gzip", [
        ("the segment becomes immutable", "value"),
        ("byte offsets in the index stay valid", "value")], "control")

    f.arrow([(cx - 210, 545), (520, 545)], "intel", label="no", lx=566, ly=535)
    f.box(120, 495, 400, 100, "Keep appending", [
        ("the hot file stays small", "value"),
        ("a cold query reads its tail", "value")], "intel")

    f.note(120, 660, "Each level answers one question: what is it, how long does it live, "
                     "is it big enough to seal.", size=19)
    f.note(120, 686, "Nothing is deleted by a rule that is not drawn here — and the tree is "
                     "deliberately short enough to check.", size=19)
    return f.save(os.path.join(os.path.dirname(__file__), "fig", "10-decision-tree.svg"))


ALL = [context_map, envelope, market_data, book_aggregate, risk_gate, recorder, lake,
       control, stores, decision_tree]

if __name__ == "__main__":
    for fn in ALL:
        print("wrote", fn())
