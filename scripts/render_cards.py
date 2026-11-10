#!/usr/bin/env python3
"""Render the GitHub profile README as a stack of animated SVG cards.

    python scripts/render_cards.py            # animated cards in cards/
    STATIC=1 python scripts/render_cards.py   # final frame only, for screenshots

Reads profile.json (what to show) and data/github.json (contributions and repo
details, refreshed daily by .github/workflows/profile-cards.yml), then writes
cards/*.svg and README.md.

The cards are shown through <img> tags, so each SVG is self-contained:
  - CSS keyframes in an inline <style> block, and SMIL <animate> elements
  - fonts subset with pyftsubset to the characters each card uses, embedded as
    base64 WOFF data URIs
  - every animation is off under prefers-reduced-motion (CSS animations are
    disabled; SMIL parts are swapped for a still copy)
Delays are staggered across cards so the page plays from top to bottom.
"""
import base64
import calendar
import datetime as dt
import html
import io
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "cards"
STATIC = os.environ.get("STATIC", "") not in ("", "0", "false")

# Colours: one accent on white.
C = {
    # Bold, playful primaries with ink outlines. No purple.
    "white": "#ffffff",
    "ink": "#1b1b1f",
    "yellow": "#ffd43b",
    "red": "#ff5a4e",
    "blue": "#3a86ff",
    "green": "#1fb89a",
    "sky": "#d6e8ff",
    "mint": "#cdf3e6",
    "glass": "#eef6ff",
    "gray_300": "#2f2f36",     # body text
    "gray_400": "#4a4a55",     # secondary text
    "gray_500": "#8a8a99",
}

FONTS = {
    # key: (file, css family, weight). Fredoka for headings, Nunito for text.
    "display": ("Fredoka-Bold.woff", "ProfileDisplay", 700),
    "mono_bold": ("Fredoka-SemiBold.woff", "ProfileDisplay", 600),
    "sans": ("Nunito-Medium.woff", "ProfileText", 500),
    "mono": ("Nunito-Bold.woff", "ProfileText", 700),
    "sans_bold": ("Nunito-ExtraBold.woff", "ProfileText", 800),
}
FALLBACK = {"ProfileDisplay": "'Trebuchet MS',sans-serif", "ProfileText": "'Segoe UI',system-ui,sans-serif"}
_font_cache = {}


def font(key):
    if key not in _font_cache:
        _font_cache[key] = TTFont(ROOT / "fonts" / FONTS[key][0])
    return _font_cache[key]


def text_width(s, key, size):
    f = font(key)
    cmap, hmtx, upm = f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm
    space = hmtx[cmap[32]][0]
    return sum(hmtx[cmap[ord(ch)]][0] if ord(ch) in cmap else space for ch in s) * size / upm


def wrap(s, key, size, width, max_lines):
    words, lines, cur = s.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_width(trial, key, size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > max_lines:
        last = lines[max_lines - 1]
        while text_width(last + "…", key, size) > width and " " in last:
            last = last.rsplit(" ", 1)[0]
        lines = lines[:max_lines - 1] + [last.rstrip(",.;:") + "…"]
    return lines


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- the SVG card

class Card:
    """Collects the elements and characters of one card, then writes it out."""

    def __init__(self, name, width, height, delay):
        self.name, self.w, self.h, self.delay = name, width, height, delay
        self.parts, self.chars, self.defs = [], {}, []

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, key="sans", size=16, fill=C["gray_300"], anchor="start", extra="", attrs=""):
        self.chars.setdefault(key, set()).update(s)
        family, weight = FONTS[key][1], FONTS[key][2]
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family},{FALLBACK[family]}" font-weight="{weight}" '
                 f'font-size="{size}" fill="{fill}" text-anchor="{anchor}"{attrs}{anim_attr(extra)}>{esc(s)}</text>')

    def font_css(self):
        css = []
        for key, chars in self.chars.items():
            data = subset_font(key, "".join(sorted(chars)))
            family, weight = FONTS[key][1], FONTS[key][2]
            css.append(f"@font-face{{font-family:{family};font-weight:{weight};"
                       f"src:url(data:font/woff;base64,{data}) format('woff')}}")
        return "".join(css)

    def write(self):
        style = self.font_css()
        if not STATIC:
            style += ANIMATION_CSS
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
               f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}">'
               f'<title>{esc(self.label)}</title><style>{style}</style>'
               f'<defs>{"".join(self.defs)}</defs>{"".join(self.parts)}</svg>')
        out = CARDS / f"{self.name}.svg"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg, encoding="utf-8")
        return len(svg)


def subset_font(key, chars):
    """Subset one font to the given characters with pyftsubset; return base64 WOFF."""
    src = ROOT / "fonts" / FONTS[key][0]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sub.woff"
        (Path(tmp) / "chars.txt").write_text(chars + " ", encoding="utf-8")
        tool = [shutil.which("pyftsubset")] if shutil.which("pyftsubset") else [sys.executable, "-m", "fontTools.subset"]
        subprocess.run(tool + [str(src),
                        f"--text-file={Path(tmp) / 'chars.txt'}", "--flavor=woff",
                        "--layout-features=kern,liga", "--no-hinting", "--desubroutinize",
                        f"--output-file={out}"], check=True)
        # Selawik's licence reserves its name, so the modified (subset) copy gets a new one.
        f = TTFont(out)
        family = FONTS[key][1]
        for rec in f["name"].names:
            if rec.nameID in (1, 3, 4, 6, 16):
                rec.string = family if rec.nameID != 6 else family.replace(" ", "")
        buf = io.BytesIO()
        f.flavor = "woff"
        f.save(buf)
        return base64.b64encode(buf.getvalue()).decode()


# --------------------------------------------------------------- animation

