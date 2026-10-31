"""亲密度存储：按人记录聊天天数、首次见面、昵称。"""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path


def today() -> str:
    return date.today().isoformat()


class IntimacyStore:
    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "users.json"
        self._lock = threading.Lock()
        self.data: dict = self._load()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def save(self) -> None:
        with self._lock:
            try:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                tmp.replace(self.path)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def get(self, uid: str) -> dict:
        return self.data.get(str(uid)) or {}

    def hit(self, uid: str, name: str = "") -> dict:
        """记一次聊天：今天没记过就加一天。"""
        uid = str(uid)
        day = today()
        rec = self.data.get(uid)
        if not isinstance(rec, dict):
            rec = {"first": day, "last": day, "days": [], "count": 0, "name": ""}
        days = rec.get("days")
        if not isinstance(days, list):
            days = []
        if day not in days:
            days.append(day)
            if len(days) > 400:  # 只留最近 400 个聊天日
                days = days[-400:]
        rec["days"] = days
        rec["last"] = day
        rec["count"] = int(rec.get("count") or 0) + 1
        if name:
            rec["name"] = name
        if not rec.get("first"):
            rec["first"] = day
        self.data[uid] = rec
        self.save()
        return rec

    # ------------------------------------------------------------------
    @staticmethod
    def days_chatted(rec: dict) -> int:
        days = rec.get("days") or []
        return len(days) if isinstance(days, list) else 0

    @staticmethod
    def span_days(rec: dict) -> int:
        """从第一次见面到今天，跨了多少天。"""
        first = rec.get("first")
        if not first:
            return 0
        try:
            y, m, d = (int(x) for x in str(first).split("-")[:3])
            return max(0, (date.today() - date(y, m, d)).days) + 1
        except Exception:
            return 0

    def snapshot(self, uid: str) -> dict:
        rec = self.get(uid)
        return {
            "days_chatted": self.days_chatted(rec),
            "span_days": self.span_days(rec),
            "count": int(rec.get("count") or 0),
            "first": rec.get("first") or "",
            "last": rec.get("last") or "",
            "name": rec.get("name") or "",
        }
