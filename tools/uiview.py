"""把 tmp/dbg_out.jsonl 里最近一条 ui 结果整理成人能看的列表（UTF-8 写文件）。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tmp" / "dbg_out.jsonl"
OUT = ROOT / "tmp" / "ui_summary.txt"


def main() -> None:
    lines = [ln for ln in SRC.read_text(encoding="utf-8").splitlines() if ln.strip()]
    uis = [json.loads(ln) for ln in lines if json.loads(ln).get("_action") == "ui"]
    if not uis:
        OUT.write_text("没有 ui 结果\n", encoding="utf-8")
        return
    last = uis[-1]
    data = last.get("data") or {}
    nodes = data.get("nodes") or []
    out = [
        f"pkg={data.get('pkg')} w={data.get('w')} h={data.get('h')} nodes={len(nodes)} "
        f"total={data.get('total')} truncated={data.get('truncated')}",
        "",
    ]
    for idx, node in enumerate(nodes):
        text = node.get("text") or ""
        desc = node.get("desc") or ""
        label = text or ("<desc>" + desc if desc else "")
        if not label:
            continue
        cls = str(node.get("cls") or "").replace("android.widget.", "").replace("android.view.", "")
        flags = []
        if node.get("clickable"):
            flags.append("可点")
        if node.get("editable"):
            flags.append("可输入")
        out.append(
            f"[{idx:02d}] {label!r:44s} {cls:22s} "
            f"({node.get('cx')},{node.get('cy')}) {node.get('left')},{node.get('top')}"
            f"-{node.get('right')},{node.get('bottom')} {' '.join(flags)}"
        )
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(out)} lines)")


if __name__ == "__main__":
    main()
