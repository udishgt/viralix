from pathlib import Path
import difflib
b = Path('outputs/transcript_comparison/baseline_20260519T151300/baseline.srt').read_text(encoding='utf-8').splitlines()
u = Path('outputs/transcript_comparison/upgraded_20260519T151308/upgraded.srt').read_text(encoding='utf-8').splitlines()
d = difflib.unified_diff(b, u, fromfile='baseline.srt', tofile='upgraded.srt', lineterm='')
print('\n'.join(d))
