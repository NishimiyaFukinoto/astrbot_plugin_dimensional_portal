"""打一个可以直接在 AstrBot WebUI「导入压缩包」的插件 zip。

产物：dist/astrbot_plugin_dimensional_portal.zip
      zip 内只有一层目录 astrbot_plugin_dimensional_portal/，里面是插件本体（仓库根目录的
      main.py、metadata.yaml、_conf_schema.json、bridge/、safety/）
      （main.py、metadata.yaml、_conf_schema.json、bridge/、safety/），外加：
        - logo.png                ← 来自 assets/logo.png（放在插件根，插件列表里显示）
        - phone/dimensional_portal_agent.js ← 手机端脚本（保持占位符版本，方便用户自己改地址/token）

AstrBot 的安装逻辑见 core/astrbot/core/star/updater.py：
  _resolve_archive_root_dir() 找出唯一顶层目录 → find_plugin_metadata_entry()
  要求该目录下存在 metadata.yaml/yml → 解压时把这一层拍平。
所以「单层同名目录 + 目录里有 metadata.yaml」是最稳的布局。

用 AstrBot 的「导入压缩包」上传本文件即可，不需要 git、不需要命令行。

用法：
  python tools/make_release_zip.py                 # 输出到 dist/
  python tools/make_release_zip.py --out-dir 某个目录
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT  # 插件本体就在仓库根目录
ASSETS_LOGO = ROOT / "assets" / "logo.png"
PHONE_SCRIPT = ROOT / "phone" / "dimensional_portal_agent.js"
DIST_DIR = ROOT / "dist"
NAME = "astrbot_plugin_dimensional_portal"
SKIP_DIRS = {"__pycache__", "data", ".git"}
# 只收插件本体的目录/文件，docs、tests、tools、phone、assets、tmp、dist 一律不进 zip。
INCLUDE_DIRS = {"bridge", "safety"}
INCLUDE_FILES = {
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def read_version() -> str:
    text = (PLUGIN_DIR / "metadata.yaml").read_text(encoding="utf-8")
    m = re.search(r"^version:\s*[\"']?([^\"'\s]+)", text, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


def build_zip(out: Path) -> list[str]:
    """写 zip，返回 namelist。插件本体 + logo.png + phone/ 脚本。"""
    entries: list[tuple[Path, str]] = []
    for path in sorted(PLUGIN_DIR.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_dir():
            continue
        if path.suffix in SKIP_SUFFIXES or path.name == "audit.jsonl":
            continue
        rel = path.relative_to(PLUGIN_DIR)
        top = rel.parts[0]
        if len(rel.parts) == 1:
            if top not in INCLUDE_FILES:
                continue
        elif top not in INCLUDE_DIRS:
            continue
        entries.append((path, Path(NAME, rel.as_posix())))

    # logo 在 assets/、手机端脚本在 phone/，单独补进插件根目录。
    if ASSETS_LOGO.is_file():
        entries.append((ASSETS_LOGO, Path(NAME, "logo.png").as_posix()))
    else:
        print(f"[!] 找不到 {ASSETS_LOGO}，zip 里不会有 logo.png")
    if PHONE_SCRIPT.is_file():
        entries.append((PHONE_SCRIPT, Path(NAME, "phone", PHONE_SCRIPT.name).as_posix()))
    else:
        print(f"[!] 找不到 {PHONE_SCRIPT}，zip 里不会有手机端脚本")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in entries:
            zf.write(src, arc)

    with zipfile.ZipFile(out, "r") as zf:
        return zf.namelist()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="打 AstrBot「导入压缩包」可安装的插件 zip",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DIST_DIR,
        help=f"输出目录（默认 {DIST_DIR}）",
    )
    args = parser.parse_args(argv)

    if not (PLUGIN_DIR / "metadata.yaml").is_file():
        print(f"[x] 找不到 {PLUGIN_DIR / 'metadata.yaml'}")
        return 1

    version = read_version()
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{NAME}.zip"
    out_versioned = out_dir / f"{NAME}-{version}.zip"

    names = build_zip(out)
    shutil.copyfile(out, out_versioned)

    print(f"[ok] {out}  ({out.stat().st_size} 字节, {len(names)} 个文件)")
    print(f"[ok] {out_versioned}")
    print("[i] 内容预览：")
    for n in names[:10]:
        print("    " + n)
    if len(names) > 10:
        print(f"    ... 还有 {len(names) - 10} 个")

    fail = [n for n in names if not n.startswith(NAME + "/")]
    if fail:
        print(f"[x] 有文件不在 {NAME}/ 下：{fail[:3]}")
        return 1
    for must in ("metadata.yaml", "logo.png", "phone/dimensional_portal_agent.js"):
        if f"{NAME}/{must}" not in names:
            print(f"[x] zip 里缺少 {must}，检查一下再发版")
            return 1
    print("[i] 布局检查通过：单层目录 + metadata.yaml/logo/手机脚本都在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
