"""창 크기별 비교.

실무에서는 3시간 창으로 집계하면 오차가 너무 커서, 창을 3일로 넓혀
경계 효과를 줄인 뒤 '남은 차이가 세션 잔여분 수준'이라고 판단했다.
이 함수는 그 판단을 숫자로 보여준다. 창이 작을수록 원시 차이 비율은 커지지만,
경계 효과를 정확히 빼고 나면 창 크기와 상관없이 같은 결함만 남는다.
"""
from datetime import timedelta
from .engine import sweep


def compare_window_sizes(conn, scenario, start, end, steps):
    rows = []
    for label, step in steps:
        res = sweep(conn, scenario, start, end, step)
        m0 = [r.metrics[0] for r in res]
        ratios = [abs(m.raw_diff) / m.detail_total for m in m0 if m.detail_total]
        rows.append({
            "창 크기": label,
            "창 개수": len(res),
            "원시 차이 평균 비율": sum(ratios) / len(ratios) if ratios else 0.0,
            "원시 차이 최대 비율": max(ratios) if ratios else 0.0,
            "경계 효과로 설명된 금액(절대값 합)": sum(abs(m.expected_diff) for m in m0),
            "미설명 금액 합": sum(m.unexplained for m in m0),
            "기준 지표": scenario.metrics[0].label,
            "결함 후보 창": sum(1 for r in res if r.verdict == "결함 후보"),
            "등식 검증": all(r.closure_ok for r in res),
        })
    return rows


DEFAULT_STEPS = [("1시간", timedelta(hours=1)), ("3시간", timedelta(hours=3)),
                 ("6시간", timedelta(hours=6)), ("1일", timedelta(days=1)),
                 ("3일", timedelta(days=3))]
