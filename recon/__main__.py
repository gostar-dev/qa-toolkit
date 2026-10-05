"""명령행 실행.

예)
  python -m recon generate --scenario game --db data/game.db --inject
  python -m recon check --scenario game --db data/game.db --start "2026-03-02 00:00" --end "2026-03-05 00:00"
  python -m recon check --scenario game --db data/game.db --start "2026-03-02 00:00" --end "2026-03-05 00:00" --step 3h

종료 코드: 0 정상 / 1 주의 / 2 결함 후보 / 3 검증 불가
CI 파이프라인에서 이 코드로 다음 단계 진행 여부를 결정할 수 있다.
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .scenarios import SCENARIOS
from .engine import Reconciler, sweep
from . import generate as gen
from . import report

EXIT = {"정상": 0, "주의": 1, "결함 후보": 2, "검증 불가": 3}


def parse_step(text):
    unit = text[-1]
    n = int(text[:-1])
    return {"h": timedelta(hours=n), "d": timedelta(days=n), "m": timedelta(minutes=n)}[unit]


def parse_dt(text):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"날짜 형식 오류: {text}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="recon", description="정합성 대사 도구")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="가짜 데이터 생성")
    g.add_argument("--scenario", choices=SCENARIOS, required=True)
    g.add_argument("--db", required=True)
    g.add_argument("--inject", action="store_true", help="결함을 심는다")
    g.add_argument("--seed", type=int, default=7)

    c = sub.add_parser("check", help="대사 실행")
    c.add_argument("--scenario", choices=SCENARIOS, required=True)
    c.add_argument("--db", required=True)
    c.add_argument("--start", type=parse_dt, required=True)
    c.add_argument("--end", type=parse_dt, required=True)
    c.add_argument("--step", help="창을 잘게 나눌 때 크기 (예: 3h, 1d)")
    c.add_argument("--csv", help="결함 후보 CSV 저장 경로")
    c.add_argument("--md", help="마크다운 리포트 저장 경로")

    a = p.parse_args(argv)
    sc = SCENARIOS[a.scenario]

    if a.cmd == "generate":
        Path(a.db).parent.mkdir(parents=True, exist_ok=True)
        if Path(a.db).exists():
            Path(a.db).unlink()
        conn = gen.connect(a.db)
        start, end = datetime(2026, 3, 2), datetime(2026, 3, 5)
        if a.scenario == "game":
            n = gen.generate_game(conn, seed=a.seed)
            inj = gen.inject_game_defects(conn, start, end) if a.inject else []
        else:
            n = gen.generate_payment(conn, seed=a.seed)
            inj = gen.inject_payment_defects(conn, start, end) if a.inject else []
        print(f"생성 완료: 상세 {n[0]:,}건, 묶음 {n[1]:,}개, 심은 결함 {len(inj)}개")
        for i in inj:
            print(f"  - [{i.kind}] {i.ref}: {i.note}")
        return 0

    conn = sqlite3.connect(a.db)
    if a.step:
        results = sweep(conn, sc, a.start, a.end, parse_step(a.step))
        print(report.sweep_md(results))
    else:
        results = [Reconciler(conn, sc).check(a.start, a.end)]
    for r in results:
        if a.step and r.verdict == "정상":
            continue
        print(report.result_md(r))
    if a.csv:
        Path(a.csv).parent.mkdir(parents=True, exist_ok=True)
        report.findings_csv(results, a.csv)
    if a.md:
        Path(a.md).parent.mkdir(parents=True, exist_ok=True)
        Path(a.md).write_text("\n".join(report.result_md(r) for r in results), encoding="utf-8")
    worst = max(results, key=lambda r: EXIT[r.verdict])
    return EXIT[worst.verdict]


if __name__ == "__main__":
    sys.exit(main())
