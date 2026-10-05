"""리포트 출력: 마크다운, HTML, CSV."""
import csv
import html
from datetime import datetime

from .engine import KIND_LABEL


def _n(v):
    return f"{v:,}"


def _pct(v):
    return f"{v * 100:.2f}%"


# ---------------------------------------------------------------------------
# 마크다운
# ---------------------------------------------------------------------------
def result_md(r) -> str:
    s = r.scenario
    out = [f"## {s.title}",
           f"- 대사 창: {r.start:%Y-%m-%d %H:%M} ~ {r.end:%Y-%m-%d %H:%M}",
           f"- 확인한 {s.group_unit_label}: {_n(r.groups_checked)}개 / 상세 건: {_n(r.rows_checked)}건",
           f"- 판정: **{r.verdict}** (FAIL {r.fail_count}건, WARN {r.warn_count}건)",
           f"- 등식 검증(엔진 자기 점검): {'통과' if r.closure_ok else '실패'}", "",
           "| 지표 | 상세 합계 | 집계 합계 | 원시 차이 | 경계 효과 | 미설명 차이 | 허용 오차 내 |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for m in r.metrics:
        out.append(f"| {m.metric.label} | {_n(m.detail_total)} | {_n(m.group_total)} | {_n(m.raw_diff)} | "
                   f"{_n(m.expected_diff)} | {_n(m.unexplained)} | {_n(m.mismatch_tolerated)} |")
    out.append("")
    if r.findings:
        out += ["### 결함 후보", "", "| 구분 | 등급 | 대상 | 내용 |", "|---|---|---|---|"]
        for f in r.findings:
            out.append(f"| {KIND_LABEL[f.kind]} | {f.severity} | {f.ref} | {f.note}. {_detail_text(f)} |")
    else:
        out.append("결함 후보 없음.")
    return "\n".join(out) + "\n"


def _detail_text(f) -> str:
    if f.kind == "mismatch":
        parts = [f"{k} 차이 {_n(v['차이'])} (허용 {_n(v['허용'])})" for k, v in f.detail.items()]
        return ", ".join(parts)
    return ", ".join(f"{k} {v:,}" if isinstance(v, int) else f"{k} {v}" for k, v in f.detail.items())


def sweep_md(results) -> str:
    s = results[0].scenario
    m0 = s.metrics[0]
    out = [f"## {s.title} — 창별 결과", "",
           f"| 창 | {m0.label} 원시 차이 | 경계 효과 | 미설명 | 판정 |", "|---|---:|---:|---:|---|"]
    for r in results:
        m = r.metrics[0]
        out.append(f"| {r.start:%m-%d %H:%M} ~ {r.end:%m-%d %H:%M} | {_n(m.raw_diff)} | "
                   f"{_n(m.expected_diff)} | {_n(m.unexplained)} | {r.verdict} |")
    return "\n".join(out) + "\n"


def comparison_md(rows, title) -> str:
    out = [f"## {title}", "", f"기준 지표: {rows[0]['기준 지표']}", "",
           "| 창 크기 | 창 개수 | 원시 차이 평균 비율 | 최대 비율 | 미설명 금액 합 | 결함 후보 창 | 등식 검증 |",
           "|---|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        out.append(f"| {r['창 크기']} | {r['창 개수']} | {_pct(r['원시 차이 평균 비율'])} | "
                   f"{_pct(r['원시 차이 최대 비율'])} | {_n(r['미설명 금액 합'])} | {r['결함 후보 창']} | "
                   f"{'통과' if r['등식 검증'] else '실패'} |")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# CSV (결함 후보 목록, 개발팀 전달용)
# ---------------------------------------------------------------------------
def findings_csv(results, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["창 시작", "창 끝", "구분", "등급", "대상", "설명", "상세"])
        for r in results:
            for f in r.findings:
                w.writerow([f"{r.start:%Y-%m-%d %H:%M}", f"{r.end:%Y-%m-%d %H:%M}",
                            KIND_LABEL[f.kind], f.severity, f.ref, f.note, _detail_text(f)])


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
CSS = """
body{font-family:'Noto Sans CJK KR','Malgun Gothic',sans-serif;color:#1d1d1f;max-width:1080px;margin:32px auto;padding:0 20px;font-size:14px;line-height:1.55}
h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:34px 0 10px;padding-left:10px;border-left:4px solid #2f5bea}
.meta{color:#6e6e73;font-size:12.5px}
table{border-collapse:collapse;width:100%;margin:8px 0 14px;font-size:13px}
th{background:#1d2433;color:#fff;font-weight:600;padding:7px 9px;text-align:center}
td{border-bottom:1px solid #e6e6e9;padding:6px 9px} td.n{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-weight:700;font-size:12px}
.b-ok{background:#e5f6ea;color:#16794a} .b-fail{background:#fde8e8;color:#c0392b} .b-warn{background:#fff4d6;color:#8a6100} .b-err{background:#1d1d1f;color:#fff}
.box{background:#f5f6f8;border-radius:8px;padding:12px 16px;margin:10px 0;font-size:13px}
"""


def _badge(verdict):
    cls = {"정상": "b-ok", "결함 후보": "b-fail", "주의": "b-warn", "검증 불가": "b-err",
           "FAIL": "b-fail", "WARN": "b-warn"}.get(verdict, "b-warn")
    return f'<span class="badge {cls}">{html.escape(verdict)}</span>'


def result_html_section(r) -> str:
    s = r.scenario
    rows = "".join(
        f"<tr><td>{m.metric.label}</td><td class=n>{_n(m.detail_total)}</td><td class=n>{_n(m.group_total)}</td>"
        f"<td class=n>{_n(m.raw_diff)}</td><td class=n>{_n(m.expected_diff)}</td>"
        f"<td class=n><b>{_n(m.unexplained)}</b></td><td class=n>{_n(m.mismatch_tolerated)}</td></tr>"
        for m in r.metrics)
    frows = "".join(
        f"<tr><td>{KIND_LABEL[f.kind]}</td><td>{_badge(f.severity)}</td><td>{html.escape(f.ref)}</td>"
        f"<td>{html.escape(f.note)}. {html.escape(_detail_text(f))}</td></tr>" for f in r.findings)
    findings = (f"<table><tr><th>구분</th><th>등급</th><th>대상</th><th>내용</th></tr>{frows}</table>"
                if r.findings else "<p>결함 후보 없음.</p>")
    return (f"<h2>{html.escape(s.title)}</h2>"
            f"<p class=meta>대사 창 {r.start:%Y-%m-%d %H:%M} ~ {r.end:%Y-%m-%d %H:%M} | "
            f"{s.group_unit_label} {_n(r.groups_checked)}개, 상세 {_n(r.rows_checked)}건 | "
            f"판정 {_badge(r.verdict)} | 등식 검증 {'통과' if r.closure_ok else '실패'}</p>"
            f"<table><tr><th>지표</th><th>상세 합계</th><th>집계 합계</th><th>원시 차이</th>"
            f"<th>경계 효과</th><th>미설명 차이</th><th>허용 오차 내</th></tr>{rows}</table>{findings}")


def comparison_html(rows, title) -> str:
    body = "".join(
        f"<tr><td>{r['창 크기']}</td><td class=n>{r['창 개수']}</td><td class=n>{_pct(r['원시 차이 평균 비율'])}</td>"
        f"<td class=n>{_pct(r['원시 차이 최대 비율'])}</td><td class=n>{_n(r['미설명 금액 합'])}</td>"
        f"<td class=n>{r['결함 후보 창']}</td><td>{'통과' if r['등식 검증'] else '실패'}</td></tr>" for r in rows)
    return (f"<h2>{html.escape(title)}</h2><p class=meta>기준 지표: {html.escape(rows[0]['기준 지표'])}</p><table><tr><th>창 크기</th><th>창 개수</th><th>원시 차이 평균 비율</th>"
            f"<th>최대 비율</th><th>미설명 금액 합</th><th>결함 후보 창</th><th>등식 검증</th></tr>{body}</table>")


def page_html(title, sections, intro="") -> str:
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body><h1>{html.escape(title)}</h1>"
            f"<p class=meta>생성 {datetime.now():%Y-%m-%d %H:%M} | 모든 데이터는 생성기로 만든 가짜 데이터</p>"
            f"{intro}{''.join(sections)}</body></html>")
