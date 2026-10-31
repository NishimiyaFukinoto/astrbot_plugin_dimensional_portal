"""让正在运行的 AstrBot 重载本插件（开发时省得手动重启）。

做法：用本机 AstrBot 配置里的 dashboard.jwt_secret 签一个和面板登录同款的 JWT，
再 POST /api/plugin/reload。只在 localhost 用，不打印密钥。

跑法：
  venv/Scripts/python.exe tools/reload_plugin.py
  venv/Scripts/python.exe tools/reload_plugin.py --name astrbot_plugin_dafeiyu_phone
"""

from __future__ import annotations

import argparse
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
DEFAULT_BASE = f"http://127.0.0.1:{os.environ.get('ASTRBOT_DASH_PORT', '6185')}"


def mint_token(username: str, secret: str) -> str:
    import jwt  # AstrBot 依赖，venv 里有

    payload = {
        "username": username,
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(minutes=10),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="astrbot_plugin_dafeiyu_phone")
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    dash = cfg.get("dashboard") or {}
    secret = dash.get("jwt_secret")
    username = dash.get("username")
    if not secret or not username:
        print("❌ cmd_config.json 里没有 dashboard.jwt_secret / username，面板可能还没初始化")
        return 1

    token = mint_token(username, secret)
    body = json.dumps({"name": args.name}).encode()
    req = urllib.request.Request(
        f"{args.base}/api/plugin/reload",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"HTTP {resp.status}")
            print(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}")
        print(exc.read().decode("utf-8", "replace"))
        return 1
    except urllib.error.URLError as exc:
        print(f"❌ 连不上面板 {args.base}：{exc.reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
