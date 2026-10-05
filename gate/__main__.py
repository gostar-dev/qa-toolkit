"""명령행 실행.

  python -m gate --results gate/samples/release_nogo/tc_results.csv --defects gate/samples/release_nogo/open_defects.csv

종료 코드: 0 GO / 1 CONDITIONAL GO / 2 NO-GO / 3 입력 오류
배포 파이프라인에서 종료 코드가 2 이상이면 배포 단계를 막는 식으로 연결한다.
"""
import argparse
import sys
from pathlib import Path

from .gate import run, InputError, RULES_PATH
from .report import to_md


def main(argv=None):
    p = argparse.ArgumentParser(prog="gate", description="릴리즈 품질 게이트")
    p.add_argument("--results", required=True, help="TC 결과 CSV")
    p.add_argument("--defects", required=True, help="결함 목록 CSV")
    p.add_argument("--rules", default=str(RULES_PATH))
    p.add_argument("--md", help="마크다운 리포트 저장 경로")
    a = p.parse_args(argv)
    try:
        g = run(a.results, a.defects, a.rules)
    except InputError as e:
        print(f"[입력 오류] {e}\n입력이 잘못되면 판정을 내리지 않는다.", file=sys.stderr)
        return 3
    text = to_md(g)
    print(text)
    if a.md:
        Path(a.md).parent.mkdir(parents=True, exist_ok=True)
        Path(a.md).write_text(text, encoding="utf-8")
    return g.exit_code


if __name__ == "__main__":
    sys.exit(main())
