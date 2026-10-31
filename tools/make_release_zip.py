"""打一个可以直接在 AstrBot WebUI「导入压缩包」的插件 zip。

产物：dist/astrbot_plugin_excalibur.zip
      zip 内只有一层目录 astrbot_plugin_excalibur/，里面是 plugin/ 的内容
      （main.py、metadata.yaml、_conf_schema.json、bridge/、safety/）

AstrBot 的安装逻辑见 core/astrbot/core/star/updater.py：
  _resolve_archive_root_dir() 找出唯一顶层目录 → find_plugin_metadata_entry()
  要求该目录下存在 metadata.yaml/yml → 解压时把这一层拍平。
所以「单层同名目录 + 目录里有 metadata.yaml」是最稳的布局。

用 AstrBot 的「导入压缩包」上传本文件即可，不需要 git、不需要命令行。
"""

from __future__ import annotations

import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "plugin"
DIST_DIR = ROOT / "dist"
NAME = "astrbot_plugin_excalibur"
SKIP_DIRS = {"__pycache__", "data", ".git"}


def read_version() -> str:
    text = (PLUGIN_DIR / "metadata.yaml").read_text(encoding="utf-8")
    m = re.search(r"^version:\s*[\"']?([^\"'\s]+)", text, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


def main() -> int:
    if not (PLUGIN_DIR / "metadata.yaml").is_file():
        print(f"[x] 找不到 {PLUGIN_DIR / 'metadata.yaml'}")
        return 1

    version = read_version()
    DIST_DIR.mkdir(exist_ok=True)
    out = DIST_DIR / f"{NAME}.zip"
    out_versioned = DIST_DIR / f"{NAME}-{version}.zip"

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.is_dir():
                continue
            if path.suffix in {".pyc", ".pyo"} or path.name == "audit.jsonl":
                continue
            arc = Path(NAME) / path.relative_to(PLUGIN_DIR)
            zf.write(path, arc.as_posix())

    shutil.copyfile(out, out_versioned)

    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()
    print(f"[ok] {out}  ({out.stat().st_size} 字节, {len(names)} 个文件)")
    print(f"[ok] {out_versioned}")
    print("[i] 内容预览：")
    for n in names[:8]:
        print("    " + n)
    if len(names) > 8:
        print(f"    ... 还有 {len(names) - 8} 个")

    fail = [n for n in names if not n.startswith(NAME + "/")]
    if fail:
        print(f"[x] 有文件不在 {NAME}/ 下：{fail[:3]}")
        return 1
    if f"{NAME}/metadata.yaml" not in names:
        print("[x] zip 里没有 metadata.yaml，AstrBot 会拒绝安装")
        return 1
    print("[i] 布局检查通过：单层目录 + metadata.yaml 在根")
    return 0


if __name__ == "__main__":
    sys.exit(main())
