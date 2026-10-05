"""대사 엔진.

[왜 차이가 생기나]
두 원장은 기록 기준 시각이 다르다.
  - 상세 로그: 건이 발생한 시각에 한 줄씩 기록 (스핀 1회, 승인 1건)
  - 집계 원장: 묶음이 마감되는 시각에 합계로 기록 (세션 종료, 정산 배치 마감)
같은 시간 창 [start, end) 로 두 쪽을 합산하면 창 경계에 걸친 묶음 때문에 차이가 난다.

[엔진이 하는 일]
원시 차이(raw_diff) = 집계 합계 - 상세 합계 를 아래처럼 쪼갠다.

  raw_diff = carried_in - carried_out          <- 경계 때문에 생기는 '설명되는 차이'
           + (묶음 합계 - 묶음에 속한 건 합계)    <- 묶음 불일치 (허용 오차 내 / 초과)
           - 마감 이후 기록                       <- 이미 마감된 묶음에 늦게 찍힌 건
           - 집계 누락                            <- 어느 묶음에도 속하지 않은 건

  carried_in : 이번 창에 마감된 묶음에 들어 있지만, 건 자체는 창 밖에서 발생한 금액
  carried_out: 이번 창에 발생했지만, 묶음이 아직 안 닫혔거나 창 이후에 마감되는 금액

경계 효과를 빼고 남는 차이(unexplained)는 반드시 아래 세 항목의 합과 같아야 한다.
엔진은 이 등식이 맞는지 스스로 확인한다(closure check). 안 맞으면 엔진이 뭔가를
놓치고 있다는 뜻이므로 결과를 믿으면 안 된다.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import sqlite3

from .scenarios import Scenario, Metric

TS_FMT = "%Y-%m-%d %H:%M:%S"


def ts(dt: datetime) -> str:
    return dt.strftime(TS_FMT)


# ---------------------------------------------------------------------------
# 결과 구조
# ---------------------------------------------------------------------------
@dataclass
class MetricResult:
    metric: Metric
    detail_total: int = 0      # 상세 로그를 발생 시각 기준으로 창 안에서 합산
    group_total: int = 0       # 집계 원장을 마감 시각 기준으로 창 안에서 합산
    carried_in: int = 0
    carried_out: int = 0
    mismatch_tolerated: int = 0   # 허용 오차 이내 묶음 불일치 합
    mismatch_flagged: int = 0     # 허용 오차 초과 묶음 불일치 합
    late_rows: int = 0            # 마감 이후 기록 금액
    orphan_rows: int = 0          # 집계 누락 금액

    @property
    def raw_diff(self) -> int:
        return self.group_total - self.detail_total

    @property
    def expected_diff(self) -> int:
        """창 경계 때문에 당연히 생기는 차이."""
        return self.carried_in - self.carried_out

    @property
    def unexplained(self) -> int:
        """경계 효과를 빼고도 남는 차이."""
        return self.raw_diff - self.expected_diff

    @property
    def attributed(self) -> int:
        """드릴다운으로 원인을 특정한 금액의 합."""
        return (self.mismatch_tolerated + self.mismatch_flagged
                - self.late_rows - self.orphan_rows)

    @property
    def closure_ok(self) -> bool:
        return self.unexplained == self.attributed


@dataclass
class Finding:
    kind: str          # mismatch / orphan / late_row / dup_key / time_inversion
    severity: str      # FAIL / WARN
    ref: str           # 묶음 ID 또는 건 ID
    detail: dict = field(default_factory=dict)
    note: str = ""


KIND_LABEL = {
    "mismatch": "합계 불일치",
    "orphan": "집계 누락",
    "late_row": "마감 이후 기록",
    "dup_key": "상세 로그 중복 적재",
    "time_inversion": "시간 역전 (묶음 기간 밖에 찍힌 건)",
}


@dataclass
class WindowResult:
    scenario: Scenario
    start: datetime
    end: datetime
    metrics: list
    findings: list
    groups_checked: int = 0
    rows_checked: int = 0

    @property
    def closure_ok(self) -> bool:
        return all(m.closure_ok for m in self.metrics)

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "FAIL")

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "WARN")

    @property
    def verdict(self) -> str:
        if not self.closure_ok:
            return "검증 불가"      # 엔진 자체 등식이 깨짐. 결과를 믿으면 안 된다.
        if self.fail_count:
            return "결함 후보"
        if self.warn_count:
            return "주의"
        return "정상"


# ---------------------------------------------------------------------------
# 엔진
# ---------------------------------------------------------------------------
class Reconciler:
    def __init__(self, conn: sqlite3.Connection, scenario: Scenario):
        self.conn = conn
        self.s = scenario

    # 합계 식을 metric 순서대로 만든다. 건수는 detail_col 이 "1" 이라 SUM(1) = COUNT
    def _sum_exprs(self, prefix: str, side: str) -> str:
        cols = []
        for m in self.s.metrics:
            col = m.detail_col if side == "detail" else m.group_col
            expr = col if col == "1" else f"{prefix}{col}"
            cols.append(f"COALESCE(SUM({expr}), 0)")
        return ", ".join(cols)

    def _one(self, sql: str, params: tuple) -> list:
        row = self.conn.execute(sql, params).fetchone()
        return [int(v) for v in row]

    def check(self, start: datetime, end: datetime) -> WindowResult:
        s = self.s
        a, b = ts(start), ts(end)
        D, G = s.detail_table, s.group_table

        # 1) 상세 로그: 발생 시각 기준 합계
        detail = self._one(
            f"SELECT {self._sum_exprs('', 'detail')} FROM {D} "
            f"WHERE {s.detail_time} >= ? AND {s.detail_time} < ?", (a, b))

        # 2) 집계 원장: 마감 시각 기준 합계
        group = self._one(
            f"SELECT {self._sum_exprs('', 'group')} FROM {G} "
            f"WHERE {s.group_close} >= ? AND {s.group_close} < ?", (a, b))

        # 3) carried_in: 이번 창에 마감된 묶음 안의, 창 밖에서 발생한 건
        cin = self._one(
            f"SELECT {self._sum_exprs('d.', 'detail')} FROM {D} d "
            f"JOIN {G} g ON d.{s.group_fk} = g.{s.group_key} "
            f"WHERE g.{s.group_close} >= ? AND g.{s.group_close} < ? "
            f"AND (d.{s.detail_time} < ? OR d.{s.detail_time} >= ?)", (a, b, a, b))

        # 4) carried_out: 이번 창에 발생했지만 묶음이 열려 있거나 창 이후에 마감되는 건
        cout = self._one(
            f"SELECT {self._sum_exprs('d.', 'detail')} FROM {D} d "
            f"JOIN {G} g ON d.{s.group_fk} = g.{s.group_key} "
            f"WHERE d.{s.detail_time} >= ? AND d.{s.detail_time} < ? "
            f"AND (g.{s.group_close} IS NULL OR g.{s.group_close} >= ?)", (a, b, b))

        metrics = [MetricResult(m, detail[i], group[i], cin[i], cout[i])
                   for i, m in enumerate(s.metrics)]
        findings: list = []

        groups_checked = self._check_group_mismatch(a, b, metrics, findings)
        self._check_orphans(a, b, metrics, findings)
        self._check_late_rows(a, b, metrics, findings)
        self._check_dup_keys(a, b, findings)
        self._check_time_inversion(a, b, findings)

        rows_checked = self.conn.execute(
            f"SELECT COUNT(*) FROM {D} WHERE {s.detail_time} >= ? AND {s.detail_time} < ?",
            (a, b)).fetchone()[0]

        return WindowResult(s, start, end, metrics, findings, groups_checked, rows_checked)

    # --- 드릴다운 ----------------------------------------------------------
    def _check_group_mismatch(self, a, b, metrics, findings) -> int:
        """이번 창에 마감된 묶음마다, 묶음 합계와 그 묶음에 속한 건 합계를 비교한다.
        이때 건은 발생 시각과 상관없이 전부 더한다(묶음 단위로 맞는지를 보는 것이므로)."""
        s = self.s
        sub_sums = ", ".join(
            f"SUM({'1' if m.detail_col == '1' else m.detail_col}) AS s{i}"
            for i, m in enumerate(s.metrics))
        g_cols = ", ".join(f"g.{m.group_col}" for m in s.metrics)
        s_cols = ", ".join(f"COALESCE(x.s{i}, 0)" for i in range(len(s.metrics)))
        sql = (
            f"SELECT g.{s.group_key}, COALESCE(x.n, 0), {g_cols}, {s_cols} "
            f"FROM {s.group_table} g "
            f"LEFT JOIN (SELECT {s.group_fk} AS k, COUNT(*) AS n, {sub_sums} "
            f"           FROM {s.detail_table} "
            f"           WHERE {s.group_fk} IN (SELECT {s.group_key} FROM {s.group_table} "
            f"                                  WHERE {s.group_close} >= ? AND {s.group_close} < ?) "
            f"           GROUP BY {s.group_fk}) x ON x.k = g.{s.group_key} "
            f"WHERE g.{s.group_close} >= ? AND g.{s.group_close} < ?")
        k = len(s.metrics)
        count = 0
        for row in self.conn.execute(sql, (a, b, a, b)):
            count += 1
            gid, n = row[0], int(row[1])
            g_vals = [int(v or 0) for v in row[2:2 + k]]
            d_vals = [int(v) for v in row[2 + k:2 + 2 * k]]
            bad = {}
            for i, m in enumerate(s.metrics):
                diff = g_vals[i] - d_vals[i]
                if diff == 0:
                    continue
                allowed = m.tolerance_per_row * n
                if abs(diff) <= allowed:
                    metrics[i].mismatch_tolerated += diff
                else:
                    metrics[i].mismatch_flagged += diff
                    bad[m.label] = {"집계": g_vals[i], "상세합": d_vals[i],
                                   "차이": diff, "허용": allowed}
            if bad:
                findings.append(Finding("mismatch", "FAIL", gid, bad,
                                        f"{s.group_unit_label} 합계가 소속 건 합계와 다름 (소속 건 {n:,}개)"))
        return count

    def _check_orphans(self, a, b, metrics, findings):
        """창 안에서 발생했는데 소속 묶음이 집계 원장에 아예 없는 건."""
        s = self.s
        cols = ", ".join(("1" if m.detail_col == "1" else f"d.{m.detail_col}") for m in s.metrics)
        sql = (f"SELECT d.{s.detail_key}, d.{s.group_fk}, d.{s.detail_time}, {cols} "
               f"FROM {s.detail_table} d LEFT JOIN {s.group_table} g "
               f"ON d.{s.group_fk} = g.{s.group_key} "
               f"WHERE d.{s.detail_time} >= ? AND d.{s.detail_time} < ? AND g.{s.group_key} IS NULL")
        by_group: dict = {}
        for row in self.conn.execute(sql, (a, b)):
            fk = row[1]
            vals = [int(v) for v in row[3:]]
            for i, v in enumerate(vals):
                metrics[i].orphan_rows += v
            agg = by_group.setdefault(fk, {"건수": 0, "금액": [0] * len(vals)})
            agg["건수"] += 1
            agg["금액"] = [x + y for x, y in zip(agg["금액"], vals)]
        for fk, agg in by_group.items():
            detail = {m.label: agg["금액"][i] for i, m in enumerate(s.metrics)}
            findings.append(Finding("orphan", "FAIL", str(fk), detail,
                                    f"소속 {s.group_unit_label} 없음, 누락 건 {agg['건수']}개"))

    def _check_late_rows(self, a, b, metrics, findings):
        """창 안에서 발생했는데, 소속 묶음은 이미 창 시작 전에 마감된 건.
        마감 후에 늦게 적재됐거나 시각이 잘못 찍힌 경우다."""
        s = self.s
        cols = ", ".join(("1" if m.detail_col == "1" else f"d.{m.detail_col}") for m in s.metrics)
        sql = (f"SELECT d.{s.detail_key}, d.{s.group_fk}, d.{s.detail_time}, g.{s.group_close}, {cols} "
               f"FROM {s.detail_table} d JOIN {s.group_table} g "
               f"ON d.{s.group_fk} = g.{s.group_key} "
               f"WHERE d.{s.detail_time} >= ? AND d.{s.detail_time} < ? AND g.{s.group_close} < ?")
        for row in self.conn.execute(sql, (a, b, a)):
            vals = [int(v) for v in row[4:]]
            for i, v in enumerate(vals):
                metrics[i].late_rows += v
            findings.append(Finding("late_row", "FAIL", str(row[0]),
                                    {"소속": row[1], "기록시각": row[2], "마감시각": row[3]},
                                    "이미 마감된 묶음에 늦게 기록됨"))

    def _check_dup_keys(self, a, b, findings):
        s = self.s
        sql = (f"SELECT {s.detail_key}, COUNT(*) FROM {s.detail_table} "
               f"WHERE {s.detail_time} >= ? AND {s.detail_time} < ? "
               f"GROUP BY {s.detail_key} HAVING COUNT(*) > 1")
        for key, n in self.conn.execute(sql, (a, b)):
            findings.append(Finding("dup_key", "FAIL", str(key), {"적재 횟수": n},
                                    "같은 건 ID가 여러 번 적재됨"))

    def _check_time_inversion(self, a, b, findings):
        """금액에는 영향이 없을 수 있지만 데이터 품질 문제. 묶음 기간 밖에 찍힌 건."""
        s = self.s
        sql = (f"SELECT d.{s.detail_key}, d.{s.group_fk}, d.{s.detail_time}, "
               f"g.{s.group_open}, g.{s.group_close} "
               f"FROM {s.detail_table} d JOIN {s.group_table} g "
               f"ON d.{s.group_fk} = g.{s.group_key} "
               f"WHERE d.{s.detail_time} >= ? AND d.{s.detail_time} < ? "
               f"AND (d.{s.detail_time} < g.{s.group_open} "
               f"     OR (g.{s.group_close} IS NOT NULL AND d.{s.detail_time} > g.{s.group_close}))")
        late_refs = {f.ref for f in findings if f.kind == "late_row"}
        for row in self.conn.execute(sql, (a, b)):
            if str(row[0]) in late_refs:
                continue  # 이미 '마감 이후 기록'으로 잡힌 건은 중복 보고하지 않는다
            findings.append(Finding("time_inversion", "WARN", str(row[0]),
                                    {"소속": row[1], "기록시각": row[2],
                                     "시작": row[3], "마감": row[4]},
                                    "금액 합계에는 영향 없음, 시각 기록 점검 필요"))


def split_windows(start: datetime, end: datetime, step: timedelta):
    t = start
    while t < end:
        nxt = min(t + step, end)
        yield t, nxt
        t = nxt


def sweep(conn, scenario, start, end, step):
    """구간을 일정 크기 창으로 잘라 차례로 대사한다."""
    r = Reconciler(conn, scenario)
    return [r.check(a, b) for a, b in split_windows(start, end, step)]
