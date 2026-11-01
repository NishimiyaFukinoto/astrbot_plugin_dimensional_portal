"""次元门插件（AstrBot v4）。

QQ → 大肥鱼 → 本插件（aiohttp :8765）→ 手机 AutoX.js 执行器 → 结果回传。

本文件只做三件事：
1. 起停 HTTP 桥接服务；
2. 给 LLM 注册 phone_* 工具；
3. 把工具的返回值整理成模型/人话都能读懂的中文。

真正的协议与状态机在 bridge/ 和 safety/ 里，方便脱离 AstrBot 单测。
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
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
    WRITE_ACTIONS,
)
from .bridge.server import PhoneBridgeServer
from .safety.policy import SafetyPolicy, _as_list

SHOT_DIR = Path(gettempdir()) / "dimensional_portal_phone"
SHOT_KEEP_S = 3600  # 截图临时文件保留 1 小时

# 手机端 actKey 只支持这些；menu/power 等无障碍注入不了的键不要写进来，
# 否则插件放行、手机端抛「不认识的按键」，模型看到的是假支持。
VALID_KEYS = {
    "back", "home", "recents", "enter", "delete", "volume_up", "volume_down",
}


def _audit_path() -> Path:
    """审计日志放 AstrBot 的 data/plugin_data 下，不混在插件代码目录里（升级插件不丢）。"""
    override = os.environ.get("DIMENSIONAL_PORTAL_AUDIT_PATH")
    if override:
        return Path(override)
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        base = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_dimensional_portal"
    except Exception:  # 老版本没有这个工具函数
        base = Path(__file__).parent / "data"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(__file__).parent
    return base / "audit.jsonl"


def _cfg_int(value: Any, default: int, key: str) -> int:
    """配置里的数值可能是面板填错的字符串；别让一个错字把整个插件搞挂。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(
            "[dimensional-portal] 配置 %s=%r 不是整数，已回退默认值 %s（去面板改一下）", key, value, default
        )
        return default


