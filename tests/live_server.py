"""用真实 AstrBot 环境把插件类跑起来，走一遍「工具函数 → hub → HTTP → 假手机 → 回传」。

和 smoke_test.py 的区别：
  - smoke_test 只测 bridge/（不碰 astrbot 包、不碰插件类）
  - 这里加载的是真正的 plugin/main.py，实例化真正的 ExcaliburPhoneBridge，
    调用它真正的 initialize() 起 aiohttp，再直接调 phone_* 工具函数

所以能提前抓到「initialize 起不来 / 工具里 _gate 逻辑写错 / event.send 用法不对」这类问题。

跑法：
  venv/Scripts/python.exe tests/live_server.py
"""

from __future__ import annotations

import asyncio
import base64
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
    pkg_name = "astrbot_plugin_excalibur"
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

FAKE_STATE = {"cur_pkg": "com.example.notes"}

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
                    data_out = {"image_b64": TINY_JPG_B64, "w": 1080, "h": 2400,
                                "pkg": "com.example.notes", "format": "jpg", "quality": 55}
                elif action == "ui":
                    data_out = UI_PAYLOAD
                elif action == "tap":
                    data_out = {"clicked": [params.get("x"), params.get("y")],
                                "pkg": "com.example.notes"}
                elif action == "key":
                    data_out = {"key": params.get("name"), "pkg": "com.example.notes"}
                else:
                    data_out = {"pkg": "com.example.notes"}
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
    }
    plugin = module.ExcaliburPhoneBridge(None, cfg)

    print(f"\n[1] initialize() 真实监听 {HOST}:{PORT}")
    await plugin.initialize()
    check("服务已启动", plugin.server.running)

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

            print("\n[3] 假手机心跳 → hub.online")
            check("手机上线", await wait_online(plugin.hub), f"snapshot={plugin.hub.snapshot()}")

            print("\n[4] 直接调工具函数（走真实 _gate → _submit → HTTP）")
            ev = FakeEvent()
            status = await module.ExcaliburPhoneBridge.phone_status(plugin, ev)
            check("phone_status 报在线", "在线" in status and "1080x2400" in status, status)

            tap = await module.ExcaliburPhoneBridge.phone_tap(plugin, ev, 540, 1200)
            check("phone_tap 成功", "已点击" in tap, tap)

            ui = await module.ExcaliburPhoneBridge.phone_ui(plugin, ev, 80)
            check("phone_ui 渲染出坐标", "@(900,2200)" in ui, ui)
            check("phone_ui 过滤空容器", "FrameLayout" not in ui, ui)
            check("phone_ui 标出可点", "[可点]" in ui, ui)

            shot = await module.ExcaliburPhoneBridge.phone_see(plugin, ev, False)
            check("phone_see 返回截图摘要", "截图已发到聊天" in shot, shot)
            check("phone_see 真的 send 了 1 条消息", len(ev.sent) == 1, f"{len(ev.sent)} 条")

            how = await module.ExcaliburPhoneBridge.phone_key(plugin, ev, "home")
            check("phone_key 成功", "已按 home" in how, how)

            bad = await module.ExcaliburPhoneBridge.phone_key(plugin, ev, "selfdestruct")
            check("phone_key 拒绝非法按键", "不认识的按键" in bad, bad)

            print("\n[5] 白名单拦截")
            stranger = FakeEvent("99999")
            denied = await module.ExcaliburPhoneBridge.phone_tap(plugin, stranger, 1, 1)
            check("非白名单用户被拒", "没有权限" in denied, denied)
            check("被拒不发送任何消息", not stranger.sent, str(stranger.sent))

            print("\n[6] 敏感界面不截图")
            FAKE_STATE["cur_pkg"] = "com.tencent.mm"  # 让下一次心跳上报微信在前台
            await asyncio.sleep(0.6)
            blocked = await module.ExcaliburPhoneBridge.phone_see(plugin, ev, False)
            check("微信界面拒绝截图", "不把截图推到 QQ" in blocked, blocked)
            FAKE_STATE["cur_pkg"] = "com.example.notes"
            await asyncio.sleep(0.6)
            allowed = await module.ExcaliburPhoneBridge.phone_see(plugin, ev, False)
            check("切走后恢复截图", "截图已发到聊天" in allowed, allowed)

            print("\n[7] 急停")
            stopd = await module.ExcaliburPhoneBridge.phone_stop(plugin, ev)
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

    print("\n[8] terminate()")
    await plugin.terminate()
    check("服务已停止", not plugin.server.running)

    # 截图临时文件应该留在 temp 里（发给 QQ 后由平台读取）
    shot_dir = Path(module.SHOT_DIR)
    check("截图文件已落到临时目录", shot_dir.exists() and any(shot_dir.glob("shot_*.jpg")))

    print(f"\n{'=' * 46}\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
