-- 게임 재화 시나리오 ---------------------------------------------------------
-- 스핀 1회 = 1행. 스핀이 일어난 시각(played_at)에 기록된다.
-- play_id 에 PRIMARY KEY 를 일부러 걸지 않았다. 실제 로그 적재 환경에서도
-- 중복 적재가 일어날 수 있고, 그걸 잡아내는 것도 이 도구의 역할이기 때문이다.
CREATE TABLE IF NOT EXISTS play_log (
    play_id     TEXT NOT NULL,
    session_id  TEXT,
    user_id     TEXT NOT NULL,
    played_at   TEXT NOT NULL,        -- 'YYYY-MM-DD HH:MM:SS'
    bet_amount  INTEGER NOT NULL,
    win_amount  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_play_time    ON play_log(played_at);
CREATE INDEX IF NOT EXISTS ix_play_session ON play_log(session_id);

-- 세션 1개 = 1행. 세션이 끝나는 시각(closed_at)에 합계로 기록된다.
-- 아직 진행 중인 세션은 closed_at 과 합계가 NULL 이다.
CREATE TABLE IF NOT EXISTS session_log (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    opened_at   TEXT NOT NULL,
    closed_at   TEXT,
    total_bet   INTEGER,
    total_win   INTEGER,
    spin_count  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_session_close ON session_log(closed_at);

-- 결제 시나리오 ---------------------------------------------------------------
-- 승인 1건 = 1행. 승인 시각(approved_at)에 기록된다. 취소는 음수 금액 행으로 들어온다.
CREATE TABLE IF NOT EXISTS approval_log (
    tx_id        TEXT NOT NULL,
    batch_id     TEXT,
    merchant_id  TEXT NOT NULL,
    approved_at  TEXT NOT NULL,
    amount       INTEGER NOT NULL,
    fee          INTEGER NOT NULL      -- 건별 수수료 (원 단위 절사)
);
CREATE INDEX IF NOT EXISTS ix_appr_time  ON approval_log(approved_at);
CREATE INDEX IF NOT EXISTS ix_appr_batch ON approval_log(batch_id);

-- 가맹점별 하루 1개 정산 배치. 매일 23:00 컷오프에 마감된다.
-- 즉 23:00~24:00 승인 건은 다음 날 배치로 넘어간다.
CREATE TABLE IF NOT EXISTS settlement_batch (
    batch_id      TEXT PRIMARY KEY,
    merchant_id   TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    closed_at     TEXT,
    settle_amount INTEGER,
    settle_fee    INTEGER,           -- 배치 합계 금액에 수수료율을 곱한 뒤 한 번 절사
    tx_count      INTEGER
);
CREATE INDEX IF NOT EXISTS ix_batch_close ON settlement_batch(closed_at);
