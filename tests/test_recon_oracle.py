"""손으로 계산한 정답과 엔진 결과를 비교하는 테스트.

생성기로 만든 데이터만 쓰면 생성기와 엔진이 같은 실수를 할 때 못 잡는다.
그래서 결과를 손으로 계산할 수 있을 만큼 작은 데이터를 직접 만들어 정확한 숫자로 확인한다.

데이터 (분석 창 10:00 ~ 13:00)
  S1  09:00 시작, 10:30 마감  스핀 09:10(100) 10:10(200)   -> 창 안 마감, 09:10 건은 창 밖  carried_in 100
  S2  11:00 시작, 11:30 마감  스핀 11:05(50)  11:20(50)    -> 완전히 창 안
  S3  12:30 시작, 13:30 마감  스핀 12:40(70)  13:10(30)    -> 창 이후 마감                carried_out 70
  S4  12:50 시작, 진행 중      스핀 12:55(40)               -> 열린 세션                   carried_out 40
  상세 합계 = 200 + 100 + 70 + 40 = 410
  집계 합계 = 300 + 100 = 400
  원시 차이 = -10, 경계 효과 = 100 - 110 = -10, 미설명 = 0
"""
from datetime import datetime
import pytest

from recon.generate import connect
from recon.engine import Reconciler
from recon.scenarios import GAME

W0, W1 = datetime(2026, 3, 2, 10), datetime(2026, 3, 2, 13)
D = "2026-03-02 "


@pytest.fixture
def conn():
    c = connect()
    plays = [
        ("P1", "S1", "U1", D + "09:10:00", 100, 0),
        ("P2", "S1", "U1", D + "10:10:00", 200, 400),
        ("P3", "S2", "U2", D + "11:05:00", 50, 0),
        ("P4", "S2", "U2", D + "11:20:00", 50, 100),
        ("P5", "S3", "U3", D + "12:40:00", 70, 0),
        ("P6", "S3", "U3", D + "13:10:00", 30, 0),
        ("P7", "S4", "U4", D + "12:55:00", 40, 0),
    ]
    sessions = [
        ("S1", "U1", D + "09:00:00", D + "10:30:00", 300, 400, 2),
        ("S2", "U2", D + "11:00:00", D + "11:30:00", 100, 100, 2),
        ("S3", "U3", D + "12:30:00", D + "13:30:00", 100, 0, 2),
        ("S4", "U4", D + "12:50:00", None, None, None, None),
    ]
    c.executemany("INSERT INTO play_log VALUES (?,?,?,?,?,?)", plays)
    c.executemany("INSERT INTO session_log VALUES (?,?,?,?,?,?,?)", sessions)
    return c


def bet(r):
    return r.metrics[0]


def test_clean_numbers_exact(conn):
    r = Reconciler(conn, GAME).check(W0, W1)
    m = bet(r)
    assert (m.detail_total, m.group_total) == (410, 400)
    assert (m.carried_in, m.carried_out) == (100, 110)
    assert (m.raw_diff, m.expected_diff, m.unexplained) == (-10, -10, 0)
    assert r.verdict == "정상" and r.closure_ok and r.findings == []


def test_count_metric_exact(conn):
    spins = Reconciler(conn, GAME).check(W0, W1).metrics[2]
    # 상세 5건(P2,P3,P4,P5,P7), 집계 4회(S1 2 + S2 2), carried_in 1(P1), carried_out 2(P5,P7)
    assert (spins.detail_total, spins.group_total, spins.carried_in, spins.carried_out) == (5, 4, 1, 2)
    assert spins.unexplained == 0


def test_duplicate_payout(conn):
    conn.execute("UPDATE session_log SET total_win = total_win + 100 WHERE session_id='S2'")
    r = Reconciler(conn, GAME).check(W0, W1)
    win = r.metrics[1]
    assert win.unexplained == 100 and win.mismatch_flagged == 100
    assert [(f.kind, f.ref) for f in r.findings] == [("mismatch", "S2")]


def test_lost_log(conn):
    conn.execute("DELETE FROM play_log WHERE play_id='P4'")
    r = Reconciler(conn, GAME).check(W0, W1)
    assert bet(r).unexplained == 50
    assert r.metrics[2].unexplained == 1
    assert [(f.kind, f.ref) for f in r.findings] == [("mismatch", "S2")]


def test_duplicate_row(conn):
    conn.execute("INSERT INTO play_log VALUES ('P3','S2','U2','2026-03-02 11:05:00',50,0)")
    r = Reconciler(conn, GAME).check(W0, W1)
    assert bet(r).unexplained == -50
    assert sorted((f.kind, f.ref) for f in r.findings) == [("dup_key", "P3"), ("mismatch", "S2")]


def test_orphan_rows(conn):
    conn.execute("DELETE FROM session_log WHERE session_id='S2'")
    r = Reconciler(conn, GAME).check(W0, W1)
    m = bet(r)
    assert m.orphan_rows == 100 and m.unexplained == -100 and r.closure_ok
    assert [(f.kind, f.ref) for f in r.findings] == [("orphan", "S2")]


def test_late_row(conn):
    # 09:50 에 마감된 세션 S0 를 추가하고, 그 스핀 하나를 창 안 시각으로 옮긴다
    conn.execute("INSERT INTO session_log VALUES ('S0','U9','2026-03-02 09:00:00','2026-03-02 09:50:00',10,0,1)")
    conn.execute("INSERT INTO play_log VALUES ('P0','S0','U9','2026-03-02 10:20:00',10,0)")
    r = Reconciler(conn, GAME).check(W0, W1)
    m = bet(r)
    assert m.late_rows == 10 and m.unexplained == -10 and r.closure_ok
    kinds = [(f.kind, f.ref) for f in r.findings]
    assert kinds == [("late_row", "P0")]          # 시간 역전으로 중복 보고되지 않아야 한다


def test_time_inversion_is_warn_when_amount_neutral(conn):
    conn.execute("UPDATE play_log SET played_at='2026-03-02 11:35:00' WHERE play_id='P4'")
    r = Reconciler(conn, GAME).check(W0, W1)
    assert bet(r).unexplained == 0
    assert r.verdict == "주의"
    assert [(f.kind, f.severity, f.ref) for f in r.findings] == [("time_inversion", "WARN", "P4")]


def test_time_inversion_crossing_window_edge_becomes_late_row(conn):
    """창을 잘게 나누면 같은 시간 역전 건이 '마감 이후 기록'으로 잡힌다.
    S2 는 11:30 마감인데 P4 가 11:35 로 찍힘. 창을 11:32 에서 자르면
    앞 창에서는 경계 효과로 설명되고, 뒤 창에서는 이미 마감된 세션에 늦게 들어온 금액이 된다."""
    conn.execute("UPDATE play_log SET played_at='2026-03-02 11:35:00' WHERE play_id='P4'")
    cut = datetime(2026, 3, 2, 11, 32)
    r1 = Reconciler(conn, GAME).check(W0, cut)
    r2 = Reconciler(conn, GAME).check(cut, W1)
    assert bet(r1).unexplained == 0 and r1.verdict == "정상"
    assert bet(r2).unexplained == -50 and [(f.kind, f.ref) for f in r2.findings] == [("late_row", "P4")]
    assert r1.closure_ok and r2.closure_ok


def test_empty_window(conn):
    r = Reconciler(conn, GAME).check(datetime(2026, 3, 3), datetime(2026, 3, 3, 1))
    assert r.verdict == "정상" and r.rows_checked == 0 and all(m.detail_total == 0 for m in r.metrics)
