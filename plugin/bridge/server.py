"""HTTP 服务：手机端主动来要任务的那一端。

端点：
    POST /hb        心跳（10s 一次）
    GET  /poll      长轮询（最多挂 25s），有任务立刻返回
    POST /result    回传执行结果
    GET  /healthz   存活探测（无需 token，只回服务名和在线状态）

鉴权：请求头 X-Token，或 query 参数 ?token=（手机端有些库设头不方便时用）。
"""

from __future__ import annotations

import hmac
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .hub import DeviceHub
from .protocol import dumps, loads, now_ts, truncate

try:
    # 在 AstrBot 里跑时用它的插件日志代理（会自动打上 [插件名] 标签）。
    # 千万别自己 logging.getLogger("astrbot.xxx")——那种子 logger 不经过 AstrBot 的
    # _RecordEnricherFilter，日志格式化时会报
    # "Formatting field not found in record: 'plugin_tag'" 并把插件加载搞挂。
    from astrbot.api import logger as log  # type: ignore[assignment]
except Exception:  # 脱离 AstrBot 单独跑测试（tests/ 里的脚本）
    log = logging.getLogger("astrbot.phone_bridge")

MAX_POLL_WAIT_S = 30.0
DEFAULT_MAX_BODY = 16 * 1024 * 1024  # 截图 base64 走这条通道，留足余量

# 管理控制台：同一 IP 每分钟最多失败 10 次，超了锁 60 秒（内存计数，重启即清）
ADMIN_MAX_FAILS = 10
ADMIN_FAIL_WINDOW_S = 60.0
CONSOLE_HTML_PATH = Path(__file__).with_name("console.html")


def _token_match(supplied: str, expected: str) -> bool:
    """常量时间比较 token；任一侧含非 ASCII 字符时按不匹配处理。

    hmac.compare_digest 对 str 只接受 ASCII：手机端如果把中文 token 一类的值
    塞进 ?token=，直接比较会抛 TypeError 变成 HTTP 500。这里兜成 401。
    """
    try:
        return hmac.compare_digest(supplied, expected)
    except (TypeError, UnicodeError):
        return False


