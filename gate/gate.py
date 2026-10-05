"""릴리즈 품질 게이트 판정 로직."""
from dataclasses import dataclass, field
from pathlib import Path
import csv
import json

SEVERITIES = ["Cosmetic", "Minor", "Major", "Critical"]   # 낮음 -> 높음
PRIORITIES = ["P1", "P2", "P3"]
RULES_PATH = Path(__file__).parent / "rules.json"

LEVEL_ORDER = {"BLOCK": 3, "CONDITION": 2, "INFO": 1}


class InputError(Exception):
    """입력 파일 자체가 잘못된 경우. 판정을 내리면 안 된다."""


@dataclass
class Reason:
    level: str      # BLOCK / CONDITION / INFO
    code: str
    message: str


@dataclass
class CategoryStat:
    category: str
    total: int
    passed: int
    failed: int
    blocked: int
    na: int
    critical: bool

    @property
    def executed(self):
        return self.total - self.na

    @property
    def pass_rate(self):
        return self.passed / self.executed if self.executed else 0.0


@dataclass
class Defect:
    id: str
    title: str
    category: str
    severity: str
    priority: str
    status: str
    fix_plan: bool
    effective_severity: str = ""
    decision: str = ""          # block / conditional / after / closed


@dataclass
class GateResult:
    verdict: str                # GO / CONDITIONAL GO / NO-GO
    reasons: list
    categories: list
    defects: list
    overall_pass_rate: float
    na_rate: float

    @property
    def exit_code(self):
        return {"GO": 0, "CONDITIONAL GO": 1, "NO-GO": 2}[self.verdict]


# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------
def _read_csv(path, required):
    with open(path, encoding="utf-8-sig", newline="") as fp:
        rows = list(csv.DictReader(fp))
    if not rows:
        raise InputError(f"{path}: 데이터가 없음")
    missing = [c for c in required if c not in rows[0]]
    if missing:
        raise InputError(f"{path}: 필수 컬럼 없음 {missing}")
    return rows


def _int(v, where):
    try:
        n = int(str(v).strip())
    except ValueError:
        raise InputError(f"{where}: 숫자가 아님 '{v}'")
    if n < 0:
        raise InputError(f"{where}: 음수 '{v}'")
    return n


def load_results(path, rules):
    rows = _read_csv(path, ["category", "total", "pass", "fail", "blocked", "na"])
    crit = set(rules["critical_categories"])
    stats, seen = [], set()
    for i, r in enumerate(rows, start=2):
        cat = r["category"].strip()
        if cat in seen:
            raise InputError(f"{path} {i}행: 카테고리 중복 '{cat}'")
        seen.add(cat)
        nums = [_int(r[k], f"{path} {i}행 {k}") for k in ("total", "pass", "fail", "blocked", "na")]
        total, p, f, b, na = nums
        if p + f + b + na != total:
            raise InputError(f"{path} {i}행 '{cat}': pass+fail+blocked+na({p + f + b + na}) != total({total})")
        stats.append(CategoryStat(cat, total, p, f, b, na, cat in crit))
    return stats


def load_defects(path, rules):
    rows = _read_csv(path, ["id", "title", "category", "severity", "priority", "status", "fix_plan"])
    known = set(rules["open_statuses"]) | set(rules["closed_statuses"])
    out, seen = [], set()
    for i, r in enumerate(rows, start=2):
        did = r["id"].strip()
        if did in seen:
            raise InputError(f"{path} {i}행: 결함 ID 중복 '{did}'")
        seen.add(did)
        sev, pri, st = r["severity"].strip(), r["priority"].strip(), r["status"].strip()
        if sev not in SEVERITIES:
            raise InputError(f"{path} {i}행 {did}: 알 수 없는 심각도 '{sev}'")
        if pri not in PRIORITIES:
            raise InputError(f"{path} {i}행 {did}: 알 수 없는 우선순위 '{pri}'")
        if st not in known:
            raise InputError(f"{path} {i}행 {did}: 알 수 없는 상태 '{st}'")
        plan = r["fix_plan"].strip().upper()
        if plan not in ("Y", "N", ""):
            raise InputError(f"{path} {i}행 {did}: fix_plan 은 Y 또는 N")
        out.append(Defect(did, r["title"].strip(), r["category"].strip(), sev, pri, st, plan == "Y"))
    return out


