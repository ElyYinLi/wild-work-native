#!/usr/bin/env python3
"""把上游版本写入 ios/project.yml 的四个版本字段（幂等，可重复执行）。

Info.plist 的 CFBundleShortVersionString / CFBundleVersion 与 Xcode 的
MARKETING_VERSION / CURRENT_PROJECT_VERSION 必须一致，否则 Xcode 里显示的版本
和 App 里读到的版本会不同。CI 在 `xcodegen generate` 之前调用本脚本。

用法：
  python3 scripts/set_ios_version.py --version 2.11.7 --code 21107
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "ios" / "project.yml"

# 字段 -> 使用 version 还是 code
FIELDS = {
    "CFBundleShortVersionString": "version",
    "MARKETING_VERSION": "version",
    "CFBundleVersion": "code",
    "CURRENT_PROJECT_VERSION": "code",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="形如 2.11.7")
    ap.add_argument("--code", required=True, help="整数，形如 21107")
    ap.add_argument("--spec", default=str(DEFAULT_SPEC), help="project.yml 路径")
    args = ap.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        sys.exit(f"✗ 版本号格式不正确: {args.version}")
    if not re.fullmatch(r"\d+", args.code):
        sys.exit(f"✗ versionCode 必须是整数: {args.code}")

    path = pathlib.Path(args.spec)
    if not path.is_file():
        sys.exit(f"✗ 找不到 {path}")

    text = path.read_text(encoding="utf-8")
    for key, kind in FIELDS.items():
        value = args.version if kind == "version" else args.code
        pattern = re.compile(rf"(^[ \t]*{re.escape(key)}[ \t]*:[ \t]*).*$", re.M)
        # 保留原有缩进，只替换值；重复执行时依然能匹配，因此幂等
        text, count = pattern.subn(lambda m: f'{m.group(1)}"{value}"', text)
        if count == 0:
            sys.exit(f"✗ 找不到版本字段: {key}")
    path.write_text(text, encoding="utf-8")
    print(f"✓ 版本已写入 {path}: {args.version} ({args.code})")


if __name__ == "__main__":
    main()
