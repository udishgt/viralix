import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, out.strip()


def check_binary(name: str) -> bool:
    if shutil.which(name):
        return True
    win_candidate = PYTHON.parent / f"{name}.exe"
    unix_candidate = PYTHON.parent / name
    return win_candidate.exists() or unix_candidate.exists()


def main() -> int:
    parser = argparse.ArgumentParser(description="Viralix MVP readiness check")
    parser.add_argument("--with-frontend", action="store_true", help="Run frontend build check")
    args = parser.parse_args()

    print("== Viralix MVP Readiness Check ==")
    print(f"Python: {PYTHON}")

    checks: list[tuple[str, bool, str]] = []

    ffmpeg_ok = check_binary("ffmpeg")
    ytdlp_ok = check_binary("yt-dlp")
    checks.append(("ffmpeg available", ffmpeg_ok, "required for clipping"))
    checks.append(("yt-dlp available", ytdlp_ok, "required for YouTube URL ingestion"))

    rc, out = run([str(PYTHON), "-m", "pytest", "-q", "backend/test_auth_core_pytest.py", "backend/test_auth_api_pytest.py", "backend/test_job_ownership_pytest.py"], cwd=ROOT)
    checks.append(("backend pytest suite", rc == 0, out))

    if args.with_frontend:
        npm = "npm.cmd" if sys.platform.startswith("win") else "npm"
        rc, out = run([npm, "run", "build"], cwd=ROOT / "frontend")
        checks.append(("frontend production build", rc == 0, out))

    failed = False
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}")
        if not ok:
            failed = True
            if detail:
                print(detail[:1200])

    if failed:
        print("\nReadiness check failed. Fix the failing items above.")
        return 1

    print("\nAll readiness checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
