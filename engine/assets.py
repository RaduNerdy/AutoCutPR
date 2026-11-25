"""Measure a logo's real visible bounds and work out how to place it.

The logo is a full-frame alpha matte: a 1920x1080 canvas that is mostly
transparent, with the actual mark somewhere inside it. Neither its size nor its
position in that canvas is known, so a nominal "165%" says nothing about what
lands on screen — and centring the *canvas* does not centre the *mark*.

Pillow gives the alpha bounding box, which pins both down. Everything else here
is the arithmetic from that box to the Motion values Premiere needs.
"""
import json
from pathlib import Path

# Premiere "Set to Frame Size" fits the whole source inside the frame
def _fit_factor(src_w: int, src_h: int, frame_w: int, frame_h: int) -> float:
    return min(frame_w / src_w, frame_h / src_h)


def measure_logo(path: Path, cache_dir: Path | None = None) -> dict:
    """Visible (non-transparent) bounds of the logo, in source pixels."""
    from PIL import Image

    cache = None
    if cache_dir:
        cache = cache_dir / f"logo_{path.stem}.json"
        if cache.exists():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
                if cached.get("mtime") == path.stat().st_mtime:
                    return cached
            except (json.JSONDecodeError, OSError):
                pass

    with Image.open(path) as im:
        src_w, src_h = im.size
        if "A" in im.getbands():
            bbox = im.getchannel("A").getbbox()
        else:
            # no alpha: fall back to non-black content
            bbox = im.convert("L").point(lambda p: 255 if p > 8 else 0).getbbox()

    if not bbox:  # fully transparent
        bbox = (0, 0, src_w, src_h)
    x0, y0, x1, y1 = bbox
    info = {
        "path": str(path),
        "mtime": path.stat().st_mtime,
        "source_w": src_w,
        "source_h": src_h,
        "visible_x": x0,
        "visible_y": y0,
        "visible_w": x1 - x0,
        "visible_h": y1 - y0,
        "has_alpha": True,
    }
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(info), encoding="utf-8")
    return info


def plan_placement(
    logo: dict,
    scale_pct: float = 165.0,
    target_y: float = 0.735,
    target_x: float = 0.49,
    frame_w: int = 1080,
    frame_h: int = 1920,
    target_width_pct: float | None = None,
) -> dict:
    """Motion Scale/Position that put the *visible mark* where it is wanted.

    Premiere's Position moves the whole source canvas, so a mark sitting off
    centre inside its 1920x1080 matte would land off target. The canvas is
    therefore offset by however far the mark is from the canvas centre.
    """
    fit = _fit_factor(logo["source_w"], logo["source_h"], frame_w, frame_h)

    if target_width_pct is not None:
        # solve for the scale that makes the mark this share of frame width
        want_px = frame_w * target_width_pct / 100.0
        scale_pct = want_px / (logo["visible_w"] * fit) * 100.0

    k = fit * scale_pct / 100.0
    on_w = logo["visible_w"] * k
    on_h = logo["visible_h"] * k

    # where the mark's centre sits relative to the canvas centre, in source px
    mark_cx = logo["visible_x"] + logo["visible_w"] / 2.0
    mark_cy = logo["visible_y"] + logo["visible_h"] / 2.0
    dx = (mark_cx - logo["source_w"] / 2.0) * k / frame_w
    dy = (mark_cy - logo["source_h"] / 2.0) * k / frame_h

    return {
        "scale": round(scale_pct, 2),
        "position": [round(target_x - dx, 5), round(target_y - dy, 5)],
        "on_screen_w": round(on_w, 1),
        "on_screen_h": round(on_h, 1),
        "pct_of_frame_w": round(on_w / frame_w * 100, 1),
        "fits": on_w <= frame_w and on_h <= frame_h,
        "summary": (
            f"mark {logo['visible_w']}x{logo['visible_h']}px of "
            f"{logo['source_w']}x{logo['source_h']} canvas -> "
            f"{on_w:.0f}x{on_h:.0f}px on screen "
            f"({on_w / frame_w * 100:.0f}% of frame width) at scale {scale_pct:.0f}%"
        ),
    }


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    info = measure_logo(Path(sys.argv[1]))
    scale = float(sys.argv[2]) if len(sys.argv) > 2 else 165.0
    print(json.dumps(info, indent=2))
    print(json.dumps(plan_placement(info, scale), indent=2))
