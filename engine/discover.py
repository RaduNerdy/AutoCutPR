"""Find the reel-worthy moments in an episode, without a client list.

The model is asked to behave exactly like the client does: name a rough
timecode and quote the first and last few words. Those quotes go through the
same matcher the manual path uses, so a found moment and a listed one are the
same object by the time anything downstream sees them, and every fix made to
matching, edge snapping and dead-air removal applies here for free.

Asking for timestamps directly would be simpler and worse: models drift by
seconds, and seconds are the whole game here.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import match
import llm

# how much transcript goes to the model at once; recall drops when a whole hour
# is in one call, and overlap stops a moment being cut in half by a boundary
WINDOW_S = 25 * 60
OVERLAP_S = 2 * 60
# whisper segments average ~1.4s, which is noise to read; merge to this
BLOCK_S = 15.0
MAX_TOKENS = 8000
# a candidate must resolve this well or it is dropped rather than guessed at
MIN_MATCH_SCORE = 70
# keep accepted moments from butting up against each other
MIN_GAP_S = 8.0

SCORE_WEIGHTS = {"hook": 1.2, "self_contained": 1.0, "emotion": 0.9, "clarity": 0.8}


def attribute_speakers(words: list[dict], turns: list[dict]) -> list[dict]:
    """Tag every word with the speaker whose turn covers its midpoint."""
    out = []
    i = 0
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        while i < len(turns) - 1 and turns[i]["end"] < mid:
            i += 1
        spk = turns[i]["speaker"] if turns and turns[i]["start"] <= mid <= turns[i]["end"] else None
        out.append({**w, "speaker": spk})
    return out


def speaker_seconds(turns: list[dict], in_s: float, out_s: float) -> dict:
    """How long each speaker holds the floor inside a span."""
    totals = {}
    for t in turns:
        a, b = max(t["start"], in_s), min(t["end"], out_s)
        if b > a:
            totals[t["speaker"]] = totals.get(t["speaker"], 0.0) + (b - a)
    return totals


def render_blocks(words: list[dict], turns: list[dict], host: int,
                  lo: float, hi: float) -> str:
    """`[mm:ss] GUEST: ...` lines, merged into readable blocks."""
    tagged = [w for w in attribute_speakers(words, turns) if lo <= w["start"] < hi]
    if not tagged:
        return ""
    lines = []
    cur_spk = tagged[0]["speaker"]
    cur_start = tagged[0]["start"]
    buf = []
    for w in tagged:
        changed = w["speaker"] != cur_spk
        too_long = w["start"] - cur_start >= BLOCK_S
        if (changed or too_long) and buf:
            label = "HOST" if cur_spk == host else "GUEST"
            lines.append(f"[{int(cur_start // 60)}:{int(cur_start % 60):02d}] "
                         f"{label}: {''.join(buf).strip()}")
            buf = []
            cur_spk, cur_start = w["speaker"], w["start"]
        buf.append(w["word"])
    if buf:
        label = "HOST" if cur_spk == host else "GUEST"
        lines.append(f"[{int(cur_start // 60)}:{int(cur_start % 60):02d}] "
                     f"{label}: {''.join(buf).strip()}")
    return "\n".join(lines)


def _prompt(block_text: str, want: int, min_len: int, max_len: int,
            target_len: int, guest_only: bool) -> str:
    who = ("The GUEST must carry the moment. Ignore anything led by the HOST."
           if guest_only else
           "Moments may include both speakers where the exchange itself is the point.")
    return (
        "You select moments from a Romanian interview podcast to cut as vertical "
        "social clips. This is a transcript window with timecodes and speaker labels.\n\n"
        f"{block_text}\n\n"
        f"Pick up to {want} moments that would hold a stranger's attention on their own, "
        "with no surrounding context.\n\n"
        "What makes one work: it opens on something that makes you want the next "
        "sentence, it completes a single thought, and it is concrete. A story, a "
        "strong opinion, a number that surprises, a turn of phrase. Avoid pleasantries, "
        "setup, and anything that only makes sense if you heard the previous ten minutes.\n"
        f"{who}\n"
        f"Length is a hard rule. The span between your two quotes must run at least "
        f"{min_len} seconds and at most {max_len} seconds, ideally around {target_len}. "
        f"A good thought that only takes {min_len - 5}s is not usable: either carry the "
        f"end quote further into the answer until it reaches {min_len}s, or leave it out. "
        f"Use the timecodes above to judge this, do not guess.\n\n"
        "For each moment give the timecode it starts at, then quote the FIRST 5-8 words "
        "and the LAST 5-8 words exactly as they appear above, copied character for "
        "character. Those quotes are used to locate the cut, so they must be verbatim.\n\n"
        "Reply with JSON only, no prose:\n"
        '{"moments":[{"start_tc":"12:34","start_quote":"...","end_tc":"13:10",'
        '"end_quote":"...","summary":"o propoziție în română","why":"under 12 words",'
        '"visual":"b-roll idea, under 10 words","hook":0-10,"self_contained":0-10,'
        '"emotion":0-10,"clarity":0-10}]}'
    )


def ask_model(block_text: str, want: int, env: dict, min_len: int, max_len: int,
              target_len: int, guest_only: bool, client=None) -> list[dict]:
    client = client or llm.ModelClient(env)
    return client.generate(_prompt(block_text, want, min_len, max_len,
                                   target_len, guest_only), "moments",
                           temperature=0.4, max_tokens=MAX_TOKENS)["moments"]


def _as_reel_list(cands: list[dict]) -> str:
    """The model's picks, written the way a client writes them."""
    lines = []
    for c in cands:
        lines.append(f"{c['start_tc']} {c['start_quote']} .... "
                     f"{c.get('end_tc', c['start_tc'])} {c['end_quote']}")
    return "\n\n".join(lines)