class PhoneBridgeServer:
    def __init__(
        self,
        hub: DeviceHub,
        token: str,
        host: str = "0.0.0.0",
        port: int = 8765,
        max_body: int = DEFAULT_MAX_BODY,
        policy: Any = None,
        debug_token: str = "",
        admin_token: str = "",
        console: Any = None,
    ) -> None:
        self.hub = hub
        self.token = token or ""
        self.host = host
        self.port = int(port)
        self.policy = policy
        self.debug_token = debug_token or ""
        # 管理控制台是可选件：admin_token 为空 / "off" / 没给适配器，就完全不注册路由。
        self.admin_token = (admin_token or "").strip()
        self.console = console
        self._admin_routes_added = False
        self._admin_fails: dict[str, list[float]] = {}
        self._console_html: str | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.started_at: float = 0.0

        self.app = web.Application(client_max_size=int(max_body))
        self.app.router.add_post("/hb", self.handle_heartbeat)
        self.app.router.add_get("/poll", self.handle_poll)
        self.app.router.add_post("/result", self.handle_result)
        self.app.router.add_get("/healthz", self.handle_healthz)
        if self.debug_token:
            self.app.router.add_post("/debug/submit", self.handle_debug_submit)
        self._ensure_admin_routes()

    # ------------------------------------------------------------ 管理控制台

    @property
    def admin_enabled(self) -> bool:
        return self._admin_routes_added

    def _ensure_admin_routes(self) -> None:
        """路由注册只做一次；没有适配器或 token 无效时保持 404。"""
        if self._admin_routes_added:
            return
        if self.console is None or not self.admin_token or self.admin_token.lower() == "off":
            return
        self.app.router.add_get("/console", self.handle_console)
        self.app.router.add_get("/admin/state", self.handle_admin_state)
        self.app.router.add_post("/admin/config", self.handle_admin_config)
        self.app.router.add_post("/admin/stop", self.handle_admin_stop)
        self._admin_routes_added = True
        log.info("[dimensional-portal] 管理控制台已启用（/console）")

    def enable_admin(self, admin_token: str) -> None:
        """initialize() 里自动生成 admin_token 后补注册控制台路由。"""
        self.admin_token = (admin_token or "").strip()
        self._ensure_admin_routes()

    def _admin_authorized(self, request: web.Request) -> bool:
        supplied = request.headers.get("X-Admin-Token") or request.query.get("k") or ""
        return _token_match(supplied, self.admin_token)

    def _admin_throttled(self, ip: str) -> bool:
        """同一 IP 60 秒内失败满 10 次就锁住（锁随窗口过期自动解除）。"""
        now = time.monotonic()
        hits = [t for t in self._admin_fails.get(ip, []) if now - t < ADMIN_FAIL_WINDOW_S]
        self._admin_fails[ip] = hits
        return len(hits) >= ADMIN_MAX_FAILS

    def _admin_fail(self, ip: str) -> None:
        self._admin_fails.setdefault(ip, []).append(time.monotonic())
        # 顺手清掉过期的 IP，防止被人扫端口时 dict 无限涨
        if len(self._admin_fails) > 64:
            cutoff = time.monotonic() - ADMIN_FAIL_WINDOW_S
            for key in [k for k, v in self._admin_fails.items() if not v or v[-1] < cutoff]:
                self._admin_fails.pop(key, None)

    def _admin_guard(self, request: web.Request) -> web.Response | None:
        ip = self._client_ip(request)
        if self._admin_throttled(ip):
            log.warning(
                "[dimensional-portal] 管理控制台尝试过多 %s %s from %s",
                request.method,
                request.path,
                ip,
            )
            return web.json_response({"ok": False, "error": "too many attempts"}, status=429)
        if not self._admin_authorized(request):
            self._admin_fail(ip)
            return self._deny_admin(request)
        return None

    def _deny_admin(self, request: web.Request) -> web.Response:
        log.warning(
            "[dimensional-portal] 管理控制台鉴权失败 %s %s from %s",
            request.method,
            request.path,
            self._client_ip(request),
        )
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    def _load_console_html(self) -> str | None:
        if self._console_html is not None:
            return self._console_html
        try:
            self._console_html = CONSOLE_HTML_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            log.error("[dimensional-portal] 读不到控制台页面 %s：%s", CONSOLE_HTML_PATH, exc)
            return None
        return self._console_html

    # ------------------------------------------------------------ 生命周期

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        self.started_at = time.monotonic()
        log.info(
            "[dimensional-portal] HTTP 服务已启动 http://%s:%s（token %s）",
            self.host,
            self.port,
            "已设置" if self.token else "未设置（危险）",
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            log.info("[dimensional-portal] HTTP 服务已停止")

    @property
    def running(self) -> bool:
        return self._runner is not None

    # ------------------------------------------------------------ 工具

    def _authorized(self, request: web.Request) -> bool:
        if not self.token:
            return True
        supplied = request.headers.get("X-Token") or request.query.get("token") or ""
        return _token_match(supplied, self.token)

    @staticmethod
    def _client_ip(request: web.Request) -> str:
        # Tailscale/局域网都直连，peername 就是真实来源
        peer = request.transport.get_extra_info("peername") if request.transport else None
        return peer[0] if peer else "-"

    def _deny(self, request: web.Request, reason: str) -> web.Response:
        log.warning(
            "[dimensional-portal] 拒绝 %s %s from %s：%s",
            request.method,
            request.path,
            self._client_ip(request),
            reason,
        )
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    # ------------------------------------------------------------ 路由

    async def handle_heartbeat(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return self._deny(request, "token 不匹配")
        raw = await request.read()
        payload = loads(raw) or {}
        if not isinstance(payload, dict):
            payload = {}

        info = {
            "device_id": str(payload.get("device_id") or "unknown")[:64],
            "device": str(payload.get("device") or "")[:64],
            "android": str(payload.get("android") or "")[:32],
            "w": _to_int(payload.get("w")),
            "h": _to_int(payload.get("h")),
            "battery": _to_int(payload.get("battery"), -1),
            "charging": bool(payload.get("charging")),
            "screen_on": bool(payload.get("screen_on")),
            "cur_pkg": str(payload.get("cur_pkg") or "")[:128],
            "accessibility": bool(payload.get("accessibility")),
            "screen_capture": bool(payload.get("screen_capture")),
            # 悬浮窗守卫决定「后台能不能打开微信/QQ」；旧脚本不上报，用 None 区分「没报」和「关了」。
            "overlay": None if payload.get("overlay") is None else bool(payload.get("overlay")),
            "overlay_note": str(payload.get("overlay_note") or "")[:128],
            "agent_version": str(payload.get("agent_version") or "")[:32],
            "protocol": _to_int(payload.get("protocol")),
            "ip": self._client_ip(request),
        }
        resp = self.hub.on_heartbeat(info)
        log.debug(
            "[dimensional-portal] 心跳 %s %sx%s 电量 %s%% 前台 %s",
            info["device_id"],
            info["w"],
            info["h"],
            info["battery"],
            info["cur_pkg"],
        )
        return web.json_response(resp)

    async def handle_poll(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return self._deny(request, "token 不匹配")
        # since 目前只作为手机端的游标回报，不参与任务回放：投递即出队（at-most-once）。
        try:
            since = int(request.query.get("since", "0"))
        except (TypeError, ValueError):
            since = 0
        try:
            wait_s = float(request.query.get("wait", "25"))
        except (TypeError, ValueError):
            wait_s = 25.0
        wait_s = max(0.5, min(wait_s, MAX_POLL_WAIT_S))

        payload = await self.hub.poll(since=since, wait_s=wait_s)
        return web.json_response(payload)

    async def handle_result(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return self._deny(request, "token 不匹配")
        payload = loads(await request.read()) or {}
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "bad payload"}, status=400)

        task_id = str(payload.get("id") or "")
        if not task_id:
            return web.json_response({"ok": False, "error": "missing id"}, status=400)

        ok = payload.get("ok")
        if not isinstance(ok, bool):
            # 不能 bool() 兜底：字符串 "false" 会被当成 True，工具会把失败当成功返回。
            return web.json_response({"ok": False, "error": "ok must be a boolean"}, status=400)
        data = payload.get("data")
        error = payload.get("error")
        accepted = self.hub.on_result(task_id, ok, data, error)
        log.info(
            "[dimensional-portal] 结果 %s %s%s",
            task_id,
            "成功" if ok else "失败",
            "" if accepted else "（已被丢弃：超时或未知任务）",
        )
        return web.json_response({"ok": True, "accepted": accepted})

    async def handle_healthz(self, request: web.Request) -> web.Response:
        # 不带 token 时只证明「服务活着」，不暴露在线状态/队列长度/运行时长。
        body: dict[str, Any] = {"ok": True, "service": "dimensional-portal-phone-bridge"}
        if not self._authorized(request):
            return web.json_response(body)
        body.update(
            {
                "online": self.hub.online,
                "queue": self.hub.queue_size,
                "uptime_s": round(time.monotonic() - self.started_at, 1)
                if self.started_at
                else 0,
                "ts": now_ts(),
            }
        )
        return web.json_response(body)


    async def handle_debug_submit(self, request: web.Request) -> web.Response:
        """本地调试用：直接向手机提交一个任务并等结果，不经过 LLM。

        只有插件配置里 debug_token 非空时才会注册这个路由，且请求头必须带
        匹配的 X-Debug-Token。正式使用时请把 debug_token 清空，避免有人绕过
        白名单和限流直接驱动手机。
        """
        if not self.debug_token:
            return web.json_response(
                {"ok": False, "error": "debug endpoint disabled"}, status=404
            )
        supplied = request.headers.get("X-Debug-Token") or ""
        if not _token_match(supplied, self.debug_token):
            return self._deny(request, "debug token 不匹配")

        payload = loads(await request.read()) or {}
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "bad payload"}, status=400)
        action = str(payload.get("action") or "")
        if not action:
            return web.json_response({"ok": False, "error": "missing action"}, status=400)
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            return web.json_response({"ok": False, "error": "bad params"}, status=400)
        timeout_ms = _to_int(payload.get("timeout_ms"), 20000)

        log.info("[dimensional-portal] 调试任务 %s %s", action, params)
        try:
            data = await self.hub.submit(action, params, timeout_ms=timeout_ms)
        except Exception as exc:  # 离线/超时/手机端报错：原样回给调用方
            return web.json_response({"ok": False, "error": str(exc)})
        return web.json_response({"ok": True, "data": data})

    # ------------------------------------------------------------ 控制台接口

    async def handle_console(self, request: web.Request) -> web.StreamResponse:
        ip = self._client_ip(request)
        if self._admin_throttled(ip):
            log.warning("[dimensional-portal] 管理控制台尝试过多 from %s", ip)
            return web.Response(status=429, text="Too Many Requests")
        if not self._admin_authorized(request):
            self._admin_fail(ip)
            log.warning("[dimensional-portal] 管理控制台鉴权失败 from %s", ip)
            return web.Response(status=403, text="Forbidden")
        html = self._load_console_html()
        if html is None:
            return web.json_response({"ok": False, "error": "console.html 缺失"}, status=500)
        resp = web.Response(text=html, content_type="text/html", charset="utf-8")
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"
        )
        return resp

    async def handle_admin_state(self, request: web.Request) -> web.StreamResponse:
        guard = self._admin_guard(request)
        if guard is not None:
            return guard
        snap = self.console.snapshot() or {}
        return web.json_response(
            {
                "ok": True,
                "online": self.hub.online,
                "device": self.hub.device,
                "config": snap.get("config") or {},
                "audit": snap.get("audit") or [],
                "queue": self.hub.queue_size,
                "uptime_s": round(time.monotonic() - self.started_at, 1) if self.started_at else 0.0,
            }
        )

    async def handle_admin_config(self, request: web.Request) -> web.StreamResponse:
        guard = self._admin_guard(request)
        if guard is not None:
            return guard
        try:
            payload = await request.json(loads=loads)
        except Exception:
            return web.json_response({"ok": False, "error": "请求体必须是 JSON"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "请求体必须是 JSON 对象"}, status=400)
        try:
            applied = self.console.apply_config(payload, ip=self._client_ip(request))
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        log.info("[dimensional-portal] 控制台改了白名单：%s", applied.get("allowed_user_ids"))
        return web.json_response({"ok": True, "applied": applied})

    async def handle_admin_stop(self, request: web.Request) -> web.StreamResponse:
        guard = self._admin_guard(request)
        if guard is not None:
            return guard
        cleared = int(self.console.stop_all())
        log.info("[dimensional-portal] 控制台急停：清掉排队任务 %s 个", cleared)
        return web.json_response({"ok": True, "cleared": cleared})


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = ["PhoneBridgeServer", "dumps", "truncate"]
