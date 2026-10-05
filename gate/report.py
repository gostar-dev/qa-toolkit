"""게이트 판정 리포트."""
import html

LEVEL_KO = {"BLOCK": "차단", "CONDITION": "조건", "INFO": "참고"}
DECISION_KO = {"block": "출시 차단", "conditional": "조건부", "after": "출시 후 대응", "closed": "종료"}


def to_md(g, title="릴리즈 품질 게이트") -> str:
    out = [f"## {title}", "", f"**판정: {g.verdict}**", "",
           f"- 전체 통과율 {g.overall_pass_rate:.2%} (N/A 제외), N/A 비율 {g.na_rate:.2%}", "",
           "### 판정 근거", ""]
    if g.reasons:
        out += [f"- [{LEVEL_KO[r.level]}] {r.message}" for r in g.reasons]
    else:
        out.append("- 걸리는 항목 없음")
    out += ["", "### 카테고리별 결과", "",
            "| 카테고리 | 금전성 | 전체 | Pass | Fail | Blocked | N/A | 통과율 |",
            "|---|---|---:|---:|---:|---:|---:|---:|"]
    for c in g.categories:
        out.append(f"| {c.category} | {'O' if c.critical else ''} | {c.total} | {c.passed} | {c.failed} | "
                   f"{c.blocked} | {c.na} | {c.pass_rate:.2%} |")
    out += ["", "### 결함 분류", "", "| ID | 제목 | 영역 | 심각도(적용) | 우선순위 | 상태 | 수정계획 | 판정 |",
            "|---|---|---|---|---|---|---|---|"]
    for d in g.defects:
        sev = d.severity if d.effective_severity == d.severity else f"{d.severity}→{d.effective_severity}"
        out.append(f"| {d.id} | {d.title} | {d.category} | {sev} | {d.priority} | {d.status} | "
                   f"{'Y' if d.fix_plan else 'N'} | {DECISION_KO[d.decision]} |")
    return "\n".join(out) + "\n"


def to_html_section(g, title="릴리즈 품질 게이트") -> str:
    cls = {"GO": "b-ok", "CONDITIONAL GO": "b-warn", "NO-GO": "b-fail"}[g.verdict]
    lcls = {"BLOCK": "b-fail", "CONDITION": "b-warn", "INFO": "b-ok"}
    reasons = "".join(f"<tr><td><span class='badge {lcls[r.level]}'>{LEVEL_KO[r.level]}</span></td>"
                      f"<td>{html.escape(r.message)}</td></tr>" for r in g.reasons) or \
        "<tr><td colspan=2>걸리는 항목 없음</td></tr>"
    cats = "".join(f"<tr><td>{html.escape(c.category)}</td><td>{'O' if c.critical else ''}</td>"
                   f"<td class=n>{c.total}</td><td class=n>{c.passed}</td><td class=n>{c.failed}</td>"
                   f"<td class=n>{c.blocked}</td><td class=n>{c.na}</td><td class=n>{c.pass_rate:.2%}</td></tr>"
                   for c in g.categories)
    return (f"<h2>{html.escape(title)}</h2>"
            f"<p>판정 <span class='badge {cls}' style='font-size:14px'>{g.verdict}</span> "
            f"<span class=meta>전체 통과율 {g.overall_pass_rate:.2%}, N/A {g.na_rate:.2%}</span></p>"
            f"<table><tr><th style='width:70px'>구분</th><th>판정 근거</th></tr>{reasons}</table>"
            f"<table><tr><th>카테고리</th><th>금전성</th><th>전체</th><th>Pass</th><th>Fail</th>"
            f"<th>Blocked</th><th>N/A</th><th>통과율</th></tr>{cats}</table>")
