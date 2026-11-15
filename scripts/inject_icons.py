#!/usr/bin/env python3
"""给 .app 补一层「传统图标」兜底。

为什么需要
----------
iOS 的桌面图标有两条路径：

  1. asset catalog（Assets.car）→ 由 actool 编译，CFBundleIcons 由 actool 自动注入；
  2. 传统 CFBundleIconFiles + bundle 根目录的 `AppIcon60x60@2x.png` 之类的 PNG。

只要 (1) 正常，本脚本什么都不做。但 actool 一旦因为 xcodegen 配置、Xcode 版本
等原因没跑，(1) 就静默失效：App 内 UI 完全正常（它用自己的 SF Symbol / 内联图形），
桌面上却是一片空白 —— 这个坑已经踩过一次。

所以打包后在这里补 (2)：把 icon-*.png 按系统约定命名拷进 .app 根目录，并在
Info.plist 写入 CFBundleIcons。两条路径同时存在时系统优先用 Assets.car，
不会因为兜底而变丑。

用法：inject_icons.py <app 目录>
"""

from __future__ import annotations

import plistlib
import shutil
import struct
import sys
from pathlib import Path

# (目标文件名, 期望像素边长)
SLOTS = [
    ("AppIcon29x29@2x.png", 58),
    ("AppIcon29x29@3x.png", 87),
    ("AppIcon40x40@2x.png", 80),
    ("AppIcon40x40@3x.png", 120),
    ("AppIcon60x60@2x.png", 120),
    ("AppIcon60x60@3x.png", 180),
]


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        data = path.read_bytes()[:24]
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    except Exception:
        return None


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：inject_icons.py <app 目录>", file=sys.stderr)
        return 2
    app = Path(sys.argv[1])
    info_path = app / "Info.plist"
    if not info_path.is_file():
        print(f"[inject-icons] {info_path} 不存在，跳过", file=sys.stderr)
        return 1

    info = plistlib.loads(info_path.read_bytes())
    icons = info.get("CFBundleIcons")
    if isinstance(icons, dict):
        primary = icons.get("CFBundlePrimaryIcon", {})
        if primary.get("CFBundleIconName") or primary.get("CFBundleIconFiles"):
            print("[inject-icons] 已有 CFBundleIcons，保持 actool 结果不动")
            return 0

    # 找源图：优先 bundle 里已有的 icon-*.png，其次仓库里的 AppIcon.appiconset
    sources: list[Path] = []
    sources.extend(sorted(app.glob("icon-*.png")))
    if not sources:
        repo_set = Path(__file__).resolve().parent.parent / \
            "ios/WildWorkNative/Assets.xcassets/AppIcon.appiconset"
        sources.extend(sorted(repo_set.glob("*.png")))
    if not sources:
        print("[inject-icons] 找不到任何图标源文件", file=sys.stderr)
        return 1

    by_size: dict[int, Path] = {}
    for p in sources:
        sz = png_size(p)
        if sz and sz[0] == sz[1]:
            by_size[sz[0]] = p

    written = 0
    for name, want in SLOTS:
        src = by_size.get(want)
        if not src:
            # 允许用更大的图代替（系统会缩放），但只用恰好存在的尺寸，避免放大模糊
            continue
        shutil.copyfile(src, app / name)
        written += 1

    if written == 0:
        print("[inject-icons] 没有任何尺寸匹配，未写入", file=sys.stderr)
        return 1

    info["CFBundleIcons"] = {
        "CFBundlePrimaryIcon": {
            "CFBundleIconFiles": ["AppIcon60x60"],
            "CFBundleIconName": "AppIcon",
        }
    }
    info_path.write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
    print(f"[inject-icons] 写入 {written} 个 PNG + CFBundleIcons 兜底")
    return 0


if __name__ == "__main__":
    sys.exit(main())
