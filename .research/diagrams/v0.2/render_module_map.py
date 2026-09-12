"""Render the architecture overview as editable SVG and a PNG preview.

This is a documentation drawing helper, not an implementation of the agent.
Uses the already-available Pillow package for raster output; no network access.
"""

from html import escape
from math import atan2, cos, sin, pi
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
W, H = 2160, 1810
BG = "#f6f8fc"
INK = "#152b45"
DIM = "#53667e"
LINE = "#64809e"
PURPLE = "#8461b5"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
image = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(image)
svg = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title description">',
    '<title id="title">Agent module ownership map: all 23 requested modules</title>',
    '<desc id="description">Six ownership groups contain all 23 requested Python modules. Context compilation, runtime supervision, operational durability, and an isolated C++ memory engine provide shared support. ACP means Agent Client Protocol. Detailed flow diagrams accompany this map.</desc>',
    f'<rect width="{W}" height="{H}" fill="{BG}"/>',
]


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONT_DIR / name), size)


def text(x, y, value, size=22, color=INK, bold=False, center=False):
    f = font(size, bold)
    anchor = "mt" if center else "lt"
    draw.text((x, y), value, font=f, fill=color, anchor=anchor)
    align = ' text-anchor="middle"' if center else ""
    weight = "700" if bold else "400"
    svg.append(
        f'<text x="{x}" y="{y}" font-family="DejaVu Sans, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{color}" '
        f'dominant-baseline="text-before-edge"{align}>{escape(value)}</text>'
    )


def rect(x, y, w, h, fill, stroke, radius=18, width=2):
    draw.rounded_rectangle((x, y, x + w, y + h), radius, fill=fill, outline=stroke, width=width)
    svg.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'
    )


def path(points, color=LINE, width=3, dashed=False, arrow=True):
    if dashed:
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if not length:
                continue
            for start in range(0, int(length), 17):
                end = min(start + 9, length)
                draw.line((x1 + (x2-x1)*start/length, y1 + (y2-y1)*start/length,
                           x1 + (x2-x1)*end/length, y1 + (y2-y1)*end/length), fill=color, width=width)
    else:
        draw.line(points, fill=color, width=width, joint="curve")
    coords = " ".join(f"{x},{y}" for x, y in points)
    dash = ' stroke-dasharray="9 8"' if dashed else ""
    svg.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"{dash}/>')
    if arrow:
        x, y = points[-1]
        px, py = points[-2]
        angle = atan2(y - py, x - px)
        tri = [(x, y), (x-14*cos(angle-pi/6), y-14*sin(angle-pi/6)),
               (x-14*cos(angle+pi/6), y-14*sin(angle+pi/6))]
        draw.polygon(tri, fill=color)
        svg.append(f'<polygon points="{" ".join(f"{a:.2f},{b:.2f}" for a,b in tri)}" fill="{color}"/>')


def box(x, y, w, h, title, lines, kind="python"):
    styles = {
        "python": ("#edf5ff", "#739ecf"),
        "cpp": ("#f1ebfb", "#a389c5"),
        "store": ("#fff5de", "#c5a966"),
        "external": ("#edf0f4", "#99a8b8"),
    }
    fill, stroke = styles[kind]
    rect(x, y, w, h, fill, stroke)
    title_size = 24
    while font(title_size, True).getlength(title) > w - 28:
        title_size -= 1
    text(x+w/2, y+20, title, title_size, bold=True, center=True)
    for i, line in enumerate(lines):
        body_size = 19
        while font(body_size).getlength(line) > w - 24:
            body_size -= 1
        text(x+w/2, y+58+i*28, line, body_size, DIM, center=True)


# Ownership map; the companion Mermaid diagrams specify runtime flow.
text(60, 40, "An agent with a continuing digital situation", 43, bold=True)
text(60, 101, "23 Python modules  /  shared operational state  /  isolated C++ memory", 24, DIM)
text(2055, 47, "v0.2", 28, DIM, center=True)
text(60, 145, "Module names clarified against the user-supplied source; architecture designed for this discussion.", 19, DIM)


def card(x, y, w, h, heading, rows, note=None, kind="python"):
    fill, stroke = ("#edf5ff", "#739ecf") if kind == "python" else ("#f1ebfb", "#a389c5")
    rect(x, y, w, h, fill, stroke)
    text(x+25, y+22, heading, 25, bold=True)
    for i, (label, desc) in enumerate(rows):
        yy=y+76+i*56
        assert font(22, True).getlength(label) < w-50, label
        assert font(18).getlength(desc) < w-50, desc
        text(x+25, yy, label, 22, bold=True)
        text(x+25, yy+28, desc, 18, DIM)
    if note:
        assert font(17).getlength(note) < w-50, note
        text(x+25, y+h-36, note, 17, DIM)

