#!/usr/bin/env python3
"""校验 iOS / Android 两份「容器注入层」脚本逐行一致。

背景：面板适配靠运行时注入，不改上游源码。iOS 的脚本内联在 RootView.swift 的
PanelTweaks.script，Android 的在 PanelTweaks.java 的 SCRIPT。两份必须完全一样，
否则两个平台会出现「同一个 bug 只修了一半」。CI 在构建前跑这一步，几毫秒成本。
"""
import difflib
import pathlib
import re
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]
SWIFT = ROOT / "ios/WildWorkNative/RootView.swift"
JAVA = ROOT / "android/app/src/main/java/com/wildwork/nativeapp/PanelTweaks.java"

# 三引号块：Swift 多行字符串 / Java text block
PATTERNS = {
    SWIFT: r'static let script = """\n(.*?)\n[ \t]*"""',
    JAVA: r'public static final String SCRIPT = """\n(.*?)\n[ \t]*"""',
}

REQUIRED = [
    "viewport-fit=cover",        # 安全区
    "safe-area-inset-top",       # 顶部状态栏留白
    "safe-area-inset-bottom",    # 底部 Home 指示条
    "右键托盘图标退出",           # 桌面专属 footer 清理
    "acct-show-na",              # 虚拟账号首次启动收起
    "内置免费通道",               # 虚拟账号常驻说明
]


def extract(path: pathlib.Path, pattern: str) -> list[str]:
    if not path.is_file():
        sys.exit(f"✗ 文件不存在：{path}")
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.S)
    if not match:
        sys.exit(f"✗ 找不到注入脚本块（三引号提取失败）：{path}")
    lines = [line.strip() for line in textwrap.dedent(match.group(1)).strip().splitlines()]
    if not lines:
        sys.exit(f"✗ 注入脚本为空：{path}")
    return lines


def main() -> None:
    swift = extract(SWIFT, PATTERNS[SWIFT])
    java = extract(JAVA, PATTERNS[JAVA])

    if swift != java:
        print("✗ iOS / Android 注入脚本不一致：")
        print("\n".join(difflib.unified_diff(swift, java, "RootView.swift", "PanelTweaks.java", lineterm="")))
        sys.exit(1)

    for token in REQUIRED:
        if not any(token in line for line in swift):
            sys.exit(f"✗ 注入脚本缺少关键项：{token}")

    print(f"✓ 注入脚本逐行一致（{len(swift)} 行），关键项齐全")


if __name__ == "__main__":
    main()