def composite(c: dict) -> float:
    return sum(w * float(c.get(k, 0) or 0) for k, w in SCORE_WEIGHTS.items())


def discover(transcript: dict, speakers: dict, env: dict, *, min_count: int = 8,
             max_count: int = 12, min_len: int = 20, max_len: int = 90,
             target_len: int = 45, guest_only: bool = True,
             host_max_s: float = 5.0, allow_intertwined: bool = False,
             silences=None, silence_preset="moderate", silence_mode="hybrid") -> list[dict]:
    words = transcript["words"]
    duration = transcript["duration"]
    turns = speakers["turns"]
    host = speakers["host"]

    # 1. ask each window for candidates
    cands = []
    client = llm.ModelClient(env)
    client.preflight()
    lo = 0.0
    per_window = max(3, round(max_count * 2 / max(1, duration / WINDOW_S)))
    while lo < duration:
        hi = min(lo + WINDOW_S, duration)
        text = render_blocks(words, turns, host, lo, hi)
        if text:
            print(f"[discover] window {int(lo//60)}-{int(hi//60)} min")
            got = ask_model(text, per_window, env, min_len, max_len,
                            target_len, guest_only, client=client)
            print(f"[discover]   {len(got)} candidates")
            cands.extend(got)
        lo += WINDOW_S - OVERLAP_S
    if not cands:
        return []

    # 2. resolve quotes to real cuts through the normal matcher
    cuts = match.compute_cuts(
        _as_reel_list(cands), transcript, silence_preset,
        silences=silences, silence_mode=silence_mode,
    )
    if len(cuts) != len(cands):
        print(f"[discover] {len(cands)} candidates parsed to {len(cuts)} cuts; "
              "falling back to the shorter list")
    merged = []
    for cut, cand in zip(cuts, cands):
        cut = dict(cut)
        cut.update({
            "summary": cand.get("summary", ""),
            "why": cand.get("why", ""),
            "visual": cand.get("visual", ""),
            "scores": {k: cand.get(k) for k in SCORE_WEIGHTS},
            "model_score": round(composite(cand), 2),
        })
        merged.append(cut)

    # 3. drop what did not resolve, and anything the wrong length
    kept = []
    for c in merged:
        # Judge the length the editor actually receives. Dead-air removal takes
        # several seconds out, so a 22s span can deliver a 16s reel.
        length = sum(s["out_s"] - s["in_s"] for s in c["segments"]) or (c["out_s"] - c["in_s"])
        c["final_len"] = round(length, 1)
        if c["start_match"]["score"] < MIN_MATCH_SCORE:
            c["rejected"] = f"start quote only matched {c['start_match']['score']:.0f}"
        elif length < min_len or length > max_len * 1.25:
            # The minimum is enforced strictly. The model reliably proposes good
            # 12-15s thoughts, and they are not reels.
            c["rejected"] = f"length {length:.0f}s outside {min_len}-{max_len}s"
        else:
            kept.append(c)

    # 4. speaker rules
    eligible = []
    for c in kept:
        secs = speaker_seconds(turns, c["in_s"], c["out_s"])
        host_s = secs.get(host, 0.0)
        guest_s = sum(v for k, v in secs.items() if k != host)
        c["speaker_mix"] = {"host_s": round(host_s, 1), "guest_s": round(guest_s, 1)}
        if not allow_intertwined and guest_only and host_s > host_max_s:
            c["rejected"] = f"host speaks {host_s:.0f}s (limit {host_max_s:.0f}s)"
            continue
        if guest_only and guest_s <= host_s:
            c["rejected"] = "host dominates the moment"
            continue
        eligible.append(c)

    # 5. rank, refuse overlaps, then take min..max
    eligible.sort(key=lambda c: c["model_score"], reverse=True)
    chosen = []
    for c in eligible:
        if any(c["in_s"] < o["out_s"] + MIN_GAP_S and o["in_s"] < c["out_s"] + MIN_GAP_S
               for o in chosen):
            c["rejected"] = "overlaps a stronger moment"
            continue
        chosen.append(c)
        if len(chosen) >= max_count:
            break

    # the maximum is a soft target: past the minimum, only keep what is still
    # clearly good, so the team gets room without padding
    if len(chosen) > min_count:
        floor = 0.6 * (sum(c["model_score"] for c in chosen[:min_count]) / min_count)
        chosen = chosen[:min_count] + [c for c in chosen[min_count:]
                                       if c["model_score"] >= floor]

    chosen.sort(key=lambda c: c["in_s"])
    for i, c in enumerate(chosen, 1):
        c["reel"] = i
    print(f"[discover] {len(cands)} candidates -> {len(eligible)} eligible -> "
          f"{len(chosen)} selected")
    return chosen