ANIMATION_CSS = (
    ".up,.fade,.pop,.draw,.ray,.rise,.reach{animation-duration:.8s;animation-fill-mode:both;"
    "animation-timing-function:cubic-bezier(.2,.7,.2,1)}"
    ".up{animation-name:up}.fade{animation-name:fade}"
    ".pop{animation-name:pop;transform-box:fill-box;transform-origin:center}"
    ".draw{animation-name:draw;animation-duration:1.1s;animation-timing-function:ease-in-out}"
    ".ray{animation-name:ray;transform-box:view-box}"
    ".rise{animation-name:rise;transform-box:fill-box;transform-origin:50% 100%}"
    ".reach{animation-name:reach;transform-box:fill-box;transform-origin:0 50%}"
    "@keyframes rise{from{transform:scaleY(0)}to{transform:none}}"
    "@keyframes reach{from{transform:scaleX(0)}to{transform:none}}"
    "@keyframes up{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}"
    "@keyframes fade{from{opacity:0}to{opacity:1}}"
    "@keyframes pop{from{opacity:0;transform:scale(.2)}to{opacity:1;transform:none}}"
    "@keyframes draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}"
    "@keyframes ray{from{opacity:0;transform:scale(.15)}to{opacity:1;transform:none}}"
    ".sway,.bob,.pulse,.belt,.wave,.drift{animation-iteration-count:infinite;animation-timing-function:ease-in-out;"
    "animation-direction:alternate}"
    ".sway{animation-name:sway;animation-duration:2.6s;transform-box:fill-box;transform-origin:center}"
    ".bob{animation-name:bob;animation-duration:2.2s}"
    ".pulse{animation-name:pulse;animation-duration:1.4s;transform-box:fill-box;transform-origin:center}"
    ".drift{animation-name:drift;animation-duration:3.4s;transform-box:fill-box;transform-origin:center}"
    ".belt{animation-name:belt;animation-duration:.9s;animation-timing-function:linear;animation-direction:normal}"
    ".wave{animation-name:wave;animation-duration:1.4s;animation-timing-function:linear;animation-direction:normal}"
    "@keyframes sway{from{transform:rotate(-4deg)}to{transform:rotate(4deg)}}"
    "@keyframes bob{from{transform:translateY(-3px)}to{transform:translateY(3px)}}"
    "@keyframes pulse{from{transform:scale(.8)}to{transform:scale(1.18)}}"
    "@keyframes drift{from{transform:translate(-3px,2px) rotate(-25deg)}to{transform:translate(3px,-3px) rotate(25deg)}}"
    "@keyframes belt{from{transform:translateX(0)}to{transform:translateX(17px)}}"
    "@keyframes wave{from{transform:translateX(0)}to{transform:translateX(var(--wl))}}"
    ".still{display:none}"
    "@media (prefers-reduced-motion:reduce){*{animation:none!important}.moving{display:none}.still{display:inline}}"
)


def anim_attr(spec, origin=None):
    """spec like 'up 0.4' or 'pop 1.2 0.5' (kind, delay, optional duration) -> attributes.
    origin=(x, y) sets the point a 'ray' grows from."""
    if not spec or STATIC:
        return ""
    kind, delay, *dur = spec.split()
    style = f"animation-delay:{float(delay):.2f}s" + (f";animation-duration:{dur[0]}s" if dur else "")
    if origin:
        style += f";transform-origin:{origin[0]:.1f}px {origin[1]:.1f}px"
    return f' class="{kind}" style="{style}"'


def loop(kind, delay, dur=None, style=""):
    """Attributes for a looping CSS animation; nothing in STATIC mode."""
    if STATIC:
        return ""
    st = f"animation-delay:{delay:.2f}s" + (f";animation-duration:{dur:.2f}s" if dur else "") + (f";{style}" if style else "")
    return f' class="{kind}" style="{st}"'


def smil(moving, still):
    """SMIL can't be switched off by CSS, so a still copy replaces it under reduced motion."""
    if STATIC:
        return still
    return f'<g class="moving">{moving}</g><g class="still">{still}</g>'


# ------------------------------------------------------------------- the data

def load():
    profile = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
    data = json.loads((ROOT / "data" / "github.json").read_text(encoding="utf-8"))
    days = [(dt.date.fromisoformat(d["date"]), int(d["count"])) for d in data["days"]]
    days.sort()
    return profile, data, days


def streaks(days):
    longest = run = 0
    for _, c in days:
        run = run + 1 if c else 0
        longest = max(longest, run)
    current, i = 0, len(days) - 1
    if i >= 0 and days[i][1] == 0:
        i -= 1  # today may simply not have commits yet
    while i >= 0 and days[i][1]:
        current += 1
        i -= 1
    return current, longest


def monthly(days, n=12):
    last = days[-1][0]
    keys = []
    y, m = last.year, last.month
    for _ in range(n):
        keys.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    totals = OrderedDict((k, 0) for k in reversed(keys))
    for d, c in days:
        if (d.year, d.month) in totals:
            totals[(d.year, d.month)] += c
    return totals


def fmt_date(d):
    return f"{d.day} {calendar.month_abbr[d.month]} {d.year}"


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


# ------------------------------------------------------------------- the cards

# The page is one marble machine. A red marble leaves the hopper in the hero, rolls down a
# chute along every card's left edge, and each card knows when it arrives (t0, seconds), so
# the marble seems to roll from card to card. The run repeats every PERIOD seconds.
SPEED = 380.0
PERIOD = 8.0
CHUTE_X = 40
DISCRETE = ' calcMode="discrete"'


def loop_anim(attr, values, key_times, t_abs, extra=""):
    return (f'<animate attributeName="{attr}" values="{values}" keyTimes="{key_times}" dur="{PERIOD}s" '
            f'begin="{t_abs:.2f}s" repeatCount="indefinite"{extra}/>')


def plate(card, uid, delay=0.0, fill=None):
    """A sticker-like panel: chunky ink outline and a hard offset shadow."""
    w, h = card.w, card.h
    card.add(f'<g{anim_attr(f"fade {delay:.2f}")}>'
             f'<rect x="7" y="7" width="{w - 9}" height="{h - 9}" rx="18" fill="{C["ink"]}"/>'
             f'<rect x="1.5" y="1.5" width="{w - 9}" height="{h - 9}" rx="18" fill="{fill or C["white"]}" '
             f'stroke="{C["ink"]}" stroke-width="3"/></g>')


def tube(path, delay):
    """A chunky glass tube along a path: thick ink stroke with a white core."""
    return (f'<g{anim_attr(f"fade {delay:.2f}")}><path d="{path}" fill="none" stroke="{C["ink"]}" stroke-width="15" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="{path}" fill="none" stroke="{C["white"]}" stroke-width="9" stroke-linecap="round" '
            f'stroke-linejoin="round"/></g>')


def marble_svg(r=6, color=None):
    color = color or C["red"]
    return (f'<circle r="{r}" fill="{color}" stroke="{C["ink"]}" stroke-width="2"/>'
            f'<circle cx="{-r * .35:.1f}" cy="{-r * .35:.1f}" r="{r * .3:.1f}" fill="#fff" opacity=".85"/>')


def roll(card, path, t_abs, length):
    """The marble rolling along a path once per PERIOD."""
    dur = length / SPEED
    f = min(.95, dur / PERIOD)
    m = (f'<g opacity="0">{marble_svg()}'
         f'<animateMotion path="{path}" keyPoints="0;1;1" keyTimes="0;{f:.4f};1" calcMode="linear" '
         f'dur="{PERIOD}s" begin="{t_abs:.2f}s" repeatCount="indefinite"/>'
         f'{loop_anim("opacity", "1;0", f"0;{f:.4f}", t_abs, DISCRETE)}</g>')
    card.add(smil(m, ""))
    return t_abs + dur