card(60, 200, 650, 510, "01  CLIENTS AND CHANNELS", [
    ("acp", "Agent Client Protocol: sessions, prompts, updates"),
    ("channel-adapters", "Concrete transports, account health, attachments"),
    ("channel-core", "Typed messages, identity, routing, input admission"),
    ("delivery", "Durable outbox, ordered sends, receipts and retries"),
], "Input acceptance and output delivery progress independently.")

card(755, 200, 650, 510, "02  SITUATION AND INTENT", [
    ("profile", "Durable configuration, scoped roots and resources"),
    ("awareness", "Watchers, observations, current world and self-state"),
    ("goals", "Desired outcomes, cumulative budgets and evidence"),
    ("commitments", "Accepted responsibilities, waits and satisfaction"),
    ("initiative", "Opportunity candidates, value, urgency and cost"),
    ("attention", "Proactive policy plus focus and evidence selection"),
    ("planner", "Direct, planned or delegated work; dependencies"),
])

card(1450, 200, 650, 510, "03  CAPABILITIES AND DELEGATION", [
    ("plugin-sdk", "Manifests, tools/commands, hooks and contracts"),
    ("plugin-host", "Isolated hosting, migration, health and rollback"),
    ("skills", "Scoped procedures, synthesis and package lifecycle"),
    ("subagents", "Bounded child work, messages, workspaces, findings"),
    ("tool-runner", "Canonical dispatch + restricted execution backends"),
], "Every agent tool action uses the same recorded contract.")

card(60, 755, 650, 335, "04  TIME AND SHARED CONTRACTS", [
    ("scheduler", "Calendar jobs, durable wakes and common admission"),
    ("protocol", "Versioned events, calls, results and domain schemas"),
    ("framing", "Bounded transport byte codec for internal IPC"),
], "Framing delimits bytes; context compilation is separate.")

card(755, 755, 650, 335, "05  MODEL PROVIDERS", [
    ("provider-catalog", "Provider declarations + fresh capability snapshots"),
    ("provider-adapters", "Requests, roles, tools, streams, cancellation, usage"),
], "Each invocation pins its model contract and input packet.")

card(1450, 755, 650, 335, "06  DIAGNOSIS AND IMPROVEMENT", [
    ("meta-harness", "Trace diagnosis, repair hypotheses, fixed evaluation"),
    ("self-evolution", "Stage code, verify, canary, promote, observe, revert"),
], "Candidate code cannot redefine its own acceptance rules.")

text(60, 1132, "SHARED SUPPORTING COMPONENTS", 21, DIM, bold=True)
box(60, 1177, 650, 160, "Python context compiler + runtime support", [
    "Immutable labeled context and exact inclusion manifests",
    "Process supervision, health, recovery and grant checks",
    "Independent ingress, inference, execution and delivery lanes",
])
box(755, 1177, 650, 160, "Durable operational journal + outbox", [
    "Domain records, events, action intents, waits and cursors",
    "One operational transaction boundary with explicit owners",
    "Artifact bodies retain typed evidence and exact ranges",
], "store")
box(1450, 1177, 650, 160, "C++ memory process", [
    "Typed memories, stable concepts, evidence and time",
    "Embedding / lexical indexes and scoped retrieval",
    "Snapshot leases, staged revisions, atomic publication",
], "cpp")

# Native memory access has its own bounded asynchronous channel.
path([(385, 1360), (385, 1394), (1775, 1394), (1775, 1360)], color=PURPLE, dashed=True)
text(1080, 1408, "Python memory client + daily sleep  |  bounded async IPC  |  live interaction continues", 21, PURPLE, center=True)

rect(60, 1480, 2040, 180, "#ffffff", "#dce4ee", 22)
text(1080, 1506, "OBSERVE  →  UNDERSTAND  →  EXPECT  →  ACT  →  CHECK THE CONSEQUENCE", 27, bold=True, center=True)
text(1080, 1560, "Stable objects, source evidence and unresolved responsibilities persist across model invocations.", 23, DIM, center=True)
text(1080, 1605, "Daily sleep consolidates memory. Evaluated evolution changes behavior through a separate publication.", 21, DIM, center=True)

text(1080, 1701, "Ownership map, not a synchronous call chain. Detailed runtime and lifecycle flows are supplied as Mermaid sources.", 20, DIM, center=True)
text(1080, 1744, "Design proposal • ACP delivery depends on negotiated capabilities • No runtime implementation or performance claim", 18, DIM, center=True)
svg.append("</svg>")
(ROOT / "module-map.svg").write_text("\n".join(svg) + "\n")
image.save(ROOT / "module-map.png", optimize=True)
print(ROOT / "module-map.svg")
print(ROOT / "module-map.png")
