"""接入监控领域服务：按班次核对落盘范围，把两班实收与未齐区段关联成明确的待补捕获范围。

核心口径：
- 班次网格按规则版本（采样频率）生成应收点，实收以独立核对后的观测为准并按（测点, 时刻）去重；
- 已接收的连续区间原样保留，后到的完整区间不得掩盖旧班未确认缺口；
- 补采申请打开时冻结当时缺口快照，发出时再扣减其间已核实收，已确认点不得重复计入派发；
- 派发台账留存每次实际派发的捕获范围，领取前做重叠校验，防止重复领取；
- 安全接收窗口内仍缺的时刻归为「边缘等待区间」，超出窗口才是确认缺口。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Callable

from app.services.base import BaseModuleService
from app.store import store


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _format_ts(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


class IngestMonitorService(BaseModuleService):
    MODULE = "ingest_monitor"
    ENTITY = "监控班次"
    OPERATION_TERM = "核对"
    LIST_FIELDS = [
        "监控编号", "测点编号", "班次", "规则版本", "应收点数", "实收点数",
        "缺口时刻", "安全接收窗口", "核对时间",
    ]
    REQUIRED_FIELDS = ["监控编号", "测点编号", "班次"]
    STATUS_ORDER = ["草稿", "核对中", "已核对", "已关闭"]
    ACTION_RULES = {"提交核对": "核对中", "确认核对": "已核对", "关闭看板": "已关闭"}
    NEGATIVE_ACTIONS = ["关闭看板"]

    DEMO_POINT = "SENS-SH1"
    SAFE_WINDOW_SECONDS = 120

    def __init__(self) -> None:
        # 时钟可被测试替换，便于复现「打开申请→旧包到达→发出申请」的事件顺序
        self.now_fn: Callable[[], float] = time.time
        self._bootstrapped = False
        self._shifts: dict[str, dict[str, Any]] = {}
        self._rules: dict[str, dict[str, Any]] = {}
        self._receipts: list[dict[str, Any]] = []
        self._requests: list[dict[str, Any]] = []
        self._ledger: list[dict[str, Any]] = []
        self._request_seq = 0
        self._dispatch_seq = 0

    # ------------------------------------------------------------------ 场景装配

    def reset(self) -> None:
        """清空引擎内存态（供测试与演示重置）。"""
        self._bootstrapped = False
        self._shifts = {}
        self._rules = {}
        self._receipts = []
        self._requests = []
        self._ledger = []
        self._request_seq = 0
        self._dispatch_seq = 0

    def bootstrap_demo(self) -> dict[str, Any]:
        """装配题设场景：旧班 1Hz 两次缺测，换班后 2Hz 新班半段先完整送达。

        幂等：已装配时直接返回当前班次结构。
        """
        if self._bootstrapped:
            return {"point": self.DEMO_POINT, "shifts": list(self._shifts), "bootstrapped": False}

        now = datetime.fromtimestamp(self.now_fn())
        base = now - timedelta(seconds=90)
        handover = base + timedelta(seconds=10)

        # 旧班 A：1Hz（规则 v1.0），网格 10 个应收点，其中两个时刻缺测
        shift_a_slots = [_format_ts(base + timedelta(seconds=i)) for i in range(10)]
        missing_a = {shift_a_slots[3], shift_a_slots[7]}
        # 新班 B：2Hz（规则 v2.0），半段 8 个应收点，边缘先送满完整半段
        shift_b_slots = [
            _format_ts(handover + timedelta(milliseconds=500 * i)) for i in range(8)
        ]

        self._rules = {
            "v1.0": {"rule_version": "v1.0", "interval_s": 1.0, "frequency_hz": 1},
            "v2.0": {"rule_version": "v2.0", "interval_s": 0.5, "frequency_hz": 2},
        }
        self._shifts = {
            "A": {
                "shift_id": "SHIFT-A", "班次": "A", "point": self.DEMO_POINT,
                "rule_version": "v1.0", "interval_s": 1.0,
                "start": shift_a_slots[0], "end": shift_a_slots[-1],
                "grid": shift_a_slots,
            },
            "B": {
                "shift_id": "SHIFT-B", "班次": "B", "point": self.DEMO_POINT,
                "rule_version": "v2.0", "interval_s": 0.5,
                "start": shift_b_slots[0], "end": shift_b_slots[-1],
                "grid": shift_b_slots,
            },
        }

        # 独立实收核对：旧班先到 8 个（两次缺测），新班完整半段 8 个先落盘
        for ts in shift_a_slots:
            if ts not in missing_a:
                self._accept_receipt(self.DEMO_POINT, ts, "A", source="edge-batch-A")
        for ts in shift_b_slots:
            self._accept_receipt(self.DEMO_POINT, ts, "B", source="edge-batch-B")

        self._bootstrapped = True
        return {
            "point": self.DEMO_POINT,
            "shifts": list(self._shifts),
            "bootstrapped": True,
            "safe_window_seconds": self.SAFE_WINDOW_SECONDS,
        }

    def _ensure_bootstrapped(self) -> None:
        if not self._bootstrapped:
            self.bootstrap_demo()

    # ------------------------------------------------------------------ 实收核对

    def _accept_receipt(self, point: str, timestamp: str, shift: str, source: str) -> bool:
        if any(r["point"] == point and r["timestamp"] == timestamp for r in self._receipts):
            return False
        self._receipts.append({
            "point": point,
            "timestamp": timestamp,
            "shift": shift,
            "source": source,
            "ingested_at": _format_ts(datetime.fromtimestamp(self.now_fn())),
        })
        return True

    def ingest_observation(self, point: str, timestamp: str, source: str = "edge-late") -> dict[str, Any]:
        """独立实收核对：登记一个观测点，重复（同测点同时刻）不再计一次。"""
        self._ensure_bootstrapped()
        shift_id = self._shift_of(point, timestamp)
        if shift_id is None:
            return {"ok": False, "reason": "时刻不在任何班次应收网格内", "accepted": False}
        accepted = self._accept_receipt(point, timestamp, shift_id, source)
        result = {
            "ok": accepted,
            "accepted": accepted,
            "point": point,
            "timestamp": timestamp,
            "shift": shift_id,
            "reason": "已核对入账" if accepted else "重复实收，已去重",
        }
        if accepted:
            self._write_jsonl(
                self._data_dir("ingest_monitor") / f"receipts_{point}.jsonl",
                [{"point": point, "timestamp": timestamp, "shift": shift_id,
                  "source": source, "ingested_at": self._receipts[-1]["ingested_at"]}],
            )
        return result

    def _shift_of(self, point: str, timestamp: str) -> str | None:
        for shift_id, shift in self._shifts.items():
            if shift["point"] == point and timestamp in shift["grid"]:
                return shift_id
        return None

    def _received(self, point: str, shift_id: str) -> set[str]:
        return {
            r["timestamp"] for r in self._receipts
            if r["point"] == point and r["shift"] == shift_id
        }

    # ------------------------------------------------------------------ 区间与缺口

    @staticmethod
    def _merge_continuous(grid: list[str], received: set[str]) -> list[dict[str, Any]]:
        """把实收时刻按网格相邻关系合并成连续区间，保留先到的完整区间。"""
        intervals: list[dict[str, Any]] = []
        current: list[str] = []
        for ts in grid:
            if ts in received:
                current.append(ts)
            else:
                if current:
                    intervals.append({"start": current[0], "end": current[-1], "points": len(current)})
                    current = []
        if current:
            intervals.append({"start": current[0], "end": current[-1], "points": len(current)})
        return intervals

    def _waiting_boundary(self, shift: dict[str, Any]) -> datetime:
        return _parse_ts(shift["end"]) + timedelta(seconds=self.SAFE_WINDOW_SECONDS)

    def _missing_buckets(self, shift: dict[str, Any]) -> dict[str, list[str]]:
        """把仍缺时刻分成边缘等待（安全窗口内）与确认缺口（窗口外）两段。"""
        received = self._received(shift["point"], shift["班次"])
        missing = [ts for ts in shift["grid"] if ts not in received]
        now = datetime.fromtimestamp(self.now_fn())
        waiting_boundary = self._waiting_boundary(shift)
        waiting = [ts for ts in missing if now <= waiting_boundary]
        confirmed = [ts for ts in missing if now > waiting_boundary]
        return {"missing": missing, "waiting": waiting, "confirmed_gap": confirmed}

    # ------------------------------------------------------------------ 班次看板

    def shift_board(self, point: str | None = None) -> dict[str, Any]:
        """班次看板：各班应收/实收、连续区间、仍缺前后两段时刻与领取资格。"""
        self._ensure_bootstrapped()
        point = point or self.DEMO_POINT
        board_shifts = []
        for shift_id, shift in self._shifts.items():
            if shift["point"] != point:
                continue
            received = self._received(point, shift_id)
            buckets = self._missing_buckets(shift)
            intervals = self._merge_continuous(shift["grid"], received)
            active_request = next(
                (r for r in self._requests
                 if r["point"] == point and r["shift"] == shift_id
                 and r["status"] in ("待派发", "已派发")),
                None,
            )
            board_shifts.append({
                "shift_id": shift["shift_id"],
                "班次": shift_id,
                "规则版本": shift["rule_version"],
                "采样频率Hz": self._rules[shift["rule_version"]]["frequency_hz"],
                "起始时刻": shift["start"],
                "截止时刻": shift["end"],
                "应收点数": len(shift["grid"]),
                "实收点数": len(received),
                "连续区间": intervals,
                "边缘等待时刻": buckets["waiting"],
                "确认缺口时刻": buckets["confirmed_gap"],
                "仍缺时刻": buckets["missing"],
                "安全接收窗口截止": _format_ts(self._waiting_boundary(shift)),
                "在途补采申请": active_request["申请编号"] if active_request else None,
                "下一次领取资格": self._claim_eligibility(point, shift_id),
            })
        return {"point": point, "generated_at": _format_ts(datetime.fromtimestamp(self.now_fn())),
                "shifts": board_shifts}

    # ------------------------------------------------------------------ 采样覆盖对照

    def coverage_report(self, point: str | None = None) -> dict[str, Any]:
        """按规则版本核算每段应收点数、实收点数与仍可能在边缘等待的区间。"""
        self._ensure_bootstrapped()
        point = point or self.DEMO_POINT
        segments = []
        for shift_id, shift in self._shifts.items():
            if shift["point"] != point:
                continue
            received = self._received(point, shift_id)
            buckets = self._missing_buckets(shift)
            waiting_ranges = self._merge_continuous(shift["grid"], set(buckets["waiting"]))
            confirmed_ranges = self._merge_continuous(shift["grid"], set(buckets["confirmed_gap"]))
            segments.append({
                "班次": shift_id,
                "规则版本": shift["rule_version"],
                "采样间隔秒": shift["interval_s"],
                "应收点数": len(shift["grid"]),
                "实收点数": len(received),
                "仍缺点数": len(buckets["missing"]),
                "边缘等待区间": waiting_ranges,
                "确认缺口区间": confirmed_ranges,
                "安全接收窗口截止": _format_ts(self._waiting_boundary(shift)),
            })
        report = {
            "point": point,
            "rule_versions": sorted({s["规则版本"] for s in segments}),
            "generated_at": _format_ts(datetime.fromtimestamp(self.now_fn())),
            "segments": segments,
        }
        self._write_jsonl(
            self._data_dir("ingest_monitor") / f"coverage_{point}.jsonl",
            [report],
        )
        return report

    # ------------------------------------------------------------------ 补采申请与派发

    def open_capture_request(self, point: str, shift_id: str) -> dict[str, Any]:
        """打开补采申请：冻结当时缺口快照；同班次已有未关闭申请时拒绝，防止重复领取。"""
        self._ensure_bootstrapped()
        shift = self._shifts.get(shift_id)
        if shift is None or shift["point"] != point:
            return {"ok": False, "message": f"测点 {point} 不存在班次 {shift_id}"}
        duplicate = next(
            (r for r in self._requests
             if r["point"] == point and r["shift"] == shift_id
             and r["status"] in ("待派发", "已派发")),
            None,
        )
        if duplicate is not None:
            return {"ok": False, "message": f"班次 {shift_id} 已有在途补采申请 {duplicate['申请编号']}，禁止重复打开",
                    "request_id": duplicate["id"]}

        buckets = self._missing_buckets(shift)
        snapshot = list(buckets["missing"])
        if not snapshot:
            return {"ok": False, "message": f"班次 {shift_id} 无缺口，无需补采"}

        self._request_seq += 1
        req = {
            "id": self._request_seq,
            "申请编号": f"CAP-{self._request_seq:04d}",
            "point": point,
            "shift": shift_id,
            "status": "待派发",
            "opened_at": _format_ts(datetime.fromtimestamp(self.now_fn())),
            "issued_at": None,
            "缺口快照": snapshot,
            "派发范围": [],
            "已核扣减": [],
            "dispatch_no": None,
            "领取人": None,
            "claimed_at": None,
        }
        self._requests.append(req)
        return {"ok": True, "message": "补采申请已打开，缺口快照已留存", "request": req}

    def issue_capture_request(self, request_id: int) -> dict[str, Any]:
        """发出申请：按当前实收扣减快照内已核点，实际派发范围只含仍缺点，台账留存两份范围。"""
        self._ensure_bootstrapped()
        req = next((r for r in self._requests if r["id"] == request_id), None)
        if req is None:
            return {"ok": False, "message": f"补采申请 {request_id} 不存在"}
        if req["status"] != "待派发":
            return {"ok": False, "message": f"申请 {req['申请编号']} 当前状态 {req['status']}，不可发出"}

        confirmed_now = self._received(req["point"], req["shift"])
        scope = [ts for ts in req["缺口快照"] if ts not in confirmed_now]
        deducted = [ts for ts in req["缺口快照"] if ts in confirmed_now]
        if not scope:
            return {"ok": False, "message": "快照缺口已全部由实收补齐，无可派发范围",
                    "申请编号": req["申请编号"], "缺口快照": req["缺口快照"], "已核扣减": deducted}

        self._dispatch_seq += 1
        req["status"] = "已派发"
        req["issued_at"] = _format_ts(datetime.fromtimestamp(self.now_fn()))
        req["派发范围"] = scope
        req["已核扣减"] = deducted
        req["dispatch_no"] = self._dispatch_seq

        dispatch = {
            "dispatch_no": self._dispatch_seq,
            "request_id": req["id"],
            "申请编号": req["申请编号"],
            "point": req["point"],
            "shift": req["shift"],
            "issued_at": req["issued_at"],
            "打开时缺口快照": list(req["缺口快照"]),
            "实际派发范围": list(scope),
            "发出前已核扣减": list(deducted),
            "claimed": False,
            "claimant": None,
            "claimed_at": None,
        }
        self._ledger.append(dispatch)
        self._write_jsonl(
            self._data_dir("ingest_monitor") / "dispatch_ledger.jsonl",
            [dispatch],
        )
        return {"ok": True, "message": "捕获范围已派发并留存台账", "request": req, "dispatch": dispatch}

    def _claim_eligibility(self, point: str, shift_id: str) -> dict[str, Any]:
        """下一次领取资格：仍缺点是否已被某张已领取台账覆盖。"""
        received = self._received(point, shift_id)
        shift = self._shifts.get(shift_id)
        if shift is None:
            return {"eligible": False, "reason": "班次不存在"}
        remaining = [ts for ts in shift["grid"] if ts not in received]
        blocking = [
            d for d in self._ledger
            if d["point"] == point and d["shift"] == shift_id and d["claimed"]
            and any(ts in remaining for ts in d["实际派发范围"])
        ]
        if blocking:
            return {
                "eligible": False,
                "reason": "仍缺范围已被领取中的派发覆盖，禁止重复领取",
                "blocking_dispatch": [d["dispatch_no"] for d in blocking],
                "remaining_missing": remaining,
            }
        return {"eligible": True, "reason": "无在途领取冲突", "remaining_missing": remaining}

    def claim_dispatch(self, request_id: int, claimant: str = "值班员") -> dict[str, Any]:
        """领取派发：校验同测点没有重叠的已领取派发，防止重复领取。"""
        self._ensure_bootstrapped()
        req = next((r for r in self._requests if r["id"] == request_id), None)
        if req is None:
            return {"ok": False, "message": f"补采申请 {request_id} 不存在"}
        if req["status"] != "已派发":
            return {"ok": False, "message": f"申请 {req['申请编号']} 状态 {req['status']}，尚不可领取"}
        if req["领取人"] is not None:
            return {"ok": False, "message": f"申请 {req['申请编号']} 已被 {req['领取人']} 领取，禁止重复领取"}

        dispatch = next(d for d in self._ledger if d["request_id"] == req["id"])
        overlap = [
            d for d in self._ledger
            if d["dispatch_no"] != dispatch["dispatch_no"]
            and d["point"] == dispatch["point"]
            and d["claimed"]
            and set(d["实际派发范围"]) & set(dispatch["实际派发范围"])
        ]
        if overlap:
            return {"ok": False, "message": "捕获范围与已领取派发重叠，禁止重复领取",
                    "overlap_with": [d["dispatch_no"] for d in overlap]}

        claimed_at = _format_ts(datetime.fromtimestamp(self.now_fn()))
        dispatch["claimed"] = True
        dispatch["claimant"] = claimant
        dispatch["claimed_at"] = claimed_at
        req["领取人"] = claimant
        req["claimed_at"] = claimed_at
        # 追加领取事件，保证台账文件能还原同一次派发的领取状态（JSONL 只追加不改写）
        self._write_jsonl(
            self._data_dir("ingest_monitor") / "dispatch_ledger.jsonl",
            [{"event": "claim", "dispatch_no": dispatch["dispatch_no"],
              "request_id": req["id"], "申请编号": req["申请编号"],
              "point": dispatch["point"], "shift": dispatch["shift"],
              "claimant": claimant, "claimed_at": claimed_at}],
        )
        return {"ok": True, "message": "领取成功", "dispatch": dispatch,
                "下一次领取资格": self._claim_eligibility(req["point"], req["shift"])}

    def list_requests(self, point: str | None = None) -> list[dict[str, Any]]:
        self._ensure_bootstrapped()
        if point is None:
            return list(self._requests)
        return [r for r in self._requests if r["point"] == point]

    def dispatch_ledger(self) -> list[dict[str, Any]]:
        self._ensure_bootstrapped()
        return list(self._ledger)
