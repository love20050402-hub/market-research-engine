"""Run all tests with a production-data guard and verify state bytes are unchanged."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    files = list((ROOT/'data').glob('*')) + list((ROOT/'output').glob('*'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}


def main():
    before = snapshot()
    env = dict(os.environ, RADAR_TEST_MODE='1', PYTHONUTF8='1')
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT, env=env)
    if before != snapshot():
        print('FAIL: production data/output changed during tests', file=sys.stderr)
        return 1
    print('Production history and output unchanged (SHA-256 verified).')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
