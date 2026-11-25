"""Match a client reel list against a word-level transcript and emit corrected cuts.

Usage: python match.py "reel list.txt" "cache/episode.words.json"
Output: cache/<episode>.cuts.json + human-readable report on stdout.

Reel list format (one reel per block, blocks separated by blank lines):
    3:38 o parte din rău …. 6:21 este dispreț
Each block has a start timecode+phrase and optionally an end timecode+phrase.
Client timecodes are treated as hints only (they can be off by minutes);
the quoted phrases are the real anchors, fuzzy-matched against the transcript.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz

import silence

# Windows consoles default to cp1252, which can't print Romanian diacritics
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# normal timecodes, plus malformed ones the client sometimes sends ("27:" alone)
TIMECODE_RE = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?|\d{1,2}:(?!\d))")
# editor notes in the reel list, e.g. "(nume)", "( ce zice el)" — not spoken words
PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
# This client habitually writes the end as an instruction rather than a quote:
# "până când vorbește gazda", "pana cand zice iar dc este el fericit". Matching
# those against the transcript finds nonsense, so treat them as no phrase at all
# and fall back to the timecode.
INSTRUCTION_RE = re.compile(r"^(p[aâî]n[aă]\s+c[aâî]nd|p[aâî]n[aă]\s+l[aa]\s+c[aâî]nd)\b",
                            re.IGNORECASE)
# hint window: how far the true position may drift from the client's timecode
HINT_WINDOW_S = 300
# below this score the match is untrusted and we retry over the whole episode
GOOD_SCORE = 80
# padding applied around the matched words so cuts don't clip speech
PAD_S = 0.15
# fallback reel length when the client gave no end phrase
DEFAULT_REEL_LEN_S = 90
# a reel never runs longer than this; bounds the search for the end phrase
MAX_REEL_S = 300
# floor for a reel built from a timecode-only end, in case the clock is off
MIN_REEL_S = 8
# score points shed per minute of drift from the client's timecode, so a
# perfect match far away loses to a good one where the reel was said to be
DRIFT_PENALTY_PER_MIN = 8.0


# Clients type digits ("când avea 4 ani", "spun 1 2 replici") but whisper spells
# numbers out ("la nici patru ani"), so the two never matched. Both sides are
# expanded to words. Gendered forms (doi/două) are close enough for fuzzy match.
NUMBER_WORDS = {
    "0": "zero", "1": "unu", "2": "doi", "3": "trei", "4": "patru", "5": "cinci",
    "6": "sase", "7": "sapte", "8": "opt", "9": "noua", "10": "zece",
    "11": "unsprezece", "12": "doisprezece", "13": "treisprezece",
    "14": "paisprezece", "15": "cincisprezece", "16": "saisprezece",
    "17": "saptesprezece", "18": "optsprezece", "19": "nouasprezece",
    "20": "douazeci", "30": "treizeci", "40": "patruzeci", "50": "cincizeci",
    "60": "saizeci", "70": "saptezeci", "80": "optzeci", "90": "nouazeci",
    "100": "o suta", "1000": "o mie",
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\d+", lambda m: NUMBER_WORDS.get(m.group(), m.group()), text)
    return re.sub(r"\s+", " ", text).strip()


def tc_to_seconds(tc: str) -> float:
    parts = [int(p) if p else 0 for p in tc.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def fmt(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m)}:{s:05.2f}"


def tidy_timecodes(block: str) -> str:
    """Repair the ways clients mistype timecodes before they are parsed.

    These are all real: `05;39` (semicolon, shift key missed), `41:031` (an
    extra digit), and a bare `45` used as an end. Left alone the first two lose
    the timecode entirely and the third gets swallowed into the phrase, which
    surfaces as a bogus start or a spurious "NO END GIVEN".
    """
    # 05;39 / 05.39 -> 05:39
    block = re.sub(r"(?<=\d)[;.](?=\d{2}(?!\d))", ":", block)
    # 41:031 -> 41:03 (keep two digits, drop the stray one)
    block = re.sub(r"\b(\d{1,2}:\d{2})\d\b", r"\1", block)
    # a bare minute where an end timecode belongs: "... … 45 pana cand ..."
    block = re.sub(r"([.…]\s*)(\d{1,2})(?=\s+\D)", r"\1\2:00", block)
    return block


def parse_reel_list(text: str) -> list[dict]:
    reels = []
    for block in re.split(r"\n\s*\n", text.strip()):
        block = " ".join(block.split())
        if not block:
            continue
        block = tidy_timecodes(block)
        parts = TIMECODE_RE.split(block)
        # parts: [pre, tc, phrase, tc, phrase, ...]
        anchors = []
        for i in range(1, len(parts) - 1, 2):
            raw_phrase = parts[i + 1]
            # A parenthetical at the START of a phrase, "(nume) a vrut să te ia",
            # stands in for words the client did not transcribe — usually a name.
            # That is the one case where the reel must begin before the quote.
            # A trailing "( ce zice el)" is just a note and means nothing here.
            placeholder = bool(re.match(r"^[\s.…]*\(", raw_phrase))
            phrase = PARENTHETICAL_RE.sub(" ", raw_phrase)
            phrase = re.sub(r"[.…]{2,}", " ", phrase)
            phrase = " ".join(phrase.split()).strip(" .…")
            if INSTRUCTION_RE.match(phrase):
                phrase = ""
            # Keep the anchor even with no quotable words left. "34:47 (până
            # când vorbește gazda)" is an instruction, not a quote — but the
            # timecode is still a real end point, and dropping the anchor turned
            # it into "NO END GIVEN" plus a 90s guess.
            anchors.append({
                "hint_s": tc_to_seconds(parts[i]),
                "phrase": phrase,
                "placeholder": placeholder,
            })
        if not anchors:
            continue
        reels.append(
            {
                "raw": block,
                "start": anchors[0],
                "end": anchors[1] if len(anchors) > 1 else None,
            }
        )
    return reels


def best_window(
    query: str, words: list[dict], lo_s: float, hi_s: float, hint_s: float | None = None
) -> dict:
    """Slide word windows over transcript words within [lo_s, hi_s] and return
    the best fuzzy match for the normalized query phrase."""
    q_norm = normalize(query)
    n = len(q_norm.split())
    idxs = [i for i, w in enumerate(words) if lo_s <= w["start"] <= hi_s]
    best = {"score": -1.0, "ranked": -1e9, "start_s": lo_s, "end_s": lo_s, "text": ""}
    for size in range(max(1, n - 2), n + 3):
        for i in idxs:
            j = i + size
            if j > len(words):
                break
            window = words[i:j]
            cand = normalize("".join(w["word"] for w in window))
            score = fuzz.ratio(q_norm, cand)
            # Rank by score *discounted for distance from the hint*. Short
            # generic phrases ("și acuma") match perfectly all over an hour of
            # speech, so raw score alone happily picks one 11 minutes away.
            ranked = score
            if hint_s is not None:
                drift_min = abs(window[0]["start"] - hint_s) / 60.0
                ranked -= DRIFT_PENALTY_PER_MIN * drift_min
            if ranked > best["ranked"]:
                best = {
                    "ranked": ranked,
                    "score": score,
                    "start_s": window[0]["start"],
                    "end_s": window[-1]["end"],
                    "text": "".join(w["word"] for w in window).strip(),
                }
    return best


def locate(
    query: str,
    hint_s: float,
    words: list[dict],
    duration: float,
    lo_s: float | None = None,
    hi_s: float | None = None,
) -> dict:
    """Find `query` near `hint_s`. lo_s/hi_s hard-bound the search."""
    lo = hint_s - HINT_WINDOW_S if lo_s is None else lo_s
    hi = hint_s + HINT_WINDOW_S if hi_s is None else hi_s
    m = best_window(query, words, lo, hi, hint_s)
    m["searched"] = "hint-window"
    if m["score"] < GOOD_SCORE and lo_s is None and hi_s is None:
        # Only leave the hinted neighbourhood for a match that is genuinely
        # strong. A mediocre match elsewhere in the episode is far worse than a
        # mediocre one near the client's timecode: the first sends the editor
        # 30 minutes away, the second is a few seconds' nudge.
        g = best_window(query, words, 0, duration, hint_s)
        if g["score"] >= GOOD_SCORE and g["ranked"] > m["ranked"]:
            g["searched"] = "global"
            m = g
    m["query"] = query
    m["hint_s"] = hint_s
    return m


def compute_cuts(
    reel_text: str,
    transcript: dict,
    silence_preset: str | None = silence.DEFAULT_PRESET,
    silences: list[dict] | None = None,
    silence_mode: str = "hybrid",
    protect_dramatic: bool = True,
    snap_back: float = silence.SNAP_MAX_BACK,
) -> list[dict]:
    """Match every reel. silence_preset=None keeps each reel in one piece."""
    words = transcript["words"]
    duration = transcript["duration"]
    reels = parse_reel_list(reel_text)

    cuts = []
    for idx, reel in enumerate(reels, 1):
        flags = []
        start = locate(reel["start"]["phrase"], reel["start"]["hint_s"], words, duration)
        if not reel["start"]["phrase"]:
            # nothing quotable: the timecode is all we have
            start = {"score": 0, "start_s": reel["start"]["hint_s"],
                     "end_s": reel["start"]["hint_s"], "text": "",
                     "query": "", "hint_s": reel["start"]["hint_s"],
                     "searched": "timecode-only"}
            flags.append("START FROM TIMECODE (no quotable phrase)")

        # How far the client's clock runs from the real audio. Measured on the
        # start, then reused for an end we cannot match on words.
        drift = start["start_s"] - start["hint_s"] if reel["start"]["phrase"] else 0.0

        end = None
        if reel["end"] and reel["end"]["phrase"]:
            # The end must come after the start, and a reel is not hours long.
            # Without these bounds a short end phrase can match anywhere in the
            # episode and produce a 12-minute "reel".
            end = locate(
                reel["end"]["phrase"], reel["end"]["hint_s"], words, duration,
                lo_s=start["start_s"],
                hi_s=start["start_s"] + MAX_REEL_S,
            )

        cut_in = start["start_s"] - PAD_S
        if end and end["end_s"] > start["start_s"]:
            cut_out = end["end_s"] + PAD_S
        elif reel["end"]:
            # An end timecode with no usable words — "(până când vorbește
            # gazda)" is an instruction. Take the timecode, corrected by the
            # drift measured at the start; far better than a blind 90s.
            cut_out = max(reel["end"]["hint_s"] + drift, cut_in + MIN_REEL_S)
            flags.append(f"END FROM TIMECODE (drift {drift:+.0f}s applied)")
        else:
            cut_out = cut_in + DEFAULT_REEL_LEN_S

        if start["score"] and start["score"] < GOOD_SCORE:
            flags.append(f"LOW-CONFIDENCE START ({start['score']:.0f})")
        if reel["end"] is None:
            flags.append("NO END GIVEN (default length used)")
        elif end and end["score"] < GOOD_SCORE:
            flags.append(f"LOW-CONFIDENCE END ({end['score']:.0f})")
        elif end and end["end_s"] <= start["start_s"]:
            flags.append("END MATCHED BEFORE START (default length used)")

        # Move the edges to the real start/end of the thought before anything
        # downstream (segments, markers, razor points) sees them.
        snap = {"in_delta": 0.0, "out_delta": 0.0}
        if snap_back > 0:
            cut_in, cut_out, snap = silence.snap_reel_edges(
                words, silences, cut_in, cut_out, max_back=snap_back,
                long_back=reel["start"].get("placeholder", False),
            )

        if silence_preset:
            segments, saved = silence.segment_reel(
                words, cut_in, cut_out, silence_preset,
                silences=silences, mode=silence_mode,
                protect_dramatic=protect_dramatic,
            )
        else:
            segments = [{"in_s": round(cut_in, 3), "out_s": round(cut_out, 3)}]
            saved = 0.0

        cuts.append(
            {
                "reel": idx,
                "raw": reel["raw"],
                "in_s": round(cut_in, 3),
                "out_s": round(cut_out, 3),
                "segments": segments,
                "saved_s": saved,
                "snap": snap,
                "recovered": "".join(
                    w["word"] for w in words
                    if cut_in <= w["start"] < cut_in + snap["in_delta"]
                ).strip(),
                "start_match": start,
                "end_match": end,
                "flags": flags,
            }
        )

    return cuts


def print_report(transcript: dict, cuts: list[dict]):
    print(f"Episode: {transcript['audio']}  ({fmt(transcript['duration'])})\n")
    for c in cuts:
        s, e = c["start_match"], c["end_match"]
        drift = s["start_s"] - s["hint_s"]
        print(f"REEL {c['reel']}:  {fmt(c['in_s'])} -> {fmt(c['out_s'])}  "
              f"(len {fmt(c['out_s'] - c['in_s'])}, drift {drift:+.0f}s)")
        if c["snap"]["in_delta"] or c["snap"]["out_delta"]:
            bits = []
            if c["snap"]["in_delta"]:
                bits.append(f"start -{c['snap']['in_delta']:.2f}s")
            if c["snap"]["out_delta"]:
                bits.append(f"end +{c['snap']['out_delta']:.2f}s")
            note = f"  snap: {', '.join(bits)}"
            if c["recovered"]:
                note += f"  recovered \"{c['recovered']}\""
            print(note)
        if c["saved_s"]:
            kept = sum(s["out_s"] - s["in_s"] for s in c["segments"])
            print(f"  cut: {len(c['segments'])} segments, {fmt(kept)} kept, "
                  f"{c['saved_s']:.1f}s dead air removed")
        print(f"  start [{s['score']:.0f}] \"{s['query']}\" -> \"{s['text']}\"")
        if e:
            print(f"  end   [{e['score']:.0f}] \"{e['query']}\" -> \"{e['text']}\"")
        for f in c["flags"]:
            print(f"  !! {f}")
        print()


def main(reel_path: Path, transcript_path: Path):
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    cuts = compute_cuts(reel_path.read_text(encoding="utf-8"), transcript)
    out_path = transcript_path.with_name(
        transcript_path.name.replace(".words.json", ".cuts.json")
    )
    out_path.write_text(json.dumps(cuts, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(transcript, cuts)
    print(f"[done] {len(cuts)} reels -> {out_path}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
