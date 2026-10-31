"""重置 AstrBot 面板登录密码（忘了密码时用；AstrBot 最好先停掉）。

原理：面板密码存在 data/cmd_config.json 的 dashboard.pbkdf2_password，
格式 `pbkdf2_sha256$600000$<salt_hex>$<digest_hex>`（600000 轮 PBKDF2-HMAC-SHA256），
老的 dashboard.password 是 MD5。这里两个都写新值，并让下次登录强制改密码。

用法（用 AstrBot 自己的 venv python 跑）：
  python tools/reset_dashboard_password.py                 # 生成随机密码
  python tools/reset_dashboard_password.py 你想用的密码     # 指定密码（≥8 位）
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import secrets
import os
import shutil
import string
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def _find_config() -> Path:
    """AstrBot 实例的 cmd_config.json：优先环境变量 ASTRBOT_INSTANCE，否则取启动器下的第一个实例。"""
    env = os.environ.get("ASTRBOT_INSTANCE")
    if env:
        return Path(env) / "core" / "data" / "cmd_config.json"
    base = Path.home() / ".astrbot_launcher" / "instances"
    cands = sorted(p for p in base.glob("*") if (p / "core").is_dir()) if base.is_dir() else []
    if not cands:
        raise SystemExit(
            "找不到 AstrBot 实例：请设置环境变量 ASTRBOT_INSTANCE 指向实例目录（含 core/ 的那层）"
        )
    return cands[0] / "core" / "data" / "cmd_config.json"


CONFIG = _find_config()
ITERATIONS = 600_000


def hash_password(raw: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", raw.encode("utf-8"), bytes.fromhex(salt), ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def gen_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("password", nargs="?", default="")
    args = ap.parse_args()

    raw = args.password or gen_password()
    if len(raw) < 8:
        print("❌ 面板要求密码至少 8 位")
        return 1

    raw_bytes = CONFIG.read_bytes()
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    text = raw_bytes.decode("utf-8-sig")
    cfg = json.loads(text)
    dash = cfg.get("dashboard") or {}
    username = dash.get("username")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = CONFIG.with_name(f"cmd_config.json.bak-{stamp}")
    shutil.copy2(CONFIG, backup)

    new_pbkdf2 = hash_password(raw)
    new_md5 = hashlib.md5(raw.encode("utf-8")).hexdigest()

    out, n1 = re.subn(
        r'("pbkdf2_password"\s*:\s*)"[^"]*"', lambda m: f'{m.group(1)}"{new_pbkdf2}"', text
    )
    out, n2 = re.subn(
        r'("password"\s*:\s*)"[^"]*"', lambda m: f'{m.group(1)}"{new_md5}"', out, count=1
    )
    out, n3 = re.subn(
        r'("password_change_required"\s*:\s*)(true|false)',
        r"\1true",
        out,
    )
    if not (n1 and n2):
        print(f"❌ 没找到字段（pbkdf2={n1}, password={n2}），配置结构可能变了")
        return 1

    CONFIG.write_bytes(out.encode("utf-8-sig" if has_bom else "utf-8"))
    print(f"✅ 已重置：用户名 {username}，新密码 {raw}")
    print(f"   备份：{backup.name}")
    print("   下次登录会要求改密码（password_change_required=true 命中 {} 处）".format(n3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