def _cfg_float(value: Any, default: float, key: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning(
            "[dimensional-portal] 配置 %s=%r 不是数字，已回退默认值 %s（去面板改一下）", key, value, default
        )
        return default


def _local_ip() -> str:
    """取本机在局域网/Tailscale 里的地址，只用来打印控制台网址。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("10.255.255.255", 1))  # 不会真的发包，只是让内核选出口网卡
            return str(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def _split_ids(value: Any) -> list[str]:
    """白名单统一成 list[str]：兼容 "10001,10002" / ["10001"] / 单个号。"""
    return _as_list(value)


class _ConsoleAdapter:
    """管理控制台需要的最小能力：读快照、改白名单、急停。

    隔这一层是为了让 bridge/server.py 完全不碰 AstrBot 的 self.config；
    以后 V2 加"安全策略开关"时只改这里，HTTP 层不用动。
    """

    def __init__(self, plugin: "DimensionalPortalPhoneBridge") -> None:
        self.plugin = plugin

    def snapshot(self) -> dict[str, Any]:
        return {
            "config": {
                "allowed_user_ids": _split_ids(self.plugin.config.get("allowed_user_ids", "")),
                "rate_limit_per_minute": int(self.plugin.policy.rate_limit),
            },
            "audit": self._audit_tail(20),
        }

    def apply_config(self, payload: dict[str, Any], ip: str = "-") -> dict[str, Any]:
        """只处理 allowed_user_ids，其它字段一律忽略；返回落盘后回读的真实值。"""
        if "allowed_user_ids" in payload:
            ids = list(dict.fromkeys(_split_ids(payload.get("allowed_user_ids"))))
            if not ids:
                raise ValueError("白名单至少要保留一个 QQ 号，不能删光")
            bad = [item for item in ids if not item.isdigit()]
            if bad:
                raise ValueError(f"QQ 号必须是纯数字：{', '.join(bad)}")
            self.plugin.config["allowed_user_ids"] = ",".join(ids)
            saver = getattr(self.plugin.config, "save_config", None)
            if callable(saver):
                saver()
            self.plugin._rebuild_policy()
            server = getattr(self.plugin, "server", None)
            if server is not None:
                server.policy = self.plugin.policy
            self.plugin.policy.audit("admin", ip=ip, change={"allowed_user_ids": ids})
        return {
            "allowed_user_ids": _split_ids(self.plugin.config.get("allowed_user_ids", "")),
            "rate_limit_per_minute": int(self.plugin.policy.rate_limit),
        }

    def stop_all(self) -> int:
        return int(self.plugin.hub.emergency_clear())

    def _audit_tail(self, limit: int) -> list[dict[str, Any]]:
        path = getattr(self.plugin, "audit_path", None)
        if not path:
            return []
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out


class DimensionalPortalPhoneBridge(Star):
    """让「大肥鱼」通过 QQ 远程操控安卓手机：看屏幕、点击、输入、开应用。

    手机端装 AutoX.js 跑 dimensional_portal_agent.js，主动长轮询本插件的 HTTP 端口。
    唯一的大脑是 AstrBot 里的模型，手机上不跑任何模型。
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}

        token = str(self._cfg("token", "") or "").strip()
        host = str(self._cfg("listen_host", "0.0.0.0"))
        port = max(1, min(_cfg_int(self._cfg("listen_port", 8765), 8765, "listen_port"), 65535))
        offline_after = max(
            5.0,
            min(
                _cfg_float(self._cfg("offline_after_seconds", 40), 40.0, "offline_after_seconds"),
                600.0,
            ),
        )

        self.audit_path = _audit_path()
        self.policy = self._make_policy()
        self.hub = DeviceHub(offline_after=offline_after)
        self.console = _ConsoleAdapter(self)
        self.server = PhoneBridgeServer(
            hub=self.hub,
            token=token,
            host=host,
            port=port,
            policy=self.policy,
            debug_token=self._cfg("debug_token", ""),
            admin_token=str(self.config.get("admin_token", "") or ""),
            console=self.console,
        )
        self.default_timeout_ms = max(
            1000,
            min(_cfg_int(self._cfg("task_timeout_ms", 20000), 20000, "task_timeout_ms"), 120000),
        )
        self.quality = max(
            1, min(_cfg_int(self._cfg("screenshot_quality", 55), 55, "screenshot_quality"), 95)
        )
        self.max_shot_bytes = (
            max(100, min(_cfg_int(self._cfg("max_screenshot_kb", 1500), 1500, "max_screenshot_kb"), 20000))
            * 1024
        )
        self.allow_sensitive_shot = bool(self._cfg("allow_screenshot_sensitive", False))
        self.allow_sensitive_write = bool(self._cfg("allow_sensitive_write", False))

        if not self.policy.allowed_user_ids:
            logger.warning(
                "[dimensional-portal] allowed_user_ids 为空：任何能给大肥鱼发消息的人都能操控手机，"
                "正式使用前请填上你自己的 QQ 号。",
            )

    # ------------------------------------------------------------ 生命周期

    async def initialize(self) -> None:
        """插件加载时启动 HTTP 服务。端口被占用只记错误，不影响 AstrBot 其它功能。"""
        if not self.server.token:
            logger.error(
                "[dimensional-portal] 还没填 token：出于安全考虑不启动 HTTP 服务。"
                "请在插件配置里填一个别人猜不到的字符串（手机端 dimensional_portal_agent.js 的 TOKEN 必须一致），"
                "然后重载插件。"
            )
            return
        self._ensure_admin()
        try:
            await self.server.start()
        except Exception as exc:  # 端口占用、权限等都算
            logger.error(
                "[dimensional-portal] 无法监听 %s:%s（%s）。换个 listen_port 再重载插件。",
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
            "[dimensional-portal] 就绪：请让手机上的 AutoX.js 脚本连 http://<电脑IP>:%s",
            self.server.port,
        )
        if self.server.admin_enabled:
            logger.info("[dimensional-portal] 管理控制台：%s", self._console_url())

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

    def _make_policy(self) -> SafetyPolicy:
        return SafetyPolicy(
            allowed_user_ids=self._cfg("allowed_user_ids", ""),
            rate_limit=_cfg_int(
                self._cfg("rate_limit_per_minute", 20), 20, "rate_limit_per_minute"
            ),
            rate_window_s=60.0,
            sensitive_packages=self._cfg("sensitive_packages", None) or None,
            package_blacklist=self._cfg("package_blacklist", None) or None,
            audit_path=self.audit_path,
        )

    def _rebuild_policy(self) -> None:
        """白名单改了：就地重建 SafetyPolicy，不重载插件（会话和队列都还在）。"""
        self.policy = self._make_policy()

    def _ensure_admin(self) -> None:
        """admin_token 为空就现场生成并写盘；填 off 就彻底不做控制台。"""
        raw = str(self.config.get("admin_token", "") or "").strip()
        if raw.lower() == "off":
            logger.info("[dimensional-portal] 管理控制台已关闭（admin_token=off）")
            return
        if raw:
            self.server.enable_admin(raw)
            return
        token = secrets.token_urlsafe(24)
        try:
            self.config["admin_token"] = token
            saver = getattr(self.config, "save_config", None)
            if callable(saver):
                saver()
            else:
                logger.warning(
                    "[dimensional-portal] 配置不支持 save_config，自动生成的 admin_token 只在本次运行有效"
                )
            self.server.enable_admin(token)
            logger.info("[dimensional-portal] 已自动生成 admin_token 并写入插件配置")
        except Exception as exc:  # 写盘失败也别影响手机桥接
            logger.error("[dimensional-portal] 自动生成 admin_token 失败：%s", exc)

    def _console_url(self) -> str:
        host = self.server.host
        if host in ("", "0.0.0.0", "::"):
            host = _local_ip()
        return f"http://{host}:{self.server.port}/console?k={self.server.admin_token}"

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
                "📴 手机不在线。请确认：① AutoX.js 里 dimensional_portal_agent.js 正在运行；"
                "② 无障碍服务已开启；③ 手机和电脑在同一网络（或 Tailscale 已连）。"
            )
        # 敏感界面默认也不让动手：不截图保护的是画面，拦不住往密码框里打字/点「确认支付」。
        # 只拦 WRITE_ACTIONS；phone_see / phone_ui 这类只读动作照常放行。
        if action in WRITE_ACTIONS and not self.allow_sensitive_write:
            cur = str(self.hub.device.get("cur_pkg") or "")
            sensitive, why = self.policy.is_sensitive_screen(cur)
            if sensitive:
                self.policy.audit(
                    "deny_sensitive_write",
                    user=uid,
                    action=action,
                    pkg=cur,
                    reason=why,
                )
                return (
                    f"🔒 {why}，已拦截写操作（{action}），没有点、没有输入。\n"
                    "敏感界面默认只能看（phone_see / phone_ui）。确实需要操作时，"
                    "在插件配置里打开「敏感界面写操作」再试。"
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
        if n < 1024:
            return f"{n}B"  # 1x1 缩略图别显示成「0KB」
        if n < 1024 * 1024:
            return f"{n / 1024:.0f}KB"
        return f"{n / 1048576:.1f}MB"

    @staticmethod
    def _render_ui(data: dict[str, Any], limit: int) -> str:
        nodes = data.get("nodes") or []
        pkg = data.get("pkg") or "?"
        w, h = data.get("w"), data.get("h")
        lines: list[str] = []
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
        # 表头用实际渲染条数：被跳过的空容器也算进「共 N 个」只会让数字对不上。
        header = f"界面 {pkg}  {w}x{h}（列出 {shown} 个控件，手机共报 {len(nodes)} 个节点）"
        return "\n".join([header, *lines])

    @staticmethod
    def _brief(data: dict[str, Any]) -> str:
        pkg = data.get("pkg") or data.get("cur_pkg") or ""
        extra = f"，当前界面 {pkg}" if pkg else ""
        return extra

    @staticmethod
    def _render_seen(seen: Any) -> str:
        """把 open_app 没打开目标那一刻的界面快照渲染成一句人话。"""
        if not isinstance(seen, dict):
            return ""
        pkg = str(seen.get("pkg") or "?")
        win = seen.get("win")
        if isinstance(win, dict):
            win = win.get("pkg") or win.get("type") or ""
        win = str(win or "").strip()
        texts = [str(t).strip() for t in (seen.get("texts") or []) if str(t).strip()]
        parts = [f"当时前台：{pkg}"]
        if win:
            parts.append(f"窗口：{win}")
        if seen.get("popup_maybe"):
            parts.append("可能有弹窗挡住")
        if texts:
            parts.append("可见文字：" + " | ".join(texts[:10]))
        return "；".join(parts)

    def _check_point(self, x: Any, y: Any) -> tuple[int, int, str | None]:
        """校验点击坐标在屏幕内（用手机报的 w/h）；手机没报分辨率时不拦。"""
        try:
            xi, yi = int(x), int(y)
        except (TypeError, ValueError):
            return 0, 0, "坐标必须是整数像素"
        dev = self.hub.device
        try:
            wi, hi = int(dev.get("w") or 0), int(dev.get("h") or 0)
        except (TypeError, ValueError):
            return xi, yi, None
        if wi > 0 and hi > 0 and not (0 <= xi < wi and 0 <= yi < hi):
            return xi, yi, (
                f"坐标 ({xi},{yi}) 超出屏幕范围（{wi}x{hi}），左上角是 (0,0)，"
                "请从 phone_see / phone_ui 里取坐标"
            )
        return xi, yi, None

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
            # 0.0.0.0 是监听地址，不是手机能连的地址；提示用户换真实 IP。
            host = self.server.host
            addr = (
                "电脑的真实局域网 IP 或 Tailscale IP"
                if host in ("0.0.0.0", "::", "")
                else host
            )
            return (
                f"📴 手机离线。{hint}\n"
                "排查：① AutoX.js 脚本是否在跑（通知栏应有「脚本运行中」）；"
                "② 无障碍服务是否开启；③ 手机能不能访问电脑的 "
                f"{addr} 的 {self.server.port} 端口（浏览器打开 /healthz 试试）。"
            )
        battery = dev.get("battery", -1)
        bat = f"{battery}%{'（充电中）' if dev.get('charging') else ''}" if battery >= 0 else "未知"
        # 悬浮窗守卫是「后台能打开微信/QQ」的前提（Android 10+ 的后台启动豁免），
        # 旧脚本不上报这个字段，别把「不知道」说成「关了」。
        overlay = dev.get("overlay")
        if overlay is True:
            overlay_line = "悬浮窗守卫：开（后台启动应用可用）"
        elif overlay is False:
            note = str(dev.get("overlay_note") or "").strip()
            overlay_line = "悬浮窗守卫：关" + (f"（{note}）" if note else "")
            overlay_line += " ⚠️ 微信/QQ 这类应用会打不开，请给 AutoX.js 悬浮窗权限并重启脚本"
        else:
            overlay_line = "悬浮窗守卫：未上报（手机端脚本版本较旧）"
        return (
            f"📱 手机在线（{snap['heartbeat_age']}s 前心跳）\n"
            f"设备：{dev.get('device') or '?'} / Android {dev.get('android') or '?'}"
            f" / {dev.get('w')}x{dev.get('h')}\n"
            f"电量：{bat}　屏幕：{'亮' if dev.get('screen_on') else '灭'}\n"
            f"前台应用：{dev.get('cur_pkg') or '?'}\n"
            f"{overlay_line}\n"
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

        # 心跳里的 cur_pkg 最长 10 秒旧；手机回传的 pkg 才是按下快门那一刻的真实前台。
        # 拿它再判一次（with_ui 时连界面文字一起），否则「心跳后切到微信 → 立刻截图」会漏过。
        pkg = str(data.get("pkg") or cur or "?")
        ui_data: dict[str, Any] | None = None
        ui_text = ""
        if with_ui:
            try:
                ui_data = await self._submit(event, ACT_UI, {"max_nodes": 400}, 12000)
                ui_text = " ".join(
                    (n.get("text") or n.get("desc") or "").strip()
                    for n in (ui_data.get("nodes") or [])
                )[:3000]
            except _ToolError:
                ui_data = None

        sensitive, why = self.policy.is_sensitive_screen(pkg, ui_text)
        if sensitive and not self.allow_sensitive_shot:
            # 到这一步还没落盘、更没 send：直接拒绝，不把敏感界面推给聊天。
            self.policy.audit(
                "deny_screenshot",
                user=str(event.get_sender_id() or ""),
                pkg=pkg,
                reason=why,
            )
            raise _ToolError(
                f"🙈 {why}，截图已拦截，未发送（按安全策略不把敏感界面推到 QQ）。"
                "改用 phone_ui 读界面文字。"
            )

        w, h = data.get("w"), data.get("h")
        try:
            path = self._save_shot(raw)
            await event.send(MessageChain([Image.fromFileSystem(str(path))]))
        except Exception as exc:  # 消息平台出错不影响返回文本
            logger.error("[dimensional-portal] 发送截图失败：%s", exc)
            return f"截图已拿到（{w}x{h}，{pkg}），但推到 QQ 失败：{exc}"

        summary = f"📸 截图已发到聊天（{w}x{h}，{self._fmt_size(len(raw))}，前台 {pkg}）"
        if ui_data is not None:
            texts = [
                (n.get("text") or n.get("desc") or "").strip()
                for n in (ui_data.get("nodes") or [])
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
        xi, yi, err = self._check_point(x, y)
        if err:
            return err
        try:
            data = await self._submit(event, ACT_TAP, {"x": xi, "y": yi}, 8000)
        except _ToolError as exc:
            return str(exc)
        return f"👆 已点击 ({xi}, {yi}){self._brief(data)}"

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
        xi, yi, err = self._check_point(x, y)
        if err:
            return err
        duration = max(300, min(_cfg_int(duration_ms or 800, 800, "duration_ms"), 5000))
        try:
            data = await self._submit(
                event, ACT_LONG_PRESS, {"x": xi, "y": yi, "duration": duration}, 10000
            )
        except _ToolError as exc:
            return str(exc)
        return f"👆 已长按 ({xi}, {yi}) {duration}ms{self._brief(data)}"

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
        raw = str(text or "")
        if not raw.strip():  # 纯空格不算输入内容
            return "输入内容为空"
        try:
            data = await self._submit(
                event, ACT_TEXT, {"text": raw, "submit": bool(submit)}, 15000
            )
        except _ToolError as exc:
            return str(exc)
        how = data.get("method") or "setText"
        return f"⌨️ 已输入「{raw[:40]}」（{how}）{'，并已提交' if submit else ''}{self._brief(data)}"

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
        if "." in target:
            ok, why = self.policy.package_allowed(target)
            if not ok:
                return f"⛔ {why}"
        params = {"package": target} if "." in target else {"name": target}
        try:
            data = await self._submit(event, ACT_OPEN_APP, params, 20000)
        except _ToolError as exc:
            return str(exc)

        # 中文应用名在服务端解析不成包名，只能等手机回报真实前台再查一次黑名单，
        # 否则「打开微信」会绕过只认包名的 package_blacklist。
        pkg = str(data.get("pkg") or "").strip()
        ok, why = self.policy.package_allowed(pkg)
        if not ok:
            self.policy.audit(
                "deny_blacklist",
                user=str(event.get_sender_id() or ""),
                action=ACT_OPEN_APP,
                target=target,
                pkg=pkg,
                reason=why,
            )
            raise _ToolError(
                f"⛔ 已拦截：「{target}」打开后前台是 {pkg}，{why}。"
                "按安全策略不能停留在该应用，建议立刻用 phone_key home 返回桌面。"
            )

        launched = data.get("launched")
        warning = str(data.get("warning") or "").strip()
        seen_note = self._render_seen(data.get("seen"))
        if launched is False:
            text = f"❌ 没能打开「{target}」"
            text += f"：{warning}" if warning else "：系统没接受启动请求，可能没装或应用名不对"
        else:
            text = f"🚀 已打开「{target}」，当前前台：{pkg or '?'}"
            if data.get("bounced"):
                text += "\n（走了兜底路径：先把 AutoX 顶到前台再唤起，屏幕闪一下 AutoX 是正常的）"
            if warning:
                # 启动请求发出去了但前台没变，warning 里是手机端的具体原因，原样带给模型。
                text += f"\n⚠️ 手机端提示：{warning}"
        if seen_note and (launched is False or warning):
            text += f"\n{seen_note}"
        return text

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
        logger.warning("[dimensional-portal] 急停已执行，清空 %s 个任务", dropped)
        return f"🛑 已急停，清空 {dropped} 个待执行任务。手机端正在跑的那一步会做完。"


class _ToolError(Exception):
    """工具内部错误，message 直接展示给模型。"""


__all__ = ["DimensionalPortalPhoneBridge"]
