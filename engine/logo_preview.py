"""Composite the real logo over a real 9:16 frame at several heights.

Renders exactly what the panel would produce, so a height can be chosen by
looking instead of by guessing numbers.
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw
import assets

LOGO = Path(sys.argv[1])
VIDEO = Path(sys.argv[2])
OUT = Path(__file__).resolve().parent.parent / "previews"
OUT.mkdir(exist_ok=True)
FRAME_W, FRAME_H = 1080, 1920
FRAME_X = 0.44          # same default the panel uses
# (x, y, width%, label) — target measured off the editor's finished reel
CANDIDATES = [
    (0.50, 0.750, 18.2, "OLD  centre low, 18% wide"),
    (0.49, 0.735, 27.0, "NEW  matches reference (27% wide)"),
]

# grab a frame from inside the first reel
still = OUT / "frame.png"
if not still.exists():
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "260", "-i", str(VIDEO),
                    "-frames:v", "1", str(still)], check=True)

src = Image.open(still).convert("RGBA")
logo_img = Image.open(LOGO).convert("RGBA")
info = assets.measure_logo(LOGO)

# reproduce the fill-and-centre crop: scale so height covers, shift by FRAME_X
k = FRAME_H / src.height
scaled = src.resize((round(src.width * k), FRAME_H), Image.LANCZOS)
# FRAME_X is where the frame centre sits in the scaled image
cx = scaled.width * FRAME_X
left = round(cx - FRAME_W / 2)
left = max(0, min(scaled.width - FRAME_W, left))
base_crop = scaled.crop((left, 0, left + FRAME_W, FRAME_H))

fit = min(FRAME_W / info["source_w"], FRAME_H / info["source_h"])

tiles = []
for tx, ty, wpct, name in CANDIDATES:
    canvas = base_crop.copy()
    pl = assets.plan_placement(info, target_y=ty, target_x=tx,
                               target_width_pct=wpct,
                               frame_w=FRAME_W, frame_h=FRAME_H)
    k_logo = fit * pl["scale"] / 100.0
    lw, lh = round(logo_img.width * k_logo), round(logo_img.height * k_logo)
    logo_scaled = logo_img.resize((lw, lh), Image.LANCZOS)
    # plan_placement gives the CANVAS centre; convert to a paste corner
    px, py = pl["position"]
    cx_px, cy_px = px * FRAME_W, py * FRAME_H
    canvas.alpha_composite(logo_scaled, (round(cx_px - lw / 2), round(cy_px - lh / 2)))

    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, FRAME_W, 96], fill=(0, 0, 0, 205))
    d.text((16, 20), name, fill=(255, 255, 255, 255))
    d.text((16, 52), f"x {tx}  y {ty}  scale {pl['scale']:.0f}%  "
                     f"= {pl['on_screen_w']:.0f}px wide", fill=(150, 210, 255, 255))
    # TikTok/Reels caption+button zone, for reference
    d.rectangle([0, FRAME_H - 320, FRAME_W, FRAME_H], outline=(90, 190, 255, 170), width=4)
    d.text((16, FRAME_H - 310), "typical caption / UI zone", fill=(140, 210, 255, 255))
    tiles.append(canvas)

gap = 24
sheet = Image.new("RGBA", (len(tiles) * FRAME_W + (len(tiles) - 1) * gap, FRAME_H),
                  (24, 24, 24, 255))
for i, t in enumerate(tiles):
    sheet.paste(t, (i * (FRAME_W + gap), 0))
sheet = sheet.resize((sheet.width // 2, sheet.height // 2), Image.LANCZOS)
out = OUT
out.mkdir(exist_ok=True)
out = out / "logo_heights.png"
sheet.convert("RGB").save(out, quality=92)
print("wrote", out)
print("logo mark on screen: %.0fx%.0f px (%.0f%% of frame width)"
      % (info["visible_w"] * k_logo, info["visible_h"] * k_logo,
         info["visible_w"] * k_logo / FRAME_W * 100))
