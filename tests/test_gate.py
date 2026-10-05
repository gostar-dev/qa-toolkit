"""릴리즈 품질 게이트 테스트."""
from pathlib import Path
import pytest

from gate.gate import run, evaluate, load_rules, CategoryStat, Defect, InputError, classify
from gate.__main__ import main as gate_main

SAMPLES = Path(__file__).parent.parent / "gate" / "samples"
RULES = load_rules()


def cat(name, total=100, p=100, f=0, b=0, na=0):
    return CategoryStat(name, total, p, f, b, na, name in RULES["critical_categories"])


def defect(sev, pri, category="기본기능", status="Open", plan=False, did="D-1"):
    return Defect(did, "테스트 결함", category, sev, pri, status, plan)


def reasons(g):
    return [r.code for r in g.reasons]


# --- 샘플 3종 --------------------------------------------------------------
@pytest.mark.parametrize("name,verdict,code", [
    ("release_go", "GO", 0), ("release_conditional", "CONDITIONAL GO", 1), ("release_nogo", "NO-GO", 2)])
def test_samples(name, verdict, code):
    d = SAMPLES / name
    g = run(d / "tc_results.csv", d / "open_defects.csv")
    assert g.verdict == verdict and g.exit_code == code
    assert gate_main(["--results", str(d / "tc_results.csv"), "--defects", str(d / "open_defects.csv")]) == code


# --- 매트릭스 ----------------------------------------------------------------
@pytest.mark.parametrize("sev", ["Critical", "Major", "Minor", "Cosmetic"])
@pytest.mark.parametrize("pri", ["P1", "P2", "P3"])
def test_matrix_non_critical_area(sev, pri):
    assert classify(defect(sev, pri), RULES) == RULES["matrix"][sev][pri]


def test_escalation_in_money_area():
    d = defect("Minor", "P2", category="결제")
    assert classify(d, RULES) == "block" and d.effective_severity == "Major"


def test_escalation_caps_at_critical():
    d = defect("Critical", "P3", category="재화")
    assert classify(d, RULES) == "block" and d.effective_severity == "Critical"


def test_closed_defects_ignored():
    for st in ("Verified", "Closed"):
        g = evaluate([cat("기본기능")], [defect("Critical", "P1", status=st)], RULES)
        assert g.verdict == "GO"


def test_fixed_but_not_verified_still_blocks():
    g = evaluate([cat("기본기능")], [defect("Critical", "P1", status="Fixed")], RULES)
    assert g.verdict == "NO-GO"


def test_deferred_does_not_escape_gate():
    g = evaluate([cat("기본기능")], [defect("Major", "P1", status="Deferred")], RULES)
    assert g.verdict == "NO-GO"


def test_conditional_needs_fix_plan():
    assert evaluate([cat("기본기능")], [defect("Major", "P3", plan=True)], RULES).verdict == "CONDITIONAL GO"
    g = evaluate([cat("기본기능")], [defect("Major", "P3", plan=False)], RULES)
    assert g.verdict == "NO-GO" and "NO_FIX_PLAN" in reasons(g)


# --- 통과율, 차단 TC, N/A ------------------------------------------------------
def test_money_area_must_be_100_percent():
    g = evaluate([cat("결제", 100, 99, 1), cat("기본기능")], [defect("Minor", "P3", category="결제")], RULES)
    assert g.verdict == "NO-GO" and "LOW_PASS_CRITICAL" in reasons(g)


def test_pass_rate_boundary_95_percent():
    ok = evaluate([cat("기본기능", 100, 95, 5)], [defect("Minor", "P3")], RULES)
    assert "LOW_PASS_CATEGORY" not in reasons(ok)        # 정확히 95% 는 통과
    low = evaluate([cat("기본기능", 1000, 949, 51), cat("사운드", 5000, 5000)], [defect("Minor", "P3")], RULES)
    assert "LOW_PASS_CATEGORY" in reasons(low) and low.verdict == "CONDITIONAL GO"


def test_overall_below_threshold_blocks():
    g = evaluate([cat("기본기능", 100, 90, 10)], [defect("Minor", "P3")], RULES)
    assert g.verdict == "NO-GO" and "LOW_PASS_OVERALL" in reasons(g)


def test_blocked_tc_in_money_area_blocks():
    g = evaluate([cat("재화", 100, 99, 0, 1)], [], RULES)
    assert g.verdict == "NO-GO" and "BLOCKED_CRITICAL" in reasons(g)


def test_all_na_in_money_area_blocks():
    g = evaluate([cat("DB 정합성", 50, 0, 0, 0, 50), cat("기본기능")], [], RULES)
    assert g.verdict == "NO-GO" and "NOT_EXECUTED" in reasons(g)


def test_high_na_rate_is_condition():
    g = evaluate([cat("기본기능", 100, 85, 0, 0, 15)], [], RULES)
    assert g.verdict == "CONDITIONAL GO" and "HIGH_NA" in reasons(g)


def test_failed_tc_without_defect():
    g = evaluate([cat("기본기능", 100, 98, 2)], [], RULES)
    assert "FAIL_WITHOUT_DEFECT" in reasons(g) and g.verdict == "CONDITIONAL GO"


# --- 입력 오류 ---------------------------------------------------------------
def write(tmp_path, results, defects):
    r, d = tmp_path / "r.csv", tmp_path / "d.csv"
    r.write_text(results, encoding="utf-8")
    d.write_text(defects, encoding="utf-8")
    return r, d


HEAD_R = "category,total,pass,fail,blocked,na\n"
HEAD_D = "id,title,category,severity,priority,status,fix_plan\n"


@pytest.mark.parametrize("results,defects,msg", [
    (HEAD_R + "기본기능,10,9,0,0,0\n", HEAD_D + "D1,a,기본기능,Minor,P3,Open,N\n", "total"),
    (HEAD_R + "기본기능,10,10,0,0,0\n", HEAD_D + "D1,a,기본기능,Blocker,P3,Open,N\n", "심각도"),
    (HEAD_R + "기본기능,10,10,0,0,0\n", HEAD_D + "D1,a,기본기능,Minor,P0,Open,N\n", "우선순위"),
    (HEAD_R + "기본기능,10,10,0,0,0\n", HEAD_D + "D1,a,기본기능,Minor,P3,Done,N\n", "상태"),
    (HEAD_R + "기본기능,10,10,0,0,0\n", HEAD_D + "D1,a,기본기능,Minor,P3,Open,N\nD1,b,기본기능,Minor,P3,Open,N\n", "중복"),
    (HEAD_R + "기본기능,10,10,0,0,0\n기본기능,5,5,0,0,0\n", HEAD_D + "D1,a,기본기능,Minor,P3,Open,N\n", "중복"),
    (HEAD_R + "기본기능,10,11,-1,0,0\n", HEAD_D + "D1,a,기본기능,Minor,P3,Open,N\n", "음수"),
    ("category,total,pass\n기본기능,10,10\n", HEAD_D + "D1,a,기본기능,Minor,P3,Open,N\n", "필수 컬럼"),
])
def test_input_errors(tmp_path, results, defects, msg):
    r, d = write(tmp_path, results, defects)
    with pytest.raises(InputError, match=msg):
        run(r, d)
    assert gate_main(["--results", str(r), "--defects", str(d)]) == 3
