"""테스트가 진짜로 버그를 잡는지 확인하는 스크립트 (뮤테이션 테스트).

테스트가 전부 통과했다는 것만으로는 테스트가 제대로 짜였는지 알 수 없다.
그래서 코드에 일부러 버그를 하나씩 심고, 그때마다 테스트가 실패하는지 본다.
테스트가 실패하면 '잡힘', 그대로 통과하면 '못 잡음' -> 테스트를 보강해야 한다는 뜻.

원본 코드는 건드리지 않고 임시 폴더에 복사본을 만들어서 돌린다.
  python tools/mutation_check.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

MUTANTS = [
    ("경계 효과 계산에서 carried_out 누락", "recon/engine.py",
     "return self.carried_in - self.carried_out", "return self.carried_in"),
    ("열린 세션을 carried_out 에서 제외", "recon/engine.py",
     'f"AND (g.{s.group_close} IS NULL OR g.{s.group_close} >= ?)", (a, b, b))',
     'f"AND (g.{s.group_close} >= ?)", (a, b, b))'),
    ("허용 오차 경계 <= 를 < 로", "recon/engine.py",
     "if abs(diff) <= allowed:", "if abs(diff) < allowed:"),
    ("창 끝 시각 포함 (< 를 <= 로)", "recon/engine.py",
     'f"WHERE {s.detail_time} >= ? AND {s.detail_time} < ?", (a, b))\n\n        # 2)',
     'f"WHERE {s.detail_time} >= ? AND {s.detail_time} <= ?", (a, b))\n\n        # 2)'),
    ("마감 이후 기록의 중복 보고 방지 제거", "recon/engine.py",
     "            if str(row[0]) in late_refs:\n                continue", "            pass"),
    ("금전성 영역 심각도 상향 제거", "gate/gate.py",
     'if rules.get("escalate_in_critical") and', "if False and"),
    ("통과율 경계 < 를 <= 로", "gate/gate.py",
     'if c.pass_rate < rules["min_pass_rate_category"]:', 'if c.pass_rate <= rules["min_pass_rate_category"]:'),
    ("재검증 전(Fixed) 결함을 닫힌 것으로 취급", "gate/gate.py",
     'if defect.status in rules["closed_statuses"]:', 'if defect.status in rules["closed_statuses"] + ["Fixed"]:'),
    ("실패 TC 대비 결함 미등록 검사 제거", "gate/gate.py",
     "if c.failed and c.category not in open_cats:", "if False:"),
]


def main():
    caught = 0
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / "work"
        shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("data", "reports", "__pycache__", ".pytest_cache"))
        for name, rel, old, new in MUTANTS:
            path = work / rel
            src = path.read_text(encoding="utf-8")
            if old not in src:
                print(f"[확인 필요] {name}: 바꿀 코드를 찾지 못함")
                continue
            path.write_text(src.replace(old, new, 1), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                               cwd=work, capture_output=True, text=True)
            ok = r.returncode != 0
            caught += ok
            print(f"[{'잡힘' if ok else '못 잡음'}] {name}")
            path.write_text(src, encoding="utf-8")
    print(f"\n심은 버그 {len(MUTANTS)}개 중 {caught}개를 테스트가 잡음")
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
