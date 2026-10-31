"""我的圣剑插件（AstrBot v4）。

QQ → 大肥鱼 → 本插件（aiohttp :8765）→ 手机 AutoX.js 执行器 → 结果回传。

本文件只做三件事：
1. 起停 HTTP 桥接服务；
2. 给 LLM 注册 phone_* 工具；
3. 把工具的返回值整理成模型/人话都能读懂的中文。

真正的协议与状态机在 bridge/ 和 safety/ 里，方便脱离 AstrBot 单测。
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from tempfile import gettempdir
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star

from .bridge.hub import DeviceHub, DeviceOffline, TaskRejected, TaskTimeout
from .bridge.protocol import (
    ACT_FIND_TEXT,
    ACT_KEY,
    ACT_LONG_PRESS,
    ACT_OPEN_APP,
    ACT_SCREEN,
    ACT_SWIPE,
    ACT_TAP,
    ACT_TEXT,
    ACT_UI,
)
from .bridge.server import PhoneBridgeServer
from .safety.policy import SafetyPolicy

SHOT_DIR = Path(gettempdir()) / "shengjian_phone"
SHOT_KEEP_S = 3600  # 截图临时文件保留 1 小时

VALID_KEYS = {
    "back", "home", "recents", "enter", "delete", "volume_up", "volume_down",
    "menu", "power",
}


def _audit_path() -> Path:
    """审计日志放 AstrBot 的 data/plugin_data 下，不混在插件代码目录里（升级插件不丢）。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        base = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_shengjian"
    except Exception:  # 老版本没有这个工具函数
        base = Path(__file__).parent / "data"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(__file__).parent
    return base / "audit.jsonl"


