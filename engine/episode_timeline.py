"""Full Episode's pure, frame-aligned timeline model. All public ticks are strings."""
from decimal import Decimal
from hashlib import sha256
import itertools
import json
import unicodedata
import re

TPS = 254016000000
VERSION = 1


def ticks(seconds):
    return int(Decimal(str(seconds)) * TPS)


def ceil_frame(t, frame):
    return -(-int(t) // frame) * frame


def floor_frame(t, frame):
    return int(t) // frame * frame


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode()).hexdigest()


def span(item):
    return int(item["start"]), int(item["end"])


def overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def union(intervals):
    out = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(b, out[-1][1]))
        else:
            out.append((a, b))
    return out


def subtract(intervals, occupied):
    result = list(intervals)
    for x, y in union(occupied):
        next_result = []
        for a, b in result:
            if not overlaps((a, b), (x, y)):
                next_result.append((a, b))
            else:
                if a < x:
                    next_result.append((a, x))
                if y < b:
                    next_result.append((y, b))
        result = next_result
    return result


def intersection(a, b):
    return [(max(x, u), min(y, v)) for x, y in a for u, v in b
            if max(x, u) < min(y, v)]


def map_source(records, clips):
    """Records have source ticks and path; split independently at clip instances."""
    out = []
    grouped = []
    previous, group = None, None
    for clip in sorted(clips, key=lambda c: int(c["start"])):
        if previous is None or not (
                previous["path"] == clip["path"] and previous["end"] == clip["start"]
                and previous["sourceOut"] == clip["sourceIn"]
                and previous.get("effects") == clip.get("effects")
                and previous.get("disabled") == clip.get("disabled")):
            group = clip["start"]
        grouped.append((clip, group))
        previous = clip
    for record in records:
        for clip, group in grouped:
            if record["path"] != clip["path"] or clip.get("disabled"):
                continue
            a = max(int(record["start"]), int(clip["sourceIn"]))
            b = min(int(record["end"]), int(clip["sourceOut"]))
            if b > a:
                offset = int(clip["start"]) - int(clip["sourceIn"])
                mapped = {**record, "start": str(a + offset), "end": str(b + offset)}
                if "id" in record:
                    mapped.update(sourceID=record["id"], id=record["id"]+"@"+group)
                out.append(mapped)
    return sorted(out, key=lambda r: int(r["start"]))


def source_maps(interval, clips):
    out = []
    for c in clips:
        a, b = max(interval[0], int(c["start"])), min(interval[1], int(c["end"]))
        if b > a:
            offset = int(c["sourceIn"]) - int(c["start"])
            out.append({"path": c["path"], "start": str(a), "end": str(b),
                        "sourceIn": str(a + offset), "sourceOut": str(b + offset)})
    return out


def validate_snapshot(s):
    frame = int(s["timebase"])
    if frame <= 0 or not s.get("project") or not s.get("sequenceID"):
        raise ValueError("Save the project and select a sequence with a valid timebase.")
    for track in ("v1", "v2"):
        clips = s[track]
        if not clips:
            raise ValueError("The sequence needs prim-plan on V1 and synced general footage on V2.")
        previous = -1
        for c in sorted(clips, key=lambda c: int(c["start"])):
            a, b = span(c)
            if c.get("unsupported"):
                raise ValueError(f"{track.upper()} {c.get('name', '')}: {c['unsupported']}")
            if not c.get("path") or c.get("speed") != 1 or c.get("reverse"):
                raise ValueError(f"{track.upper()} supports ordinary, forward, 1x file clips only.")
            if a < previous or a < 0 or b <= a or (b-a) != int(c["sourceOut"])-int(c["sourceIn"]):
                raise ValueError(f"Invalid or overlapping {track.upper()} timing.")
            if a % frame or b % frame:
                raise ValueError(f"{track.upper()} has an edit outside the sequence frame grid.")
            previous = b
    selected = [c for c in s["v1"] if c["path"] == s["mediaPath"] and not c.get("disabled")]
    if not selected or len(selected) != len(s["v1"]):
        raise ValueError("V1 must contain only enabled copies of the selected prim-plan media.")
    if len(union([span(c) for c in selected])) != 1:
        raise ValueError("V1 contains a gap. Close it before preparing Full Episode.")
    return min(int(c["start"]) for c in selected), max(int(c["end"]) for c in selected)


