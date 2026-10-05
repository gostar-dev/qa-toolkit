"""전체 데모 실행.

  python run_demo.py

1) 게임 재화 데이터와 결제 데이터를 새로 만든다 (정상본, 결함 주입본)
2) 정합성 대사를 돌린다
3) 창 크기별로 원시 차이가 어떻게 달라지는지 비교한다
4) 릴리즈 게이트 샘플 3종을 판정한다
5) reports/ 폴더에 HTML, 마크다운, CSV 리포트를 저장한다
"""
from datetime import datetime, timedelta
from pathlib import Path
import time

from recon import generate as gen
from recon.engine import Reconciler, sweep, KIND_LABEL
from recon.analysis import compare_window_sizes, DEFAULT_STEPS
from recon.scenarios import GAME, PAYMENT
from recon import report as rr
from gate.gate import run as gate_run
from gate import report as gr

ROOT = Path(__file__).parent
REPORTS, DATA = ROOT / "reports", ROOT / "data"
W0, W1 = datetime(2026, 3, 2), datetime(2026, 3, 5)


def fresh_db(name):
    p = DATA / name
    if p.exists():
        p.unlink()
    return gen.connect(str(p))


def main():
    REPORTS.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)
    t0 = time.time()
    html_sections, md = [], ["# QA Toolkit 실행 리포트", "",
                             "모든 데이터는 생성기로 만든 가짜 데이터다. 실제 회사 데이터는 쓰지 않았다.", ""]

    # --- 게임 재화 ---------------------------------------------------------
    clean = fresh_db("game_clean.db")
    n = gen.generate_game(clean)
    print(f"[게임] 정상 데이터 생성: 스핀 {n[0]:,}건, 세션 {n[1]:,}개")
    cmp_clean = compare_window_sizes(clean, GAME, W0, W1, DEFAULT_STEPS)

    bad = fresh_db("game_defects.db")
    gen.generate_game(bad)
    truth = gen.inject_game_defects(bad, W0, W1)
    print(f"[게임] 결함 {len(truth)}개 주입")
    r3d = Reconciler(bad, GAME).check(W0, W1)
    cmp_bad = compare_window_sizes(bad, GAME, W0, W1, DEFAULT_STEPS)
    s3h = sweep(bad, GAME, W0, W1, timedelta(hours=3))
    rr.findings_csv([r3d], REPORTS / "game_findings.csv")

    found = {(f.kind, f.ref) for f in r3d.findings}
    expected = {(i.kind, i.ref) for i in truth}
    print(f"[게임] 3일 창 판정: {r3d.verdict} / 심은 결함 {len(expected)}개 중 {len(found & expected)}개 검출, "
          f"오탐 {len(found - expected)}개 / 등식 검증 {'통과' if r3d.closure_ok else '실패'}")

    truth_rows = "".join(f"<tr><td>{KIND_LABEL[i.kind]}</td><td>{i.ref}</td><td>{i.note}</td></tr>" for i in truth)
    html_sections += [
        rr.comparison_html(cmp_clean, "① 정상 데이터: 창 크기별 원시 차이 (경계 효과만 존재)"),
        "<div class=box>정상 데이터인데도 창을 3시간으로 자르면 베팅 합계가 창마다 수 % 씩 어긋난다. "
        "세션 경계에 걸친 금액 때문이다. 엔진은 이 금액을 세션 단위로 정확히 계산해 빼기 때문에 "
        "어떤 창 크기에서도 미설명 금액이 0으로 나온다.</div>",
        rr.comparison_html(cmp_bad, "② 결함 주입 데이터: 창 크기별 비교"),
        rr.result_html_section(r3d),
        f"<h2>심은 결함 (정답지)</h2><table><tr><th>구분</th><th>대상</th><th>내용</th></tr>{truth_rows}</table>",
    ]
    md += [rr.comparison_md(cmp_clean, "정상 데이터: 창 크기별 원시 차이"),
           rr.comparison_md(cmp_bad, "결함 주입 데이터: 창 크기별 비교"),
           rr.result_md(r3d), rr.sweep_md(s3h)]

    # --- 결제 ---------------------------------------------------------------
    pay = fresh_db("payment_defects.db")
    n = gen.generate_payment(pay)
    print(f"[결제] 데이터 생성: 승인 {n[0]:,}건, 배치 {n[1]:,}개")
    ptruth = gen.inject_payment_defects(pay, W0, W1)
    days = sweep(pay, PAYMENT, W0, W1, timedelta(days=1))
    rr.findings_csv(days, REPORTS / "payment_findings.csv")
    pfound = set().union(*({(f.kind, f.ref) for f in d.findings} for d in days))
    pexp = {(i.kind, i.ref) for i in ptruth}
    print(f"[결제] 일별 대사 3일: 심은 결함 {len(pexp)}개 중 {len(pfound & pexp)}개 검출, 오탐 {len(pfound - pexp)}개")
    html_sections += [rr.result_html_section(d) for d in days]
    md += [rr.result_md(d) for d in days]

    # --- 릴리즈 게이트 -----------------------------------------------------------
    for name, label in [("release_go", "샘플 A"), ("release_conditional", "샘플 B"), ("release_nogo", "샘플 C")]:
        d = ROOT / "gate" / "samples" / name
        g = gate_run(d / "tc_results.csv", d / "open_defects.csv")
        print(f"[게이트] {label} ({name}): {g.verdict}")
        html_sections.append(gr.to_html_section(g, f"릴리즈 품질 게이트 — {label}"))
        md.append(gr.to_md(g, f"릴리즈 품질 게이트 — {label}"))

    (REPORTS / "report.html").write_text(rr.page_html("QA Toolkit 실행 리포트", html_sections), encoding="utf-8")
    (REPORTS / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n리포트 저장: {REPORTS / 'report.html'}  ({time.time() - t0:.1f}초)")


if __name__ == "__main__":
    main()
