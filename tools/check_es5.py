"""手机端脚本的 ES5 守卫。

AutoX.js 6 跑在 Rhino（ES5）里：`=>`、`let`、`const`、模板字符串、`.includes()` 这类
ES6 语法会直接抛语法错误，而 `node --check` 是宽松的（Node 认 ES2022），拦不住。
CI 和本地都跑这个脚本，扫 `phone/*.js` 里禁止的写法。

用法：
  python tools/check_es5.py            # 扫 phone/ 下所有 .js
  python tools/check_es5.py 文件.js ...  # 只扫指定文件

发现问题返回 1；全干净返回 0。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHONE_DIR = ROOT / "phone"

# (正则, 说明)。顺序无所谓，同一行最多报一条。
RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"=>"), "箭头函数 =>"),
    (re.compile(r"(?<![\w$.])let\s+"), "let 声明（ES5 只有 var）"),
    (re.compile(r"(?<![\w$.])const\s+"), "const 声明（ES5 只有 var）"),
    (re.compile(r"`"), "模板字符串反引号"),
    (re.compile(r"\.includes\s*\("), ".includes()"),
    (re.compile(r"\.startsWith\s*\("), ".startsWith()"),
    (re.compile(r"\.endsWith\s*\("), ".endsWith()"),
    (re.compile(r"(?<![\w$.])class\s+\w"), "class 语法"),
    (re.compile(r"(?<![\w$.])\.\.\.\s*\w"), "展开运算符 ..."),
]


def _code_part(line: str) -> str:
    """去掉行尾 // 注释，避免注释里的「=>」误报（简单版，够用）。"""
    idx = line.find("//")
    return line if idx < 0 else line[:idx]


def scan_file(path: Path) -> list[str]:
    problems: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = _code_part(line)
        for pattern, label in RULES:
            if pattern.search(code):
                problems.append(f"{path.as_posix()}:{lineno}: {label} → {line.strip()[:90]}")
                break
    return problems


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] if argv else sorted(PHONE_DIR.glob("*.js"))
    if not paths:
        print(f"[x] {PHONE_DIR} 下没有 .js")
        return 1
    problems: list[str] = []
    for path in paths:
        if not path.is_file():
            print(f"[x] 找不到 {path}")
            return 1
        problems.extend(scan_file(path))
    if problems:
        print(f"[x] 发现 {len(problems)} 处 ES6 语法，AutoX.js(Rhino) 会语法错误：")
        for p in problems:
            print("  " + p)
        return 1
    print(f"[ok] ES5 检查通过：{len(paths)} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
