"""가짜 데이터 생성기와 결함 주입기.

실제 회사 데이터는 쓰지 않는다. 테이블 구조와 기록 방식만 같은 데이터를 새로 만든다.
결함 주입 함수는 '무엇을 어디에 심었는지(정답지)'를 돌려준다. 테스트에서는 엔진이
이 정답지를 빠짐없이, 그리고 그 외에는 아무것도 잡지 않는지(오탐 없음) 확인한다.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import random
import sqlite3

from .engine import ts

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def connect(path=":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


@dataclass
class Injected:
    kind: str     # 엔진의 Finding.kind 와 같은 이름
    ref: str      # 엔진이 보고해야 하는 ID (묶음 ID 또는 건 ID)
    note: str


# ---------------------------------------------------------------------------
# 게임 재화
# ---------------------------------------------------------------------------
BET_LEVELS = [1_000, 5_000, 10_000, 50_000, 100_000]
BET_WEIGHTS = [30, 30, 25, 10, 5]
# 배수와 확률. 기대값 약 0.91 (임의로 정한 분포, 실제 게임 수치 아님)
PAYOUTS = [(0, 0.66), (0.5, 0.10), (1, 0.10), (2, 0.08), (5, 0.04), (10, 0.015), (50, 0.005)]


def _win(rng, bet):
    r, acc = rng.random(), 0.0
    for mult, p in PAYOUTS:
        acc += p
        if r < acc:
            return int(bet * mult)
    return 0


def generate_game(conn, seed=7, n_users=200,
                  data_start=datetime(2026, 3, 1, 12), now=datetime(2026, 3, 5, 12)):
    """유저마다 세션을 여러 번 만들고, 세션 안에서 스핀을 일정 간격으로 발생시킨다.
    now 시점에 아직 안 끝난 세션은 열린 세션(closed_at NULL)으로 남긴다."""
    rng = random.Random(seed)
    plays, sessions = [], []
    pid = sid = 0
    for u in range(n_users):
        user = f"U{u:05d}"
        t = data_start + timedelta(minutes=rng.randint(0, 360))
        while t < now:
            sid += 1
            session = f"S{sid:06d}"
            bet = rng.choices(BET_LEVELS, BET_WEIGHTS)[0]
            length = timedelta(minutes=rng.randint(5, 120))
            opened, cur = t, t
            tb = tw = n = 0
            while cur < opened + length and cur < now:
                pid += 1
                w = _win(rng, bet)
                plays.append((f"P{pid:08d}", session, user, ts(cur), bet, w))
                tb, tw, n = tb + bet, tw + w, n + 1
                cur += timedelta(seconds=rng.randint(20, 90))
            close = cur + timedelta(seconds=rng.randint(1, 30))
            if close <= now and cur < now:
                sessions.append((session, user, ts(opened), ts(close), tb, tw, n))
            else:
                sessions.append((session, user, ts(opened), None, None, None, None))
            t = close + timedelta(minutes=rng.randint(30, 600))
    conn.executemany("INSERT INTO play_log VALUES (?,?,?,?,?,?)", plays)
    conn.executemany("INSERT INTO session_log VALUES (?,?,?,?,?,?,?)", sessions)
    conn.commit()
    return len(plays), len(sessions)


def _sessions_closed_in(conn, start, end, min_rows=3):
    rows = conn.execute(
        "SELECT s.session_id, s.closed_at FROM session_log s "
        "WHERE s.closed_at >= ? AND s.closed_at < ? "
        "AND (SELECT COUNT(*) FROM play_log p WHERE p.session_id = s.session_id) >= ? "
        "ORDER BY s.session_id", (ts(start), ts(end), min_rows)).fetchall()
    return rows


def inject_game_defects(conn, start, end, seed=11, counts=None):
    """분석 창 [start, end) 안에 결함을 심는다. 결함끼리 같은 세션을 건드리지 않게 한다."""
    counts = counts or {"dup_payout": 2, "lost_log": 2, "dup_log": 1, "orphan": 1,
                        "count_error": 1, "late_row": 1, "time_inversion": 1}
    rng = random.Random(seed)
    # 경계 근처 세션은 피한다 (창 시작 후 6시간 ~ 창 끝 6시간 전에 마감된 세션만)
    pool = _sessions_closed_in(conn, start + timedelta(hours=6), end - timedelta(hours=6))
    rng.shuffle(pool)
    used = iter(pool)
    out = []

    for _ in range(counts.get("dup_payout", 0)):
        sid, _c = next(used)
        w = conn.execute("SELECT MAX(win_amount) FROM play_log WHERE session_id=?", (sid,)).fetchone()[0] or 1000
        w = w or 1000
        conn.execute("UPDATE session_log SET total_win = total_win + ? WHERE session_id=?", (w, sid))
        out.append(Injected("mismatch", sid, f"재화 중복 지급: 세션 지급 합계에 {w:,}원 이중 반영"))

    for _ in range(counts.get("lost_log", 0)):
        sid, _c = next(used)
        pid = conn.execute("SELECT play_id FROM play_log WHERE session_id=? ORDER BY played_at LIMIT 1 OFFSET 1",
                           (sid,)).fetchone()[0]
        conn.execute("DELETE FROM play_log WHERE play_id=?", (pid,))
        out.append(Injected("mismatch", sid, f"스핀 로그 누락: {pid} 1건이 적재되지 않음"))

    for _ in range(counts.get("dup_log", 0)):
        sid, _c = next(used)
        row = conn.execute("SELECT * FROM play_log WHERE session_id=? ORDER BY played_at LIMIT 1 OFFSET 1",
                           (sid,)).fetchone()
        conn.execute("INSERT INTO play_log VALUES (?,?,?,?,?,?)", row)
        out.append(Injected("mismatch", sid, f"로그 중복 적재: {row[0]} 가 두 번 들어감"))
        out.append(Injected("dup_key", row[0], "로그 중복 적재"))

    for _ in range(counts.get("orphan", 0)):
        sid, _c = next(used)
        conn.execute("DELETE FROM session_log WHERE session_id=?", (sid,))
        out.append(Injected("orphan", sid, "세션 집계 누락: 세션 행 자체가 기록되지 않음"))

    for _ in range(counts.get("count_error", 0)):
        sid, _c = next(used)
        conn.execute("UPDATE session_log SET spin_count = spin_count + 1 WHERE session_id=?", (sid,))
        out.append(Injected("mismatch", sid, "집계 건수 오류: 스핀 수가 1 많게 집계됨"))

    for _ in range(counts.get("late_row", 0)):
        # 창 시작 '전'에 마감된 세션의 스핀 1건을 창 안 시각으로 옮긴다 = 마감 후 지연 적재
        sid, close = conn.execute(
            "SELECT s.session_id, s.closed_at FROM session_log s WHERE s.closed_at >= ? AND s.closed_at < ? "
            "AND (SELECT COUNT(*) FROM play_log p WHERE p.session_id=s.session_id) >= 3 "
            "ORDER BY s.closed_at LIMIT 1",
            (ts(start - timedelta(hours=6)), ts(start - timedelta(hours=1)))).fetchone()
        pid = conn.execute("SELECT play_id FROM play_log WHERE session_id=? ORDER BY played_at DESC LIMIT 1",
                           (sid,)).fetchone()[0]
        new_t = ts(start + timedelta(minutes=rng.randint(10, 120)))
        conn.execute("UPDATE play_log SET played_at=? WHERE play_id=?", (new_t, pid))
        out.append(Injected("late_row", pid, f"마감 이후 기록: {sid} 마감({close}) 뒤 {new_t} 로 찍힘"))

    for _ in range(counts.get("time_inversion", 0)):
        sid, close = next(used)
        pid = conn.execute("SELECT play_id FROM play_log WHERE session_id=? ORDER BY played_at DESC LIMIT 1",
                           (sid,)).fetchone()[0]
        new_t = ts(datetime.strptime(close, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=5))
        conn.execute("UPDATE play_log SET played_at=? WHERE play_id=?", (new_t, pid))
        out.append(Injected("time_inversion", pid, "세션 마감 5분 뒤 시각으로 찍힘 (금액 영향 없음)"))

    conn.commit()
    return out


# ---------------------------------------------------------------------------
# 결제 승인 vs 정산 배치
# ---------------------------------------------------------------------------
CUTOFF_HOUR = 23
HOURLY = [1, 1, 0, 0, 0, 0, 1, 2, 4, 5, 6, 7, 9, 8, 6, 6, 7, 8, 9, 10, 9, 7, 5, 3]  # 시간대별 상대 거래량


def fee_for(amount, rate_bp):
    """건별 수수료. rate_bp 는 1만분율(예: 250 = 2.5%). 원 단위 절사, 취소는 같은 규칙으로 음수."""
    sign = -1 if amount < 0 else 1
    return sign * (abs(amount) * rate_bp // 10_000)


def batch_fee(total, rate_bp):
    """배치 수수료. 합계에 수수료율을 곱하고 한 번만 절사한다."""
    sign = -1 if total < 0 else 1
    return sign * (abs(total) * rate_bp // 10_000)


def _amount(rng):
    # 소액이 많고 고액이 드문 분포. 100원 단위
    base = rng.choice([5_000, 9_900, 12_000, 15_000, 23_000, 39_000, 59_000, 120_000, 350_000])
    return max(1_000, int(base * rng.uniform(0.6, 1.6)) // 100 * 100)


def generate_payment(conn, seed=21, n_merchants=30,
                     first_day=datetime(2026, 3, 1), days=4, now=datetime(2026, 3, 5, 12)):
    """가맹점마다 하루 1개 배치. 배치 기간은 전날 23:00 ~ 당일 23:00.
    마지막 배치는 now 시점에 아직 마감 전이라 열린 배치로 남긴다."""
    rng = random.Random(seed)
    rows, batches = [], []
    tid = 0
    for m in range(n_merchants):
        mid = f"M{m:03d}"
        rate = rng.choice([180, 220, 250, 280, 330])
        scale = rng.uniform(0.5, 2.5)
        for d in range(days + 1):
            day = first_day + timedelta(days=d)
            opened = day - timedelta(days=1) + timedelta(hours=CUTOFF_HOUR)
            closed = day + timedelta(hours=CUTOFF_HOUR)
            bid = f"B{day:%m%d}-{mid}"
            total = cnt = fsum = 0
            t = opened
            while t < closed and t < now:
                per_hour = HOURLY[t.hour] * scale
                k = int(per_hour) + (1 if rng.random() < per_hour - int(per_hour) else 0)
                for _ in range(k):
                    at = t + timedelta(seconds=rng.randint(0, 3599))
                    if at >= closed or at >= now:
                        continue
                    tid += 1
                    amt = _amount(rng)
                    # 3% 확률로 같은 배치 안에서 취소 발생 (음수 행)
                    rows.append((f"T{tid:08d}", bid, mid, ts(at), amt, fee_for(amt, rate)))
                    total, cnt = total + amt, cnt + 1
                    if rng.random() < 0.03:
                        cat = at + timedelta(minutes=rng.randint(1, 50))
                        if cat < closed and cat < now:
                            tid += 1
                            rows.append((f"T{tid:08d}", bid, mid, ts(cat), -amt, fee_for(-amt, rate)))
                            total, cnt = total - amt, cnt + 1
                t += timedelta(hours=1)
            if closed <= now:
                batches.append((bid, mid, ts(opened), ts(closed), total, batch_fee(total, rate), cnt))
            else:
                batches.append((bid, mid, ts(opened), None, None, None, None))
    conn.executemany("INSERT INTO approval_log VALUES (?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO settlement_batch VALUES (?,?,?,?,?,?,?)", batches)
    conn.commit()
    return len(rows), len(batches)


def inject_payment_defects(conn, start, end, seed=31, counts=None):
    counts = counts or {"dup_capture": 1, "missing_capture": 1, "cancel_not_reflected": 1,
                        "orphan": 1, "fee_error": 1}
    rng = random.Random(seed)
    pool = conn.execute(
        "SELECT batch_id, merchant_id, tx_count FROM settlement_batch "
        "WHERE closed_at >= ? AND closed_at < ? AND tx_count >= 5 ORDER BY batch_id",
        (ts(start), ts(end))).fetchall()
    rng.shuffle(pool)
    used = iter(pool)
    out = []

    def rate_of(mid):
        # 배치 수수료와 건별 수수료로부터 역산하지 않고, 승인 행 하나로 추정한다 (테스트용)
        a, f = conn.execute("SELECT amount, fee FROM approval_log WHERE merchant_id=? "
                            "ORDER BY amount DESC LIMIT 1", (mid,)).fetchone()
        return round(f * 10_000 / a)

    for _ in range(counts.get("dup_capture", 0)):
        bid, mid, n = next(used)
        amt, fee = conn.execute("SELECT amount, fee FROM approval_log WHERE batch_id=? AND amount>0 LIMIT 1",
                                (bid,)).fetchone()
        conn.execute("UPDATE settlement_batch SET settle_amount=settle_amount+?, settle_fee=settle_fee+?, "
                     "tx_count=tx_count+1 WHERE batch_id=?", (amt, fee, bid))
        out.append(Injected("mismatch", bid, f"중복 매입: {amt:,}원 거래가 배치에 두 번 반영"))

    for _ in range(counts.get("missing_capture", 0)):
        bid, mid, n = next(used)
        amt, fee = conn.execute("SELECT amount, fee FROM approval_log WHERE batch_id=? AND amount>0 LIMIT 1 OFFSET 1",
                                (bid,)).fetchone()
        conn.execute("UPDATE settlement_batch SET settle_amount=settle_amount-?, settle_fee=settle_fee-?, "
                     "tx_count=tx_count-1 WHERE batch_id=?", (amt, fee, bid))
        out.append(Injected("mismatch", bid, f"매입 누락: {amt:,}원 거래가 배치에서 빠짐"))

    for _ in range(counts.get("cancel_not_reflected", 0)):
        bid, mid, n = next(used)
        tx, amt, at = conn.execute("SELECT tx_id, amount, approved_at FROM approval_log "
                                   "WHERE batch_id=? AND amount>0 ORDER BY approved_at LIMIT 1", (bid,)).fetchone()
        r = rate_of(mid)
        cat = ts(datetime.strptime(at, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=3))
        conn.execute("INSERT INTO approval_log VALUES (?,?,?,?,?,?)",
                     (tx + "-C", bid, mid, cat, -amt, fee_for(-amt, r)))
        out.append(Injected("mismatch", bid, f"취소 미반영: {tx} 취소({amt:,}원)가 배치 합계에 반영 안 됨"))

    for _ in range(counts.get("orphan", 0)):
        bid, mid, n = next(used)
        conn.execute("DELETE FROM settlement_batch WHERE batch_id=?", (bid,))
        out.append(Injected("orphan", bid, "배치 생성 누락: 해당 가맹점 당일 배치가 없음"))

    for _ in range(counts.get("fee_error", 0)):
        bid, mid, n = next(used)
        delta = n + 500   # 절사 허용 오차(건수 x 1원)를 확실히 넘는 금액
        conn.execute("UPDATE settlement_batch SET settle_fee=settle_fee+? WHERE batch_id=?", (delta, bid))
        out.append(Injected("mismatch", bid, f"수수료 계산 오류: 배치 수수료가 {delta:,}원 과다"))

    conn.commit()
    return out
