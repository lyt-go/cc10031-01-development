"""采集心跳领域服务：维护采集终端心跳上报间隔、丢包次数与缓存积压的健康判定。
检测采集终端心跳数据生成健康状态判定并写入心跳健康日志数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DeviceHealthService(BaseModuleService):
    MODULE = "device_health"
    ENTITY = "心跳记录"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["心跳编号", "采集终端", "上报时刻", "间隔秒数", "丢包次数", "缓存积压", "判定阈值", "心跳状态"]
    # 数据采集操作：检查采集终端心跳数据进行健康评估判定写入心跳健康日志文件
    REQUIRED_FIELDS = ["心跳编号", "采集终端", "上报时刻"]
    STATUS_ORDER = ["草稿", "正常", "异常", "已失联"]
    ACTION_RULES = {"标记正常": "正常", "标记异常": "异常", "标记失联": "已失联"}
    NEGATIVE_ACTIONS = ["标记异常", "标记失联"]

    def check_health(self, heartbeat_id: int) -> dict[str, Any]:
        """检查采集终端心跳数据进行健康评估判定，写入心跳健康状态日志文件。"""
        hb = store.find(self.MODULE, heartbeat_id)
        if hb is None:
            return {"error": f"心跳记录 {heartbeat_id} 不存在"}
        terminal = hb["采集终端"]
        interval = int(hb.get("间隔秒数", "30"))
        loss = int(hb.get("丢包次数", "0"))
        backlog = int(hb.get("缓存积压", "0"))
        threshold = int(hb.get("判定阈值", "120"))

        if interval > threshold:
            self.run_action(heartbeat_id, "标记失联")
            # 数据校验输出：汇总健康诊断写入采集终端监控数据文件
            status = "已失联"
        elif loss > 5 or backlog > 1000:
            self.run_action(heartbeat_id, "标记异常")
            status = "异常"
        else:
            self.run_action(heartbeat_id, "标记正常")
            status = "正常"

        # 写入心跳健康诊断数据文件
        self._write_jsonl(
            self._data_dir("heartbeat_logs") / f"{terminal}_health.jsonl",
            [{"terminal": terminal, "interval_s": interval, "packet_loss": loss,
              "buffer_backlog": backlog, "threshold_s": threshold, "status": status,
              "checked_at": time.time()}]
        )
        return {"terminal": terminal, "interval_s": interval, "packet_loss": loss,
                "backlog": backlog, "health_status": status}

    def scan_all_terminals(self) -> dict[str, Any]:
        """批量扫描所有采集终端的心跳记录进行健康状态汇总诊断。"""
        all_hbs = store.rows(self.MODULE)
        results = []
        for hb in all_hbs:
            r = self.check_health(hb["id"])
            results.append(r)
        normal = sum(1 for r in results if r.get("health_status") == "正常")
        abnormal = sum(1 for r in results if r.get("health_status") == "异常")
        lost = sum(1 for r in results if r.get("health_status") == "已失联")
        # 汇总健康诊断写入采集终端健康监控文件
        self._write_jsonl(
            self._data_dir("heartbeat_logs") / "scan_summary.jsonl",
            [{"terminals_total": len(results), "normal": normal,
              "abnormal": abnormal, "lost": lost, "scanned_at": time.time()}]
        )
        return {"total": len(results), "normal": normal,
                "abnormal": abnormal, "lost_contact": lost}