def load_rules(path=RULES_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------
def classify(defect: Defect, rules) -> str:
    """결함 하나가 출시에 어떤 영향을 주는지 결정한다."""
    if defect.status in rules["closed_statuses"]:
        defect.effective_severity = defect.severity
        return "closed"
    sev = defect.severity
    if rules.get("escalate_in_critical") and defect.category in rules["critical_categories"]:
        idx = SEVERITIES.index(sev)
        sev = SEVERITIES[min(idx + 1, len(SEVERITIES) - 1)]
    defect.effective_severity = sev
    return rules["matrix"][sev][defect.priority]


def evaluate(stats, defects, rules) -> GateResult:
    reasons: list = []

    # 1) TC 통과율 ------------------------------------------------------------
    executed = sum(c.executed for c in stats)
    passed = sum(c.passed for c in stats)
    total = sum(c.total for c in stats)
    na = sum(c.na for c in stats)
    overall = passed / executed if executed else 0.0
    na_rate = na / total if total else 0.0

    if executed == 0:
        reasons.append(Reason("BLOCK", "NO_EXECUTION", "수행된 TC가 없음"))
    elif overall < rules["min_pass_rate_overall"]:
        reasons.append(Reason("BLOCK", "LOW_PASS_OVERALL",
                              f"전체 통과율 {overall:.2%} < 기준 {rules['min_pass_rate_overall']:.0%}"))

    for c in stats:
        if c.executed == 0:
            lvl = "BLOCK" if c.critical else "CONDITION"
            reasons.append(Reason(lvl, "NOT_EXECUTED", f"[{c.category}] 수행된 TC 없음 (전부 N/A)"))
            continue
        if c.critical:
            if c.pass_rate < rules["min_pass_rate_critical"]:
                reasons.append(Reason("BLOCK", "LOW_PASS_CRITICAL",
                                      f"[{c.category}] 금전성 영역 통과율 {c.pass_rate:.2%} (기준 100%)"))
            if c.blocked:
                reasons.append(Reason("BLOCK", "BLOCKED_CRITICAL",
                                      f"[{c.category}] 수행 못 한 TC {c.blocked}건. 검증 안 된 것은 통과가 아님"))
        else:
            if c.pass_rate < rules["min_pass_rate_category"]:
                reasons.append(Reason("CONDITION", "LOW_PASS_CATEGORY",
                                      f"[{c.category}] 통과율 {c.pass_rate:.2%} < 기준 {rules['min_pass_rate_category']:.0%}"))
            if c.blocked:
                reasons.append(Reason("CONDITION", "BLOCKED",
                                      f"[{c.category}] 수행 못 한 TC {c.blocked}건"))

    if na_rate > rules["max_na_rate"]:
        reasons.append(Reason("CONDITION", "HIGH_NA",
                              f"N/A 비율 {na_rate:.2%} > 기준 {rules['max_na_rate']:.0%}. 제외 사유 확인 필요"))

    # 2) 결함 ------------------------------------------------------------------
    for d in defects:
        d.decision = classify(d, rules)
        esc = f" (금전성 영역이라 {d.severity}→{d.effective_severity} 상향)" if d.effective_severity != d.severity else ""
        tag = f"{d.id} {d.title} [{d.effective_severity}/{d.priority}, {d.status}]{esc}"
        if d.decision == "block":
            note = " 수정됐지만 재검증 전" if d.status == "Fixed" else (" 보류 처리돼도 차단 기준" if d.status == "Deferred" else "")
            reasons.append(Reason("BLOCK", "BLOCKING_DEFECT", f"출시 차단 결함: {tag}.{note}".rstrip(".")))
        elif d.decision == "conditional":
            if d.fix_plan:
                reasons.append(Reason("CONDITION", "CONDITIONAL_DEFECT", f"수정 계획 합의된 조건부 결함: {tag}"))
            else:
                reasons.append(Reason("BLOCK", "NO_FIX_PLAN", f"조건부 결함인데 수정 계획 미합의: {tag}"))
        elif d.decision == "after":
            reasons.append(Reason("INFO", "AFTER_RELEASE", f"출시 후 대응: {tag}"))

    # 3) 실패 TC 대비 결함 등록 누락 -------------------------------------------
    open_cats = {d.category for d in defects if d.decision != "closed"}
    for c in stats:
        if c.failed and c.category not in open_cats:
            reasons.append(Reason("CONDITION", "FAIL_WITHOUT_DEFECT",
                                  f"[{c.category}] 실패 TC {c.failed}건인데 열린 결함이 없음. 리포팅 누락 확인 필요"))

    reasons.sort(key=lambda r: -LEVEL_ORDER[r.level])
    if any(r.level == "BLOCK" for r in reasons):
        verdict = "NO-GO"
    elif any(r.level == "CONDITION" for r in reasons):
        verdict = "CONDITIONAL GO"
    else:
        verdict = "GO"
    return GateResult(verdict, reasons, stats, defects, overall, na_rate)


def run(results_csv, defects_csv, rules_path=RULES_PATH) -> GateResult:
    rules = load_rules(rules_path)
    return evaluate(load_results(results_csv, rules), load_defects(defects_csv, rules), rules)
