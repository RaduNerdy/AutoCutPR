"""Suggest a trailer from reels that already exist.

Recommendations only, nothing is built. The editor's judgement is what makes a
trailer work; this narrows an hour down to five candidates and three shapes.

Each candidate is a ~20s window *inside* an existing reel, given as quotes and
resolved by the same matcher everything else uses, so a suggestion is always a
real cut and never an invented timestamp.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import match
import llm

CLIP_S = 20
MAX_TOKENS = 6000


def reel_text(words: list[dict], in_s: float, out_s: float, limit: int = 700) -> str:
    return "".join(w["word"] for w in words
                   if in_s <= w["start"] and w["end"] <= out_s).strip()[:limit]


def _prompt(reels_block: str) -> str:
    return (
        "These are finished clips from one episode of a Romanian interview podcast. "
        "You are assembling a trailer of about one minute: three clips of roughly "
        f"{CLIP_S} seconds each.\n\n"
        f"{reels_block}\n\n"
        "A trailer needs three things. A HOOK that makes someone stop scrolling in the "
        "first two seconds. A CONSTANT that holds the middle and says what the episode "
        "is about. A CLIMAX that lands hardest and makes them want the full thing.\n\n"
        f"First pick the 5 strongest windows across these clips. Each must be about "
        f"{CLIP_S} seconds of speech, never more than {CLIP_S + 8}. That is short: pick "
        "the sharpest sentence or two, not the whole argument. For each, name the reel it "
        "comes from, quote the FIRST 5-8 words and the LAST 5-8 words of the window "
        "exactly as written above, summarise it in one Romanian sentence, say why it "
        "works in under 12 words, and give one concrete visual to show over it.\n\n"
        "Number these candidates 1 to 5, in the order you list them.\n\n"
        "Then give 3 different orderings, each using 3 of those 5. Refer to a clip by its "
        "candidate number 1 to 5, NOT by its reel number. Label each clip hook, constant "
        "or climax, and say in one line why that order works.\n\n"
        "Reply with JSON only, no prose:\n"
        '{"candidates":[{"reel":1,"start_quote":"...","end_quote":"...",'
        '"summary":"...","why":"...","visual":"..."}],'
        '"orders":[{"name":"...","why":"...","clips":[{"candidate":1,"role":"hook",'
        '"summary":"...","why":"..."}]}]}'
    )


def suggest(transcript: dict, cuts: list[dict], env: dict) -> dict:
    words = transcript["words"]
    usable = [c for c in cuts if c.get("segments")]
    if not usable:
        return {"error": "no reels to work from"}

    block = "\n\n".join(
        f"REEL {i+1} [{int(c['in_s']//60)}:{int(c['in_s']%60):02d}]"
        f"{(' — ' + c['summary']) if c.get('summary') else ''}\n"
        f"{reel_text(words, c['in_s'], c['out_s'])}"
        for i, c in enumerate(usable))

    try:
        data = llm.ModelClient(env).generate(_prompt(block), "trailer",
                                             temperature=0.5, max_tokens=MAX_TOKENS)
        if any(c["reel"] > len(usable) for c in data["candidates"]):
            raise llm.ModelError("Trailer model referenced a reel that does not exist.")
    except llm.ModelError as e:
        return {"error": str(e)}

    # Resolve each candidate's quotes inside its own reel, so a suggestion can
    # never point outside the material it claims to come from.
    resolved = []
    for i, cand in enumerate(data.get("candidates", []), 1):
        idx = int(cand.get("reel", 0)) - 1
        if not 0 <= idx < len(usable):
            continue
        reel = usable[idx]
        start = match.locate(cand.get("start_quote", ""), reel["in_s"], words,
                             transcript["duration"],
                             lo_s=reel["in_s"], hi_s=reel["out_s"])
        end = match.locate(cand.get("end_quote", ""), reel["out_s"], words,
                           transcript["duration"],
                           lo_s=start["start_s"], hi_s=reel["out_s"] + 2)
        in_s = start["start_s"]
        out_s = end["end_s"] if end["end_s"] > in_s else min(in_s + CLIP_S, reel["out_s"])
        # A trailer clip that runs 50s is not a trailer clip. The model reaches
        # for the whole argument, so the ceiling is enforced here rather than
        # trusted to the prompt. Snap the ceiling back to a word end, otherwise
        # a clamped clip stops mid-syllable.
        cap = min(in_s + CLIP_S * 1.25, reel["out_s"])
        if out_s > cap:
            ends = [w["end"] for w in words if in_s < w["end"] <= cap]
            out_s = max(ends) if ends else cap
        resolved.append({
            "n": i, "reel": idx + 1,
            "in_s": round(in_s, 3), "out_s": round(out_s, 3),
            "length": round(out_s - in_s, 1),
            "summary": cand.get("summary", ""),
            "why": cand.get("why", ""),
            "visual": cand.get("visual", ""),
            "match_score": start["score"],
        })

    def find_candidate(clip: dict):
        """Models mix up candidate number and reel number here, so try both,
        then fall back to the summary text."""
        try:
            n = int(clip.get("candidate", 0))
        except (TypeError, ValueError):
            n = 0
        by_n = next((r for r in resolved if r["n"] == n), None)
        if by_n:
            return by_n
        by_reel = next((r for r in resolved if r["reel"] == n), None)
        if by_reel:
            return by_reel
        text = (clip.get("summary") or "").strip().lower()[:40]
        if text:
            return next((r for r in resolved
                         if r["summary"].strip().lower().startswith(text[:20])), None)
        return None

    for o in data.get("orders", []):
        for clip in o.get("clips", []):
            src = find_candidate(clip)
            if src:
                clip["candidate"] = src["n"]
                clip["in_s"], clip["out_s"] = src["in_s"], src["out_s"]
                clip["length"] = src["length"]

    total = sum(r["length"] for r in resolved[:3])
    return {"candidates": resolved, "orders": data.get("orders", []),
            "note": f"three clips run about {total:.0f}s"}
