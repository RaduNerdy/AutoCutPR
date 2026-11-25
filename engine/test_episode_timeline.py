"""Deterministic fixtures; these do not open Premiere or process client media."""
import copy
import unittest
from episode_timeline import (TPS, ticks, schedule, validate_plan, map_source,
                              host_turns, sponsor_mentions, validate_snapshot)


def clip(a, b, source=0, path="main.mp4"):
    return {"path": path, "start": str(ticks(a)), "end": str(ticks(b)),
            "sourceIn": str(ticks(source)), "sourceOut": str(ticks(source+b-a)),
            "disabled": False, "speed": 1, "reverse": False, "effects": []}


def fixture():
    s = {"project": "test.prproj", "sequenceID": "source", "timebase": str(TPS//25),
         "mediaPath": "main.mp4", "v1": [clip(0, 1200)], "v2": [clip(15, 1180, 45, "general.mp4")], "upper": []}
    c = {"show": "CPI", "general": True, "sponsor": True, "subscribe": True,
         "host": True, "hostSpeaker": "0"}
    a = {"assets": {k: {"duration": str(ticks(d)), "path": k+".mov"} for k, d in
                    [("host", 8), ("sponsor", 8), ("subscribe", 6)]},
         "words": [{"word": w, "start": str(ticks(20+i)), "end": str(ticks(21+i))}
                   for i, w in enumerate(["susținut", "de", "Paralela", "45"])],
         "shots": [{"id": str(i), "start": str(ticks(a)), "end": str(ticks(b)), "host": True}
                   for i, (a, b) in enumerate([(0, 100), (250, 350), (550, 650), (850, 950)])],
         "turns": [{"speaker": 0, "start": str(ticks(100)), "end": str(ticks(110))}]}
    return s, c, a


class TimelineTests(unittest.TestCase):
    def test_independent_source_offsets(self):
        records = [{"path": "general.mp4", "start": str(ticks(55)), "end": str(ticks(65))}]
        result = map_source(records, [clip(15, 80, 45, "general.mp4")])
        self.assertEqual((result[0]["start"], result[0]["end"]), (str(ticks(25)), str(ticks(35))))

    def test_harmless_razor_vs_jump(self):
        record = {"id": "scene", "path": "main.mp4", "start": "0", "end": str(ticks(80))}
        contiguous = map_source([record], [clip(0, 10), clip(10, 20, 10)])
        self.assertEqual(contiguous[0]["id"], contiguous[1]["id"])
        jump = map_source([record], [clip(0, 10), clip(10, 20, 40)])
        self.assertNotEqual(jump[0]["id"], jump[1]["id"])

    def test_four_total_and_subscribe_after_opening(self):
        plan = schedule(*fixture())
        sponsor = [e for e in plan["events"] if e["kind"] == "sponsor"]
        self.assertEqual(len(sponsor), 4)
        opening = next(e for e in plan["events"] if e["reason"] == "Opening Like & Subscribe")
        self.assertEqual(opening["start"], sponsor[0]["end"])
        closing = next(e for e in plan["events"] if e["reason"].startswith("Finishes"))
        self.assertEqual(closing["end"], str(ticks(1190)))
        self.assertFalse(plan["unresolved"])

    def test_arena_ignores_saved_sponsor(self):
        s, c, a = fixture(); c["show"] = "AIP"
        p = schedule(s, c, a)
        self.assertFalse(any(e["kind"] == "sponsor" for e in p["events"]))
        self.assertEqual(next(e["start"] for e in p["events"] if e["kind"] == "subscribe"), str(ticks(30)))

    def test_all_off_keeps_general(self):
        s, c, a = fixture(); c["overlaysOff"] = True
        p = schedule(s, c, {})
        self.assertEqual([e["kind"] for e in p["events"]], ["general"]*3)

    def test_insufficient_sponsors_require_ack(self):
        s, c, a = fixture(); a["shots"] = a["shots"][:1]
        p = schedule(s, c, a)
        for e in p["events"]: e["reviewed"] = True
        with self.assertRaisesRegex(ValueError, "reduced sponsor"):
            validate_plan(p)
        p["reducedSponsorAcknowledged"] = True
        validate_plan(p)

    def test_unresolved_opening_blocks_subscribe(self):
        s, c, a = fixture(); a["words"] = []
        p = schedule(s, c, a)
        self.assertTrue(p["unresolved"])
        self.assertFalse(any(e["reason"] == "Opening Like & Subscribe" for e in p["events"]))

    def test_romanian_spelled_number(self):
        ws = [{"word": w, "start": str(ticks(i)), "end": str(ticks(i+1))}
              for i, w in enumerate(["susținut", "de", "Paralela", "patruzeci", "și", "cinci"])]
        self.assertEqual(sponsor_mentions(ws, (0, ticks(1000)))[0]["start"], "0")

    def test_guest_prevents_pause_merge(self):
        turns = [{"speaker": s, "start": str(ticks(a)), "end": str(ticks(b))}
                 for s, a, b in [(0, 0, 10), (1, 10.1, 10.2), (0, 10.3, 20)]]
        self.assertEqual(len(host_turns(turns, 0)), 2)
        self.assertEqual(len(host_turns([turns[0], turns[2]], 0)), 1)

    def test_host_needs_visible_and_speaking(self):
        s, c, a = fixture(); c.update(sponsor=False, subscribe=False)
        a["turns"] = [{"speaker": 0, "start": str(ticks(255)), "end": str(ticks(340))}]
        p = schedule(s, c, a)
        self.assertEqual(sum(e["kind"] == "host" for e in p["events"]), 1)
        a["shots"][1]["host"] = False
        self.assertEqual(sum(e["kind"] == "host" for e in schedule(s, c, a)["events"]), 0)

    def test_missing_v2_window_skip(self):
        s, c, a = fixture(); s["v2"] = [clip(400, 410, 20, "general.mp4")]; c["overlaysOff"] = True
        p = schedule(s, c, a)
        self.assertFalse(p["events"]); self.assertEqual(len(p["warnings"]), 3)

    def test_frame_precision_and_end_not_v2_overhang(self):
        s, c, a = fixture(); f = TPS*1001//30000; s["timebase"] = str(f)
        end = (ticks(1200)//f)*f
        s["v1"][0].update(end=str(end), sourceOut=str(end))
        s["v2"] = [clip(0, 1400, 0, "general.mp4")]
        s["v2"][0].update(end=str(ticks(1400)//f*f), sourceOut=str(ticks(1400)//f*f))
        c["overlaysOff"] = True
        p = schedule(s, c, a)
        self.assertEqual(p["bounds"][1], str(end))
        self.assertTrue(all(int(e["start"]) % f == int(e["end"]) % f == 0 for e in p["events"]))

    def test_manual_overlap_and_stale_snapshot(self):
        p = schedule(*fixture())
        for e in p["events"]: e["reviewed"] = True
        validate_plan(p)
        p["snapshot"]["name"] = "changed"
        with self.assertRaisesRegex(ValueError, "Snapshot changed"):
            validate_plan(p)

    def test_reject_gap_and_reverse(self):
        s, _, _ = fixture(); s["v1"] = [clip(0, 50), clip(60, 120, 60)]
        with self.assertRaisesRegex(ValueError, "gap"):
            validate_snapshot(s)
        s, _, _ = fixture(); s["v2"][0]["reverse"] = True
        with self.assertRaisesRegex(ValueError, "forward"):
            validate_snapshot(s)


if __name__ == "__main__":
    unittest.main()