def chute(card, t0, top=0, bottom=None):
    """The straight part of the run, down a card's left edge. Returns when the marble leaves."""
    bottom = card.h if bottom is None else bottom
    card.add(tube(f"M{CHUTE_X} {top} V{bottom}", t0))
    return roll(card, f"M{CHUTE_X} {top} V{bottom}", t0, bottom - top)


def bounce(inner, cx, cy, t_abs, height=7):
    """Wrap a drawing so it hops once when the marble goes by (still under reduced motion)."""
    if STATIC:
        return inner
    hop = (f'<animateTransform attributeName="transform" type="translate" values="0 0;0 {-height};0 0;0 0" '
           f'keyTimes="0;.03;.07;1" dur="{PERIOD}s" begin="{t_abs:.2f}s" repeatCount="indefinite"/>')
    return f'<g class="moving"><g>{inner}{hop}</g></g><g class="still">{inner}</g>'


def sticker(card, x, y, w, h, fill, text, key, size, color, rot=0, delay=0.0, radius=10, motion="sway"):
    cx, cy = x + w / 2, y + h / 2
    rnd = random.Random(int(x * 7 + y))
    card.add(f'<g transform="rotate({rot} {cx:.1f} {cy:.1f})"><g{anim_attr(f"pop {delay:.2f}")}>'
             f'<g{loop(motion, delay + .8 + rnd.uniform(0, 1.2), rnd.uniform(2, 3.2)) if motion else ""}>'
             f'<rect x="{x + 3}" y="{y + 3}" width="{w}" height="{h}" rx="{radius}" fill="{C["ink"]}"/>'
             f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{C["ink"]}" stroke-width="2.5"/>')
    card.text(cx, cy + size * .35, text, key, size, color, "middle")
    card.add('</g></g></g>')


def confetti(card, n, seed, avoid, delay):
    rnd = random.Random(seed)
    cols = [C["red"], C["blue"], C["green"], C["white"]]
    placed = 0
    while placed < n:
        x, y = rnd.uniform(40, card.w - 30), rnd.uniform(20, card.h - 26)
        if any(a <= x <= c and b <= y <= d for a, b, c, d in avoid):
            continue
        placed += 1
        col, kind, rot = rnd.choice(cols), rnd.random(), rnd.uniform(0, 180)
        if kind < .4:
            shape = f'<circle cx="{x:.0f}" cy="{y:.0f}" r="4" fill="{col}" stroke="{C["ink"]}" stroke-width="1.5"/>'
        elif kind < .75:
            shape = (f'<rect x="{x - 4:.0f}" y="{y - 4:.0f}" width="8" height="8" fill="{col}" stroke="{C["ink"]}" '
                     f'stroke-width="1.5" transform="rotate({rot:.0f} {x:.0f} {y:.0f})"/>')
        else:
            shape = (f'<path d="M{x - 9:.0f} {y:.0f} q4.5 -6 9 0 t9 0" fill="none" stroke="{col if col != C["white"] else C["ink"]}" '
                     f'stroke-width="2.5" stroke-linecap="round" transform="rotate({rot:.0f} {x:.0f} {y:.0f})"/>')
        card.add(f'<g{anim_attr(f"pop {delay + rnd.uniform(0, .6):.2f}")}>'
                 f'<g{loop("drift", delay + 1 + rnd.uniform(0, 2), rnd.uniform(2.4, 4.2))}>{shape}</g></g>')


def gear(cx, cy, r, teeth, fill, spin_s, reverse=False):
    pts = []
    for k in range(teeth * 2):
        a = math.pi * k / teeth
        rr = r if k % 2 == 0 else r * .78
        a1, a2 = a - math.pi / teeth * .45, a + math.pi / teeth * .45
        pts += [(cx + rr * math.cos(a1), cy + rr * math.sin(a1)), (cx + rr * math.cos(a2), cy + rr * math.sin(a2))]
    d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + " Z"
    body = (f'<path d="{d}" fill="{fill}" stroke="{C["ink"]}" stroke-width="2.5" stroke-linejoin="round"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r * .3:.1f}" fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="2.5"/>')
    spin = (f'<animateTransform attributeName="transform" type="rotate" from="0 {cx} {cy}" '
            f'to="{-360 if reverse else 360} {cx} {cy}" dur="{spin_s}s" repeatCount="indefinite"/>')
    return smil(f"<g>{body}{spin}</g>", body)


def hero(profile, t0):
    """The machine's input: a hopper, gears and the first stretch of tube."""
    card = Card("hero", 840, 370, t0)
    mach = profile.get("machine", {})
    card.label = f"{profile['name']}: {profile['tagline']}"
    plate(card, "h", t0, C["yellow"])
    confetti(card, 18, 3, [(40, 40, 500, 330), (540, 10, 830, 300)], t0 + .2)

    x0 = 56
    card.text(x0, 82, "Hi, I'm", "sans_bold", 22, C["ink"], extra=f"up {t0 + .15:.2f}")
    size = 72
    while text_width(profile["name"], "display", size) > 440:
        size -= 2
    card.text(x0 - 3, 148, profile["name"], "display", size, C["ink"], extra=f"up {t0 + .25:.2f}")
    nw = text_width(profile["name"], "display", size)
    seg = nw / 8
    # One wavelength longer than needed, clipped, and slid sideways by a wavelength in a loop.
    wave = "M{:.1f} 166 ".format(x0 - 2 * seg) + " ".join(f"q{seg / 2:.1f} {-8 if k % 2 == 0 else 8} {seg:.1f} 0" for k in range(10))
    card.defs.append(f'<clipPath id="waveclip"><rect x="{x0 - 6}" y="150" width="{nw + 12:.0f}" height="30"/></clipPath>')
    card.add(f'<g clip-path="url(#waveclip)"><g{anim_attr(f"fade {t0 + .55:.2f}")}>'
             f'<g{loop("wave", t0 + 1.2, 1.4, f"--wl:{2 * seg:.1f}px")}>'
             f'<path d="{wave}" fill="none" stroke="{C["red"]}" stroke-width="5" stroke-linecap="round"/></g></g></g>')

    # Speech bubble with the one line about me.
    lines = wrap(profile["tagline"], "sans_bold", 18, 380, 2)
    bw, bh, by = 420, 30 + 26 * len(lines), 192
    card.add(f'<g{anim_attr(f"pop {t0 + .45:.2f}")}><g{loop("bob", t0 + 1.4, 2.8)}>'
             f'<rect x="{x0 + 4}" y="{by + 4}" width="{bw}" height="{bh}" rx="16" fill="{C["ink"]}"/>'
             f'<path d="M{x0 + 34} {by + bh} l-6 22 l26 -22" fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="3" stroke-linejoin="round"/>'
             f'<rect x="{x0}" y="{by}" width="{bw}" height="{bh}" rx="16" fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="3"/>'
             f'<path d="M{x0 + 30} {by + bh - 1.5} h26" stroke="{C["white"]}" stroke-width="4"/>')
    for i, line in enumerate(lines):
        card.text(x0 + 20, by + 34 + i * 26, line, "sans_bold", 18, C["ink"])
    card.add('</g></g>')
    sticker(card, x0 + 70, 286, text_width(profile["handle"], "mono", 15) + 30, 32, C["blue"], profile["handle"],
            "mono", 15, C["white"], -3, t0 + .7, 16)

    portrait = find_portrait(profile)
    if portrait:
        card.defs.append('<clipPath id="face"><circle cx="458" cy="96" r="38"/></clipPath>')
        card.add(f'<g{anim_attr(f"pop {t0 + .3:.2f}")}><circle cx="462" cy="100" r="40" fill="{C["ink"]}"/>'
                 f'<image href="{portrait}" x="420" y="58" width="76" height="76" preserveAspectRatio="xMidYMid slice" '
                 f'clip-path="url(#face)"/><circle cx="458" cy="96" r="38" fill="none" stroke="{C["ink"]}" stroke-width="3"/></g>')

    # The hopper, with the three inputs dropping in.
    hx = 668
    card.add(f'<g{anim_attr(f"pop {t0 + .3:.2f}")}>'
             f'<path d="M{hx - 96} 96 L{hx + 96} 96 L{hx + 22} 168 L{hx - 22} 168 Z" fill="{C["white"]}" '
             f'stroke="{C["ink"]}" stroke-width="3" stroke-linejoin="round"/>'
             f'<path d="M{hx - 22} 168 h44 v20 h-44 Z" fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="3"/></g>')
    ins = mach.get("inputs", ["Code", "Data", "Curiosity"])
    cols = [C["red"], C["blue"], C["green"]]
    t_drop = t0 + .6
    for i, (name, col) in enumerate(zip(ins, cols)):
        bx = hx - 50 + i * 50
        rest_y = 130 - abs(i - 1) * 6
        sticker(card, bx - text_width(name, "mono", 13) / 2 - 10, 36 + (i % 2) * 22, text_width(name, "mono", 13) + 20, 24,
                col, name, "mono", 13, C["white"], [-6, 4, -3][i], t0 + .35 + i * .08, 12, "bob")
        ball_still = f'<g transform="translate({bx} {rest_y})">{marble_svg(11, col)}</g>'
        drop = (f'<g transform="translate({bx} {rest_y})"><g>{marble_svg(11, col)}'
                f'<animateTransform attributeName="transform" type="translate" values="0 -150;0 -150;0 0;0 -4;0 0;0 0" '
                f'keyTimes="0;{(i * .12) / PERIOD:.4f};{(i * .12 + .35) / PERIOD:.4f};{(i * .12 + .42) / PERIOD:.4f};'
                f'{(i * .12 + .5) / PERIOD:.4f};1" dur="{PERIOD}s" begin="{t_drop:.2f}s" repeatCount="indefinite"/></g></g>')
        card.add(smil(drop, ball_still))

    # The tube from the hopper down, then left along the bottom and out through the floor.
    path = (f"M{hx} 188 V214 Q{hx} 240 {hx - 26} 240 H{hx - 92} Q{hx - 118} 240 {hx - 118} 266 V306 "
            f"Q{hx - 118} 332 {hx - 144} 332 H{CHUTE_X + 26} Q{CHUTE_X} 332 {CHUTE_X} 358 V{card.h}")
    q = math.pi * 26 / 2
    length = 26 + q + 66 + q + 40 + q + (hx - 144 - CHUTE_X - 26) + q + (card.h - 358)
    card.add(tube(path, t0 + .4))

    # Gears under the hopper turn the inputs into the output.
    card.add(f'<g{anim_attr(f"pop {t0 + .45:.2f}")}>{gear(hx - 40, 226, 34, 10, C["red"], 6)}'
             f'{gear(hx + 16, 250, 22, 8, C["blue"], 4, True)}</g>')
    out_txt = mach.get("output", "Intelligent solutions")
    ow = text_width(out_txt, "mono_bold", 16) + 28
    sticker(card, 806 - ow, 280, ow, 36, C["green"], out_txt, "mono_bold", 16, C["white"], 3, t0 + .9, 12)

    card.exit = roll(card, path, t_drop + .9, length)
    return card


def find_portrait(profile):
    """Embed assets/portrait.* as a small base64 image if it exists (the hero works without one)."""
    candidates = [ROOT / profile.get("portrait", "")] + sorted((ROOT / "assets").glob("portrait.*"))
    for p in candidates:
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            raw, mime = p.read_bytes(), "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            try:
                from PIL import Image
                im = Image.open(p).convert("RGB")
                side = min(im.size)
                im = im.crop(((im.width - side) // 2, (im.height - side) // 2,
                              (im.width + side) // 2, (im.height + side) // 2)).resize((160, 160))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
                raw, mime = buf.getvalue(), "image/jpeg"
            except ImportError:
                pass
            return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    return None


ICONS = {
    "person": lambda c: (f'<circle cy="-6" r="7" fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="2.5"/>'
                         f'<path d="M-12 13 Q-12 2 0 2 Q12 2 12 13 Z" fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="2.5" stroke-linejoin="round"/>'),
    "stack": lambda c: (f'<rect x="-13" y="2" width="26" height="10" rx="3" fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="2.5"/>'
                        f'<rect x="-11" y="-8" width="22" height="10" rx="3" fill="{C["yellow"]}" stroke="{C["ink"]}" stroke-width="2.5"/>'
                        f'<rect x="-9" y="-17" width="18" height="9" rx="3" fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="2.5"/>'),
    "planet": lambda c: (f'<circle r="9" fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="2.5"/>'
                         f'<ellipse rx="16" ry="5" fill="none" stroke="{C["ink"]}" stroke-width="2.5" transform="rotate(-20)"/>'),
}


def tile(profile, data, t, i, t0):
    """A link as a little sticker card; the first one carries the chute."""
    card = Card(f"tile-{t['id']}", 272, 124, t0)
    sub = t["subtitle"].replace("{public_repos}", str(data.get("public_repos", "")))
    card.label = f"{t['title']}: {sub}"
    plate(card, f"t{i}", t0)
    col = [C["red"], C["blue"], C["green"]][i % 3]
    ix = 88 if i == 0 else 50
    icon = (f'<circle cx="{ix + 3}" cy="63" r="25" fill="{C["ink"]}"/>'
            f'<circle cx="{ix}" cy="60" r="25" fill="{col}" stroke="{C["ink"]}" stroke-width="2.5"/>'
            f'<g transform="translate({ix} 61)">{ICONS.get(t["icon"], ICONS["planet"])(col)}</g>')
    card.add(f'<g{anim_attr(f"pop {t0 + .15:.2f}")}><g{loop("bob", t0 + 1 + i * .4, 2.4)}>'
             f'{bounce(icon, ix, 60, t0 + 60 / SPEED)}</g></g>')
    tx = ix + 38
    card.text(tx, 54, t["title"], "display", 22, C["ink"], extra=f"up {t0 + .25:.2f}")
    sub_size = 14
    while text_width(sub, "sans", sub_size) > 262 - tx and sub_size > 11:
        sub_size -= 1
    card.text(tx + 1, 78, sub, "sans", sub_size, C["gray_400"], extra=f"up {t0 + .32:.2f}")
    if i == 0:
        card.exit = chute(card, t0)
    else:
        card.exit = t0
    return card


def activity(data, days, t0):
    """Twelve jars, one per month, each holding marbles in proportion to that month's commits."""
    card = Card("activity", 840, 430, t0)
    months = monthly(days)
    current, longest = streaks(days)
    active = sum(1 for _, c in days if c)
    busiest_day, busiest = max(days, key=lambda x: (x[1], x[0]))
    total = sum(c for _, c in days)
    best = max(months, key=months.get)
    card.label = (f"Marble jars, one per month, filled in proportion to commits: {plural(total, 'contribution')} in all. "
                  f"Current streak {plural(current, 'day')}, longest {plural(longest, 'day')}, "
                  f"{active} active days, busiest day {fmt_date(busiest_day)} with {busiest}.")
    plate(card, "a", t0, C["sky"])
    card.text(64, 62, "A year in marble jars", "display", 28, C["ink"], extra=f"up {t0 + .1:.2f}")
    card.text(64, 88, "Every jar is a month. The more I committed, the fuller it got.", "sans_bold", 15, C["gray_400"],
              extra=f"up {t0 + .15:.2f}")

    peak = max(months.values()) or 1
    jw, jh, top = 46, 132, 120
    x0 = 64
    step = (800 - x0 - jw) / (len(months) - 1)
    rnd = random.Random(21)
    palette = [C["red"], C["blue"], C["green"], C["yellow"], C["white"]]
    cap_per_row, rows = 4, 12
    for i, ((y, m), v) in enumerate(months.items()):
        x = x0 + i * step
        jar = (f'<rect x="{x + 3}" y="{top + 3}" width="{jw}" height="{jh}" rx="10" fill="{C["ink"]}"/>'
               f'<rect x="{x}" y="{top}" width="{jw}" height="{jh}" rx="10" fill="{C["glass"]}" stroke="{C["ink"]}" stroke-width="2.5"/>')
        n = 0 if not v else max(1, round(cap_per_row * rows * v / peak))
        balls = []
        for k in range(n):
            r_, c_ = divmod(k, cap_per_row)
            bx = x + 8 + c_ * 10 + (5 if r_ % 2 else 0) * (c_ < cap_per_row - 1)
            by = top + jh - 8 - r_ * 9.5
            col = rnd.choice(palette)
            balls.append(f'<g{anim_attr(f"pop {t0 + .5 + i * .06 + k * .012:.2f} .35")}>'
                         f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="4.6" fill="{col}" stroke="{C["ink"]}" stroke-width="1.4"/></g>')
        lid = (f'<rect x="{x - 3}" y="{top - 12}" width="{jw + 6}" height="14" rx="5" '
               f'fill="{C["red"] if (y, m) == best else C["white"]}" stroke="{C["ink"]}" stroke-width="2.5"/>')
        shine = f'<path d="M{x + jw - 9} {top + 14} V{top + 44}" stroke="#fff" stroke-width="3" stroke-linecap="round" opacity=".8"/>'
        group = jar + "".join(balls) + shine + lid
        card.add(f'<g{anim_attr(f"pop {t0 + .25 + i * .05:.2f}")}>{bounce(group, x, top, t0 + top / SPEED + i * .05, 5)}</g>')
        card.text(x + jw / 2, top + jh + 26, calendar.month_abbr[m], "mono", 14, C["ink"], "middle", f"fade {t0 + .4 + i * .05:.2f}")
        card.text(x + jw / 2, top + jh + 46, f"{v}", "mono_bold", 15, C["gray_400"] if not v else C["ink"], "middle",
                  f"fade {t0 + .5 + i * .05:.2f}")
        if (y, m) == best:
            sticker(card, x - 14, top - 50, 74, 28, C["yellow"], "peak!", "mono_bold", 15, C["ink"], -8, t0 + 1.4, 14)

    stats = [(plural(current, "day"), "current streak", C["white"]), (plural(longest, "day"), "longest streak", C["yellow"]),
             (f"{active} days", "with commits", C["white"]),
             (f"{busiest:,} on {busiest_day.day} {calendar.month_abbr[busiest_day.month]}", "busiest day", C["green"])]
    sx = 64
    for i, (value, label, fill) in enumerate(stats):
        w = max(text_width(value, "display", 20), text_width(label, "mono", 13)) + 36
        y = 352
        rot = [-2, 1.5, -1, 2][i]
        cx, cy = sx + w / 2, y + 26
        card.add(f'<g transform="rotate({rot} {cx:.1f} {cy:.1f})"><g{anim_attr(f"pop {t0 + 1.2 + i * .1:.2f}")}>'
                 f'<g{loop("bob", t0 + 2 + i * .35, 2.6)}>'
                 f'<rect x="{sx + 3}" y="{y + 3}" width="{w:.1f}" height="52" rx="12" fill="{C["ink"]}"/>'
                 f'<rect x="{sx}" y="{y}" width="{w:.1f}" height="52" rx="12" fill="{fill}" stroke="{C["ink"]}" stroke-width="2.5"/>')
        card.text(sx + 18, y + 24, value, "display", 20, C["ink"])
        card.text(sx + 18, y + 42, label, "mono", 13, C["gray_400"] if fill != C["green"] else C["ink"])
        card.add('</g></g></g>')
        sx += w + 18
    card.exit = chute(card, t0)
    return card


def repo_card(profile, repo, i, t0):
    """A project as a box coming off the line."""
    name = repo["name"]
    card = Card(f"repo-{name}", 840, 156, t0)
    title = profile.get("display_names", {}).get(name, name.replace("_", " ").replace("-", " "))
    desc = profile.get("descriptions", {}).get(name) or repo.get("description") or "No description yet."
    card.label = f"{title}: {desc}"
    plate(card, f"r{i}", t0)
    col = [C["red"], C["blue"], C["green"], C["yellow"]][i % 4]
    words = [w for w in title.replace("-", " ").split() if w[0].isalnum()]
    initials = (words[0][0] + words[1][0]) if len(words) > 1 else words[0][:2]
    bx, by, s = 66, 50, 52
    box = (f'<path d="M{bx} {by} l{s * .5} -{s * .28} l{s * .5} {s * .28} l-{s * .5} {s * .28} Z" fill="{C["white"]}" '
           f'stroke="{C["ink"]}" stroke-width="2.5" stroke-linejoin="round"/>'
           f'<path d="M{bx} {by} v{s * .78} l{s * .5} {s * .28} v-{s * .78} Z" fill="{col}" stroke="{C["ink"]}" stroke-width="2.5" stroke-linejoin="round"/>'
           f'<path d="M{bx + s} {by} v{s * .78} l-{s * .5} {s * .28} v-{s * .78} Z" fill="{blend(col, C["ink"], .25)}" '
           f'stroke="{C["ink"]}" stroke-width="2.5" stroke-linejoin="round"/>')
    card.add(f'<g{anim_attr(f"pop {t0 + .1:.2f}")}><g{loop("bob", t0 + 1 + i * .3, 1.8)}>'
             f'{bounce(box, bx, by, t0 + by / SPEED)}</g></g>')
    card.add(f'<g{loop("bob", t0 + 1 + i * .3, 1.8)}>')
    card.text(bx + s * .25, by + s * .5 + 6, initials, "display", 17, C["white"] if col not in (C["yellow"],) else C["ink"],
              "middle", f"pop {t0 + .15:.2f}")
    card.add('</g>')
    # a short belt under the box
    card.defs.append(f'<clipPath id="belt{i}"><rect x="{bx - 10}" y="{by + s + 14}" width="{s + 20}" height="10" rx="5"/></clipPath>')
    card.add(f'<g{anim_attr(f"fade {t0 + .15:.2f}")}><rect x="{bx - 12}" y="{by + s + 14}" width="{s + 24}" height="10" rx="5" '
             f'fill="{C["gray_500"]}" stroke="{C["ink"]}" stroke-width="2"/>'
             f'<g clip-path="url(#belt{i})"><g{loop("belt", t0 + 1, .9)}>'
             + "".join(f'<circle cx="{bx - 21 + k * 17}" cy="{by + s + 19}" r="2" fill="{C["white"]}"/>' for k in range(6))
             + '</g></g></g>')

    x0, x1 = 144, 796
    card.text(x0, 50, title, "display", 23, C["ink"], extra=f"up {t0 + .15:.2f}")
    for j, line in enumerate(wrap(desc, "sans", 15, x1 - x0, 2)):
        card.text(x0, 78 + j * 21, line, "sans", 15, C["gray_300"], extra=f"up {t0 + .25 + j * .05:.2f}")
    tags = [repo.get("language") or "", plural(repo.get("stars", 0), "star")]
    if repo.get("pushedAt"):
        tags.append(f"updated {fmt_date(dt.date.fromisoformat(repo['pushedAt'][:10]))}")
    x = x0
    for k, tg in enumerate(t for t in tags if t):
        w = text_width(tg, "mono", 13) + 22
        fill = [col, C["white"], C["white"]][min(k, 2)]
        tc = C["white"] if fill in (C["red"], C["blue"], C["green"]) else C["ink"]
        card.add(f'<g{anim_attr(f"pop {t0 + .4 + k * .06:.2f}")}><rect x="{x}" y="116" width="{w:.1f}" height="24" rx="12" '
                 f'fill="{fill}" stroke="{C["ink"]}" stroke-width="2"/>')
        card.text(x + w / 2, 132.5, tg, "mono", 13, tc, "middle")
        card.add('</g>')
        x += w + 8
    card.exit = chute(card, t0)
    return card


def repo_title(profile, name):
    t = profile.get("display_names", {}).get(name)
    if t:
        return t
    words = name.replace("_", " ").replace("-", " ").split()
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in words)


def repo_initials(title):
    words = [w for w in title.replace("-", " ").replace(",", " ").split() if w[0].isalnum()]
    return ((words[0][0] + words[1][0]) if len(words) > 1 else words[0][:2]).upper() if words else "??"


def iso_box(bx, by, s, col, initials, size):
    """A little isometric box with the project's initials on its front."""
    face = C["ink"] if col == C["yellow"] else C["white"]
    return (f'<path d="M{bx} {by} l{s * .5:.1f} -{s * .28:.1f} l{s * .5:.1f} {s * .28:.1f} l-{s * .5:.1f} {s * .28:.1f} Z" '
            f'fill="{C["white"]}" stroke="{C["ink"]}" stroke-width="2.5" stroke-linejoin="round"/>'
            f'<path d="M{bx} {by} v{s * .78:.1f} l{s * .5:.1f} {s * .28:.1f} v-{s * .78:.1f} Z" fill="{col}" '
            f'stroke="{C["ink"]}" stroke-width="2.5" stroke-linejoin="round"/>'
            f'<path d="M{bx + s} {by} v{s * .78:.1f} l-{s * .5:.1f} {s * .28:.1f} v-{s * .78:.1f} Z" '
            f'fill="{blend(col, C["ink"], .25)}" stroke="{C["ink"]}" stroke-width="2.5" stroke-linejoin="round"/>'), face


def pills(card, x, y, tags, col, delay, size=13, max_x=None):
    for k, tg in enumerate(t for t in tags if t):
        w = text_width(tg, "mono", size) + 20
        if max_x and x + w > max_x:
            break
        fill = col if k == 0 else C["white"]
        tc = C["ink"] if fill in (C["white"], C["yellow"]) else C["white"]
        h = size + 11
        card.add(f'<g{anim_attr(f"pop {delay + k * .06:.2f}")}><rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{h}" rx="{h / 2}" '
                 f'fill="{fill}" stroke="{C["ink"]}" stroke-width="2"/>')
        card.text(x + w / 2, y + h / 2 + size * .36, tg, "mono", size, tc, "middle")
        card.add('</g>')
        x += w + 7


def repo_tags(repo):
    tags = [repo.get("language") or "", plural(repo.get("stars", 0), "star")]
    if repo.get("pushedAt"):
        tags.append(f"updated {fmt_date(dt.date.fromisoformat(repo['pushedAt'][:10]))}")
    return tags


PALETTE = None


def mini_repo(profile, repo, i, t0):
    """A smaller card for every other repo, two to a row; the left column carries the chute."""
    name = repo["name"]
    left = i % 2 == 0
    card = Card(f"repo-{name}", 412, 172, t0)
    title = repo_title(profile, name)
    desc = profile.get("descriptions", {}).get(name) or repo.get("description") or "A project in my workshop."
    card.label = f"{title}: {desc}"
    plate(card, f"m{i}", t0)
    col = [C["blue"], C["green"], C["red"], C["yellow"]][i % 4]
    bx, by, s = (70 if left else 26), 46, 40
    box, face = iso_box(bx, by, s, col, "", 0)
    card.add(f'<g{anim_attr(f"pop {t0 + .1:.2f}")}><g{loop("bob", t0 + 1 + i * .2, 1.9)}>{bounce(box, bx, by, t0 + by / SPEED)}'
             f'</g></g>')
    card.add(f'<g{loop("bob", t0 + 1 + i * .2, 1.9)}>')
    card.text(bx + s * .25, by + s * .5 + 5, repo_initials(title), "display", 13, face, "middle", f"pop {t0 + .15:.2f}")
    card.add('</g>')
    x0 = bx + s + 20
    size = 19
    while text_width(title, "display", size) > 392 - x0 and size > 14:
        size -= 1
    card.text(x0, 44, title, "display", size, C["ink"], extra=f"up {t0 + .15:.2f}")
    for j, line in enumerate(wrap(desc, "sans", 13.5, 392 - x0, 3)):
        card.text(x0, 68 + j * 19, line, "sans", 13.5, C["gray_300"], extra=f"up {t0 + .22 + j * .04:.2f}")
    tags = repo_tags(repo)
    tags[-1] = tags[-1].replace("updated ", "")
    pills(card, x0, 128, [tags[0], tags[-1]] if tags[0] else [tags[-1]], col, t0 + .35, 12, 396)
    card.exit = chute(card, t0) if left else t0
    return card


def header_card(profile, repo, i):
    """A banner for the top of each repository's own README, in the same marble-machine style."""
    name = repo["name"]
    card = Card(f"headers/{name}", 840, 250, 0.0)
    title = repo_title(profile, name)
    desc = profile.get("descriptions", {}).get(name) or repo.get("description") or "A project from my workshop."
    card.label = f"{title}: {desc}"
    bgs = [C["yellow"], C["sky"], C["mint"], "#ffe0db"]
    cols = [C["red"], C["blue"], C["green"], C["yellow"]]
    bg, col = bgs[i % 4], cols[(i + 1) % 4]
    plate(card, f"hd{i}", 0, bg)
    confetti(card, 12, 40 + i, [(40, 30, 560, 230), (580, 20, 830, 240)], .2)

    x0 = 50
    card.text(x0, 58, f"From {profile['name']}'s workshop", "mono", 15, C["gray_400"], extra="up .1")
    size = 44
    while text_width(title, "display", size) > 520 and size > 26:
        size -= 2
    card.text(x0 - 2, 58 + size + 4, title, "display", size, C["ink"], extra="up .2")
    ty = 58 + size + 4
    for j, line in enumerate(wrap(desc, "sans_bold", 16, 520, 3)):
        card.text(x0, ty + 32 + j * 22, line, "sans_bold", 16, C["gray_300"], extra=f"up {.3 + j * .05:.2f}")
    pills(card, x0, 202, repo_tags(repo), col, .5, 13, 560)

    # The box rides a running belt; a marble rolls down a tube and drops into it, again and again.
    bx, by, s = 640, 120, 90
    box, face = iso_box(bx, by, s, col, "", 0)
    card.add(f'<g{anim_attr("pop .3")}><g{loop("bob", 1.2, 1.9)}>{box}</g></g>')
    card.add(f'<g{loop("bob", 1.2, 1.9)}>')
    card.text(bx + s * .25, by + s * .5 + 9, repo_initials(title), "display", 26, face, "middle", "pop .4")
    card.add('</g>')
    belt_y = by + s * 1.06 + 8
    card.defs.append(f'<clipPath id="hbelt"><rect x="{bx - 22}" y="{belt_y}" width="{s + 44}" height="12" rx="6"/></clipPath>')
    card.add(f'<g{anim_attr("fade .3")}><rect x="{bx - 24}" y="{belt_y}" width="{s + 48}" height="12" rx="6" fill="{C["gray_500"]}" '
             f'stroke="{C["ink"]}" stroke-width="2"/><g clip-path="url(#hbelt)"><g{loop("belt", 1, .9)}>'
             + "".join(f'<circle cx="{bx - 40 + k * 17}" cy="{belt_y + 6}" r="2" fill="{C["white"]}"/>' for k in range(10))
             + '</g></g></g>')
    card.add(f'<g{anim_attr("pop .45")}>{gear(bx + s + 40, by + 10, 26, 9, C["red"] if col != C["red"] else C["blue"], 6)}</g>')
    tube_path = f"M{bx + s + 66} 18 V40 Q{bx + s + 66} 58 {bx + s + 48} 58 H{bx + s * .5 + 18} Q{bx + s * .5} 58 {bx + s * .5} 76 V86"
    card.add(tube(tube_path, .3))
    drop = (f'<g opacity="0">{marble_svg(6)}'
            f'<animateMotion path="{tube_path} V{by + 6}" dur="2.4s" begin="1s" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="1;1;0" keyTimes="0;.9;1" dur="2.4s" begin="1s" repeatCount="indefinite"/></g>')
    card.add(smil(drop, ""))
    return card


def blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def graph(data, days, t0):
    """The contribution graph as a tray of marbles: one per day, bigger and brighter for busier days."""
    card = Card("graph", 840, 262, t0)
    total = sum(c for _, c in days)
    first, last = days[0][0], days[-1][0]
    card.label = f"My year in marbles: {plural(total, 'contribution')} from {fmt_date(first)} to {fmt_date(last)}, one marble per day."
    plate(card, "g", t0, C["mint"])
    card.text(64, 60, "My year in marbles", "display", 28, C["ink"], extra=f"up {t0 + .1:.2f}")
    card.text(800, 60, "One marble per day. Bigger and bolder means more commits.", "sans_bold", 14, C["gray_400"], "end",
              f"up {t0 + .15:.2f}")

    nonzero = sorted(c for _, c in days if c)
    q = [nonzero[int(len(nonzero) * p)] for p in (.25, .5, .75)] if nonzero else [1, 2, 3]
    level = lambda c: 0 if c == 0 else 1 + sum(c > t for t in q)
    RAD = [2, 3.4, 4.2, 5, 5.8]
    COL = [C["white"], C["blue"], C["green"], C["yellow"], C["red"]]
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)
    weeks = (last - start).days // 7 + 1
    gx, gy = 96, 104
    pitch = (796 - gx) / (weeks - 1)
    last_month = None
    for d, c in days:
        col, row = (d - start).days // 7, (d.weekday() + 1) % 7
        x, y = gx + col * pitch, gy + row * pitch
        lv = level(c)
        dl = t0 + .3 + col * .016
        tip = f"<title>{plural(c, 'contribution')} on {fmt_date(d)}</title>"
        if lv == 0:
            card.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{RAD[0]}" fill="{C["white"]}" opacity=".9"{anim_attr(f"fade {dl:.2f}")}>{tip}</circle>')
        else:
            beat = loop("pulse", t0 + 2 + (col % 7) * .2, 1.2 + (row % 3) * .2) if lv == 4 else ""
            card.add(f'<g{anim_attr(f"pop {dl:.2f} .45")}><g{beat}><circle cx="{x:.1f}" cy="{y:.1f}" r="{RAD[lv]}" fill="{COL[lv]}" '
                     f'stroke="{C["ink"]}" stroke-width="1.4"/></g>{tip}</g>')
        if row == 0 and d.month != last_month and d.day <= 7 and col < weeks - 2:
            card.text(x, gy - 14, calendar.month_abbr[d.month], "mono", 13, C["ink"], extra=f"fade {dl:.2f}")
            last_month = d.month
    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        card.text(gx - 14, gy + row * pitch + 4.5, name, "mono", 12, C["gray_400"], "end", f"fade {t0 + .3:.2f}")
    ly = gy + 6 * pitch + 34
    card.text(64, ly + 5, f"{total:,} contributions in the last year", "mono", 13, C["ink"], extra=f"fade {t0 + 1.2:.2f}")
    lx = 800 - 5 * 18 - 40
    card.text(lx - 10, ly + 5, "Less", "mono", 13, C["gray_400"], "end", f"fade {t0 + 1.2:.2f}")
    for i in range(5):
        stroke = '' if i == 0 else f' stroke="{C["ink"]}" stroke-width="1.4"'
        card.add(f'<circle cx="{lx + i * 18}" cy="{ly}" r="{RAD[i]}" fill="{COL[i]}"{stroke}{anim_attr(f"pop {t0 + 1.2 + i * .05:.2f}")}/>')
    card.text(lx + 4 * 18 + 12, ly + 5, "More", "mono", 13, C["gray_400"], extra=f"fade {t0 + 1.45:.2f}")
    # The marble's run ends here: the chute drops into a little cup.
    card.add(tube(f"M{CHUTE_X} 0 V{card.h - 40}", t0))
    card.add(f'<g{anim_attr(f"pop {t0 + .4:.2f}")}><path d="M{CHUTE_X - 16} {card.h - 46} q0 26 16 26 q16 0 16 -26 Z" '
             f'fill="{C["red"]}" stroke="{C["ink"]}" stroke-width="2.5"/></g>')
    card.exit = roll(card, f"M{CHUTE_X} 0 V{card.h - 36}", t0, card.h - 36)
    return card


# ----------------------------------------------------------------- README.md

def readme(profile, data, tiles, repos, others=()):
    login = profile["login"]
    lines = [
        f'<!-- Generated by scripts/render_cards.py. Edit profile.json, not this file. -->',
        f'<p align="center">',
        f'  <img src="cards/hero.svg" width="100%" alt="{esc(profile["name"])}: {esc(profile["tagline"])}" />',
        f'</p>',
        f'',
        f'<p align="center">',
    ]
    for t in tiles:
        sub = t["subtitle"].replace("{public_repos}", str(data.get("public_repos", "")))
        lines.append(f'  <a href="{esc(t["href"])}"><img src="cards/tile-{t["id"]}.svg" width="32.5%" alt="{esc(t["title"])}: {esc(sub)}" /></a>')
    lines += [
        f'</p>',
        f'',
        f'<p align="center">',
        f'  <img src="cards/activity.svg" width="100%" alt="A year in marble jars: one jar per month, filled in proportion to my commits, with my streaks and busiest day" />',
        f'</p>',
        f'',
        f'<p align="center">',
    ]
    for r in repos:
        title = profile.get("display_names", {}).get(r["name"], r["name"])
        lines.append(f'  <a href="https://github.com/{login}/{r["name"]}"><img src="cards/repo-{r["name"]}.svg" width="100%" alt="{esc(title)}" /></a>')
    if others:
        lines += [f'</p>', f'', f'<p align="center">']
        for k, r in enumerate(others):
            sep = "" if k % 2 == 0 else ""
            lines.append(f'  <a href="https://github.com/{login}/{r["name"]}"><img src="cards/repo-{r["name"]}.svg" '
                         f'width="49.5%" alt="{esc(repo_title(profile, r["name"]))}" /></a>')
    lines += [
        f'</p>',
        f'',
        f'<p align="center">',
        f'  <img src="cards/graph.svg" width="100%" alt="My year in marbles: my contribution graph for the last year" />',
        f'</p>',
        f'',
    ]
    (ROOT / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    profile, data, days = load()
    CARDS.mkdir(exist_ok=True)
    exclude = set(profile.get("exclude", [])) | {profile["login"]}
    all_repos = [r for r in data.get("repos", []) if r["name"] not in exclude]
    by_name = {r["name"]: r for r in all_repos}
    repos = [by_name.get(n, {"name": n}) for n in profile["featured"]]
    others = sorted((r for r in all_repos if r["name"] not in profile["featured"]),
                    key=lambda r: r.get("pushedAt", ""), reverse=True)

    # Each card starts when the marble arrives from the card above it.
    global PERIOD
    gap = 14 / SPEED                                 # GitHub leaves a small space between images
    for attempt in range(2):                         # second pass uses the real run length for PERIOD
        cards = [hero(profile, 0.0)]
        t = cards[-1].exit + gap
        tiles = [tile(profile, data, tl, i, t) for i, tl in enumerate(profile["tiles"])]
        cards += tiles
        t = tiles[0].exit + gap
        cards.append(activity(data, days, t)); t = cards[-1].exit + gap
        for i, r in enumerate(repos):
            cards.append(repo_card(profile, r, i, t)); t = cards[-1].exit + gap
        for i in range(0, len(others), 2):            # two to a row; the left card carries the marble
            row = [mini_repo(profile, r, i + k, t) for k, r in enumerate(others[i:i + 2])]
            cards += row
            t = row[0].exit + gap
        cards.append(graph(data, days, t))
        PERIOD = round(cards[-1].exit + 2.5, 1)       # one run, then a short pause before the next marble

    cards += [header_card(profile, r, i) for i, r in enumerate(repos + others)]
    keep = {f"{c.name}.svg" for c in cards}
    for old in CARDS.rglob("*.svg"):
        if old.relative_to(CARDS).as_posix() not in keep:
            old.unlink()
    for c in cards:
        size = c.write()
        print(f"cards/{c.name}.svg  {size / 1024:.1f} KB")
    readme(profile, data, profile["tiles"], repos, others)
    print(f"README.md  ({'static' if STATIC else 'animated'})")


if __name__ == "__main__":
    main()
