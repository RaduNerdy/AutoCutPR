"""Verify every removed stretch was genuinely silent in the waveform.

Whisper word extents are NOT a valid reference here: it routinely pads a word's
end far past the real audio (a 0.15s "la" reported as 1.6s), so checking cuts
against word spans produces false alarms. ffmpeg's silencedetect output is the
ground truth for what was actually quiet.

Usage: python verify_segments.py cuts.json words.json silence.json
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

cuts = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
words = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["words"]
sils = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))


def silent_coverage(a: float, b: float) -> float:
    """Fraction of [a,b] that ffmpeg marked as silence."""
    if b <= a:
        return 1.0
    covered = sum(
        max(0.0, min(b, s["end"]) - max(a, s["start"])) for s in sils
    )
    return covered / (b - a)


bad_audio = 0
onset_clips = 0
removed_total = 0.0
shortest = 999.0
n_segments = 0

for c in cuts:
    segs = c["segments"]
    n_segments += len(segs)
    for s in segs:
        shortest = min(shortest, s["out_s"] - s["in_s"])
    for a, b in zip(segs, segs[1:]):
        gap_a, gap_b = a["out_s"], b["in_s"]
        removed_total += gap_b - gap_a
        cov = silent_coverage(gap_a, gap_b)
        if cov < 0.95:
            bad_audio += 1
            print(f"  !! reel {c['reel']}: removed {gap_a:.2f}-{gap_b:.2f} "
                  f"is only {cov*100:.0f}% silent")
        # a word starting inside a removed stretch means a clipped onset
        for w in words:
            if gap_a < w["start"] < gap_b:
                onset_clips += 1
                print(f"  !! reel {c['reel']}: removed {gap_a:.2f}-{gap_b:.2f} "
                      f"clips onset of '{w['word'].strip()}' at {w['start']:.2f}")

print(f"\n{n_segments} segments across {len(cuts)} reels")
print(f"shortest segment : {shortest:.2f}s")
print(f"total removed    : {removed_total:.1f}s")
print(f"removed-but-not-silent : {bad_audio}")
print(f"clipped word onsets    : {onset_clips}")
