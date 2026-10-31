"""通过 /debug/submit 直接给手机下发任务，用于自动化回归（不经过大模型）。

用法：
    python tools/dbg.py '{"action":"status","params":{}}'
    python tools/dbg.py @steps.json          # steps.json = [{"action":..,"params":..}, ...]

结果同时打到 stdout（可能乱码）和 tmp/dbg_out.jsonl（UTF-8，用文件工具看）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("DAFEIYU_BRIDGE", "http://127.0.0.1:8765")
# 和插件配置里的 debug_token 保持一致；没填就让 /debug/submit 空着（返回 404）
DEBUG_TOKEN = os.environ.get("DAFEIYU_DEBUG_TOKEN", "")
OUT = Path(__file__).resolve().parent.parent / "tmp" / "dbg_out.jsonl"


def submit(action: str, params: dict, timeout_ms: int = 20000) -> dict:
    body = json.dumps(
        {"action": action, "params": params, "timeout_ms": timeout_ms}
    ).encode()
    req = urllib.request.Request(
        BASE + "/debug/submit",
        data=body,
        headers={"Content-Type": "application/json", "X-Debug-Token": DEBUG_TOKEN},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000 + 10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = {"ok": False, "error": f"HTTP {exc.code}: {exc.read()[:200]!r}"}
    except Exception as exc:  # 连接失败/超时
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    payload["_action"] = action
    payload["_params"] = params
    payload["_elapsed_ms"] = int((time.time() - started) * 1000)
    return payload


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    arg = sys.argv[1]
    if arg.startswith("@"):
        steps = json.loads(Path(arg[1:]).read_text(encoding="utf-8"))
    else:
        parsed = json.loads(arg)
        steps = parsed if isinstance(parsed, list) else [parsed]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for step in steps:
        result = submit(
            step["action"], step.get("params") or {}, int(step.get("timeout_ms", 20000))
        )
        lines.append(json.dumps(result, ensure_ascii=False))
        mark = "OK " if result.get("ok") else "ERR"
        data = result.get("data") or {}
        brief = (
            str(data.get("cur_pkg") or "")
            or f"keys={list(data)[:6]}"
            or str(result.get("error") or "")[:120]
        )
        print(f"{mark} {step['action']:10s} {result['_elapsed_ms']:6d}ms  {brief}"[:200])

    with OUT.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")
    print(f"--- 追加 {len(lines)} 条到 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
