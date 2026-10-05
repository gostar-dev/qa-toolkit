"""시나리오 정의.

엔진은 테이블 이름이나 컬럼 이름을 직접 알지 않는다.
여기 적힌 매핑만 보고 SQL을 만든다. 그래서 게임 재화와 결제 정산처럼
도메인이 달라도 '건 단위 기록 vs 묶음 단위 합계'라는 구조만 같으면
같은 엔진으로 대사할 수 있다.

주의: 여기 적힌 이름은 SQL에 그대로 들어간다(식별자는 파라미터 바인딩이 안 되기 때문).
그래서 이 값들은 코드에 고정된 설정에서만 오고, 사용자 입력을 받지 않는다.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Metric:
    name: str               # 내부 이름 (영문)
    label: str              # 리포트 표시 이름
    detail_col: str         # 상세 로그 쪽 컬럼. 건수를 셀 때는 "1"
    group_col: str          # 집계 원장 쪽 컬럼
    tolerance_per_row: int = 0  # 묶음 하나에서 건당 허용할 오차 (예: 수수료 절사 정책 차이)
    unit: str = "원"


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    detail_table: str       # 건 단위 기록 테이블
    detail_key: str         # 건 식별자
    detail_time: str        # 건이 발생한 시각
    group_fk: str           # 건이 속한 묶음 ID
    group_table: str        # 묶음 단위 합계 테이블
    group_key: str          # 묶음 식별자
    group_open: str         # 묶음 시작 시각
    group_close: str        # 묶음 마감 시각 (합계가 기록되는 기준 시각, 아직 열려 있으면 NULL)
    detail_label: str = "상세 로그"
    group_label: str = "집계 원장"
    group_unit_label: str = "묶음"
    metrics: tuple = field(default_factory=tuple)


GAME = Scenario(
    name="game",
    title="게임 재화 대사 (스핀 로그 vs 세션 집계)",
    detail_table="play_log",
    detail_key="play_id",
    detail_time="played_at",
    group_fk="session_id",
    group_table="session_log",
    group_key="session_id",
    group_open="opened_at",
    group_close="closed_at",
    detail_label="스핀 로그(play_log)",
    group_label="세션 집계(session_log)",
    group_unit_label="세션",
    metrics=(
        Metric("bet", "베팅 합계", "bet_amount", "total_bet"),
        Metric("win", "지급 합계", "win_amount", "total_win"),
        Metric("spins", "스핀 수", "1", "spin_count", unit="회"),
    ),
)

PAYMENT = Scenario(
    name="payment",
    title="결제 대사 (승인 내역 vs 정산 배치)",
    detail_table="approval_log",
    detail_key="tx_id",
    detail_time="approved_at",
    group_fk="batch_id",
    group_table="settlement_batch",
    group_key="batch_id",
    group_open="opened_at",
    group_close="closed_at",
    detail_label="승인 내역(approval_log)",
    group_label="정산 배치(settlement_batch)",
    group_unit_label="배치",
    metrics=(
        Metric("amount", "거래 금액", "amount", "settle_amount"),
        # 건별 수수료는 건마다 원 단위 절사, 배치 수수료는 합계에서 한 번 절사한다.
        # 그래서 정상이어도 배치당 최대 (건수 - 1)원 차이가 난다. 이건 결함이 아니라 정책 차이다.
        Metric("fee", "수수료", "fee", "settle_fee", tolerance_per_row=1),
        Metric("count", "거래 건수", "1", "tx_count", unit="건"),
    ),
)

SCENARIOS = {s.name: s for s in (GAME, PAYMENT)}
