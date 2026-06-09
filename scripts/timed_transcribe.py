import time
from pathlib import Path
src = Path('test_media/real_clip.mp4.mp4')
if not src.exists():
    print('Source not found:', src)
    exit(1)

# Baseline timing
try:
    import whisper
    t0 = time.time()
    model = whisper.load_model('base')
    res = model.transcribe(str(src), word_timestamps=True)
    t1 = time.time()
    print(f'Baseline (whisper base) elapsed: {t1-t0:.1f}s')
except Exception as e:
    print('Baseline failed:', e)

# Upgraded timing
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.server import transcribe_local

def run_upgraded():
    t0 = time.time()
    res = asyncio.run(transcribe_local('timed', str(src)))
    t1 = time.time()
    print(f'Upgraded (transcribe_local) elapsed: {t1-t0:.1f}s')

run_upgraded()
