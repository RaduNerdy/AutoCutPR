"""Why does hybrid keep silence that db-only would cut? Inspect one reel.

Usage: python compare_modes.py words.json silence.json <reel_in_s> <reel_out_s>
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

words = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["words"]
sils = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
lo, hi = float(sys.argv[3]), float(sys.argv[4])

print(f"reel {lo:.1f}..{hi:.1f}\n")
for s in sils:
    a, b = max(s["start"], lo), min(s["end"], hi)
    if b - a <= 0.5:
        continue
    inside = [w for w in words if a < (w["start"] + w["end"]) / 2 < b]
    touching = [w for w in words if w["start"] < b and w["end"] > a]
    verdict = "VETOED" if inside else "cut"
    print(f"{a:8.2f}-{b:8.2f} ({b-a:4.2f}s)  {verdict}")
    if inside:
        for w in inside:
            print(f"      word inside: '{w['word'].strip()}' "
                  f"[{w['start']:.2f}-{w['end']:.2f}] dur={w['end']-w['start']:.2f}")
    elif touching:
        print(f"      (edge bleed only: "
              f"{', '.join(repr(w['word'].strip()) for w in touching)})")
