"""API 하네스가 서버 결함을 실제로 잡는지 확인한다 (결함 주입).

서버에 결함을 하나씩 심어 띄우고, 그때마다 API 계약 테스트가 실패하는지 본다.
실패하면 '잡힘', 그대로 통과하면 '못 잡음' -> 하네스를 보강해야 한다는 뜻.
  python tools/api_fault_check.py
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apiqa.server import BUGS  # noqa: E402


def run(bug=None):
    env = dict(os.environ)
    env.pop("APIQA_BUG", None)
    if bug:
        env["APIQA_BUG"] = bug
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "tests/test_api_contract.py"], cwd=ROOT, env=env, capture_output=True, text=True)
    failed = [ln.split("::")[-1].split(" ")[0] for ln in r.stdout.splitlines() if ln.startswith("FAILED")]
    return r.returncode, failed


def main():
    code, _ = run()
    if code != 0:
        print("정상 서버에서 테스트가 실패함. 결함 주입 전에 하네스부터 확인해야 함.")
        return 1
    print("정상 서버: 전부 통과\n")
    caught = 0
    for bug, desc in BUGS.items():
        code, failed = run(bug)
        ok = code != 0
        caught += ok
        names = ", ".join(sorted(set(failed))[:3])
        print(f"[{'잡힘' if ok else '못 잡음'}] {bug:15s} {desc}" + (f"\n          실패한 테스트: {names}" if ok else ""))
    print(f"\n심은 서버 결함 {len(BUGS)}개 중 {caught}개를 하네스가 잡음")
    return 0 if caught == len(BUGS) else 1


if __name__ == "__main__":
    sys.exit(main())
