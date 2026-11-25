"""Print transcript segments around a time point: python context.py words.json 1073 1085 ..."""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for center in (float(a) for a in sys.argv[2:]):
    print(f"--- around {int(center // 60)}:{int(center % 60):02d} ---")
    for seg in data["segments"]:
        if center - 15 <= seg["end"] and seg["start"] <= center + 15:
            print(f"  [{seg['start']:7.1f}] {seg['text'].strip()}")
