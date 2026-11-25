"""Decide what to cut out of a reel: real silence, but not meaningful pauses.

Two independent sources of truth, because each fails differently:

  * ffmpeg `silencedetect` reads the actual waveform, so its boundaries are exact
    — but it will happily flag a quiet word, a held breath, or a low mumble.
  * whisper's word timestamps know where speech *means* something, but the
    boundaries are only accurate to ~±100-200ms.

`hybrid` (the default) uses dB for the boundaries and whisper as a veto: a stretch
is only cut when the waveform says it is silent AND no word was transcribed inside
it. That is strictly safer than either source alone.

"Dramatic air" — a long pause after a finished sentence — is deliberate delivery,
not dead air, so it is kept (trimmed to DRAMATIC_KEEP rather than collapsed).
"""
import json
import re
import subprocess
from pathlib import Path

PRESETS = {
    #                min gap to cut, gap left behind
    "conservative": (1.00, 0.35),
    "moderate": (0.50, 0.20),
    "aggressive": (0.25, 0.10),
}
DEFAULT_PRESET = "moderate"
# breathing room kept at the very start/end of a reel
EDGE_PAD = 0.12
# never emit a segment shorter than this — slivers are useless on a timeline
MIN_SEGMENT_S = 0.30
# below this level counts as silence for the waveform pass
NOISE_DB = -35.0
# a pause this long right after a finished sentence reads as deliberate
DRAMATIC_S = 1.20
# ...and keeps this much of itself when protected
DRAMATIC_KEEP = 0.70
# a pause at least this long counts as "dead space" before a reel start
SNAP_MIN_SILENCE = 0.35
# how far a reel edge may travel to reach that pause; beyond this we are
# mid-idea and the matched point is already right
SNAP_MAX_BACK = 6.0
# ...but without an explicit "(nume)" placeholder the reach is this short, so a
# reel starts on the quote instead of on the clause leading up to it
SNAP_SHORT_BACK = 0.6
# words whisper reports as touching still need a sliver of room, or the last
# consonant is audibly clipped ("plecat" -> "pleca")
CONTIG_PAD = 0.08

SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


def detect_silence(
    audio_path: Path,
    cache_dir: Path,
    noise_db: float = NOISE_DB,
    min_dur: float = 0.20,
) -> list[dict]:
    """Waveform silence intervals via ffmpeg, cached per (file, threshold)."""
    cache = cache_dir / f"{audio_path.stem}.silence_{int(abs(noise_db))}db.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    proc = subprocess.run(
        # -vn is not optional: the source is often a multi-GB 1080p master, and
        # without it ffmpeg decodes every video frame to measure audio levels
        # (hours instead of seconds).
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio_path),
         "-vn", "-sn", "-dn", "-map", "0:a:0",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    intervals = []
    start = None
    for kind, value in SILENCE_RE.findall(proc.stderr or ""):
        t = float(value)
        if kind == "start":
            start = t
        elif start is not None:
            intervals.append({"start": round(start, 3), "end": round(t, 3)})
            start = None

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(intervals), encoding="utf-8")
    return intervals


def snap_reel_edges(
    words: list[dict],
    silences: list[dict] | None,
    in_s: float,
    out_s: float,
    max_back: float = SNAP_MAX_BACK,
    min_silence: float = SNAP_MIN_SILENCE,
    long_back: bool = False,
) -> tuple[float, float, dict]:
    """Move a matched reel edge to where the speaker actually starts/stops.

    Reaching backwards is deliberately timid. The quote *is* where the client
    wants the reel to begin, so pulling back to the start of the sentence drags
    in lead-ins nobody asked for ("Chiar m-a mirat că...", "zic măi..."). Only a
    short reach is allowed by default — enough to rescue a first word the match
    clipped when an idea follows a pause.

    `long_back` lifts that limit, and is set only when the client wrote a
    leading placeholder such as "(nume) a vrut să te ia": there the missing
    words genuinely precede the quote and must be recovered.
    """
    info = {"in_delta": 0.0, "out_delta": 0.0}
    if not silences:
        return in_s, out_s, info

    reach = max_back if long_back else SNAP_SHORT_BACK

    # Latest silence that ends before the in-point and is long enough to be a
    # real break rather than a beat between words.
    lead = [
        s for s in silences
        if s["end"] <= in_s
        and s["end"] >= in_s - reach
        and s["end"] - s["start"] >= min_silence
    ]
    if lead:
        onset = max(s["end"] for s in lead)
        # ffmpeg misses quiet onsets ("Fugeam", "n-am"), so the silence can be
        # reported as ending a beat after the word really began. Never start
        # inside a word: fall back to its start.
        straddling = [w["start"] for w in words if w["start"] < onset < w["end"]]
        if straddling:
            onset = min(straddling)
        # only useful if speech actually resumes between there and the match
        if any(onset <= w["start"] < in_s for w in words):
            info["in_delta"] = round(in_s - onset, 3)
            in_s = onset

    # Mirror on the way out so a trailing word is never clipped.
    trail = [
        s for s in silences
        if s["start"] >= out_s
        and s["start"] <= out_s + max_back
        and s["end"] - s["start"] >= min_silence
    ]
    if trail:
        ending = min(s["start"] for s in trail)
        straddling = [w["end"] for w in words if w["start"] < ending < w["end"]]
        if straddling:
            ending = max(straddling)
        if any(out_s < w["end"] <= ending for w in words):
            info["out_delta"] = round(ending - out_s, 3)
            out_s = ending

    return in_s, out_s, info


