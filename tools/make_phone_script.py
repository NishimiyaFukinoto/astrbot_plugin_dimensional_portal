"""把仓库里的手机脚本模板生成为「带真实地址和 token」的本地脚本。

仓库里的 `phone/excalibur_agent.js` 只有占位符（`http://100.x.y.z:8765` / `改成你的token`），
是为了开源脱敏。真正推到手机上跑的是这个脚本生成的 `.local.js`（已在 .gitignore 里）。

用法：

    # 1) 直接在命令行给值（最省事）
    EXCALIBUR_BASES="http://100.88.64.39:8765,http://10.172.89.162:8765" \
    EXCALIBUR_TOKEN="你的token" \
    python tools/make_phone_script.py

    # 2) 或者写一个 .env 风格的本地文件，再 source 进来
    python tools/make_phone_script.py --out phone/excalibur_agent.local.js

生成完用 adb 推到手机：

    adb push phone/excalibur_agent.local.js /storage/emulated/0/脚本/excalibur_agent.js

地址怎么查：电脑上执行 "C:\\Program Files\\Tailscale\\tailscale.exe" ip -4 拿 100.x，
ipconfig 拿局域网地址；两个都用英文逗号隔开，第一个放 Tailscale。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "phone" / "excalibur_agent.js"
DEFAULT_OUT = ROOT / "phone" / "excalibur_agent.local.js"

# Windows 控制台默认 GBK，打印中文/符号会 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _die(msg: str) -> "None":
    print(msg, file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成带真实配置的手机端脚本")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出文件（默认 phone/excalibur_agent.local.js）")
    parser.add_argument("--token", default=os.environ.get("EXCALIBUR_TOKEN", ""), help="插件配置里的 token")
    parser.add_argument(
        "--bases",
        default=os.environ.get("EXCALIBUR_BASES", ""),
        help="电脑地址，逗号分隔，第一个建议填 Tailscale 的 100.x",
    )
    args = parser.parse_args()

    token = (args.token or "").strip()
    if not token or token == "改成你的token":
        _die(
            "缺少 token。\n"
            "  用 EXCALIBUR_TOKEN=\"你的token\" python tools/make_phone_script.py\n"
            "  token 在 AstrBot WebUI → 插件 → 我的圣剑 → 手机端共享密钥"
        )

    raw_bases = [b.strip().rstrip("/") for b in (args.bases or "").split(",") if b.strip()]
    if not raw_bases:
        _die(
            "缺少电脑地址。\n"
            "  用 EXCALIBUR_BASES=\"http://100.x.y.z:8765,http://192.168.x.y:8765\" python tools/make_phone_script.py\n"
            "  查 Tailscale 地址：\"C:\\\\Program Files\\\\Tailscale\\\\tailscale.exe\" ip -4"
        )
    for base in raw_bases:
        if not re.match(r"^https?://[0-9A-Za-z_.\-]+:\d+$", base):
            _die(f"地址格式不对：{base}（要写成 http://IP:端口，不要带路径）")

    if not TEMPLATE.is_file():
        _die(f"找不到模板：{TEMPLATE}")
    text = TEMPLATE.read_text(encoding="utf-8")

    bases_block = "var BASES = [\n" + ",\n".join(f'    "{b}"' for b in raw_bases) + "\n];"
    text, n1 = re.subn(r"var BASES = \[.*?\];", bases_block, text, count=1, flags=re.S)
    text, n2 = re.subn(r'var TOKEN = "[^"]*";', f'var TOKEN = "{token}";', text, count=1)
    if not (n1 and n2):
        _die("模板结构变了：没找到 `var BASES = [...]` 或 `var TOKEN = \"...\"`，请更新生成脚本")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    print(f"✅ 已生成 {out}")
    print(f"   地址 {len(raw_bases)} 个：{', '.join(raw_bases)}")
    print(f"   token 长度 {len(token)}")
    print("   推到手机：adb push \"%s\" /storage/emulated/0/脚本/excalibur_agent.js" % out.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
