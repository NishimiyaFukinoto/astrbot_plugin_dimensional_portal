"""M0 冒烟测试：不连真手机，用一个假手机客户端跑通整条链路。

跑法：  venv/Scripts/python.exe tests/smoke_test.py
它验证的是 bridge/ 与 safety/ 两个包（不依赖 astrbot），所以可以直接跑。
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import aiohttp

# Windows 控制台默认 GBK，打不出 ✅，统一走 UTF-8（重定向到文件后可正常读）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugin"
sys.path.insert(0, str(PLUGIN_DIR))

from bridge.hub import DeviceHub, DeviceOffline, TaskTimeout  # noqa: E402
from bridge.server import PhoneBridgeServer  # noqa: E402
from safety.policy import SafetyPolicy  # noqa: E402

HOST = "127.0.0.1"
PORT = 18765
TOKEN = "smoke-token"
BASE = f"http://{HOST}:{PORT}"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  ✅ {name}")
    else:
        FAILED.append(name)
        print(f"  ❌ {name} {detail}")


FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 512 + b"\xff\xd9"


class FakePhone:
    """模拟 shengjian_agent.js：心跳 + 长轮询 + 执行 + 回传。"""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.seq = 0
        self.executed: list[str] = []
        self.stopped = asyncio.Event()

    async def heartbeat_loop(self) -> None:
        while not self.stopped.is_set():
            try:
                await self.session.post(
                    f"{BASE}/hb",
                    json={
                        "device_id": "fake-phone",
                        "device": "Test Phone",
                        "android": "15",
                        "w": 1080,
                        "h": 2400,
                        "battery": 87,
                        "charging": True,
                        "screen_on": True,
                        "cur_pkg": "com.android.settings",
                        "accessibility": True,
                        "screen_capture": True,
                        "agent_version": "0.1.0-test",
                        "protocol": 1,
                    },
                )
            except Exception:
                pass
            await asyncio.sleep(0.3)

    async def poll_loop(self) -> None:
        while not self.stopped.is_set():
            try:
                async with self.session.get(
                    f"{BASE}/poll", params={"since": self.seq, "wait": 0.5}
                ) as resp:
                    payload = await resp.json()
            except Exception:
                await asyncio.sleep(0.2)
                continue
            self.seq = payload.get("server_seq", self.seq)
            for job in payload.get("jobs") or []:
                self.executed.append(job["action"])
                ok, data, error = self._run(job)
                try:
                    await self.session.post(
                        f"{BASE}/result",
                        json={"id": job["id"], "ok": ok, "data": data, "error": error},
                    )
                except Exception:
                    pass

    @staticmethod
    def _run(job: dict) -> tuple[bool, dict | None, str | None]:
        action, params = job["action"], job.get("params") or {}
        if action == "tap":
            return True, {"pkg": "com.android.settings", "clicked": [params["x"], params["y"]]}, None
        if action == "screen":
            return True, {
                "image_b64": base64.b64encode(FAKE_JPEG).decode(),
                "w": 1080,
                "h": 2400,
                "pkg": "com.android.settings",
            }, None
        if action == "ui":
            return True, {
                "pkg": "com.android.settings",
                "w": 1080,
                "h": 2400,
                "nodes": [
                    {"cls": "android.widget.TextView", "text": "设置", "clickable": True,
                     "editable": False, "cx": 540, "cy": 120, "id": "title"},
                    {"cls": "android.widget.EditText", "text": "", "desc": "搜索框",
                     "clickable": True, "editable": True, "cx": 540, "cy": 300, "id": "search"},
                ],
            }, None
        if action == "boom":
            return False, None, "手机端故意失败"
        return True, {"echo": action}, None

    async def start(self) -> None:
        self._hb = asyncio.create_task(self.heartbeat_loop())
        self._poll = asyncio.create_task(self.poll_loop())

    async def stop(self) -> None:
        self.stopped.set()
        for task in (self._hb, self._poll):
            task.cancel()
        await asyncio.gather(self._hb, self._poll, return_exceptions=True)


async def main() -> int:
    hub = DeviceHub(offline_after=5.0)
    policy = SafetyPolicy(
        allowed_user_ids="10001",
        rate_limit=1000,
        audit_path=Path(__file__).parent / "audit_test.jsonl",
    )
    server = PhoneBridgeServer(hub=hub, token=TOKEN, host=HOST, port=PORT, policy=policy)
    await server.start()

    headers = {"X-Token": TOKEN}
    async with aiohttp.ClientSession(headers=headers) as session:
        print("\n[1] 鉴权")
        async with session.get(f"{BASE}/healthz") as resp:
            check("healthz 无需 token", resp.status == 200, f"status={resp.status}")
        bad2 = aiohttp.ClientSession(headers={"X-Token": "wrong"})
        async with bad2.post(f"{BASE}/hb", json={}) as resp:
            check("错误 token 被拒 (401)", resp.status == 401, f"status={resp.status}")
        await bad2.close()

        print("\n[2] 离线时拒绝任务")
        check("无心跳时 offline", not hub.online)
        try:
            await hub.submit("tap", {"x": 1, "y": 1}, timeout_ms=500)
            check("离线拒绝", False, "应该抛 DeviceOffline")
        except DeviceOffline:
            check("离线拒绝", True)

        print("\n[3] 手机上线（心跳 → 长轮询）")
        phone = FakePhone(session)
        await phone.start()
        for _ in range(30):
            if hub.online:
                break
            await asyncio.sleep(0.1)
        check("心跳后 online", hub.online)
        snap = hub.snapshot()
        check("设备信息记录正确", snap["device"].get("w") == 1080, str(snap["device"])[:120])
        check("电量记录正确", snap["device"].get("battery") == 87)

        print("\n[4] 端到端任务")
        data = await hub.submit("tap", {"x": 100, "y": 200}, timeout_ms=5000)
        check("tap 拿到结果", data.get("clicked") == [100, 200], str(data))

        data = await hub.submit("screen", {"quality": 55}, timeout_ms=5000)
        raw = base64.b64decode(data["image_b64"])
        check("截图 base64 往返一致", raw == FAKE_JPEG, f"{len(raw)} bytes")
        check("截图带分辨率", data.get("w") == 1080 and data.get("h") == 2400)

        data = await hub.submit("ui", {}, timeout_ms=5000)
        check("UI 树 2 个节点", len(data.get("nodes") or []) == 2)
        editable = [n for n in data["nodes"] if n.get("editable")]
        check("UI 树标记了输入框", len(editable) == 1)

        print("\n[5] 失败与超时")
        try:
            await hub.submit("boom", {}, timeout_ms=3000)
            check("失败任务抛异常", False)
        except Exception as exc:  # TaskRejected
            check("失败任务抛异常", "故意失败" in str(exc), str(exc))

        print("\n[6] 统计与急停")
        stats = hub.snapshot()["stats"]
        check("成功计数", stats["tasks_succeeded"] >= 3, str(stats))
        check("失败计数", stats["tasks_failed"] >= 1, str(stats))

        print("\n[6b] 手机在线但不来取任务 → 超时")
        phone._poll.cancel()  # 只停长轮询，心跳还活着
        await asyncio.sleep(0.2)
        check("心跳还在，仍算在线", hub.online)
        try:
            await hub.submit("tap", {"x": 1, "y": 1}, timeout_ms=800)
            check("无人取任务时超时", False)
        except TaskTimeout:
            check("无人取任务时超时", True)

        print("\n[6c] 急停清空队列")
        t1 = asyncio.create_task(
            hub.submit("tap", {"x": 1, "y": 1}, timeout_ms=3000, require_online=False)
        )
        t2 = asyncio.create_task(
            hub.submit("tap", {"x": 2, "y": 2}, timeout_ms=3000, require_online=False)
        )
        await asyncio.sleep(0.1)
        dropped = hub.emergency_clear()
        check("急停丢弃 2 个任务", dropped == 2, f"dropped={dropped}")
        await asyncio.gather(t1, t2, return_exceptions=True)

        print("\n[6d] 手机彻底停掉 → 离线")
        await phone.stop()
        for _ in range(80):
            if not hub.online:
                break
            await asyncio.sleep(0.1)
        check("心跳停了变离线", not hub.online)
        try:
            await hub.submit("tap", {"x": 1, "y": 1}, timeout_ms=500)
            check("手机停后拒绝", False)
        except DeviceOffline:
            check("手机停后拒绝", True)

    print("\n[7] 安全策略（脱离服务器）")
    p = SafetyPolicy(allowed_user_ids="10001, 10002", rate_limit=3, rate_window_s=60)
    check("白名单放行", p.user_allowed("10001")[0])
    check("白名单拒绝", not p.user_allowed("99999")[0])
    for _ in range(3):
        p.rate_check("10001")
    check("限流生效", not p.rate_check("10001")[0])
    check("敏感包识别", p.is_sensitive_package("com.tencent.mm"))
    check("普通包放行", not p.is_sensitive_package("com.example.notes"))
    check("敏感界面文字", p.is_sensitive_screen("com.foo", "请输入支付密码")[0])
    check("普通界面放行", not p.is_sensitive_screen("com.foo", "今天天气不错")[0])

    await server.stop()

    print("\n" + "=" * 56)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：" + "、".join(FAILED))
        return 1
    print("M0 链路 OK：心跳 → 长轮询 → 执行 → 回传 → 统计")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