def _ends_sentence(word: str) -> bool:
    return word.strip().endswith((".", "?", "!", "…"))


def _cut_regions(
    words: list[dict],
    silences: list[dict] | None,
    lo: float,
    hi: float,
    min_gap: float,
    keep_gap: float,
    mode: str,
    protect_dramatic: bool,
) -> list[dict]:
    """Stretches to remove, already shrunk by the gap we intend to leave behind."""
    half = keep_gap / 2.0
    candidates = []

    if mode in ("db", "hybrid") and silences:
        for s in silences:
            a, b = max(s["start"], lo), min(s["end"], hi)
            if b - a <= min_gap:
                continue
            if mode == "hybrid":
                # Trim rather than veto. Whisper pads word *ends* (a 0.15s "la"
                # gets reported as 1s, swallowing the pause after it), so an
                # overlapping end proves nothing. Word *starts* are reliable:
                # if one begins inside this silence, ffmpeg missed a quiet
                # onset, so stop the cut there instead of discarding the whole
                # stretch. What is left is still real, cuttable silence.
                starts_inside = [w["start"] for w in words if a < w["start"] < b]
                if starts_inside:
                    b = min(starts_inside)
                if b - a <= min_gap:
                    continue
            candidates.append((a, b))
    else:
        inside = [w for w in words if w["start"] >= lo and w["end"] <= hi]
        for prev, nxt in zip(inside, inside[1:]):
            if nxt["start"] - prev["end"] > min_gap:
                candidates.append((prev["end"], nxt["start"]))

    regions = []
    for a, b in candidates:
        keep = keep_gap
        if protect_dramatic and (b - a) >= DRAMATIC_S:
            before = [w for w in words if w["end"] <= a + 0.05]
            if before and _ends_sentence(before[-1]["word"]):
                keep = DRAMATIC_KEEP
        if (b - a) <= keep:
            continue
        pad = keep / 2.0 if keep != keep_gap else half
        regions.append({"start": round(a + pad, 3), "end": round(b - pad, 3)})
    return regions


def segment_reel(
    words: list[dict],
    in_s: float,
    out_s: float,
    preset: str = DEFAULT_PRESET,
    silences: list[dict] | None = None,
    mode: str = "hybrid",
    protect_dramatic: bool = True,
) -> tuple[list[dict], float]:
    """Return (segments, seconds_saved) for the reel spanning [in_s, out_s].

    Segments are absolute source times, in order, meant to be laid back-to-back.
    Falls back to a single full-length segment when there is nothing to cut.
    """
    full = [{"in_s": round(in_s, 3), "out_s": round(out_s, 3)}]
    min_gap, keep_gap = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])

    inside = [w for w in words if w["start"] >= in_s and w["end"] <= out_s]
    if len(inside) < 2:
        return full, 0.0

    # Snap the reel edges to word boundaries. The matched in/out point can land
    # mid-word (the client quotes loosely); starting a reel halfway through a
    # syllable sounds broken, so back off to the silence around the first and
    # last whole word without eating into the neighbouring one.
    first, last = inside[0], inside[-1]
    prev_end = max((w["end"] for w in words if w["end"] <= first["start"]), default=0.0)
    next_start = min(
        (w["start"] for w in words if w["start"] >= last["end"]), default=out_s + EDGE_PAD
    )
    # Whisper often reports neighbouring words as touching (prev_end ==
    # first.start). Clamping to that boundary leaves *zero* room, and the edge
    # consonant gets clipped — "plecat" lands as "pleca". So when there is no
    # real gap, still take CONTIG_PAD from the neighbour: a sliver of the next
    # word's onset is far less noticeable than a truncated word.
    gap_before = max(0.0, first["start"] - prev_end)
    gap_after = max(0.0, next_start - last["end"])
    lead_pad = EDGE_PAD if gap_before >= EDGE_PAD else max(gap_before, CONTIG_PAD)
    tail_pad = EDGE_PAD if gap_after >= EDGE_PAD else max(gap_after, CONTIG_PAD)

    # Clamped to the reel span: the nest the segments are cut from covers only
    # [in_s, out_s], so reaching before its start yields blank frames.
    lead = max(first["start"] - lead_pad, 0.0, in_s)
    tail = min(last["end"] + tail_pad, out_s)

    regions = _cut_regions(
        words, silences, lead, tail, min_gap, keep_gap, mode, protect_dramatic
    )

    # segments are whatever is left between the removed regions
    segments = []
    cursor = lead
    for reg in sorted(regions, key=lambda r: r["start"]):
        if reg["start"] - cursor >= MIN_SEGMENT_S:
            segments.append({"in_s": round(cursor, 3), "out_s": round(reg["start"], 3)})
        cursor = max(cursor, reg["end"])
    if tail - cursor >= MIN_SEGMENT_S:
        segments.append({"in_s": round(cursor, 3), "out_s": round(tail, 3)})

    if not segments:
        return full, 0.0

    kept = sum(s["out_s"] - s["in_s"] for s in segments)
    saved = (out_s - in_s) - kept
    if saved <= 0:
        return full, 0.0
    return segments, round(saved, 3)
