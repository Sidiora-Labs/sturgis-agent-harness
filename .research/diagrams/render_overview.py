"""Render the architecture overview as editable SVG and a PNG preview.

This is a documentation drawing helper, not an implementation of the agent.
Uses the already-available Pillow package for raster output; no network access.
"""

from html import escape
from math import atan2, cos, sin, pi
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
W, H = 1840, 1450
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
    '<title id="title">Python agent with a separate C++ memory engine</title>',
    '<desc id="description">Live input, durable operational state, context assembly, streaming model output, and supervised tools form independent paths. A separate C++ memory process supports retrieval and background daily consolidation.</desc>',
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


# Header and legend.
text(64, 48, "Agent architecture", 45, bold=True)
text(64, 110, "Python behavior and coordination  /  C++ memory engine", 24, DIM)
text(1740, 58, "v0.1", 28, DIM, center=True)
for x, label, fill, border in [
    (1110, "Python", "#edf5ff", "#739ecf"),
    (1290, "C++", "#f1ebfb", "#a389c5"),
    (1435, "Durable state", "#fff5de", "#c5a966"),
]:
    rect(x, 116, 18, 18, fill, border, 4)
    text(x+29, 113, label, 19, DIM)

rect(48, 178, 1744, 790, "#ffffff", "#dce4ee", 24)
text(80, 199, "LIVE INPUT, REASONING, ACTION, AND OUTPUT", 18, DIM, bold=True)

# Operational database bracket.
path([(595, 254), (595, 239), (1230, 239), (1230, 254)], color="#b29b63", width=2, arrow=False)
text(913, 211, "One transactional operational store", 17, "#846f3b", center=True)

# Foreground paths, drawn behind nodes.
path([(328, 315), (430, 315)])
path([(1370, 315), (1490, 315)])
path([(595, 376), (595, 510)])
text(610, 426, "ordered events", 17, DIM)
path([(760, 575), (840, 575)])
path([(1140, 575), (1250, 575)])
text(1195, 547, "packet", 16, DIM, center=True)
path([(1430, 510), (1430, 430), (1230, 430), (1230, 376)])
text(1350, 402, "visible stream", 17, DIM, center=True)
path([(1430, 640), (1430, 780)])
text(1450, 702, "action proposal", 17, DIM)
path([(1250, 845), (760, 845)])
text(863, 807, "intent persisted", 17, DIM, center=True)
text(1120, 807, "authority checked", 17, DIM, center=True)
path([(430, 845), (328, 845)])
text(379, 811, "effects", 17, DIM, center=True)
path([(208, 780), (208, 376)])
text(95, 614, "observations", 16, DIM)

# Native boundary and its query path. No shared event-loop execution.
rect(48, 1000, 1744, 330, "#f9f6fe", "#dfd5ef", 24)
text(80, 1022, "SEPARATE MEMORY PROCESS + BACKGROUND MAINTENANCE", 18, "#735495", bold=True)
path([(990, 640), (990, 1048), (450, 1048), (450, 1080)], color=PURPLE, dashed=True)
path([(990, 674), (990, 640)], color=PURPLE, dashed=True)
# A crossing without a junction; dashed memory path is independent of dispatch.
draw.rectangle((980, 837, 1000, 853), fill="#ffffff")
svg.append('<rect x="980" y="837" width="20" height="16" fill="#ffffff"/>')
path([(990, 830), (990, 860)], color=PURPLE, dashed=True, arrow=False)
text(1007, 889, "asynchronous memory IPC", 17, PURPLE)
text(1007, 916, "deadline + snapshot generation", 17, PURPLE)
path([(1020, 1175), (820, 1175)], color=PURPLE)
text(920, 1110, "stage / publish", 17, PURPLE, center=True)
text(920, 1138, "versioned API", 17, PURPLE, center=True)

# Foreground nodes.
box(88, 254, 240, 122, "Input gateway", ["User + external events", "Validate, persist, accept"])
box(430, 254, 330, 122, "Operational journal", ["Events, action intents, cursors", "Durable acceptance"], "store")
box(1090, 254, 280, 122, "Output outbox", ["Persisted text + status", "Stable stream sequences"], "store")
box(1490, 254, 250, 122, "User delivery", ["Stream, acknowledge", "Reconnect and replay"])
box(430, 510, 330, 130, "Situation + scheduler", ["Commitments, pending actions", "Attention and steering"])
box(840, 510, 300, 130, "Context assembler", ["Labeled, scoped, budgeted", "Exact inclusion manifest"])
box(1250, 510, 360, 130, "Streaming model gateway", ["Provider-compatible input", "Independent output routing"])
box(88, 780, 240, 130, "Digital environment", ["Objects and processes", "Observable consequences"], "external")
box(430, 780, 330, 130, "Supervised tools", ["Execute and normalize", "Results return to journal"])
box(1250, 780, 360, 130, "Tool contract + dispatch", ["Schema and precondition checks", "Expectation linked to action"])

# Memory nodes.
box(80, 1080, 740, 190, "C++ memory engine", [
    "Typed revisions + stable concepts + embedding / lexical indexes",
    "Replayable journal ingestion and explicit indexing freshness",
    "Scoped retrieval, snapshot leases, atomic publication",
    "Episodic / semantic / procedural memory",
], "cpp")
box(1020, 1080, 710, 190, "Python daily sleep coordinator", [
    "Snapshot -> consolidate -> validate -> stage -> publish",
    "Model-assisted proposals with source evidence",
    "Lower resource priority; checkpoint and resume",
    "Live input and output continue during maintenance",
])

text(920, 1360, "Observation -> expectation -> action -> observed consequence -> updated understanding", 22, INK, bold=True, center=True)
text(920, 1402, "Design proposal. Full lifecycle diagrams and contracts are in the companion Markdown files.", 18, DIM, center=True)

svg.append("</svg>")
(ROOT / "agent-overview.svg").write_text("\n".join(svg) + "\n")
image.save(ROOT / "agent-overview.png", optimize=True)
print(ROOT / "agent-overview.svg")
print(ROOT / "agent-overview.png")
