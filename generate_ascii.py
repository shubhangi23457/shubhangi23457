#!/usr/bin/env python3
"""
generate_ascii.py
------------------
Converts a photo into a typing-animated ASCII portrait SVG for the
GitHub profile README (assets/ascii-portrait.svg).

Usage:
    python3 generate_ascii.py <input_image> <output_svg> [--cols 46] [--rows 26]

Requirements:
    pip install pillow

What it does:
    1. Loads your photo, converts to grayscale, resizes to a character
       grid (correcting for monospace character aspect ratio so the
       portrait isn't squashed).
    2. Maps each cell's brightness to a character from a density ramp
       and to a color from the cyan -> blue -> purple theme gradient.
    3. Emits a single self-contained SVG where each row is revealed with
       a clip-path "typewriter" animation, one row after another, ending
       in a permanently blinking terminal cursor.

Re-run this any time you want to refresh the portrait with a new photo:
    python3 assets/generate_ascii.py my-new-photo.jpg assets/ascii-portrait.svg
"""

import argparse
import sys
from PIL import Image, ImageOps

# Density ramp: index 0 = darkest/sparsest -> last = brightest/densest.
# Kept short and hand-picked so the shading reads cleanly at small sizes.
RAMP = " .:-=+*#%@"

# Theme gradient stops (dark -> bright), matching the README palette.
GRADIENT = [
    (0.00, (124, 58, 237)),   # purple  #7c3aed
    (0.5, (56, 130, 246)),   # blue    #3882f6
    (1.00, (34, 211, 238)),   # cyan    #22d3ee
]

FONT_SIZE = 13
CHAR_W = FONT_SIZE * 0.6      # monospace advance width
LINE_H = FONT_SIZE * 1.15
PAD = 22
TITLEBAR_H = 34
TYPE_SPEED = 0.018            # seconds per character reveal
ROW_GAP = 0.02                # small pause between rows


def lerp(a, b, t):
    return a + (b - a) * t


def gradient_color(t):
    t = max(0.0, min(1.0, t))
    for i in range(len(GRADIENT) - 1):
        t0, c0 = GRADIENT[i]
        t1, c1 = GRADIENT[i + 1]
        if t0 <= t <= t1:
            local_t = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            r = round(lerp(c0[0], c1[0], local_t))
            g = round(lerp(c0[1], c1[1], local_t))
            b = round(lerp(c0[2], c1[2], local_t))
            return f"#{r:02x}{g:02x}{b:02x}"
    return "#22d3ee"


def image_to_grid(path, cols, rows, crop=None):
    """crop: optional (left, top, right, bottom) as fractions 0..1 of the
    original image, used to tighten framing on the subject and cut down
    busy backgrounds before the ASCII downsample."""
    img = Image.open(path).convert("L")
    if crop:
        w, h = img.size
        l, t, r, b = crop
        img = img.crop((int(w * l), int(h * t), int(w * r), int(h * b)))
    img = ImageOps.autocontrast(img, cutoff=1)
    img = img.resize((cols, rows), Image.LANCZOS)
    return list(img.getdata()), img.width, img.height


def build_rows(pixels, cols, rows):
    """Return list of rows; each row is a list of (char, color) tuples.

    Character and color are derived from the same quantized brightness
    level so runs of equal level share identical (char, color) pairs -
    this is what lets group_row() collapse them into single tspans.
    """
    out = []
    ramp_len = len(RAMP)
    for r in range(rows):
        row = []
        for c in range(cols):
            v = pixels[r * cols + c] / 255.0
            idx = min(ramp_len - 1, int(v * ramp_len))
            ch = RAMP[idx]
            color = gradient_color(idx / (ramp_len - 1))
            row.append((ch, color))
        out.append(row)
    return out


