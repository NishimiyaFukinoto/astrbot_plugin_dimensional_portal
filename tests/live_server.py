"""用真实 AstrBot 环境把插件类跑起来，走一遍「工具函数 → hub → HTTP → 假手机 → 回传」。

和 smoke_test.py 的区别：
  - smoke_test 只测 bridge/（不碰 astrbot 包、不碰插件类）
  - 这里加载的是真正的 plugin/main.py，实例化真正的 DimensionalPortalPhoneBridge，
    调用它真正的 initialize() 起 aiohttp，再直接调 phone_* 工具函数

所以能提前抓到「initialize 起不来 / 工具里 _gate 逻辑写错 / event.send 用法不对」这类问题。

跑法：
  venv/Scripts/python.exe tests/live_server.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
import types
from pathlib import Path

import aiohttp

# Windows 控制台默认 GBK，打不出 ✅，统一走 UTF-8（重定向到文件后可正常读）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def _find_instance() -> Path | None:
    """AstrBot 实例目录：优先环境变量 ASTRBOT_INSTANCE，否则取启动器下的第一个实例。

    找不到时返回 None —— 此时直接用当前环境已安装的 astrbot 包（CI 里就是这种情况）。
    """
    env = os.environ.get("ASTRBOT_INSTANCE")
    if env:
        return Path(env)
    base = Path.home() / ".astrbot_launcher" / "instances"
    cands = sorted(p for p in base.glob("*") if (p / "core").is_dir()) if base.is_dir() else []
    return cands[0] if cands else None


INSTANCE = _find_instance()
CORE = (INSTANCE / "core") if INSTANCE else None
PLUGIN = Path(__file__).resolve().parents[1] / "plugin"

if CORE is None:
    print("[提示] 没找到本地 AstrBot 实例，改为使用当前环境已安装的 astrbot 包")

for _p in ([str(CORE)] if CORE else []) + [str(PLUGIN.parent)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

HOST = "127.0.0.1"
PORT = 18766
TOKEN = "live-token"
OWNER = "10001"

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def load_plugin_module():
    pkg_name = "astrbot_plugin_dimensional_portal"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(PLUGIN)]
    sys.modules[pkg_name] = pkg
    spec = importlib.util.spec_from_file_location(f"{pkg_name}.main", PLUGIN / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.main"] = module
    spec.loader.exec_module(module)
    return module


class FakeEvent:
    """够用的 AstrMessageEvent 替身：工具只用到 get_sender_id / send。"""

    def __init__(self, sender_id: str = OWNER) -> None:
        self._sender_id = sender_id
        self.sent = []

    def get_sender_id(self) -> str:
        return self._sender_id

    async def send(self, chain) -> None:
        self.sent.append(chain)


# 1x1 深色 JPG 的 base64，够当「截图数据」用
TINY_JPG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)

FAKE_STATE = {
    "cur_pkg": "com.example.notes",   # 心跳上报的前台
    "overlay": True,                  # 悬浮窗守卫状态
    "overlay_note": "ok(WindowManager)",
    "screen_pkg": None,               # 截图那一刻的真实前台；None = 跟 cur_pkg 一致
    "ui_reply": None,                 # 自定义 UI 回包；None = 默认笔记界面
    "open_app_reply": None,           # 自定义 open_app 回包；None = 默认
}

UI_PAYLOAD = {    "pkg": "com.example.notes",
    "w": 1080,
    "h": 2400,
    "nodes": [
        {"cls": "android.widget.TextView", "text": "我的笔记", "id": "title",
         "clickable": False, "editable": False, "left": 0, "top": 100,
         "right": 1080, "bottom": 200, "cx": 540, "cy": 150},
        {"cls": "android.widget.Button", "text": "新建", "id": "fab",
         "clickable": True, "editable": False, "left": 800, "top": 2100,
         "right": 1000, "bottom": 2300, "cx": 900, "cy": 2200},
        {"cls": "android.widget.FrameLayout", "text": "", "id": "",
         "clickable": False, "editable": False, "left": 0, "top": 0,
         "right": 1080, "bottom": 2400, "cx": 540, "cy": 1200},
    ],
}


async def fake_phone(session: aiohttp.ClientSession, stop: asyncio.Event, log: list) -> None:
    """假手机：心跳 + 长轮询 + 执行。"""

    async def heartbeat_loop() -> None:
        while not stop.is_set():
            body = {
                "device_id": "fakephone1",
                "device": "Fake Pixel 9",
                "android": "15",
                "w": 1080,
                "h": 2400,
                "battery": 88,
                "charging": True,
                "screen_on": True,
                "cur_pkg": FAKE_STATE["cur_pkg"],
                "accessibility": True,
                "screen_capture": True,
                "overlay": FAKE_STATE["overlay"],
                "overlay_note": FAKE_STATE["overlay_note"],
                "agent_version": "0.1.0",
                "protocol": 1,
            }
            try:
                async with session.post(
                    f"http://{HOST}:{PORT}/hb",
                    json=body,
                    headers={"X-Token": TOKEN},
                ) as resp:
                    await resp.read()
            except Exception as exc:  # 服务还没起来之类
                log.append(f"hb-error {exc}")
            await asyncio.sleep(0.3)

    async def poll_loop() -> None:
        since = 0
        while not stop.is_set():
            try:
                async with session.get(
                    f"http://{HOST}:{PORT}/poll",
                    params={"since": since, "wait": 2},
                    headers={"X-Token": TOKEN},
                ) as resp:
                    data = await resp.json()
            except Exception as exc:
                log.append(f"poll-error {exc}")
                await asyncio.sleep(0.3)
                continue
            since = data.get("server_seq", since)
            for job in data.get("jobs") or []:
                action = job["action"]
                params = job.get("params") or {}
                data_out = {}
                if action == "screen":
                    # screen_pkg 用来模拟「心跳说 A、截图那一刻其实是 B」的 TOCTOU 场景。
                    data_out = {"image_b64": TINY_JPG_B64, "w": 1080, "h": 2400,
                                "pkg": FAKE_STATE["screen_pkg"] or FAKE_STATE["cur_pkg"],
                                "format": "jpg", "quality": 55}
                elif action == "ui":
                    data_out = FAKE_STATE["ui_reply"] or dict(UI_PAYLOAD, pkg=FAKE_STATE["cur_pkg"])
                elif action == "tap":
                    data_out = {"clicked": [params.get("x"), params.get("y")],
                                "pkg": FAKE_STATE["cur_pkg"]}
                elif action == "text":
                    data_out = {"method": "setText", "submitted": bool(params.get("submit")),
                                "pkg": FAKE_STATE["cur_pkg"]}
                elif action == "key":
                    data_out = {"key": params.get("name"), "pkg": FAKE_STATE["cur_pkg"]}
                elif action == "find_text":
                    data_out = {"found": True, "tapped": True, "total": 1,
                                "matched": params.get("text"), "cx": 900, "cy": 2200,
                                "pkg": FAKE_STATE["cur_pkg"]}
                elif action == "open_app":
                    data_out = FAKE_STATE["open_app_reply"] or {
                        "launched": True, "pkg": FAKE_STATE["cur_pkg"],
                        "target": params.get("package") or params.get("name"),
                        "bounced": False, "seen": None, "warning": "",
                    }
                else:
                    data_out = {"pkg": FAKE_STATE["cur_pkg"]}
                async with session.post(
                    f"http://{HOST}:{PORT}/result",
                    json={"id": job["id"], "ok": True, "data": data_out, "error": None},
                    headers={"X-Token": TOKEN},
                ) as resp:
                    await resp.read()

    await asyncio.gather(heartbeat_loop(), poll_loop())


async def wait_online(hub, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if hub.online:
            return True
        await asyncio.sleep(0.05)
    return False


async def main() -> int:
    module = load_plugin_module()
    cfg = {
        "token": TOKEN,
        "listen_host": HOST,
        "listen_port": PORT,
        "allowed_user_ids": OWNER,
        "rate_limit_per_minute": 100,
        "task_timeout_ms": 8000,
        "offline_after_seconds": 5,
        "screenshot_quality": 55,
        "max_screenshot_kb": 1500,
        "allow_screenshot_sensitive": False,
        # 测试里把微信也放进黑名单，才能验证「中文应用名打开后按真实 pkg 复查」这条路径。
        "package_blacklist": "com.android.systemui,com.tencent.mm",
        "debug_token": "debug-token",
    }
    plugin = module.DimensionalPortalPhoneBridge(None, cfg)

    print(f"\n[1] initialize() 真实监听 {HOST}:{PORT}")
    await plugin.initialize()
    check("服务已启动", plugin.server.running)
    # 冷启动：还没收到任何心跳，不能因为 0 秒有「上次心跳」就判在线。
    check("冷启动不算在线", plugin.hub.online is False, str(plugin.hub.snapshot()))
    check("冷启动时心跳年龄为 None", plugin.hub.snapshot()["heartbeat_age"] is None)

    stop = asyncio.Event()
    errors: list = []
    async with aiohttp.ClientSession() as session:
        phone = asyncio.create_task(fake_phone(session, stop, errors))
        try:
            print("\n[2] /healthz（免 token）")
            async with session.get(f"http://{HOST}:{PORT}/healthz") as resp:
                hz = await resp.json()
            check("healthz 200", resp.status == 200, f"status={resp.status}")
            check("healthz 免 token 可访问", hz.get("ok") is True, str(hz))
            check(
                "healthz 未授权不暴露细节",
                "online" not in hz and "uptime_s" not in hz,
                str(hz),
            )
            async with session.get(
                f"http://{HOST}:{PORT}/healthz", headers={"X-Token": TOKEN}
            ) as resp:
                hz_auth = await resp.json()
            check(
                "healthz 授权后带在线信息",
                "online" in hz_auth and "uptime_s" in hz_auth,
                str(hz_auth),
            )
            # hmac.compare_digest 遇到非 ASCII 会抛 TypeError；参数里塞中文 token
            # 必须回 401，而不是 500。
            async with session.get(
                f"http://{HOST}:{PORT}/poll", params={"token": "令牌", "wait": 0}
            ) as resp:
                await resp.read()
            check("非 ASCII token 回 401 而不是 500", resp.status == 401, f"status={resp.status}")

            print("\n[3] 假手机心跳 → hub.online")
            check("手机上线", await wait_online(plugin.hub), f"snapshot={plugin.hub.snapshot()}")

            print("\n[4] 直接调工具函数（走真实 _gate → _submit → HTTP）")
            ev = FakeEvent()
            status = await module.DimensionalPortalPhoneBridge.phone_status(plugin, ev)
            check("phone_status 报在线", "在线" in status and "1080x2400" in status, status)

            tap = await module.DimensionalPortalPhoneBridge.phone_tap(plugin, ev, 540, 1200)
            check("phone_tap 成功", "已点击" in tap, tap)

            lp = await module.DimensionalPortalPhoneBridge.phone_long_press(plugin, ev, 540, 1200, 900)
            check("phone_long_press 成功", "已长按" in lp and "900ms" in lp, lp)

            sw = await module.DimensionalPortalPhoneBridge.phone_swipe(
                plugin, ev, 900, 2000, 200, 400, 300
            )
            check(
                "phone_swipe 成功",
                "已滑动" in sw and "(900,2000)→(200,400)" in sw,
                sw,
            )

            typed = await module.DimensionalPortalPhoneBridge.phone_type(plugin, ev, "你好")
            check("phone_type 成功", "已输入" in typed and "你好" in typed, typed)

            blank = await module.DimensionalPortalPhoneBridge.phone_type(plugin, ev, "   ")
            check("phone_type 拒绝纯空格", "输入内容为空" in blank, blank)

            oob = await module.DimensionalPortalPhoneBridge.phone_tap(plugin, ev, 5000, 5000)
            check("越界坐标被拦", "超出屏幕范围" in oob, oob)

            ft = await module.DimensionalPortalPhoneBridge.phone_find_tap(plugin, ev, "设置", 0, True)
            check("phone_find_tap 找到并点击", "找到" in ft and "@(900,2200)" in ft, ft)

            async with session.post(
                f"http://{HOST}:{PORT}/debug/submit",
                headers={"X-Debug-Token": "debug-token"},
                json={"action": "tap", "params": {"x": 10, "y": 20}, "timeout_ms": 5000},
            ) as resp:
                dbg = await resp.json()
            check("/debug/submit 200", resp.status == 200 and dbg.get("ok") is True,
                  f"status={resp.status} {dbg}")
            check("调试任务拿到真实结果",
                  (dbg.get("data") or {}).get("clicked") == [10, 20], str(dbg))

            ui = await module.DimensionalPortalPhoneBridge.phone_ui(plugin, ev, 80)
            check("phone_ui 渲染出坐标", "@(900,2200)" in ui, ui)
            check("phone_ui 过滤空容器", "FrameLayout" not in ui, ui)
            check("phone_ui 标出可点", "[可点]" in ui, ui)

            shot = await module.DimensionalPortalPhoneBridge.phone_see(plugin, ev, False)
            check("phone_see 返回截图摘要", "截图已发到聊天" in shot, shot)
            check("phone_see 真的 send 了 1 条消息", len(ev.sent) == 1, f"{len(ev.sent)} 条")

            how = await module.DimensionalPortalPhoneBridge.phone_key(plugin, ev, "home")
            check("phone_key 成功", "已按 home" in how, how)

            bad = await module.DimensionalPortalPhoneBridge.phone_key(plugin, ev, "selfdestruct")
            check("phone_key 拒绝非法按键", "不认识的按键" in bad, bad)

            print("\n[5] 白名单拦截")
            stranger = FakeEvent("99999")
            denied = await module.DimensionalPortalPhoneBridge.phone_tap(plugin, stranger, 1, 1)
            check("非白名单用户被拒", "没有权限" in denied, denied)
            check("被拒不发送任何消息", not stranger.sent, str(stranger.sent))

            print("\n[6] 敏感界面不截图")
            FAKE_STATE["cur_pkg"] = "com.tencent.mm"  # 让下一次心跳上报微信在前台
            await asyncio.sleep(0.6)
            blocked = await module.DimensionalPortalPhoneBridge.phone_see(plugin, ev, False)
            check("微信界面拒绝截图", "不把截图推到 QQ" in blocked, blocked)
            FAKE_STATE["cur_pkg"] = "com.example.notes"
            await asyncio.sleep(0.6)
            allowed = await module.DimensionalPortalPhoneBridge.phone_see(plugin, ev, False)
            check("切走后恢复截图", "截图已发到聊天" in allowed, allowed)

            print("\n[7] 敏感截图复查（心跳与截图包名不一致）")
            FAKE_STATE["cur_pkg"] = "com.example.notes"
            FAKE_STATE["screen_pkg"] = "com.tencent.mm"  # 截图那一刻真实前台是微信
            await asyncio.sleep(0.6)                     # 等心跳把 cur_pkg 刷回 notes
            shots_before = len(list(Path(module.SHOT_DIR).glob("shot_*.jpg")))
            sent_before = len(ev.sent)
            try:
                recheck = await module.DimensionalPortalPhoneBridge.phone_see(plugin, ev, False)
            except module._ToolError as exc:
                recheck = str(exc)
            check("截图包名与心跳不一致时拒绝", "已拦截" in recheck, recheck)
            check("被拦截的截图没有 send", len(ev.sent) == sent_before, f"{len(ev.sent)} 条")
            check(
                "被拦截的截图没有落盘",
                len(list(Path(module.SHOT_DIR).glob("shot_*.jpg"))) == shots_before,
            )
            FAKE_STATE["screen_pkg"] = None

            print("\n[8] 敏感关键词走 UI 文本")
            FAKE_STATE["ui_reply"] = dict(
                UI_PAYLOAD,
                nodes=[
                    {"cls": "android.widget.EditText", "text": "请输入验证码", "id": "code",
                     "clickable": True, "editable": True, "left": 100, "top": 900,
                     "right": 980, "bottom": 1050, "cx": 540, "cy": 975},
                ],
            )
            try:
                keyword_blocked = await module.DimensionalPortalPhoneBridge.phone_see(plugin, ev, True)
            except module._ToolError as exc:
                keyword_blocked = str(exc)
            check("UI 关键词命中也被拦", "已拦截" in keyword_blocked, keyword_blocked)
            FAKE_STATE["ui_reply"] = None

            print("\n[9] 中文应用名开黑名单应用")
            early = await module.DimensionalPortalPhoneBridge.phone_open_app(plugin, ev, "com.tencent.mm")
            check("包名形态前置拦截", "禁止列表" in early, early)
            FAKE_STATE["open_app_reply"] = {"launched": True, "pkg": "com.tencent.mm",
                                            "target": "微信", "bounced": False,
                                            "seen": None, "warning": ""}
            try:
                opened = await module.DimensionalPortalPhoneBridge.phone_open_app(plugin, ev, "微信")
            except module._ToolError as exc:
                opened = str(exc)
            check("按真实 pkg 拦截黑名单", "已拦截" in opened and "com.tencent.mm" in opened, opened)
            FAKE_STATE["open_app_reply"] = None

            print("\n[10] 敏感界面写操作门槛")
            FAKE_STATE["cur_pkg"] = "com.tencent.mm"
            await asyncio.sleep(0.6)
            typed = await module.DimensionalPortalPhoneBridge.phone_type(plugin, ev, "hello")
            check("敏感界面打字被拒", "已拦截写操作" in typed, typed)
            plugin.allow_sensitive_write = True
            typed_ok = await module.DimensionalPortalPhoneBridge.phone_type(plugin, ev, "hello")
            check("打开敏感写开关后放行", "已输入" in typed_ok, typed_ok)
            plugin.allow_sensitive_write = False
            FAKE_STATE["cur_pkg"] = "com.example.notes"
            await asyncio.sleep(0.6)

            print("\n[11] 悬浮窗守卫透传")
            FAKE_STATE["overlay"] = False
            FAKE_STATE["overlay_note"] = "没有悬浮窗权限"
            await asyncio.sleep(0.6)
            off = await module.DimensionalPortalPhoneBridge.phone_status(plugin, ev)
            check("守卫关了要提示", "悬浮窗守卫：关" in off and "没有悬浮窗权限" in off, off)
            FAKE_STATE["overlay"] = True
            FAKE_STATE["overlay_note"] = "ok(WindowManager)"
            await asyncio.sleep(0.6)
            on = await module.DimensionalPortalPhoneBridge.phone_status(plugin, ev)
            check("守卫开着正常", "悬浮窗守卫：开" in on, on)

            print("\n[12] open_app 诊断透传")
            warning_text = "启动请求发出去了，但前台还是 com.example.notes"
            FAKE_STATE["open_app_reply"] = {"launched": True, "pkg": "com.example.notes",
                                            "target": "笔记", "bounced": True,
                                            "seen": None, "warning": warning_text}
            warned = await module.DimensionalPortalPhoneBridge.phone_open_app(plugin, ev, "笔记")
            check("warning 原文带回", warning_text in warned and "已打开" in warned, warned)
            check("bounced 兜底被说明", "兜底" in warned, warned)
            FAKE_STATE["open_app_reply"] = {"launched": False, "pkg": "com.example.notes",
                                            "target": "不存在", "bounced": False,
                                            "warning": "",
                                            "seen": {"pkg": "com.example.launcher",
                                                     "win": {"pkg": "com.example.launcher"},
                                                     "popup_maybe": True,
                                                     "texts": ["允许", "拒绝"]}}
            failed = await module.DimensionalPortalPhoneBridge.phone_open_app(plugin, ev, "不存在")
            check("失败时带 seen 快照",
                  "没能打开" in failed and "可能有弹窗" in failed and "允许" in failed, failed)
            FAKE_STATE["open_app_reply"] = None

            print("\n[13] 急停")
            stopd = await module.DimensionalPortalPhoneBridge.phone_stop(plugin, ev)
            check("phone_stop 有反馈", "急停" in stopd, stopd)
        finally:
            stop.set()
            phone.cancel()
            try:
                await phone
            except asyncio.CancelledError:
                pass

        if errors:
            print(f"\n  （假手机内部异常 {len(errors)} 条，首条：{errors[0]}）")

    print("\n[14] terminate()")
    await plugin.terminate()
    check("服务已停止", not plugin.server.running)

    # 截图临时文件应该留在 temp 里（发给 QQ 后由平台读取）
    shot_dir = Path(module.SHOT_DIR)
    check("截图文件已落到临时目录", shot_dir.exists() and any(shot_dir.glob("shot_*.jpg")))

    print(f"\n{'=' * 46}\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
