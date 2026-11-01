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
    "scope": "all",
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
        self._nick_cache: dict[str, str] = {}
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
        """统计范围：private 只私聊 / group 只群聊 / all 都算。"""
        scope = str(self._cfg("scope", "all") or "all").lower()
        try:
            is_private = bool(event.is_private_chat())
        except Exception:
            is_private = False
        if scope == "private":
            return is_private
        if scope == "group":
            return not is_private
        return True

    def _clean_name(self, uid: str, name: str) -> str:
        """昵称清洗：协议端常把昵称填成 QQ 号，这种当没有。"""
        n = (name or "").strip()
        if not n or n == str(uid) or (n.isdigit() and n == str(uid)):
            return ""
        return n

    def _display_name(self, uid: str, name: str) -> str:
        """展示用名字：没昵称就用「QQ尾号xxxx」，别把一长串数字甩给模型。"""
        n = self._clean_name(uid, name)
        if n:
            return n
        tail = str(uid)[-4:] if len(str(uid)) >= 4 else str(uid)
        return f"QQ尾号{tail}"

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

    async def _fetch_nickname(self, event: AstrMessageEvent, uid: str) -> str:
        """昵称拿不到时，直接问协议端要。拿到就缓存。"""
        cached = self._nick_cache.get(uid)
        if cached:
            return cached
        bot = getattr(event, "bot", None)
        if bot is None or not hasattr(bot, "call_action"):
            return ""
        routing = {}
        try:
            self_id = getattr(event.message_obj, "self_id", None)
            if self_id:
                routing["self_id"] = self_id
        except Exception:
            pass
        group_id = ""
        try:
            group = getattr(event.message_obj, "group", None)
            group_id = str(getattr(group, "group_id", "") or "")
        except Exception:
            group_id = ""

        name = ""
        try:
            if group_id:
                info = await bot.call_action(
                    "get_group_member_info",
                    group_id=int(group_id),
                    user_id=int(uid),
                    no_cache=False,
                    **routing,
                )
                if isinstance(info, dict):
                    name = str(info.get("card") or info.get("nickname") or "")
            if not name:
                info = await bot.call_action(
                    "get_stranger_info", user_id=int(uid), no_cache=False, **routing
                )
                if isinstance(info, dict):
                    name = str(info.get("nickname") or "")
        except Exception as exc:
            logger.debug(f"[intimacy] 取昵称失败 {uid}: {exc}")
            return ""

        name = self._clean_name(uid, name)
        if name:
            self._nick_cache[uid] = name
        return name

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
                    name = self._clean_name(uid, event.get_sender_name() or "")
                    if not name:
                        # 协议端给的是 QQ 号或者空，去问一次真名
                        name = await self._fetch_nickname(event, uid)
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
            who = self._display_name(uid, snap.get("name") or "")
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
        arguments = (getattr(event, "message_str", "") or "").split()
        if len(arguments) > 1 and arguments[1] in {"列表", "全部", "所有人", "list"}:
            async for chunk in self._owner_report(event):
                yield chunk
            return
        if self._is_owner(event):
            lines = [
                "你是主人呀，亲密度永远是最高的那一档。",
                f"不过记录还在：认识 {snap['span_days']} 天，实际聊过 {snap['days_chatted']} 天，"
                f"共 {snap['count']} 条。",
                "想看所有人的？发「/亲密度 列表」。",
            ]
            yield event.plain_result("\n".join(lines))
            return

        level = self._level(event, snap["days_chatted"])
        close = int(self._cfg("close_days", 14) or 14)
        familiar = int(self._cfg("familiar_days", 3) or 3)
        lines = [
            f"你是「{self._display_name(uid, snap.get('name') or '')}」"
            f"——和本鲸认识 {snap['span_days']} 天，实际聊过 {snap['days_chatted']} 天，共 {snap['count']} 条。",
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

    @filter.command("好感度")
    async def cmd_report(self, event: AstrMessageEvent):
        async for chunk in self._owner_report(event):
            yield chunk

    async def _owner_report(self, event: AstrMessageEvent):
        """主人专用：所有人的好感度记录 + 还差几天升级。"""
        if not self._is_owner(event):
            uid = str(event.get_sender_id() or "")
            snap = self.store.snapshot(uid)
            level = self._level(event, snap["days_chatted"])
            gap = self._gap_text(snap["days_chatted"])
            name = self._display_name(uid, snap.get("name") or "")
            yield event.plain_result(
                f"这条只有主人能看哦。\n你（{name}）现在是「{level}」，聊过 "
                f"{snap['days_chatted']} 天、{snap['count']} 条，{gap}"
            )
            return

        rows = self.store.all_snapshots()
        if not rows:
            yield event.plain_result("还没有任何记录呢，先聊几句吧。")
            return

        owners = self._owner_ids()
        decorated = []
        for uid, snap in rows:
            is_owner = uid in owners
            decorated.append((is_owner, snap["days_chatted"], snap["count"], uid, snap))
        decorated.sort(key=lambda x: (not x[0], -x[1], -x[2]))

        lines = [f"【好感度记录】共 {len(decorated)} 人"]
        for idx, (is_owner, days, count, uid, snap) in enumerate(decorated, 1):
            name = self._display_name(uid, snap.get("name") or "")
            if is_owner:
                lines.append(
                    f"{idx}. {name}　主人（恒定最高）　"
                    f"聊过 {days} 天 / {count} 条"
                )
            else:
                level = self._level_for_days(days)
                lines.append(f"{idx}. {name}　{level}　聊过 {days} 天 / {count} 条")
                lines.append(f"　　{self._gap_text(days)}")
        yield event.plain_result("\n".join(lines))

    def _level_for_days(self, days: int) -> str:
        close = int(self._cfg("close_days", 14) or 14)
        familiar = int(self._cfg("familiar_days", 3) or 3)
        if days >= close:
            return "亲密"
        if days >= familiar:
            return "眼熟"
        return "生人"

    def _gap_text(self, days: int) -> str:
        close = int(self._cfg("close_days", 14) or 14)
        familiar = int(self._cfg("familiar_days", 3) or 3)
        if days < familiar:
            return f"再聊 {familiar - days} 天升「眼熟」"
        if days < close:
            return f"再聊 {close - days} 天升「亲密」"
        return "已经到最高档啦"
