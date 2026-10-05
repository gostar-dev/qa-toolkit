"""생성기로 만든 큰 데이터로 확인하는 테스트.

1) 정상 데이터에서는 어떤 창 크기로 잘라도 결함 후보가 0건이어야 한다(오탐 없음).
2) 심은 결함은 전부 잡아야 하고, 심지 않은 것은 잡으면 안 된다(정답지와 정확히 일치).
3) 결함이 있든 없든 엔진 자기 점검 등식은 항상 맞아야 한다.
"""
from datetime import datetime, timedelta
import pytest

from recon.generate import (connect, generate_game, inject_game_defects,
                            generate_payment, inject_payment_defects)
from recon.engine import Reconciler, sweep
from recon.scenarios import GAME, PAYMENT

W0, W1 = datetime(2026, 3, 2), datetime(2026, 3, 5)
STEPS = [timedelta(hours=1), timedelta(hours=3), timedelta(days=1), timedelta(days=3)]


@pytest.fixture(scope="module")
def clean_game():
    c = connect()
    generate_game(c, seed=101, n_users=80)
    return c


@pytest.fixture(scope="module")
def clean_payment():
    c = connect()
    generate_payment(c, seed=202, n_merchants=12)
    return c


@pytest.mark.parametrize("step", STEPS, ids=["1h", "3h", "1d", "3d"])
def test_clean_game_no_false_positive(clean_game, step):
    for r in sweep(clean_game, GAME, W0, W1, step):
        assert r.closure_ok
        assert r.findings == [], (r.start, [(f.kind, f.ref) for f in r.findings])
        assert all(m.unexplained == 0 for m in r.metrics)


def test_small_windows_have_boundary_noise(clean_game):
    """경계 효과를 빼지 않으면 정상 데이터도 차이가 난다는 것을 확인 (이 도구가 필요한 이유)."""
    res = sweep(clean_game, GAME, W0, W1, timedelta(hours=3))
    noisy = [r for r in res if r.metrics[0].raw_diff != 0]
    assert len(noisy) > len(res) // 2
    assert all(r.metrics[0].unexplained == 0 for r in res)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_game_defects_found_exactly(seed):
    c = connect()
    generate_game(c, seed=seed * 13, n_users=80)
    truth = inject_game_defects(c, W0, W1, seed=seed)
    r = Reconciler(c, GAME).check(W0, W1)
    found = {(f.kind, f.ref) for f in r.findings}
    expected = {(i.kind, i.ref) for i in truth}
    assert found == expected, f"놓친 것 {expected - found}, 오탐 {found - expected}"
    assert r.closure_ok


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("step", STEPS[:3], ids=["1h", "3h", "1d"])
def test_closure_identity_always_holds(seed, step):
    c = connect()
    generate_game(c, seed=seed * 7, n_users=60)
    inject_game_defects(c, W0, W1, seed=seed)
    for r in sweep(c, GAME, W0, W1, step):
        for m in r.metrics:
            assert m.unexplained == m.attributed, (r.start, m.metric.name)


def test_payment_clean_rounding_is_tolerated(clean_payment):
    res = sweep(clean_payment, PAYMENT, W0, W1, timedelta(days=1))
    for r in res:
        assert r.verdict == "정상" and r.closure_ok
        fee = r.metrics[1]
        assert fee.mismatch_tolerated > 0           # 절사 차이는 실제로 존재하고
        assert fee.mismatch_flagged == 0            # 결함으로 잡히지 않아야 한다
        assert r.metrics[0].unexplained == 0        # 금액은 원 단위까지 정확히 맞아야 한다


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_payment_defects_found_exactly(seed):
    c = connect()
    generate_payment(c, seed=seed * 17, n_merchants=12)
    truth = inject_payment_defects(c, W0, W1, seed=seed)
    found = set()
    for r in sweep(c, PAYMENT, W0, W1, timedelta(days=1)):
        assert r.closure_ok
        found |= {(f.kind, f.ref) for f in r.findings}
    expected = {(i.kind, i.ref) for i in truth}
    assert found == expected, f"놓친 것 {expected - found}, 오탐 {found - expected}"


def test_fee_error_threshold():
    """허용 오차 경계값: 허용치 이내는 통과, 1원이라도 넘으면 결함."""
    c = connect()
    generate_payment(c, seed=99, n_merchants=3)
    bid, n = c.execute("SELECT batch_id, tx_count FROM settlement_batch WHERE closed_at >= '2026-03-03' "
                       "AND closed_at < '2026-03-04' ORDER BY batch_id LIMIT 1").fetchone()
    cur = c.execute("SELECT b.settle_fee - SUM(a.fee) FROM settlement_batch b JOIN approval_log a "
                    "ON a.batch_id=b.batch_id WHERE b.batch_id=?", (bid,)).fetchone()[0]
    allowed = n  # 건당 1원
    day = (datetime(2026, 3, 3), datetime(2026, 3, 4))

    c.execute("UPDATE settlement_batch SET settle_fee = settle_fee + ? WHERE batch_id=?", (allowed - cur, bid))
    assert Reconciler(c, PAYMENT).check(*day).verdict == "정상"

    c.execute("UPDATE settlement_batch SET settle_fee = settle_fee + 1 WHERE batch_id=?", (bid,))
    r = Reconciler(c, PAYMENT).check(*day)
    assert [(f.kind, f.ref) for f in r.findings] == [("mismatch", bid)]
