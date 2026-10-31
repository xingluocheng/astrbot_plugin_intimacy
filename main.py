"""大肥鱼的亲密度 —— 按聊天天数推进关系，主人恒定最高。"""

from __future__ import annotations

import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, StarTools

from .storage import IntimacyStore

PLUGIN_NAME = "astrbot_plugin_intimacy"

DEFAULTS = {
    "enabled": True,
    "owner_ids": "",
    "bot_ids": "",
    "familiar_days": 3,
    "close_days": 14,
    "scope": "private",
    "inject_enabled": True,
    "show_count": True,
}

LEVEL_ORDER = ["生人", "眼熟", "亲密", "主人"]


class IntimacyStar(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.store = IntimacyStore(data_dir)
        logger.info(f"[intimacy] 数据目录：{data_dir}")

    # ------------------------------------------------------------- 基础
    def _cfg(self, key: str, default=None):
        fallback = DEFAULTS.get(key, default)
        try:
            value = self.config.get(key, fallback)
        except Exception:
            return fallback
        if value is None:
            return fallback
        if isinstance(value, str) and value == "" and isinstance(fallback, str) and fallback:
            return fallback
        return value

    def _owner_ids(self) -> set[str]:
        raw = str(self._cfg("owner_ids", "") or "")
        return {w.strip() for w in raw.replace("，", ",").split(",") if w.strip()}

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        ids = self._owner_ids()
        sender = str(event.get_sender_id() or "")
        if ids:
            return sender in ids
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    def _scope_ok(self, event: AstrMessageEvent) -> bool:
        scope = str(self._cfg("scope", "private") or "private").lower()
        if scope != "private":
            return True
        try:
            return bool(event.is_private_chat())
        except Exception:
            return False

    # ------------------------------------------------------------- 等级
    def _bot_ids(self) -> set[str]:
        """大肥鱼（机器人）的 QQ。留空表示自动排除当前登录账号。"""
        raw = str(self._cfg("bot_ids", "") or "")
        return {w.strip() for w in raw.replace("，", ",").split(",") if w.strip()}

    def _is_bot(self, event: AstrMessageEvent, uid: str) -> bool:
        if uid and uid in self._bot_ids():
            return True
        try:
            self_id = str(event.get_self_id() or "")
        except Exception:
            self_id = ""
        return bool(self_id) and uid == self_id

    def _level(self, event: AstrMessageEvent, days: int) -> str:
        if self._is_owner(event):
            return "主人"
        close = int(self._cfg("close_days", 14) or 14)
        familiar = int(self._cfg("familiar_days", 3) or 3)
        if days >= close:
            return "亲密"
        if days >= familiar:
            return "眼熟"
        return "生人"

    # ------------------------------------------------------------- 事件
    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        try:
            if bool(self._cfg("enabled", True)) and self._scope_ok(event):
                uid = str(event.get_sender_id() or "")
                if uid and not self._is_bot(event, uid):
                    name = event.get_sender_name() or ""
                    self.store.hit(uid, name)
        except Exception as exc:
            logger.debug(f"[intimacy] 记录失败：{exc}")
        if False:
            yield

    @filter.on_llm_request(priority=8800)
    async def inject_intimacy(self, event: AstrMessageEvent, req):
        try:
            if not bool(self._cfg("inject_enabled", True)):
                return
            if not self._scope_ok(event):
                return
            uid = str(event.get_sender_id() or "")
            if not uid:
                return

            if self._is_owner(event):
                req.system_prompt = (
                    (req.system_prompt or "")
                    + "\n\n【熟悉度】对方是主人。亲密度：最高（永远，不随天数变化）。"
                )
                return

            snap = self.store.snapshot(uid)
            level = self._level(event, snap["days_chatted"])
            who = snap.get("name") or "这个人"
            parts = [
                f"【熟悉度】你正在和「{who}」聊天（不是主人）。",
                f"认识 {snap['span_days']} 天，实际聊过 {snap['days_chatted']} 天",
            ]
            if bool(self._cfg("show_count", True)) and snap["count"]:
                parts.append(f"，共 {snap['count']} 条")
            parts.append(f"。当前熟悉度：{level}。")
            parts.append("按人设「关系与距离」那节调整距离，别越界，也别叫主人。")
            req.system_prompt = (req.system_prompt or "") + "\n\n" + "".join(parts)
        except Exception as exc:
            logger.debug(f"[intimacy] 注入失败：{exc}")

    # ------------------------------------------------------------- 指令
    @filter.command("亲密度")
    async def cmd_level(self, event: AstrMessageEvent):
        uid = str(event.get_sender_id() or "")
        snap = self.store.snapshot(uid)
        if self._is_owner(event):
            yield event.plain_result("你是主人呀，亲密度永远是最高的那一档。")
            return
        level = self._level(event, snap["days_chatted"])
        close = int(self._cfg("close_days", 14) or 14)
        familiar = int(self._cfg("familiar_days", 3) or 3)
        lines = [
            f"和本鲸认识 {snap['span_days']} 天，实际聊过 {snap['days_chatted']} 天，共 {snap['count']} 条。",
            f"现在的熟悉度：{level}",
        ]
        days = snap["days_chatted"]
        if level == "生人":
            lines.append(f"再聊 {max(0, familiar - days)} 天就到「眼熟」啦。")
        elif level == "眼熟":
            lines.append(f"再聊 {max(0, close - days)} 天就到「亲密」啦。")
        else:
            lines.append("已经是「亲密」了哦。")
        yield event.plain_result("\n".join(lines))