def host_turns(turns, speaker):
    """Pause merging may not swallow a guest interruption, including overlapping speech."""
    guests = [span(t) for t in turns if str(t["speaker"]) != str(speaker)]
    out = []
    for t in sorted(turns, key=lambda t: int(t["start"])):
        if str(t["speaker"]) != str(speaker):
            continue
        for a, b in subtract([span(t)], guests):
            if out and 0 <= a-out[-1][1] <= ticks(.5) and not any(
                    overlaps((out[-1][1], a), g) for g in guests):
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
    return out


def sponsor_mentions(words, bounds):
    candidates = []
    ws = [w for w in words if bounds[0] <= int(w["start"]) < bounds[0]+ticks(120)]
    def norm(v):
        return re.sub(r"[^a-z0-9 ]", "", "".join(c for c in unicodedata.normalize(
            "NFKD", v.lower()) if not unicodedata.combining(c)))
    names = [norm(w.get("word", "")) for w in ws]
    for i, name in enumerate(names):
        phrase = " ".join(names[i:i+6])
        if "paralela" not in name or not re.search(r"\b(45|patruzeci (si )?cinci)\b", phrase):
            continue
        start = i
        for j in range(i-1, max(-1, i-8), -1):
            if int(ws[i]["start"])-int(ws[j]["start"]) > ticks(5):
                break
            if any(term in names[j] for term in ("sustinut", "sponsorizat", "sprijin")):
                start = j
        candidates.append({"start": ws[start]["start"],
                           "text": " ".join(w["word"] for w in ws[start:i+6])})
    return candidates


def nearest(windows, target, duration, frame):
    options = []
    for a, b in windows:
        lo, hi = ceil_frame(a, frame), floor_frame(b-duration, frame)
        if lo <= hi:
            t = max(lo, min(hi, floor_frame(target, frame)))
            options.append(t)
    return min(options, key=lambda t: (abs(t-target), t)) if options else None


