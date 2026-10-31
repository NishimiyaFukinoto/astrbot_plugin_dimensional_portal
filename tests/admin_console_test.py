"""管理控制台 V1 端到端测试。

复用 live_server.py 的插件加载器 / 假手机 / 假事件，重点验证：
  - admin_token 鉴权（无 token 401、错 token 401、控制台错 k 403 且不回页面正文）
  - /admin/state 字段齐全、/admin/config 只认白名单且落盘、/admin/stop 清队列
  - 防爆破锁定（同 IP 连续失败 → 429）
  - admin_token 留空自动生成并写盘、填 off 时四个路由全部 404

跑法：
  venv/Scripts/python.exe tests/admin_console_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import aiohttp

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_server as live  # noqa: E402

LIVE_TOKEN = "live-token"
ADMIN = "admin-secret"
OWNER = "10001"


class FakeConfig(dict):
    """AstrBot 的 config 是个带 save_config() 的 dict；这里写临时文件方便验证「真的落盘了」。"""

    def __init__(self, data: dict[str, Any], path: Path) -> None:
        super().__init__(data)
        self.path = Path(path)
        self.saves = 0

    def save_config(self) -> None:
        self.saves += 1
        self.path.write_text(json.dumps(dict(self), ensure_ascii=False), encoding="utf-8")


async def _get(session: aiohttp.ClientSession, url: str, headers: dict | None = None):
    async with session.get(url, headers=headers or {}) as resp:
        return resp.status, await resp.text()


async def _post(
    session: aiohttp.ClientSession,
    url: str,
    payload: Any = None,
    headers: dict | None = None,
):
    async with session.post(url, json=payload, headers=headers or {}) as resp:
        return resp.status, await resp.text()


def _cfg(port: int, admin_token: str) -> dict[str, Any]:
    return {
        "token": LIVE_TOKEN,
        "listen_host": "127.0.0.1",
        "listen_port": port,
        "allowed_user_ids": OWNER,
        "rate_limit_per_minute": 100,
        "task_timeout_ms": 8000,
        "offline_after_seconds": 5,
        "screenshot_quality": 55,
        "max_screenshot_kb": 1500,
        "allow_screenshot_sensitive": False,
        "admin_token": admin_token,
    }


async def main() -> int:
    module = live.load_plugin_module()
    tmp = Path(tempfile.mkdtemp(prefix="excalibur-admin-"))
    os.environ["EXCALIBUR_AUDIT_PATH"] = str(tmp / "audit.jsonl")

    live.HOST = "127.0.0.1"
    live.PORT = 18770
    live.TOKEN = LIVE_TOKEN
    live.OWNER = OWNER
    live.FAKE_STATE.update(
        {
            "cur_pkg": "com.example.notes",
            "overlay": True,
            "overlay_note": "ok(WindowManager)",
            "screen_pkg": None,
            "ui_reply": None,
            "open_app_reply": None,
        }
    )

    print("\n[1] 启动主实例（admin_token 已配置）")
    cfg_path = tmp / "config-main.json"
    plugin = module.ExcaliburPhoneBridge(None, FakeConfig(_cfg(18770, ADMIN), cfg_path))
    await plugin.initialize()
    live.check("插件服务已启动", plugin.server.running)
    live.check("控制台路由已注册", plugin.server.admin_enabled)
    base = "http://127.0.0.1:18770"

    stop = asyncio.Event()
    errors: list = []
    async with aiohttp.ClientSession() as session:
        phone = asyncio.create_task(live.fake_phone(session, stop, errors))
        try:
            live.check("假手机已上线", await live.wait_online(plugin.hub))

            print("\n[2] 鉴权")
            status, body = await _get(session, f"{base}/console?k=wrong")
            live.check("控制台错误 k → 403", status == 403, f"status={status}")
            live.check("控制台错 k 不返回页面正文", "<!DOCTYPE" not in body and "我的圣剑" not in body)
            status, body = await _get(session, f"{base}/console?k={ADMIN}")
            live.check("控制台正确 k → 200", status == 200, f"status={status}")
            live.check("返回单文件 HTML", "<!DOCTYPE html>" in body and "QQ 白名单" in body)
            live.check(
                "页面零外部请求",
                "<script src=" not in body and "https://" not in body and "<link " not in body,
            )

            status, _ = await _get(session, f"{base}/admin/state")
            live.check("/admin/state 无 token → 401", status == 401, f"status={status}")
            status, _ = await _get(session, f"{base}/admin/state?k=wrong")
            live.check("/admin/state 错 token → 401", status == 401, f"status={status}")
            status, body = await _get(
                session, f"{base}/admin/state", headers={"X-Admin-Token": ADMIN}
            )
            state = json.loads(body)
            live.check("X-Admin-Token 头也能过", status == 200 and state.get("ok") is True)
            live.check(
                "state 字段齐全",
                all(k in state for k in ("ok", "online", "device", "config", "audit", "queue", "uptime_s")),
            )
            live.check("state 反映在线", state.get("online") is True)
            live.check("device 是手机快照", state["device"].get("device") == "Fake Pixel 9")
            live.check(
                "config 只暴露两项",
                set(state["config"].keys()) == {"allowed_user_ids", "rate_limit_per_minute"},
                str(state["config"]),
            )
            live.check("白名单是数组", state["config"]["allowed_user_ids"] == [OWNER])
            live.check("audit 是列表", isinstance(state["audit"], list))

            print("\n[3] 改白名单（只认 allowed_user_ids）")
            status, body = await _post(
                session,
                f"{base}/admin/config?k={ADMIN}",
                {"allowed_user_ids": ["10001", "10002"], "rate_limit_per_minute": 5, "token": "hacked"},
            )
            applied = (json.loads(body) or {}).get("applied") or {}
            live.check("加号成功", status == 200 and applied.get("allowed_user_ids") == ["10001", "10002"])
            live.check("policy 已生效", plugin.policy.user_allowed("10002")[0] is True)
            saved = json.loads(cfg_path.read_text(encoding="utf-8"))
            live.check("配置已落盘", saved.get("allowed_user_ids") == "10001,10002")
            live.check(
                "其它字段被忽略",
                saved.get("token") == LIVE_TOKEN and applied.get("rate_limit_per_minute") == 100,
            )

            status, body = await _post(
                session, f"{base}/admin/config?k={ADMIN}", {"allowed_user_ids": ["10001"]}
            )
            live.check(
                "删到只剩一个",
                status == 200 and (json.loads(body)["applied"]["allowed_user_ids"] == ["10001"]),
            )
            status, body = await _post(
                session, f"{base}/admin/config?k={ADMIN}", {"allowed_user_ids": []}
            )
            live.check(
                "删光被拒",
                status == 400 and "至少要保留" in (json.loads(body).get("error") or ""),
                f"status={status} {body}",
            )
            status, body = await _post(
                session, f"{base}/admin/config?k={ADMIN}", {"allowed_user_ids": ["10001", "abc"]}
            )
            live.check(
                "非数字被拒",
                status == 400 and "纯数字" in (json.loads(body).get("error") or ""),
                f"status={status} {body}",
            )
            status, body = await _post(
                session, f"{base}/admin/config?k={ADMIN}", {"foo": "bar"}
            )
            live.check(
                "无白名单字段时忽略请求",
                status == 200 and json.loads(body)["applied"]["allowed_user_ids"] == ["10001"],
            )
            audit_lines = Path(os.environ["EXCALIBUR_AUDIT_PATH"]).read_text(encoding="utf-8").splitlines()
            kinds = [json.loads(line).get("kind") for line in audit_lines if line.strip()]
            live.check("配置改动写了审计", "admin" in kinds, str(kinds))

            print("\n[4] 防爆破锁定")
            statuses = []
            for _ in range(12):
                status, _ = await _get(session, f"{base}/admin/state?k=bad")
                statuses.append(status)
            live.check("先 401 后触发 429", statuses[0] == 401 and 429 in statuses, str(statuses))
            status, _ = await _get(session, f"{base}/admin/state?k={ADMIN}")
            live.check("锁定期间正确密钥也 429", status == 429, f"status={status}")
        finally:
            stop.set()
            phone.cancel()
            try:
                await phone
            except asyncio.CancelledError:
                pass

    await plugin.terminate()
    live.check("主实例已停止", not plugin.server.running)

    print("\n[5] 急停清空排队任务（独立实例）")
    # 单开一个没有假手机的实例：队列里躺着的任务不会被长轮询叼走，能稳定验证 cleared 数量。
    stopper = module.ExcaliburPhoneBridge(None, FakeConfig(_cfg(18773, ADMIN), tmp / "config-stop.json"))
    await stopper.initialize()
    tasks = [
        asyncio.create_task(
            stopper.hub.submit("wait", {"ms": 1000}, timeout_ms=4000, require_online=False)
        )
        for _ in range(3)
    ]
    await asyncio.sleep(0.2)
    live.check("任务已排队", stopper.hub.queue_size == 3, str(stopper.hub.queue_size))
    async with aiohttp.ClientSession() as session:
        status, body = await _post(session, f"http://127.0.0.1:18773/admin/stop?k={ADMIN}")
    live.check("急停返回清掉数量", status == 200 and json.loads(body).get("cleared") == 3, body)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    live.check(
        "排队中的任务被拒绝",
        all(isinstance(item, module.TaskRejected) for item in results),
        str(results),
    )
    await stopper.terminate()

    print("\n[6] admin_token 留空 → 自动生成")
    auto_path = tmp / "config-auto.json"
    plugin2 = module.ExcaliburPhoneBridge(None, FakeConfig(_cfg(18771, ""), auto_path))
    await plugin2.initialize()
    token = str(plugin2.config.get("admin_token") or "")
    live.check("生成了 admin_token", plugin2.server.admin_enabled and len(token) >= 24, token)
    saved2 = json.loads(auto_path.read_text(encoding="utf-8"))
    live.check("自动生成的 token 已落盘", saved2.get("admin_token") == token)
    async with aiohttp.ClientSession() as session:
        status, body = await _get(session, f"http://127.0.0.1:18771/console?k={token}")
        live.check("自动生成的 token 能打开页面", status == 200 and "我的圣剑" in body)
    await plugin2.terminate()

    print("\n[7] admin_token=off → 控制台彻底关闭")
    plugin3 = module.ExcaliburPhoneBridge(None, FakeConfig(_cfg(18772, "off"), tmp / "config-off.json"))
    await plugin3.initialize()
    live.check("off 时不注册控制台", not plugin3.server.admin_enabled)
    async with aiohttp.ClientSession() as session:
        checks = [
            ("GET", "/console?k=anything", None),
            ("GET", "/admin/state?k=anything", None),
            ("POST", "/admin/config?k=anything", {}),
            ("POST", "/admin/stop?k=anything", {}),
        ]
        for method, path, payload in checks:
            if method == "GET":
                status, _ = await _get(session, f"http://127.0.0.1:18772{path}")
            else:
                status, _ = await _post(session, f"http://127.0.0.1:18772{path}", payload)
            live.check(f"off 时 {path.split('?')[0]} → 404", status == 404, f"status={status}")
    await plugin3.terminate()

    if errors:
        print(f"\n  （假手机内部异常 {len(errors)} 条，首条：{errors[0]}）")

    print(f"\n{'=' * 46}\n通过 {live.PASS} 项，失败 {live.FAIL} 项")
    return 0 if live.FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