def escape(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def group_row(row):
    """Group consecutive same-color chars into single tspans (smaller SVG)."""
    groups = []
    cur_char, cur_color = row[0]
    cur_run = cur_char
    for ch, color in row[1:]:
        if color == cur_color:
            cur_run += ch
        else:
            groups.append((cur_run, cur_color))
            cur_run = ch
            cur_color = color
    groups.append((cur_run, cur_color))
    return groups


def build_svg(rows, cols, n_rows):
    width = cols * CHAR_W + PAD * 2
    height = n_rows * LINE_H + PAD * 2 + TITLEBAR_H
    content_x = PAD
    content_y = PAD + TITLEBAR_H + FONT_SIZE

    row_ids = [f"row{i}" for i in range(n_rows)]
    row_elems = []
    cursor_y_sets = []
    cursor_x_keytimes = []
    cursor_x_values = []

    total_dur = 0.0
    row_durations = []
    for i in range(n_rows):
        row_text_len = len(rows[i])
        dur = max(0.08, row_text_len * TYPE_SPEED)
        row_durations.append(dur)

    # Precompute cumulative begin times for cursor x-follow curve
    cursor_time_cursor = 0.0
    for i in range(n_rows):
        row_width_px = len(rows[i]) * CHAR_W
        dur = row_durations[i]
        start_t = cursor_time_cursor
        end_t = start_t + dur
        cursor_x_keytimes.append(start_t)
        cursor_x_values.append(content_x)
        cursor_x_keytimes.append(end_t)
        cursor_x_values.append(content_x + row_width_px)
        cursor_time_cursor = end_t + ROW_GAP

    total_dur = cursor_time_cursor

    # Normalize keytimes to 0..1 for the cursor x animate
    norm_keytimes = [round(t / total_dur, 5) if total_dur > 0 else 0 for t in cursor_x_keytimes]
    # De-duplicate ascending issues (ensure strictly non-decreasing)
    for i in range(1, len(norm_keytimes)):
        if norm_keytimes[i] <= norm_keytimes[i - 1]:
            norm_keytimes[i] = norm_keytimes[i - 1] + 0.00001
    if norm_keytimes and norm_keytimes[-1] < 1:
        norm_keytimes[-1] = 1.0
    norm_keytimes[0] = 0.0

    running_begin = 0.0
    for i in range(n_rows):
        y = content_y + i * LINE_H
        groups = group_row(rows[i])
        row_width_px = len(rows[i]) * CHAR_W
        dur = row_durations[i]

        tspans = "".join(
            f'<tspan fill="{color}">{escape(text)}</tspan>' for text, color in groups
        )

        clip_id = f"clip{i}"
        prev_id = row_ids[i - 1] if i > 0 else None
        begin_attr = "0s" if i == 0 else f"{prev_id}.end+{ROW_GAP}s"

        row_elems.append(f'''
    <clipPath id="{clip_id}">
      <rect x="{content_x}" y="{y - FONT_SIZE}" width="0" height="{LINE_H}">
        <animate id="{row_ids[i]}" attributeName="width" from="0" to="{row_width_px:.2f}"
                 begin="{begin_attr}" dur="{dur:.3f}s" fill="freeze" calcMode="linear"/>
      </rect>
    </clipPath>
    <text x="{content_x}" y="{y}" font-family="'JetBrains Mono','Fira Code','Courier New',monospace"
          font-size="{FONT_SIZE}" clip-path="url(#{clip_id})">{tspans}</text>''')

        cursor_y_sets.append(
            f'<set attributeName="y" to="{y - FONT_SIZE * 0.85}" begin="{begin_attr}"/>'
        )
        running_begin += dur + ROW_GAP

    last_row_id = row_ids[-1]
    cursor_x_vals_attr = ";".join(f"{v:.2f}" for v in cursor_x_values)
    cursor_x_keytimes_attr = ";".join(f"{t:.5f}" for t in norm_keytimes)

    svg = f'''<svg viewBox="0 0 {width:.0f} {height:.0f}" xmlns="http://www.w3.org/2000/svg"
     role="img" aria-label="Animated ASCII terminal portrait">
  <title>ASCII portrait, typed line by line</title>
  <defs>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&amp;display=swap');
      text {{ font-family: 'JetBrains Mono','Fira Code','Courier New',monospace; }}
    </style>
    <linearGradient id="titleGrad" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#7c3aed"/>
      <stop offset="50%" stop-color="#3882f6"/>
      <stop offset="100%" stop-color="#22d3ee"/>
    </linearGradient>
    <filter id="softGlow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="3" result="blur"/>
      <feMerge>
        <feMergeNode in="blur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>

  <rect x="0.5" y="0.5" width="{width - 1:.0f}" height="{height - 1:.0f}" rx="12" ry="12"
        fill="#070b14" stroke="#1e293b" stroke-width="1"/>

  <rect x="0.5" y="0.5" width="{width - 1:.0f}" height="{TITLEBAR_H}" rx="12" ry="12" fill="#0d1420"/>
  <rect x="0.5" y="{TITLEBAR_H - 12}" width="{width - 1:.0f}" height="12" fill="#0d1420"/>
  <line x1="0" y1="{TITLEBAR_H}" x2="{width:.0f}" y2="{TITLEBAR_H}" stroke="#1e293b" stroke-width="1"/>

  <circle cx="{PAD}" cy="{TITLEBAR_H / 2}" r="5" fill="#ef4444" opacity="0.85"/>
  <circle cx="{PAD + 18}" cy="{TITLEBAR_H / 2}" r="5" fill="#eab308" opacity="0.85"/>
  <circle cx="{PAD + 36}" cy="{TITLEBAR_H / 2}" r="5" fill="#22c55e" opacity="0.85"/>

  <text x="{width / 2:.0f}" y="{TITLEBAR_H / 2 + 4:.0f}" text-anchor="middle"
        font-family="'JetBrains Mono',monospace" font-size="12" fill="#64748b" letter-spacing="0.5">
    portrait.ascii
  </text>

  <g filter="url(#softGlow)">
    {"".join(row_elems)}
  </g>

  <rect id="cursor" x="{content_x}" width="{CHAR_W * 0.85:.2f}" height="{FONT_SIZE * 1.1:.2f}"
        y="{content_y - FONT_SIZE * 0.85:.2f}" fill="#22d3ee" opacity="1">
    {"".join(cursor_y_sets)}
    <animate attributeName="x" begin="0s" dur="{total_dur:.3f}s" fill="freeze"
             calcMode="linear" keyTimes="{cursor_x_keytimes_attr}" values="{cursor_x_vals_attr}"/>
    <animate attributeName="opacity" begin="{last_row_id}.end" dur="1s"
             repeatCount="indefinite" calcMode="discrete" keyTimes="0;0.5" values="1;0"/>
  </rect>
</svg>'''
    return svg


def main():
    parser = argparse.ArgumentParser(description="Convert a photo to a typing-animated ASCII SVG portrait.")
    parser.add_argument("input_image")
    parser.add_argument("output_svg")
    parser.add_argument("--cols", type=int, default=46)
    parser.add_argument("--rows", type=int, default=26)
    parser.add_argument("--crop", type=str, default=None,
                         help="left,top,right,bottom as fractions, e.g. 0.12,0.03,0.88,0.85")
    args = parser.parse_args()

    crop = None
    if args.crop:
        crop = tuple(float(x) for x in args.crop.split(","))

    pixels, cols, rows = image_to_grid(args.input_image, args.cols, args.rows, crop=crop)
    grid = build_rows(pixels, cols, rows)
    svg = build_svg(grid, cols, rows)

    with open(args.output_svg, "w") as f:
        f.write(svg)

    print(f"Wrote {args.output_svg} ({cols}x{rows} grid, {len(svg)} bytes)")


if __name__ == "__main__":
    main()