def schedule(snapshot, config, analysis):
    bounds = validate_snapshot(snapshot)
    frame = int(snapshot["timebase"])
    events, warnings, unresolved = [], [], []
    assets = analysis.get("assets", {})
    blocked = [span(c) for c in snapshot.get("upper", []) if not c.get("disabled")]
    # A razor splits edit records, not a camera shot. Union pieces of the same scene only.
    by_scene = {}
    for shot in analysis.get("shots", []):
        if shot.get("host") is True:
            by_scene.setdefault(shot["id"], []).append(span(shot))
    shots = []
    for scene_id, pieces in by_scene.items():
        for a, b in union(pieces):
            for x, y in intersection([(a+ticks(.25), b-ticks(.25))], [bounds]):
                shots.append((scene_id, x, y))
    def enabled(kind):
        return not config.get("overlaysOff", False) and config.get(kind, False)
    def occupied():
        return blocked + [span(e) for e in events]
    def duration(kind):
        if kind not in assets:
            raise ValueError(f"Choose and inspect the {kind} asset first.")
        return ceil_frame(int(assets[kind]["duration"]), frame)
    def add(kind, start, length, reason, eligibility=None, evidence=""):
        interval = (start, start+length)
        if start < bounds[0] or interval[1] > bounds[1] or any(overlaps(interval, x) for x in occupied()):
            return False
        events.append({"id": f"{kind}-{len(events)+1}", "kind": kind, "start": str(start),
                       "end": str(interval[1]), "enabled": True, "reviewed": False,
                       "reason": reason, "evidence": evidence,
                       "eligibility": [[str(a), str(b)] for a, b in (eligibility or [interval])],
                       "asset": assets.get(kind), "scale": 50 if kind == "subscribe" else 100,
                       "position": config.get("positions", {}).get(kind, [.5, .5]),
                       "sources": source_maps(interval, snapshot["v2" if kind == "general" else "v1"])})
        return True
    opening_end = None
    sponsor = config["show"] == "CPI" and enabled("sponsor")
    if sponsor:
        d = duration("sponsor")
        mentions = sponsor_mentions(analysis.get("words", []), bounds)
        override = config.get("openingSponsor")
        if override is not None:
            mentions = [{"start": str(ticks(override)), "text": "Editor-set sponsor cue"}]
        if len(mentions) == 1:
            t = ceil_frame(int(mentions[0]["start"]), frame)
            # User confirmed host on screen at the spoken cue; still require uncut scene coverage.
            all_scenes = {}
            for scene in analysis.get("shots", []):
                all_scenes.setdefault(scene["id"], []).append(span(scene))
            scene_windows = [window for pieces in all_scenes.values() for window in union(pieces)
                             if window[0] <= t and t+d <= window[1]]
            if t < bounds[0]+ticks(120) and scene_windows and add(
                    "sponsor", t, d, "Opening sponsor mention", scene_windows, mentions[0]["text"]):
                opening_end = t+d
            else:
                unresolved.append("Opening sponsor does not fit an uninterrupted shot in the first two minutes.")
        else:
            unresolved.append("Sponsor cue missing or ambiguous. Set its timeline time after reviewing the first two minutes.")
    if enabled("subscribe"):
        d = duration("subscribe")
        start = opening_end if sponsor else ceil_frame(bounds[0]+ticks(30), frame)
        if start is None:
            unresolved.append("Opening Like & Subscribe is waiting for the sponsor cue.")
        elif not add("subscribe", start, d, "Opening Like & Subscribe"):
            unresolved.append("Opening Like & Subscribe conflicts or exceeds the episode.")
        closing = floor_frame(bounds[1]-ticks(10)-d, frame)
        if not add("subscribe", closing, d, "Finishes ten seconds before the end"):
            unresolved.append("Closing Like & Subscribe conflicts or exceeds the episode.")
    if sponsor:
        d = duration("sponsor")
        options = []
        for fraction in (.25, .5, .75):
            target = bounds[0]+int((bounds[1]-bounds[0])*fraction)
            choices = []
            for sid, a, b in shots:
                a = max(a, bounds[0]+ticks(120))
                windows = subtract([(a, b)], occupied())
                t = nearest(windows, target, d, frame)
                if t is not None:
                    choices.append((abs(t-target), t, sid, windows))
            options.append(sorted(choices, key=lambda x: (x[0], x[1]))[:12]+[None])
        def score(combo):
            chosen = [x for x in combo if x is not None]
            if len({x[2] for x in chosen}) != len(chosen):
                return (99,)
            return (3-len(chosen), sum(x[0] for x in chosen), tuple(x[1] for x in chosen))
        chosen = min(itertools.product(*options), key=score)
        for choice in chosen:
            if choice is not None:
                add("sponsor", choice[1], d, "Spaced sponsor placement in a labelled host shot", choice[3])
        count = sum(e["kind"] == "sponsor" for e in events)
        if count < 4:
            warnings.append(f"Only {count}/4 sponsor placements fit. Acknowledge the reduced count before building.")
    if enabled("host"):
        d = duration("host")
        if config.get("hostSpeaker") is None:
            unresolved.append("Confirm the host speaker before scheduling host animations.")
        else:
            skipped = 0
            for turn in host_turns(analysis.get("turns", []), config["hostSpeaker"]):
                windows = subtract(intersection([(a, b) for _, a, b in shots], [turn]), occupied())
                t = nearest(windows, turn[0], d, frame)
                if t is not None:
                    add("host", t, d, "Host speaking in an uninterrupted labelled host shot", windows)
                else:
                    skipped += 1
            if skipped:
                warnings.append(f"Skipped {skipped} host turns: no free, labelled, uninterrupted view fits the full animation.")
            if not shots:
                warnings.append("No host camera views are labelled. Host animations have no eligible shots.")
    if config.get("general", True):
        coverage = union([span(c) for c in snapshot["v2"]])
        target = ((bounds[0] // ticks(300))+1)*ticks(300)
        while target < bounds[1]:
            windows = subtract(intersection(coverage, [(max(bounds[0], target-ticks(30)),
                                                         min(bounds[1], target+ticks(30)+ticks(10)))]), occupied())
            t = nearest(windows, target, ceil_frame(ticks(10), frame), frame)
            if t is None or abs(t-target) > ticks(30):
                warnings.append(f"No full general-camera window near {target/TPS:.0f}s.")
            else:
                add("general", t, ceil_frame(ticks(10), frame), "Ten seconds near the five-minute mark", windows)
            target += ticks(300)
    events.sort(key=lambda e: int(e["start"]))
    return {"version": VERSION, "snapshot": snapshot, "snapshotHash": fingerprint(snapshot),
            "config": config, "bounds": list(map(str, bounds)), "events": events,
            "warnings": warnings, "unresolved": unresolved, "reducedSponsorAcknowledged": False}


def validate_plan(plan, require_review=True):
    s, config = plan["snapshot"], plan["config"]
    bounds = validate_snapshot(s)
    if plan["snapshotHash"] != fingerprint(s):
        raise ValueError("Snapshot changed. Prepare again.")
    if plan.get("unresolved"):
        raise ValueError("; ".join(plan["unresolved"]))
    events = [e for e in plan["events"] if e["enabled"]]
    frame = int(s["timebase"])
    occupied = [span(c) for c in s.get("upper", []) if not c.get("disabled")]
    for e in sorted(events, key=lambda e: int(e["start"])):
        a, b = span(e)
        if a % frame or b % frame or a < bounds[0] or b > bounds[1] or b <= a:
            raise ValueError(f"{e['id']}: invalid frame-aligned placement.")
        duration = ceil_frame(ticks(10) if e["kind"] == "general" else int(e["asset"]["duration"]), frame)
        if b-a != duration or not any(int(x) <= a and b <= int(y) for x, y in e["eligibility"]):
            raise ValueError(f"{e['id']}: full asset no longer fits its eligible shot/turn.")
        if any(overlaps((a, b), p) for p in occupied):
            raise ValueError(f"{e['id']}: overlaps another graphic or covering track.")
        if require_review and not e.get("reviewed"):
            raise ValueError(f"Review the complete playback of {e['id']} first.")
        occupied.append((a, b))
        position = e.get("position", [.5, .5])
        if len(position) != 2 or any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in position):
            raise ValueError("Graphic positions must be between 0 and 100 percent.")
    opening_sponsor = next((e for e in events if e["reason"] == "Opening sponsor mention"), None)
    opening_subscribe = next((e for e in events if e["reason"] == "Opening Like & Subscribe"), None)
    closing_subscribe = next((e for e in events if e["reason"] == "Finishes ten seconds before the end"), None)
    if opening_sponsor and opening_subscribe and opening_subscribe["start"] != opening_sponsor["end"]:
        raise ValueError("Opening Like & Subscribe must immediately follow the opening sponsor.")
    if opening_subscribe and config["show"] == "CPI" and config.get("sponsor") and not config.get("overlaysOff") and not opening_sponsor:
        raise ValueError("Opening Like & Subscribe depends on the opening sponsor. Disable both, or turn off sponsors and recreate placements for the 30-second default.")
    if closing_subscribe and int(closing_subscribe["end"]) != floor_frame(bounds[1]-ticks(10), frame):
        raise ValueError("Closing Like & Subscribe must finish ten seconds before the end.")
    if config["show"] == "CPI" and config.get("sponsor") and not config.get("overlaysOff"):
        count = sum(e["kind"] == "sponsor" for e in events)
        if count != 4 and not plan.get("reducedSponsorAcknowledged"):
            raise ValueError(f"Acknowledge the reduced sponsor count ({count}/4).")
    if config["show"] == "AIP" and any(e["kind"] == "sponsor" for e in events):
        raise ValueError("Arena Ideilor cannot contain sponsor placements.")
    return events
