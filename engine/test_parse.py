"""Quick check: parse the real reel list and print what was understood."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from match import parse_reel_list

text = Path(sys.argv[1]).read_text(encoding="utf-8")
for i, r in enumerate(parse_reel_list(text), 1):
    end = f"{r['end']['hint_s']:.0f}s '{r['end']['phrase']}'" if r["end"] else "MISSING"
    print(f"{i:2}. start {r['start']['hint_s']:.0f}s '{r['start']['phrase']}'")
    print(f"    end   {end}")
