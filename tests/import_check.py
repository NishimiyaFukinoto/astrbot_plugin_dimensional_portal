"""验证 plugin/main.py 能在真实 AstrBot v4.29.3 环境里被导入。

这一步会真正走一遍 @filter.llm_tool 的注册逻辑（它会解析 docstring 生成 JSON Schema，
类型写错会直接抛 ValueError），注册结果进全局 llm_tools，所以能提前抓到
"工具注册失败 / 参数 schema 不对"这类只在运行时才暴露的问题。

跑法：
  venv/Scripts/python.exe tests/import_check.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

# Windows 控制台默认 GBK，打不出 ✅，统一走 UTF-8（重定向到文件后可正常读）
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
CORE = INSTANCE / "core"
PLUGIN = Path(__file__).resolve().parents[1] / "plugin"

for p in (str(CORE), str(PLUGIN.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

EXPECTED = {
    "phone_see",
    "phone_ui",
    "phone_tap",
    "phone_long_press",
    "phone_swipe",
    "phone_type",
    "phone_key",
    "phone_open_app",
    "phone_find_tap",
    "phone_status",
    "phone_stop",
}


def main() -> int:
    import astrbot

    print(f"astrbot {astrbot.__version__}  来自 {Path(astrbot.__file__).parent}")

    from astrbot.core.provider.register import llm_tools

    before = {f.name for f in llm_tools.func_list}

    pkg_name = "astrbot_plugin_shengjian"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(PLUGIN)]
    sys.modules[pkg_name] = pkg

    spec = importlib.util.spec_from_file_location(f"{pkg_name}.main", PLUGIN / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.main"] = module
    spec.loader.exec_module(module)

    after = {f.name for f in llm_tools.func_list}
    new_tools = sorted(after - before)
    print(f"\n本次注册的工具 {len(new_tools)} 个：")
    for fn in llm_tools.func_list:
        if fn.name not in new_tools:
            continue
        params = []
        for arg in fn.parameters or []:
            if isinstance(arg, dict):
                params.append(f"{arg.get('name')}:{arg.get('type')}")
            else:
                params.append(str(arg))
        desc = (fn.description or "").strip().splitlines()
        first = desc[0] if desc else "（没有说明！）"
        print(f"  - {fn.name:<18} ({', '.join(params)})  {first}")

    missing = EXPECTED - set(new_tools)
    extra = set(new_tools) - EXPECTED

    print()
    if missing:
        print(f"❌ 少了这些工具：{sorted(missing)}")
        return 1
    if extra:
        print(f"⚠️ 多出来的工具（确认一下是不是有意加的）：{sorted(extra)}")

    cls = module.ShengjianPhoneBridge
    print(f"插件类：{cls.__module__}.{cls.__name__}")
    print(f"有 initialize：{hasattr(cls, 'initialize')}  有 terminate：{hasattr(cls, 'terminate')}")

    print("\n✅ 插件模块导入成功，全部工具通过 docstring → JSON Schema 解析")
    print("   注意：这里没有真的启动 aiohttp 服务，那由 AstrBot 加载插件时调 initialize() 负责。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
