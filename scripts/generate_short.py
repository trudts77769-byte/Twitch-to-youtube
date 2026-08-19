#!/usr/bin/env python3
"""
Generate a 30-second vertical (1080x1920, 9:16) brain teaser Short.

Timeline (~30 seconds):
  0.0 - 3.0s  Hook slide — "Can you find the odd one out?"
  3.0 - 13.0s Puzzle with countdown timer (10 seconds)
  13.0 - 20.0s Answer reveal
  20.0 - 30.0s Call to action

Uses Pillow for frame rendering + ffmpeg (via imageio-ffmpeg) for encoding.
TTS via edge-tts (free, online). Shapes drawn procedurally — no emoji font required.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "shorts_output"

W, H = 1080, 1920
FPS = 30


# ---------------------------------------------------------------------------
# Utility: strip non-BMP (emoji) characters that our TTF can't render.
# Safe for YouTube titles/descriptions but PIL will show tofu otherwise.
# ---------------------------------------------------------------------------
def _ascii_safe(text: str) -> str:
    out = []
    for ch in text:
        cp = ord(ch)
        # Keep ASCII, Latin-1 supplement, and common BMP punctuation
        if cp < 0x2500 or (0x2000 <= cp < 0x2100):
            out.append(ch)
    return "".join(out)

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
FONT_DIR = ROOT / "assets" / "fonts"
DEJAVU_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def tfont(size: int, bold: bool = True):
    path = DEJAVU_BOLD if bold else DEJAVU
    if os.path.exists(path):
        return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def make_gradient(start_hex: str, end_hex: str) -> Image.Image:
    s = hex_to_rgb(start_hex); e = hex_to_rgb(end_hex)
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    for y in range(H):
        t = y / (H - 1)
        arr[y, :, 0] = int(s[0]*(1-t) + e[0]*t)
        arr[y, :, 1] = int(s[1]*(1-t) + e[1]*t)
        arr[y, :, 2] = int(s[2]*(1-t) + e[2]*t)
    return Image.fromarray(arr, "RGB")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def _fit_text(draw, text, font_start, max_width, bold=True):
    """Return the largest font (from size font_start down) where text fits max_width."""
    size = font_start
    while size > 20:
        f = tfont(size, bold=bold)
        bb = draw.textbbox((0,0), text, font=f, stroke_width=4)
        if bb[2]-bb[0] <= max_width:
            return f
        size -= 4
    return tfont(20, bold=bold)


def draw_centered_text(draw, xy, text, font=None, fill=(255,255,255),
                       stroke_fill=(0,0,0), stroke_width=4, shadow=True,
                       max_width=None, start_size=None):
    """
    Draw centered text. If font is None and start_size is given, auto-shrink to fit max_width.
    Otherwise, wraps at max_width by splitting into lines on word boundaries.
    """
    cx, cy = xy
    if font is None and start_size is not None:
        font = _fit_text(draw, text, start_size, max_width or (W-80))

    # If max_width given and text is wider than that, do simple word-wrapping
    if max_width is not None and font is not None:
        bb = draw.textbbox((0,0), text, font=font, stroke_width=stroke_width)
        if bb[2]-bb[0] > max_width:
            words = text.split()
            lines = []
            cur = ""
            for w in words:
                test = (cur + " " + w).strip()
                bb = draw.textbbox((0,0), test, font=font, stroke_width=stroke_width)
                if bb[2]-bb[0] <= max_width:
                    cur = test
                else:
                    if cur: lines.append(cur)
                    cur = w
            if cur: lines.append(cur)
            draw_multiline_centered(draw, xy, "\n".join(lines), font,
                                    fill=fill, stroke_fill=stroke_fill,
                                    stroke_width=stroke_width, shadow=shadow)
            return

    bbox = draw.textbbox((0,0), text, font=font, stroke_width=stroke_width)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    x = cx - tw//2 - bbox[0]
    y = cy - th//2 - bbox[1]
    if shadow:
        draw.text((x+4, y+4), text, font=font, fill=(0,0,0),
                  stroke_width=stroke_width, stroke_fill=(0,0,0))
    draw.text((x, y), text, font=font, fill=fill,
              stroke_width=stroke_width, stroke_fill=stroke_fill)


def draw_multiline_centered(draw, xy, text, font, **kw):
    lines = text.split("\n")
    cx, cy = xy
    line_h = 0
    for ln in lines:
        bb = draw.textbbox((0,0), ln, font=font, stroke_width=kw.get("stroke_width", 4))
        line_h = max(line_h, bb[3]-bb[1])
    total_h = line_h*len(lines) + 20*(len(lines)-1)
    y0 = cy - total_h//2
    sw = kw.pop("stroke_width", 4)
    for i, ln in enumerate(lines):
        draw_centered_text(draw, (cx, y0 + i*(line_h+20) + line_h//2),
                           ln, font, stroke_width=sw, **kw)


def draw_bubble(draw, box, fill=(255,255,255), outline=(0,0,0),
                radius=40, width=6):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


# ---------------------------------------------------------------------------
# Shape drawing primitives
# ---------------------------------------------------------------------------
def _star_points(cx, cy, r_out, r_in, n=5):
    pts = []
    for i in range(2*n):
        ang = -math.pi/2 + i * math.pi / n
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r*math.cos(ang), cy + r*math.sin(ang)))
    return pts


def draw_shape(draw, kind, cx, cy, size, color, highlight=False):
    """Draw a shape of given kind centered at (cx, cy) with bounding size `size`."""
    s = size
    if kind == "circle":
        draw.ellipse((cx-s, cy-s, cx+s, cy+s), fill=color,
                     outline=(255,255,255), width=4)
    elif kind == "square":
        draw.rounded_rectangle((cx-s, cy-s, cx+s, cy+s), radius=20,
                               fill=color, outline=(255,255,255), width=4)
    elif kind == "triangle":
        pts = [(cx, cy-s), (cx-s, cy+s*0.85), (cx+s, cy+s*0.85)]
        draw.polygon(pts, fill=color, outline=(255,255,255))
        # thick outline
        for i in range(4):
            draw.line(pts+[pts[0]], fill=(255,255,255), width=4)
    elif kind == "diamond":
        pts = [(cx, cy-s), (cx+s, cy), (cx, cy+s), (cx-s, cy)]
        draw.polygon(pts, fill=color, outline=(255,255,255))
        for i in range(4):
            draw.line(pts+[pts[0]], fill=(255,255,255), width=4)
    elif kind == "star":
        pts = _star_points(cx, cy, s, s*0.45, 5)
        draw.polygon(pts, fill=color, outline=(255,255,255))
        for i in range(4):
            draw.line(pts+[pts[0]], fill=(255,255,255), width=4)
    elif kind == "heart":
        # Proper symmetric heart: two upper circles + triangle bottom
        r = s * 0.55
        # left circle
        cx1 = cx - s*0.5
        cy1 = cy - s*0.25
        # right circle
        cx2 = cx + s*0.5
        cy2 = cy - s*0.25
        draw.ellipse((cx1-r, cy1-r, cx1+r, cy1+r), fill=color,
                     outline=(255,255,255), width=4)
        draw.ellipse((cx2-r, cy2-r, cx2+r, cy2+r), fill=color,
                     outline=(255,255,255), width=4)
        # triangle bottom to merge circles
        tri = [(cx - s*0.95, cy - s*0.05),
               (cx + s*0.95, cy - s*0.05),
               (cx, cy + s*0.95)]
        draw.polygon(tri, fill=color)
        # Cover the top gap between circles with a square of same color
        draw.rectangle((cx - s*0.5, cy - s*0.8, cx + s*0.5, cy - s*0.05),
                       fill=color)
        # Redraw the arcs cleanly for outline
        draw.arc((cx1-r, cy1-r, cx1+r, cy1+r), 180, 0,
                 fill=(255,255,255), width=4)
        draw.arc((cx2-r, cy2-r, cx2+r, cy2+r), 180, 0,
                 fill=(255,255,255), width=4)
        draw.arc((cx1-r, cy1-r, cx1+r, cy1+r), 0, 180, fill=color, width=4)
        draw.arc((cx2-r, cy2-r, cx2+r, cy2+r), 0, 180, fill=color, width=4)
        # Left/right side lines down to point
        draw.line([(cx1 - r + 1, cy1), tri[2]], fill=(255,255,255), width=4)
        draw.line([(cx2 + r - 1, cy2), tri[2]], fill=(255,255,255), width=4)
    elif kind == "heart_broken":
        draw_shape(draw, "heart", cx, cy, s, color)
        # jagged crack line
        random.seed(42)
        pts = [(cx, cy-s*0.9)]
        y = cy - s*0.9
        while y < cy + s*0.7:
            y += random.randint(15, 30)
            xoff = random.choice([-1, 1]) * random.randint(8, 18)
            pts.append((cx + xoff, y))
        for i in range(len(pts)-1):
            draw.line([pts[i], pts[i+1]], fill=(30,0,0), width=6)
    else:
        # fallback: circle
        draw.ellipse((cx-s, cy-s, cx+s, cy+s), fill=color,
                     outline=(255,255,255), width=4)


# ---------------------------------------------------------------------------
# Segment frame generators
# ---------------------------------------------------------------------------
def hook_frames(puzzle, duration=3.0):
    bg = make_gradient(*puzzle["bg"])
    n = int(duration * FPS)
    random.seed(1)
    # decor positions
    decos = []
    for k in range(14):
        decos.append({
            "kind": random.choice(["star","circle","heart","diamond"]),
            "color": tuple(random.randint(150,255) for _ in range(3)),
            "cx": random.randint(80, W-80),
            "cy": random.randint(100, H-100),
            "size": random.randint(25, 55),
            "phase": random.random() * math.pi * 2,
        })
    for i in range(n):
        t = i / max(n-1, 1)
        img = bg.copy()
        draw = ImageDraw.Draw(img)
        # floating decor
        for d in decos:
            dy = int(15 * math.sin(t*2*math.pi + d["phase"]))
            alpha_scale = 0.35 + 0.25 * (1 + math.sin(t*3 + d["phase"]))
            c = tuple(int(v*alpha_scale) for v in d["color"])
            draw_shape(draw, d["kind"], d["cx"], d["cy"]+dy, d["size"], c)
        # Pulsing hook text (auto-fit to screen width)
        scale = 1.0 + 0.04*math.sin(t*math.pi*4)
        size_h = int(110*scale)
        draw_centered_text(draw, (W//2, H//2-140),
                           puzzle["hook"], font=None, start_size=size_h,
                           max_width=W-80,
                           fill=(255,255,255), stroke_fill=(0,0,0), stroke_width=5)
        if t > 0.5:
            a = min(1.0, (t-0.5)*2)
            c = (255, 255, int(255*a))
            draw_centered_text(draw, (W//2, H//2+120),
                               puzzle["instruction"], font=None, start_size=72,
                               max_width=W-80,
                               fill=c, stroke_fill=(0,0,0), stroke_width=4)
        if t > 0.7:
            secs = ["READY", "SET", "GO!"]
            idx = min(2, int((t-0.7)/0.1))
            draw_centered_text(draw, (W//2, H-200), secs[idx],
                               font=None, start_size=100, max_width=W-80,
                               fill=(255,80,80), stroke_fill=(0,0,0), stroke_width=5)
        yield img


def draw_grid(draw, puzzle, box, highlight_answer=False, t_anim=1.0, pulse_t=0.0):
    x0,y0,x1,y1 = box
    cols = puzzle.get("cols", 4)
    total = len(puzzle["shapes"])
    rows = math.ceil(total/cols)
    cell_w = (x1-x0)/cols
    cell_h = (y1-y0)/rows
    # Auto-fit shape size to cell (leave padding)
    auto_size = int(min(cell_w, cell_h) * 0.40)
    base_size = min(puzzle.get("size", 120), auto_size)
    for idx in range(total):
        r = idx // cols; c = idx % cols
        cx = int(x0 + c*cell_w + cell_w/2)
        cy = int(y0 + r*cell_h + cell_h/2)
        # pop-in animation
        if t_anim < 1.0:
            pop = min(1.0, max(0.0, (t_anim - idx*0.03)/0.25))
            bounce = -15*math.sin(pop*math.pi) if pop < 1.0 else 0
            s = int(base_size*(0.4 + 0.6*pop))
            cy += int(bounce)
        else:
            s = base_size
        is_ans = (idx == puzzle["answer_index"])
        highlight = highlight_answer and is_ans
        if highlight:
            # pulsing yellow ring around answer
            pr = int(s*1.15 + 8 + 6*math.sin(pulse_t*8))
            draw.ellipse((cx-pr, cy-pr, cx+pr, cy+pr),
                         fill=None, outline=(255,255,0), width=10)
            draw.ellipse((cx-pr-6, cy-pr-6, cx+pr+6, cy+pr+6),
                         fill=None, outline=(255,120,0), width=4)
        kind = puzzle["shapes"][idx]
        color = tuple(puzzle["shape_colors"][idx])
        draw_shape(draw, kind, cx, cy, s, color)


def draw_choices(draw, choices, answer_idx, reveal, box, t):
    x0,y0,x1,y1 = box
    n = len(choices)
    h = (y1-y0)/n - 18
    # font size adapts to height
    ft_size = max(30, min(70, int(h * 0.5)))
    ft = tfont(ft_size)
    for i, c in enumerate(choices):
        by = int(y0 + i*(h+18))
        bx = (x0, by, x1, by+int(h))
        is_answer = reveal and i == answer_idx
        if is_answer:
            fill, outline = (80,230,80), (0,120,0)
        elif reveal:
            fill, outline = (180,180,180), (100,100,100)
        else:
            fill, outline = (255,255,255), (0,0,0)
        draw_bubble(draw, bx, fill=fill, outline=outline, radius=30, width=5)
        label = f"{chr(65+i)}.  {c}"
        draw_centered_text(draw, ((x0+x1)//2, by+int(h)//2), label,
                           font=None, start_size=ft_size, max_width=(x1-x0)-60,
                           fill=(0,0,0), stroke_fill=(0,0,0), stroke_width=0, shadow=False)


def countdown_circle(draw, center, radius, secs_left, total, t):
    cx, cy = center
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius),
                 fill=(0,0,0), outline=(255,255,255), width=6)
    frac = max(0.0, min(1.0, secs_left / max(total, 1)))
    segs = 80
    start = -math.pi/2
    for s in range(segs):
        if s/segs > frac: break
        ang = start + (s/segs)*2*math.pi
        x2 = cx + int((radius-8)*math.cos(ang))
        y2 = cy + int((radius-8)*math.sin(ang))
        draw.ellipse((x2-6,y2-6,x2+6,y2+6), fill=(255,80,80))
    color = (255,80,80) if secs_left <= 3 else (255,255,255)
    draw_centered_text(draw, center, str(secs_left), tfont(110),
                       fill=color, stroke_fill=(0,0,0), stroke_width=4)


def puzzle_frames(puzzle, duration=10.0):
    bg = make_gradient(*puzzle["bg"])
    n = int(duration*FPS); total = duration
    ptype = puzzle["type"]
    for i in range(n):
        t = i/max(n-1,1)
        elapsed = t*duration
        secs_left = max(1, int(math.ceil(total - elapsed)))
        img = bg.copy(); draw = ImageDraw.Draw(img)
        # header
        if ptype == "odd_shape":
            header = "FIND THE ODD ONE OUT!"
        else:
            header = puzzle["instruction"].upper()
        draw_centered_text(draw, (W//2-80, 180), header, font=None, start_size=70,
                           max_width=W-420,  # leave room for countdown circle
                           fill=(255,255,255), stroke_fill=(0,0,0), stroke_width=4)
        # content box
        grid_box = (80, 340, W-80, H-380)
        if ptype == "odd_shape":
            draw_grid(draw, puzzle, grid_box, highlight_answer=False,
                      t_anim=min(1.0, t*1.5))
        else:
            draw_bubble(draw, grid_box, fill=(255,255,255), outline=(0,0,0), radius=40, width=6)
            # auto-size problem text to fit box
            draw_centered_text(draw, (W//2, (grid_box[1]+grid_box[3])//2 - 200),
                               puzzle["problem"],
                               font=None, start_size=80, max_width=W-200,
                               fill=(30,30,30), stroke_fill=(0,0,0), stroke_width=0, shadow=False)
            cbox = (120, grid_box[3]-520, W-120, grid_box[3]-40)
            draw_choices(draw, puzzle["choices"], puzzle["answer_index"], False, cbox, t)
        # countdown
        countdown_circle(draw, (W-160, 180), 100, secs_left, int(total), t)
        # tension flash
        if secs_left <= 2:
            flash = int(70*abs(math.sin(elapsed*8)))
            overlay = Image.new("RGBA", img.size, (255,0,0,flash))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(img)
        yield img


def answer_frames(puzzle, duration=7.0):
    bg = make_gradient(*puzzle["bg"])
    n = int(duration*FPS); ptype = puzzle["type"]
    for i in range(n):
        t = i/max(n-1,1)
        img = bg.copy(); draw = ImageDraw.Draw(img)
        draw_centered_text(draw, (W//2, 180), "TIME'S UP!", font=None, start_size=100,
                           max_width=W-80,
                           fill=(255,255,100), stroke_fill=(0,0,0), stroke_width=5)
        grid_box = (80, 340, W-80, H-640)
        if ptype == "odd_shape":
            draw_grid(draw, puzzle, grid_box, highlight_answer=True, t_anim=1.0, pulse_t=t)
        else:
            draw_bubble(draw, grid_box, fill=(255,255,255), outline=(0,0,0), radius=40, width=6)
            draw_centered_text(draw, (W//2, (grid_box[1]+grid_box[3])//2 - 200),
                               puzzle["problem"],
                               font=None, start_size=75, max_width=W-200,
                               fill=(30,30,30), stroke_fill=(0,0,0), stroke_width=0, shadow=False)
            cbox = (120, grid_box[3]-520, W-120, grid_box[3]-40)
            draw_choices(draw, puzzle["choices"], puzzle["answer_index"], t>0.25, cbox, t)
        # explanation bar — slides in from bottom, final position leaves 60px margin
        bar_h = 260
        bar_y_final = H - 60 - bar_h
        bar_y = bar_y_final + int(max(0, (1-min(1.0,t*2))*(bar_h+60)))
        draw_bubble(draw, (40, bar_y, W-40, bar_y+bar_h), fill=(255,255,255),
                    outline=(0,0,0), radius=40, width=6)
        draw_centered_text(draw, (W//2, bar_y+bar_h//2),
                           puzzle["answer_explanation"],
                           font=None, start_size=58, max_width=W-120,
                           fill=(30,30,30), stroke_fill=(0,0,0), stroke_width=0, shadow=False)
        yield img


def cta_frames(puzzle, duration=10.0):
    bg = make_gradient(*puzzle["bg"])
    n = int(duration*FPS)
    random.seed(7)
    confetti = []
    kinds = ["star","circle","heart","diamond"]
    for k in range(25):
        confetti.append({
            "kind": random.choice(kinds),
            "color": tuple(random.randint(100,255) for _ in range(3)),
            "cx": random.randint(50, W-50),
            "size": random.randint(20,45),
            "speed": 180 + random.random()*300,
            "phase": random.random()*math.pi*2,
        })
    for i in range(n):
        t = i/max(n-1,1)
        img = bg.copy(); draw = ImageDraw.Draw(img)
        # falling confetti shapes
        for d in confetti:
            ey = int((t*d["speed"]*4) % (H+300)) - 150
            dx = int(20*math.sin(t*4 + d["phase"]))
            draw_shape(draw, d["kind"], d["cx"]+dx, ey, d["size"], d["color"])
        draw_centered_text(draw, (W//2, H//2-260), "NICE JOB!", font=None, start_size=140,
                           max_width=W-80,
                           fill=(255,255,100), stroke_fill=(0,0,0), stroke_width=6)
        draw_centered_text(draw, (W//2, H//2-60), "Did you get it right?", font=None,
                           start_size=80, max_width=W-80,
                           fill=(255,255,255), stroke_fill=(0,0,0), stroke_width=4)
        # buttons
        draw_bubble(draw, (120, H//2+80, W//2-30, H//2+260),
                    fill=(255,80,80), outline=(0,0,0), radius=40, width=6)
        draw_bubble(draw, (W//2+30, H//2+80, W-120, H//2+260),
                    fill=(80,80,255), outline=(0,0,0), radius=40, width=6)
        # heart on the LIKE button
        draw_shape(draw, "heart", W//2-160, H//2+170, 30, (255,255,255))
        draw_centered_text(draw, (W//2-50, H//2+170), "LIKE", font=None,
                           start_size=75, max_width=W//2-200,
                           fill=(255,255,255), stroke_fill=(0,0,0), stroke_width=3)
        draw_centered_text(draw, (3*W//4, H//2+170), "SUBSCRIBE", font=None,
                           start_size=68, max_width=W//2-200,
                           fill=(255,255,255), stroke_fill=(0,0,0), stroke_width=3)
        # pulsing CTA text
        scale = 1.0 + 0.06*math.sin(t*6)
        draw_centered_text(draw, (W//2, H-250), _ascii_safe(puzzle["cta"]), font=None,
                           start_size=int(70*scale), max_width=W-80,
                           fill=(255,255,255), stroke_fill=(0,0,0), stroke_width=4)
        yield img


# ---------------------------------------------------------------------------
# TTS (edge-tts, free, no API key)
# ---------------------------------------------------------------------------
VOICE = "en-US-GuyNeural"  # upbeat male US voice

def script_for(puzzle):
    ptype = puzzle["type"]
    if ptype == "odd_shape":
        puzzle_line = f"{puzzle['instruction']} Ten seconds on the clock... Go!"
    else:
        puzzle_line = f"{puzzle['instruction']} Ten seconds... Go!"
    return [
        ("hook",   puzzle["hook"],                                    0.2),
        ("puzzle", puzzle_line,                                        3.0),
        ("answer", f"Time's up! {puzzle['answer_explanation']}",      13.5),
        ("cta",    f"{puzzle['cta']} Comment 'got it' if you did!",   21.0),
    ]


async def _synth(text, out_path):
    import edge_tts
    c = edge_tts.Communicate(text, VOICE, rate="+20%", pitch="+2Hz")
    await c.save(str(out_path))


def synthesize_audio(puzzle, work_dir: Path) -> Path:
    script = script_for(puzzle)
    seg_files = []
    async def _gen():
        for i, (_, text, _) in enumerate(script):
            sp = work_dir / f"seg_{i:02d}.mp3"
            await _synth(text, sp)
            seg_files.append(sp)
    asyncio.run(_gen())
    list_file = work_dir / "segs.txt"
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in seg_files))
    out = work_dir / "voice.mp3"
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0",
                    "-i", str(list_file), "-c:a", "libmp3lame", "-b:a", "192k",
                    str(out)], check=True, capture_output=True)
    return out


# ---------------------------------------------------------------------------
# Add background music: generate a cheerful tone-bed via ffmpeg sine waves
# ---------------------------------------------------------------------------
def generate_bgm(work_dir: Path, duration_s: float = 30.0) -> Path:
    """Create a simple cheerful upbeat bed (sine waves with tremolo) via ffmpeg."""
    out = work_dir / "bgm.mp3"
    # Simple C-major arpeggio loop made from sine waves — cheerful, royalty-free
    # Using ffmpeg's sine source with multiple frequencies + filters for a playful loop
    # Frequencies (Hz): C5=523 E5=659 G5=784 C6=1047 — simple arpeggio every 0.4s
    # Build a looped tone
    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i",
        f"sine=frequency=523:duration={duration_s},"
        f"tremolo=f=2.5:d=0.4",
        "-f", "lavfi", "-i",
        f"sine=frequency=659:duration={duration_s},"
        f"tremolo=f=2.5:d=0.4",
        "-f", "lavfi", "-i",
        f"sine=frequency=784:duration={duration_s},"
        f"tremolo=f=2.5:d=0.4",
        "-filter_complex",
        # amix, then lowpass to make it sound soft, then reduce volume so voice dominates
        "[0:a][1:a][2:a]amix=inputs=3:duration=first,"
        "lowpass=f=1800,volume=0.08,"
        "afade=t=in:st=0:d=0.3,afade=t=out:st=29:d=1",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        # If bgm fails, write a silent audio file instead
        subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                        f"anullsrc=r=44100:cl=mono", "-t", str(duration_s),
                        "-c:a", "libmp3lame", "-b:a", "64k", str(out)],
                       check=True, capture_output=True)
    return out


def mix_audio(voice_path: Path, bgm_path: Path, out_path: Path) -> Path:
    """Mix voice (loud) over background music (quieter)."""
    cmd = [
        FFMPEG, "-y",
        "-i", str(voice_path), "-i", str(bgm_path),
        "-filter_complex",
        "[1:a]volume=0.35,atrim=0:30[bg];"
        "[0:a]volume=1.5,apad=pad_dur=1[v];"
        "[v][bg]amix=inputs=2:duration=longest:normalize=0:dropout_transition=2,"
        "atrim=0:30,afade=t=out:st=29:d=1",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        # Fallback: just copy voice
        shutil.copy(voice_path, out_path)
    return out_path


# ---------------------------------------------------------------------------
# Render video
# ---------------------------------------------------------------------------
def render_video(puzzle, out_path: Path, audio_path: Path | None = None,
                 timeline=None) -> Path:
    if timeline is None:
        timeline = [
            (hook_frames,    3.0),
            (puzzle_frames, 10.0),
            (answer_frames,  7.0),
            (cta_frames,    10.0),
        ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    silent = out_path.with_suffix(".silent.mp4")
    cmd = [
        FFMPEG, "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
        "-crf", "22", "-movflags", "+faststart",
        str(silent),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for gen, dur in timeline:
            for fr in gen(puzzle, dur):
                proc.stdin.write(np.array(fr).tobytes())
        proc.stdin.close()
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError("ffmpeg failed: " + proc.stderr.read().decode()[-1000:])
    finally:
        if proc.poll() is None:
            proc.kill()
    if audio_path and audio_path.exists():
        cmd2 = [
            FFMPEG, "-y", "-i", str(silent), "-i", str(audio_path),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(out_path),
        ]
        r = subprocess.run(cmd2, capture_output=True)
        if r.returncode != 0:
            shutil.copy(silent, out_path)
        silent.unlink(missing_ok=True)
    else:
        shutil.move(str(silent), str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# Title/description/tags
# ---------------------------------------------------------------------------
def _title(p):
    if p["type"] == "odd_shape":
        return f"🧩 Can You Find the Odd One Out? 🤯 #shorts #puzzle #braingames"
    if p["type"] == "math":
        return f"🧮 Solve in 10 Seconds! 🤔 #shorts #math #puzzle"
    return f"🧠 Can You Solve This Riddle? 🤯 #shorts #riddle"


def _description(p):
    return (
        f"{p['hook']}\n\n"
        f"Put your brain to the test! You have 10 seconds to find the answer — "
        f"how fast can you get it?\n\n"
        f"💡 Answer: {p['answer_explanation']}\n\n"
        f"❤️ LIKE if you got it right! 🔔 SUBSCRIBE for a new brain teaser every day! 🧩✨\n\n"
        f"#shorts #puzzle #braingames #riddle #kids #fun #challenge #oddoneout "
        f"#brainteaser #quiz #guesstheanswer #viralshorts #trending #forkids "
        f"#puzzlegame #mindgame"
    )


def _tags(p):
    base = ["shorts", "puzzle", "brain teaser", "riddle", "kids", "fun",
            "challenge", "odd one out", "brain games", "quiz",
            "guess the answer", "viral shorts", "trending", "for kids",
            "puzzle game", "mind games", "kids video"]
    if p["type"] == "math":
        base += ["math puzzle", "math tricks", "math for kids", "math shorts"]
    if p["type"] == "odd_shape":
        base += ["spot the difference", "find the odd", "odd emoji out",
                 "eye test", "visual puzzle"]
    return base


def build_short(puzzle, output_dir: Path = OUTPUT_DIR) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"short_{puzzle['id']}_", dir=str(output_dir)))
    try:
        voice = synthesize_audio(puzzle, work)
        bgm = generate_bgm(work, 30.0)
        mixed = work / "mixed.m4a"
        mix_audio(voice, bgm, mixed)
        video_path = output_dir / f"{puzzle['id']}.mp4"
        render_video(puzzle, video_path, audio_path=mixed)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    meta = {
        "id": puzzle["id"],
        "title": _title(puzzle),
        "description": _description(puzzle),
        "tags": _tags(puzzle),
        "video_path": str(video_path),
        "type": puzzle["type"],
        "made_for_kids": True,
    }
    (output_dir / f"{puzzle['id']}.json").write_text(json.dumps(meta, indent=2))
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--puzzle-id")
    ap.add_argument("--output-dir", default=str(OUTPUT_DIR))
    ap.add_argument("--silent", action="store_true", help="skip audio (faster test)")
    args = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).parent))
    from puzzles import ALL_PUZZLES, get_puzzle
    if args.puzzle_id:
        p = get_puzzle(args.puzzle_id)
        if not p: sys.exit(f"Unknown puzzle: {args.puzzle_id}")
    else:
        p = ALL_PUZZLES[0]
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if args.silent:
        # Video only, no audio (quick preview render)
        video_path = out / f"{p['id']}.mp4"
        render_video(p, video_path, audio_path=None)
        print(f"Silent render: {video_path}")
    else:
        meta = build_short(p, out)
        print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