class ShengjianPhoneBridge(Star):
    """让「大肥鱼」通过 QQ 远程操控安卓手机：看屏幕、点击、输入、开应用。

    手机端装 AutoX.js 跑 shengjian_agent.js，主动长轮询本插件的 HTTP 端口。
    唯一的大脑是 AstrBot 里的模型，手机上不跑任何模型。
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}

        token = str(self._cfg("token", "") or "").strip()
        host = str(self._cfg("listen_host", "0.0.0.0"))
        port = int(self._cfg("listen_port", 8765))
        offline_after = float(self._cfg("offline_after_seconds", 40))

        audit_path = _audit_path()
        self.policy = SafetyPolicy(
            allowed_user_ids=self._cfg("allowed_user_ids", ""),
            rate_limit=int(self._cfg("rate_limit_per_minute", 20)),
            rate_window_s=60.0,
            sensitive_packages=self._cfg("sensitive_packages", None) or None,
            package_blacklist=self._cfg("package_blacklist", None) or None,
            audit_path=audit_path,
        )
        self.hub = DeviceHub(offline_after=offline_after)
        self.server = PhoneBridgeServer(
            hub=self.hub,
            token=token,
            host=host,
            port=port,
            policy=self.policy,
            debug_token=self._cfg("debug_token", ""),
        )
        self.default_timeout_ms = int(self._cfg("task_timeout_ms", 20000))
        self.quality = int(self._cfg("screenshot_quality", 55))
        self.max_shot_bytes = int(self._cfg("max_screenshot_kb", 1500)) * 1024
        self.allow_sensitive_shot = bool(self._cfg("allow_screenshot_sensitive", False))

        if not self.policy.allowed_user_ids:
            logger.warning(
                "[shengjian] allowed_user_ids 为空：任何能给大肥鱼发消息的人都能操控手机，"
                "正式使用前请填上你自己的 QQ 号。",
            )

    # ------------------------------------------------------------ 生命周期

    async def initialize(self) -> None:
        """插件加载时启动 HTTP 服务。端口被占用只记错误，不影响 AstrBot 其它功能。"""
        if not self.server.token:
            logger.error(
                "[shengjian] 还没填 token：出于安全考虑不启动 HTTP 服务。"
                "请在插件配置里填一个别人猜不到的字符串（手机端 shengjian_agent.js 的 TOKEN 必须一致），"
                "然后重载插件。"
            )
            return
        try:
            await self.server.start()
        except Exception as exc:  # 端口占用、权限等都算
            logger.error(
                "[shengjian] 无法监听 %s:%s（%s）。换个 listen_port 再重载插件。",
                self.server.host,
                self.server.port,
                exc,
            )
            # 启动失败必须把半开的 socket 收掉，否则端口会被这个死实例一直占着，
            # 下次重载只会记录“端口被占用”而永远起不来。
            try:
                await self.server.stop()
            except Exception:
                pass
            return
        logger.info(
            "[shengjian] 就绪：请让手机上的 AutoX.js 脚本连 http://<电脑IP>:%s",
            self.server.port,
        )

    async def terminate(self) -> None:
        await self.server.stop()

    # ------------------------------------------------------------ 配置/工具函数

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except AttributeError:
            return default
        if value is None or value == "":
            return default
        return value

    def _gate(self, event: AstrMessageEvent, action: str) -> str | None:
        """白名单 + 限流 + 在线检查。返回非 None 就是拒绝理由。"""
        uid = str(event.get_sender_id() or "")
        ok, msg = self.policy.user_allowed(uid)
        if not ok:
            self.policy.audit("deny_user", user=uid, action=action, reason=msg)
            return f"⛔ 没有权限：{msg}"
        ok, msg = self.policy.rate_check(uid)
        if not ok:
            self.policy.audit("deny_rate", user=uid, action=action, reason=msg)
            return f"⏳ 操作太频繁：{msg}"
        if not self.hub.online:
            return (
                "📴 手机不在线。请确认：① AutoX.js 里 shengjian_agent.js 正在运行；"
                "② 无障碍服务已开启；③ 手机和电脑在同一网络（或 Tailscale 已连）。"
            )
        return None

    async def _submit(
        self,
        event: AstrMessageEvent,
        action: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """下发任务；异常统一在这里转成中文提示，返回值是手机回传的 data。"""
        uid = str(event.get_sender_id() or "")
        self.policy.audit("task", user=uid, action=action, params=params or {})
        try:
            return await self.hub.submit(
                action,
                params or {},
                timeout_ms=timeout_ms or self.default_timeout_ms,
            )
        except DeviceOffline as exc:
            raise _ToolError(f"📴 手机不在线：{exc}") from exc
        except TaskTimeout as exc:
            raise _ToolError(f"⌛ {exc}（手机可能卡住或锁屏）") from exc
        except TaskRejected as exc:
            raise _ToolError(f"❌ 手机端执行失败：{exc}") from exc

    def _save_shot(self, raw: bytes) -> Path:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / f"shot_{int(time.time() * 1000)}.jpg"
        path.write_bytes(raw)
        cutoff = time.time() - SHOT_KEEP_S
        for old in SHOT_DIR.glob("shot_*.jpg"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass
        return path

    @staticmethod
    def _fmt_size(n: int) -> str:
        return f"{n / 1024:.0f}KB" if n < 1024 * 1024 else f"{n / 1048576:.1f}MB"

    @staticmethod
    def _render_ui(data: dict[str, Any], limit: int) -> str:
        nodes = data.get("nodes") or []
        pkg = data.get("pkg") or "?"
        w, h = data.get("w"), data.get("h")
        lines = [f"界面 {pkg}  {w}x{h}（共 {len(nodes)} 个节点，显示前 {min(len(nodes), limit)} 个）"]
        shown = 0
        for node in nodes:
            text = (node.get("text") or "").strip()
            desc = (node.get("desc") or "").strip()
            label = text or desc
            cls = str(node.get("cls") or "").split(".")[-1]
            clickable = node.get("clickable")
            editable = node.get("editable")
            # 没字、不点、不可输入的纯容器节点对模型没用，跳过（但保留有 id 的）
            if not label and not clickable and not editable and not node.get("id"):
                continue
            if shown >= limit:
                lines.append(f"…（还有 {len(nodes) - shown} 个节点未显示）")
                break
            cx, cy = node.get("cx"), node.get("cy")
            parts = [f"{shown:>3}", cls or "?"]
            if label:
                parts.append(f'"{label[:60]}"')
            if node.get("id"):
                parts.append(f"#{str(node['id']).split('/')[-1]}")
            if cx is not None and cy is not None:
                parts.append(f"@({cx},{cy})")
            if clickable:
                parts.append("[可点]")
            if editable:
                parts.append("[输入框]")
            if node.get("selected"):
                parts.append("[选中]")
            lines.append(" ".join(parts))
            shown += 1
        return "\n".join(lines)

    @staticmethod
    def _brief(data: dict[str, Any]) -> str:
        pkg = data.get("pkg") or data.get("cur_pkg") or ""
        extra = f"，当前界面 {pkg}" if pkg else ""
        return extra

    # ============================================================ 工具：状态

    @filter.llm_tool("phone_status")
    async def phone_status(self, event: AstrMessageEvent) -> str:
        """查看手机连接状态：是否在线、电量、屏幕分辨率、当前应用、无障碍/截图权限。

        不需要在线也能调用，掉线时用它排查问题。手机离线时会告诉你该检查什么。
        """
        snap = self.hub.snapshot()
        dev = snap["device"]
        if not snap["online"]:
            age = snap["heartbeat_age"]
            hint = f"最后一次心跳 {age} 秒前。" if age is not None else "还没收到过任何心跳。"
            return (
                f"📴 手机离线。{hint}\n"
                "排查：① AutoX.js 脚本是否在跑（通知栏应有「脚本运行中」）；"
                "② 无障碍服务是否开启；③ 手机能不能访问电脑的 "
                f"{self.server.host}:{self.server.port}（浏览器打开 /healthz 试试）。"
            )
        battery = dev.get("battery", -1)
        bat = f"{battery}%{'（充电中）' if dev.get('charging') else ''}" if battery >= 0 else "未知"
        return (
            f"📱 手机在线（{snap['heartbeat_age']}s 前心跳）\n"
            f"设备：{dev.get('device') or '?'} / Android {dev.get('android') or '?'}"
            f" / {dev.get('w')}x{dev.get('h')}\n"
            f"电量：{bat}　屏幕：{'亮' if dev.get('screen_on') else '灭'}\n"
            f"前台应用：{dev.get('cur_pkg') or '?'}\n"
            f"无障碍：{'开' if dev.get('accessibility') else '关'}"
            f"　截图权限：{'开' if dev.get('screen_capture') else '关'}\n"
            f"脚本版本：{dev.get('agent_version') or '?'}"
            f"　队列：{snap['queue']}　"
            f"累计成功 {snap['stats']['tasks_succeeded']} / 失败 {snap['stats']['tasks_failed']}"
        )

    # ============================================================ 工具：看

    @filter.llm_tool("phone_see")
    async def phone_see(self, event: AstrMessageEvent, with_ui: bool = True) -> str:
        """看一眼手机现在显示什么：截一张图发到当前聊天，同时返回界面上的文字。

        这是最常用的第一个动作。看到图后，用 phone_tap 点坐标、phone_type 输入。
        敏感界面（微信、支付宝、银行、设置等）默认不截图，只返回控件文字。

        Args:
            with_ui (bool): 是否同时返回界面文字摘要，默认 True
        """
        gate = self._gate(event, ACT_SCREEN)
        if gate:
            return gate

        cur = str(self.hub.device.get("cur_pkg") or "")
        sensitive, why = self.policy.is_sensitive_screen(cur)
        if sensitive and not self.allow_sensitive_shot:
            return (
                f"🙈 {why}，按安全策略不把截图推到 QQ。\n"
                "改用 phone_ui 读界面文字，或者先 phone_open_app 切到其他应用。"
            )

        try:
            data = await self._submit(event, ACT_SCREEN, {"quality": self.quality}, 20000)
        except _ToolError as exc:
            return str(exc)

        b64 = data.get("image_b64") or ""
        if not b64:
            return "截图失败：手机没返回图像数据（可能没给「屏幕截图」权限）"

        try:
            raw = base64.b64decode(b64)
        except (ValueError, TypeError):
            return "截图失败：图像数据损坏"
        if len(raw) > self.max_shot_bytes:
            return (
                f"截图太大（{self._fmt_size(len(raw))}），已超过配置上限。"
                "把 screenshot_quality 调低（比如 40）再试。"
            )

        pkg = str(data.get("pkg") or cur or "?")
        w, h = data.get("w"), data.get("h")
        try:
            path = self._save_shot(raw)
            await event.send(MessageChain([Image.fromFileSystem(str(path))]))
        except Exception as exc:  # 消息平台出错不影响返回文本
            logger.error("[shengjian] 发送截图失败：%s", exc)
            return f"截图已拿到（{w}x{h}，{pkg}），但推到 QQ 失败：{exc}"

        summary = f"📸 截图已发到聊天（{w}x{h}，{self._fmt_size(len(raw))}，前台 {pkg}）"
        if with_ui:
            try:
                ui = await self._submit(event, ACT_UI, {"max_nodes": 400}, 12000)
                texts = [
                    (n.get("text") or n.get("desc") or "").strip()
                    for n in (ui.get("nodes") or [])
                ]
                texts = [t for t in texts if t]
                seen: list[str] = []
                for t in texts:
                    if t not in seen:
                        seen.append(t)
                    if len(seen) >= 12:
                        break
                if seen:
                    summary += "\n界面上能看到的文字：" + " | ".join(seen)
            except _ToolError:
                pass
        return summary

    @filter.llm_tool("phone_ui")
    async def phone_ui(self, event: AstrMessageEvent, max_nodes: int = 80) -> str:
        """读手机当前界面的控件树（文字、坐标、是否可点），不截图。

        适合看不清截图、或者敏感界面（微信/银行）不方便截图时用。
        每行给出中心坐标 @(x,y)，可以直接拿去 phone_tap。

        Args:
            max_nodes (int): 最多显示多少个节点，默认 80
        """
        gate = self._gate(event, ACT_UI)
        if gate:
            return gate
        limit = max(10, min(int(max_nodes or 80), 200))
        try:
            data = await self._submit(event, ACT_UI, {"max_nodes": limit * 3}, 15000)
        except _ToolError as exc:
            return str(exc)
        if data.get("error"):
            return f"读取界面失败：{data['error']}"
        return self._render_ui(data, limit)

    # ============================================================ 工具：动手

    @filter.llm_tool("phone_tap")
    async def phone_tap(self, event: AstrMessageEvent, x: int, y: int) -> str:
        """点击屏幕上一个点。坐标是真实像素，原点在左上角。

        坐标从 phone_see 的截图或 phone_ui 的 @(x,y) 里取。

        Args:
            x (int): 横坐标像素
            y (int): 纵坐标像素
        """
        gate = self._gate(event, ACT_TAP)
        if gate:
            return gate
        try:
            data = await self._submit(event, ACT_TAP, {"x": int(x), "y": int(y)}, 8000)
        except _ToolError as exc:
            return str(exc)
        return f"👆 已点击 ({x}, {y}){self._brief(data)}"

    @filter.llm_tool("phone_long_press")
    async def phone_long_press(
        self, event: AstrMessageEvent, x: int, y: int, duration_ms: int = 800
    ) -> str:
        """长按一个点（默认 800ms），常用于弹出菜单、拖动前抓取。

        Args:
            x (int): 横坐标像素
            y (int): 纵坐标像素
            duration_ms (int): 按住时长，默认 800
        """
        gate = self._gate(event, ACT_LONG_PRESS)
        if gate:
            return gate
        duration = max(300, min(int(duration_ms or 800), 5000))
        try:
            data = await self._submit(
                event, ACT_LONG_PRESS, {"x": int(x), "y": int(y), "duration": duration}, 10000
            )
        except _ToolError as exc:
            return str(exc)
        return f"👆 已长按 ({x}, {y}) {duration}ms{self._brief(data)}"

    @filter.llm_tool("phone_swipe")
    async def phone_swipe(
        self,
        event: AstrMessageEvent,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> str:
        """滑动/滚动屏幕：从 (x1,y1) 划到 (x2,y2)。向上划 = 看下面内容。

        Args:
            x1 (int): 起点横坐标
            y1 (int): 起点纵坐标
            x2 (int): 终点横坐标
            y2 (int): 终点纵坐标
            duration_ms (int): 滑动时长，默认 300，越大越慢越不容易误触
        """
        gate = self._gate(event, ACT_SWIPE)
        if gate:
            return gate
        duration = max(100, min(int(duration_ms or 300), 3000))
        params = {
            "x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2),
            "duration": duration,
        }
        try:
            data = await self._submit(event, ACT_SWIPE, params, 12000)
        except _ToolError as exc:
            return str(exc)
        return f"👉 已滑动 ({x1},{y1})→({x2},{y2}) {duration}ms{self._brief(data)}"

    @filter.llm_tool("phone_type")
    async def phone_type(
        self, event: AstrMessageEvent, text: str, submit: bool = False
    ) -> str:
        """往当前焦点输入框里打字。先 phone_tap 点一下输入框，再调这个。

        如果 setText 注入失败，手机端会自动退回剪贴板粘贴。

        Args:
            text (str): 要输入的文字
            submit (bool): 输完是否立刻回车提交（搜索框/聊天框常用），默认 False
        """
        gate = self._gate(event, ACT_TEXT)
        if gate:
            return gate
        if not text:
            return "输入内容为空"
        try:
            data = await self._submit(
                event, ACT_TEXT, {"text": str(text), "submit": bool(submit)}, 15000
            )
        except _ToolError as exc:
            return str(exc)
        how = data.get("method") or "setText"
        return f"⌨️ 已输入「{text[:40]}」（{how}）{'，并已提交' if submit else ''}{self._brief(data)}"

    @filter.llm_tool("phone_key")
    async def phone_key(self, event: AstrMessageEvent, key: str = "back") -> str:
        """按系统键：back（返回）/ home（回桌面）/ recents（最近任务）/ enter / delete / volume_up / volume_down。

        卡在某个页面时用 back 退出；想回桌面用 home。

        Args:
            key (str): 按键名，默认 back
        """
        gate = self._gate(event, ACT_KEY)
        if gate:
            return gate
        name = str(key or "back").strip().lower()
        if name not in VALID_KEYS:
            return f"不认识的按键「{name}」，可用：{'、'.join(sorted(VALID_KEYS))}"
        try:
            await self._submit(event, ACT_KEY, {"name": name}, 8000)
        except _ToolError as exc:
            return str(exc)
        return f"🔘 已按 {name}"

    @filter.llm_tool("phone_open_app")
    async def phone_open_app(self, event: AstrMessageEvent, name: str) -> str:
        """打开手机上的一个应用。可以给应用名（中文名如「微信」「相机」）或包名（com.tencent.mm）。

        会等 1.5 秒再返回前台包名，用来确认真的打开了。

        Args:
            name (str): 应用名或包名
        """
        gate = self._gate(event, ACT_OPEN_APP)
        if gate:
            return gate
        target = str(name or "").strip()
        if not target:
            return "要打开哪个应用？给应用名或包名"
        ok, why = self.policy.package_allowed(target if "." in target else "")
        if not ok:
            return f"⛔ {why}"
        params = {"package": target} if "." in target else {"name": target}
        try:
            data = await self._submit(event, ACT_OPEN_APP, params, 20000)
        except _ToolError as exc:
            return str(exc)
        pkg = data.get("pkg") or "?"
        return f"🚀 已尝试打开「{target}」，当前前台：{pkg}"

    @filter.llm_tool("phone_find_tap")
    async def phone_find_tap(
        self, event: AstrMessageEvent, text: str, index: int = 0, exact: bool = False
    ) -> str:
        """在屏幕上找一段文字并点它（比算坐标稳）。找不到会告诉你当前界面有哪些文字。

        Args:
            text (str): 要找的文字，比如「设置」「发送」「允许」
            index (int): 有多个匹配时点第几个，从 0 开始
            exact (bool): True = 文字完全相等；False = 包含即可
        """
        gate = self._gate(event, ACT_FIND_TEXT)
        if gate:
            return gate
        params = {"text": str(text), "index": int(index or 0), "exact": bool(exact), "tap": True}
        try:
            data = await self._submit(event, ACT_FIND_TEXT, params, 12000)
        except _ToolError as exc:
            return str(exc)
        if not data.get("found"):
            nearby = data.get("texts") or []
            hint = "、".join(str(t) for t in nearby[:15])
            return f"🔍 界面上没找到「{text}」" + (f"。当前文字：{hint}" if hint else "")
        cx, cy = data.get("cx"), data.get("cy")
        return f"🔍 找到「{data.get('matched') or text}」并点击 @({cx},{cy}){self._brief(data)}"

    # ============================================================ 工具：急停

    @filter.llm_tool("phone_stop")
    async def phone_stop(self, event: AstrMessageEvent) -> str:
        """急停：清空所有还没发出去的任务。用户说「停下」「别动了」时立刻调用。"""
        uid = str(event.get_sender_id() or "")
        ok, _ = self.policy.user_allowed(uid)
        if not ok:
            return "⛔ 没有权限"
        dropped = self.hub.emergency_clear()
        self.policy.audit("emergency_stop", user=uid, dropped=dropped)
        logger.warning("[shengjian] 急停已执行，清空 %s 个任务", dropped)
        return f"🛑 已急停，清空 {dropped} 个待执行任务。手机端正在跑的那一步会做完。"


class _ToolError(Exception):
    """工具内部错误，message 直接展示给模型。"""


__all__ = ["ShengjianPhoneBridge"]
