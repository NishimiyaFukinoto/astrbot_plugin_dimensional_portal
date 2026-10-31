"""本地调试 AstrBot 面板 API 的小工具（用本机 jwt_secret 签面板同款 token）。

例：
  venv/Scripts/python.exe tools/dash.py GET /api/plugins
  venv/Scripts/python.exe tools/dash.py GET /api/plugins/failed
  venv/Scripts/python.exe tools/dash.py POST /api/plugin/reload '{"name":"astrbot_plugin_shengjian"}'
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _find_instance() -> Path:
    """AstrBot 实例目录：优先环境变量 ASTRBOT_INSTANCE，否则取启动器下的第一个实例。"""
    env = os.environ.get("ASTRBOT_INSTANCE")
    if env:
        return Path(env)
    base = Path.home() / ".astrbot_launcher" / "instances"
    cands = sorted(p for p in base.glob("*") if (p / "core").is_dir()) if base.is_dir() else []
    if not cands:
        raise SystemExit(
            "找不到 AstrBot 实例：请设置环境变量 ASTRBOT_INSTANCE 指向实例目录（含 core/ 的那层）"
        )
    return cands[0]


INSTANCE = _find_instance()
CONFIG = INSTANCE / "core" / "data" / "cmd_config.json"
# 面板端口由启动器随机分配，用 netstat 查，或用 ASTRBOT_DASH_PORT 指定
BASE = f"http://127.0.0.1:{os.environ.get('ASTRBOT_DASH_PORT', '6185')}"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    method, path = argv[1].upper(), argv[2]
    payload = argv[3] if len(argv) > 3 else None

    import jwt

    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    dash = cfg.get("dashboard") or {}
    token = jwt.encode(
        {
            "username": dash["username"],
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=10),
        },
        dash["jwt_secret"],
        algorithm="HS256",
    )
    req = urllib.request.Request(
        BASE + path,
        data=payload.encode() if payload else None,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method=method,
    )
    # 不走系统代理：直连 localhost（urllib 默认会读注册表里的代理设置，导致 getaddrinfo 失败）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
            print(f"HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}")
        raw = exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        print(f"❌ {exc.reason}")
        return 1
    try:
        print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(raw[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
