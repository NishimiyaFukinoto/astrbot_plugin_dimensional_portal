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
    ) -> None:
        self.hub = hub
        self.token = token or ""
        self.host = host
        self.port = int(port)
        self.policy = policy
        self.debug_token = debug_token or ""
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

    # ------------------------------------------------------------ 生命周期

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        self.started_at = time.monotonic()
        log.info(
            "[shengjian] HTTP 服务已启动 http://%s:%s（token %s）",
            self.host,
            self.port,
            "已设置" if self.token else "未设置（危险）",
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            log.info("[shengjian] HTTP 服务已停止")

    @property
    def running(self) -> bool:
        return self._runner is not None

    # ------------------------------------------------------------ 工具

    def _authorized(self, request: web.Request) -> bool:
        if not self.token:
            return True
        supplied = request.headers.get("X-Token") or request.query.get("token") or ""
        return hmac.compare_digest(supplied, self.token)

    @staticmethod
    def _client_ip(request: web.Request) -> str:
        # Tailscale/局域网都直连，peername 就是真实来源
        peer = request.transport.get_extra_info("peername") if request.transport else None
        return peer[0] if peer else "-"

    def _deny(self, request: web.Request, reason: str) -> web.Response:
        log.warning(
            "[shengjian] 拒绝 %s %s from %s：%s",
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
            "agent_version": str(payload.get("agent_version") or "")[:32],
            "protocol": _to_int(payload.get("protocol")),
            "ip": self._client_ip(request),
        }
        resp = self.hub.on_heartbeat(info)
        log.debug(
            "[shengjian] 心跳 %s %sx%s 电量 %s%% 前台 %s",
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

        ok = bool(payload.get("ok"))
        data = payload.get("data")
        error = payload.get("error")
        accepted = self.hub.on_result(task_id, ok, data, error)
        log.info(
            "[shengjian] 结果 %s %s%s",
            task_id,
            "成功" if ok else "失败",
            "" if accepted else "（已被丢弃：超时或未知任务）",
        )
        return web.json_response({"ok": True, "accepted": accepted})

    async def handle_healthz(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "service": "shengjian-phone-bridge",
                "online": self.hub.online,
                "queue": self.hub.queue_size,
                "uptime_s": round(time.monotonic() - self.started_at, 1)
                if self.started_at
                else 0,
                "ts": now_ts(),
            }
        )


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
        if not hmac.compare_digest(supplied, self.debug_token):
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

        log.info("[shengjian] 调试任务 %s %s", action, params)
        try:
            data = await self.hub.submit(action, params, timeout_ms=timeout_ms)
        except Exception as exc:  # 离线/超时/手机端报错：原样回给调用方
            return web.json_response({"ok": False, "error": str(exc)})
        return web.json_response({"ok": True, "data": data})


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = ["PhoneBridgeServer", "dumps", "truncate"]
