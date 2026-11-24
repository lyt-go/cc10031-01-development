"""接入监控集成测试：复现换班频率切换、迟到旧包与补采申请竞态。

场景主线（对应题设）：
1. 旧班 1Hz 两次缺测；新班 2Hz 完整半段先送达——连续区间必须保留且不得掩盖旧班缺口；
2. 班长按仍未发出的缺口打开补采申请，打开后、发出前一条较早事件时间的旧观测通过独立实收核对；
3. 发出时实际派发范围须扣掉已核点，打开时缺口快照须留存；
4. 领取有资格校验，禁止重复领取；重复实收不得再计一次。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def monitor_service():
    with tempfile.TemporaryDirectory() as tmp:
        with patch("app.services.base.DATA_ROOT", Path(tmp)):
            from app.services.ingest_monitor import IngestMonitorService
            import app.services.ingest_monitor as im
            im.DATA_ROOT = Path(tmp)
            svc = IngestMonitorService()
            clock = {"t": 1_800_000_000.0}
            original_now = svc.now_fn
            svc.now_fn = lambda: clock["t"]
            svc.reset()
            yield svc, clock, Path(tmp)
            svc.now_fn = original_now
            svc.reset()


def _shift(board: dict, shift: str) -> dict:
    return next(item for item in board["shifts"] if item["班次"] == shift)


class TestShiftBoardAndCoverage:
    def test_board_shows_expected_received_and_two_missing_segments(self, monitor_service):
        svc, clock, _ = monitor_service
        svc.bootstrap_demo()

        board = svc.shift_board()
        shift_a = _shift(board, "A")
        shift_b = _shift(board, "B")

        # 旧班 A：1Hz 应收 10、实收 8，两次缺测都在安全接收窗口内等待
        assert shift_a["应收点数"] == 10
        assert shift_a["实收点数"] == 8
        assert len(shift_a["仍缺时刻"]) == 2
        assert shift_a["边缘等待时刻"] == shift_a["仍缺时刻"]
        assert shift_a["确认缺口时刻"] == []
        # 新班 B：2Hz 半段 8 点先完整送达，保留为一条完整连续区间
        assert shift_b["应收点数"] == 8
        assert shift_b["实收点数"] == 8
        assert len(shift_b["连续区间"]) == 1
        assert shift_b["连续区间"][0]["points"] == 8
        assert shift_b["仍缺时刻"] == []

    def test_coverage_report_uses_rule_versions_and_waiting_ranges(self, monitor_service):
        svc, clock, data_dir = monitor_service
        svc.bootstrap_demo()
        report = svc.coverage_report()

        assert report["rule_versions"] == ["v1.0", "v2.0"]
        seg_a = next(s for s in report["segments"] if s["班次"] == "A")
        seg_b = next(s for s in report["segments"] if s["班次"] == "B")
        assert seg_a["规则版本"] == "v1.0"
        assert seg_a["应收点数"] == 10 and seg_a["实收点数"] == 8 and seg_a["仍缺点数"] == 2
        # 两个分散缺测在安全窗口内各自成段等待
        assert sum(w["points"] for w in seg_a["边缘等待区间"]) == 2
        assert seg_b["规则版本"] == "v2.0"
        assert seg_b["应收点数"] == 8 and seg_b["实收点数"] == 8
        # 覆盖对照落盘
        coverage_file = data_dir / "ingest_monitor" / f"coverage_{svc.DEMO_POINT}.jsonl"
        assert coverage_file.exists()
        assert json.loads(coverage_file.read_text(encoding="utf-8").strip())["point"] == svc.DEMO_POINT


class TestCaptureRequestRace:
    def test_full_open_late_receipt_issue_claim_flow(self, monitor_service):
        svc, clock, data_dir = monitor_service
        svc.bootstrap_demo()
        point = svc.DEMO_POINT

        # 越过旧班安全接收窗口：两个等待点转为确认缺口，才允许派补采
        clock["t"] += 40 * 60
        board = svc.shift_board()
        shift_a = _shift(board, "A")
        missing = shift_a["确认缺口时刻"]
        assert len(missing) == 2
        assert shift_a["边缘等待时刻"] == []

        # 新班完整区间先到，不得掩盖旧班未确认缺口
        shift_b = _shift(board, "B")
        assert shift_b["连续区间"][0]["points"] == 8
        assert shift_a["仍缺时刻"] == missing

        # 打开补采申请：留存当时两个点的缺口快照
        opened = svc.open_capture_request(point, "A")
        assert opened["ok"] is True
        req_id = opened["request"]["id"]
        assert opened["request"]["缺口快照"] == missing
        assert opened["request"]["status"] == "待派发"

        # 申请还没人领取时重复打开被拒，防止重复领取
        duplicate = svc.open_capture_request(point, "A")
        assert duplicate["ok"] is False
        # 无缺口班次不允许开申请
        assert svc.open_capture_request(point, "B")["ok"] is False

        # 旧频率尾包迟到：事件时间较早的一条旧观测在申请确认前通过独立实收核对
        late_ts = missing[0]
        accepted = svc.ingest_observation(point, late_ts, source="edge-late-A")
        assert accepted["accepted"] is True and accepted["shift"] == "A"
        # 同一时刻再核对一次：已确认实收点不能再计一次
        again = svc.ingest_observation(point, late_ts, source="edge-late-A-retry")
        assert again["accepted"] is False and "去重" in again["reason"]

        # 发出申请：实际派发范围是已核去一个点后的范围，而非原待补范围
        issued = svc.issue_capture_request(req_id)
        assert issued["ok"] is True
        dispatch = issued["dispatch"]
        assert dispatch["打开时缺口快照"] == missing
        assert dispatch["实际派发范围"] == [missing[1]]
        assert dispatch["发出前已核扣减"] == [late_ts]
        assert issued["request"]["派发范围"] == [missing[1]]

        # 已发出的申请不能重复发出
        assert svc.issue_capture_request(req_id)["ok"] is False

        # 两班实收核对：旧班 9（8+迟到1且不重复），新班 8 不变
        board_after = svc.shift_board()
        shift_a_after = _shift(board_after, "A")
        shift_b_after = _shift(board_after, "B")
        assert shift_a_after["实收点数"] == 9
        assert shift_a_after["确认缺口时刻"] == [missing[1]]
        assert shift_b_after["实收点数"] == 8
        assert shift_b_after["连续区间"][0]["points"] == 8

        # 领取前具备资格；领取后同一仍缺范围不得再次领取
        assert shift_a_after["下一次领取资格"]["eligible"] is True
        claim = svc.claim_dispatch(req_id, claimant="值班员甲")
        assert claim["ok"] is True
        ledger_file = data_dir / "ingest_monitor" / "dispatch_ledger.jsonl"
        board_final = svc.shift_board()
        eligibility = _shift(board_final, "A")["下一次领取资格"]
        assert eligibility["eligible"] is False
        assert 1 in eligibility["blocking_dispatch"]
        # 重复领取被拒
        assert svc.claim_dispatch(req_id, claimant="值班员乙")["ok"] is False
        # 已在途（已派发且未完成）期间禁止再开同班次申请
        assert svc.open_capture_request(point, "A")["ok"] is False

        # 领取事件追加进台账文件，可还原同一次派发的领取状态
        ledger_lines = [
            json.loads(line)
            for line in ledger_file.read_text(encoding="utf-8").strip().split("\n")
        ]
        claim_events = [line for line in ledger_lines if line.get("event") == "claim"]
        assert len(claim_events) == 1
        assert claim_events[0]["dispatch_no"] == 1
        assert claim_events[0]["claimant"] == "值班员甲"

    def test_ledger_persists_original_snapshot_and_actual_scope(self, monitor_service):
        svc, clock, data_dir = monitor_service
        svc.bootstrap_demo()
        point = svc.DEMO_POINT
        clock["t"] += 40 * 60
        missing = _shift(svc.shift_board(), "A")["确认缺口时刻"]

        opened = svc.open_capture_request(point, "A")
        svc.ingest_observation(point, missing[0], source="edge-late-A")
        issued = svc.issue_capture_request(opened["request"]["id"])

        # 台账文件留存同一次派发所依据的原缺口与扣减后的实际范围
        ledger_file = data_dir / "ingest_monitor" / "dispatch_ledger.jsonl"
        assert ledger_file.exists()
        lines = [json.loads(line) for line in ledger_file.read_text(encoding="utf-8").strip().split("\n")]
        assert len(lines) == 1
        row = lines[0]
        assert row["dispatch_no"] == 1
        assert row["打开时缺口快照"] == missing
        assert row["实际派发范围"] == [missing[1]]
        assert row["发出前已核扣减"] == [missing[0]]
        assert issued["dispatch"]["dispatch_no"] == row["dispatch_no"]

        # 实收核对文件也真实落盘
        receipts_file = data_dir / "ingest_monitor" / f"receipts_{point}.jsonl"
        receipts = [json.loads(line) for line in receipts_file.read_text(encoding="utf-8").strip().split("\n")]
        assert any(r["timestamp"] == missing[0] and r["source"] == "edge-late-A" for r in receipts)
        # 迟到点只落盘一次（重复核对不重复入账）
        assert sum(1 for r in receipts if r["timestamp"] == missing[0]) == 1

    def test_issue_refused_when_snapshot_fully_covered_by_receipts(self, monitor_service):
        svc, clock, _ = monitor_service
        svc.bootstrap_demo()
        point = svc.DEMO_POINT
        clock["t"] += 40 * 60
        missing = _shift(svc.shift_board(), "A")["确认缺口时刻"]

        opened = svc.open_capture_request(point, "A")
        # 两个缺口在发出前全部由迟到旧包补齐
        for ts in missing:
            assert svc.ingest_observation(point, ts)["accepted"] is True
        result = svc.issue_capture_request(opened["request"]["id"])
        assert result["ok"] is False
        assert result["已核扣减"] == missing
        # 没有产生派发台账
        assert svc.dispatch_ledger() == []
