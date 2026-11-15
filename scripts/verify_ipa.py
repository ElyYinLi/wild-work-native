#!/usr/bin/env python3
"""IPA 产物硬校验。

背景
----
之前出过两次「构建看起来成功、装上去是坏包」的事故：

  1. `xcodebuild ... | tail -60` 把失败退出码吃掉了（管道返回的是 tail 的状态），
     于是打包步骤对着一个残缺目录产出了 IPA —— 里面只有一个 gateway 文件，
     连 Info.plist 都没有。LiveContainer 因此报
     `Failed to map .../Unknown.app/(null): Bad file descriptor`（`(null)` 就是
     CFBundleExecutable 取不到），桌面名字显示为 Unknown、图标当然也没有。
  2. actool 没跑，Assets.car 缺失 → App 内 UI 正常，桌面图标空白。

本脚本把「装得上去、有图标、有名字、网关真在里面」变成构建的硬性门槛，
任何一项不满足就让流水线红掉，绝不产出坏包。

用法
----
    verify_ipa.py <ipa 路径> --app-name WildWork --bundle-id com.wildwork.nativeapp
                   [--require-go] [--min-icon 60]
"""

from __future__ import annotations

import argparse
import plistlib
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

CPU_TYPE_ARM64 = 0x0100000C
MH_MAGIC_64 = 0xFEEDFACF

OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"


class Check:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.notes: list[str] = []

    def ok(self, msg: str) -> None:
        print(f"  {OK} {msg}")

    def fail(self, msg: str) -> None:
        print(f"  {BAD} {msg}")
        self.errors.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def png_dimensions(path: Path) -> tuple[int, int] | None:
    """读 PNG 宽高。

    不能硬编码偏移 16：Xcode 会用 pngcrush 产出 CgBI 变体，块顺序变成
    CgBI → IHDR，宽高位置随之后移。统一按块扫描找 IHDR。
    """
    try:
        data = path.read_bytes()[:4096]
    except Exception:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    i = 8
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        ctype = data[i + 4:i + 8]
        if ctype == b"IHDR":
            w, h = struct.unpack(">II", data[i + 8:i + 16])
            return w, h
        i += 12 + length
    return None


def macho_cputype(path: Path) -> int | None:
    """读 Mach-O 头拿 cputype；不是 Mach-O 返回 None。"""
    with path.open("rb") as f:
        head = f.read(8)
    if len(head) < 8:
        return None
    magic_le = struct.unpack("<I", head[:4])[0]
    if magic_le == MH_MAGIC_64:
        return struct.unpack("<i", head[4:8])[0] & 0xFFFFFFFF
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ipa")
    ap.add_argument("--app-name", required=True, help="期望的 .app 目录名（不含扩展名）")
    ap.add_argument("--bundle-id", required=True)
    ap.add_argument("--require-go", action="store_true", help="要求二进制里含 Go 网关符号")
    ap.add_argument("--min-icon", type=int, default=60, help="至少存在的图标像素边长")
    args = ap.parse_args()

    c = Check()
    ipa = Path(args.ipa)
    print(f"校验 {ipa}")

    if not ipa.is_file() or ipa.stat().st_size == 0:
        print(f"{BAD} IPA 不存在或为空")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="verify-ipa-"))
    # Python 的 extractall 不恢复 unix 权限，所以额外从 zip 条目里读一遍 mode。
    # 装机工具（LiveContainer / SideStore）复制 bundle 时会沿用 zip 里的权限位。
    zip_modes: dict[str, int] = {}
    with zipfile.ZipFile(ipa) as z:
        for info in z.infolist():
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode:
                zip_modes[info.filename] = mode
        z.extractall(tmp)

    payload = tmp / "Payload"
    if not payload.is_dir():
        c.fail("缺少 Payload/ 目录（IPA 结构不合规，LiveContainer/SideStore 都会解析失败）")
        return report(c)

    apps = [p for p in payload.iterdir() if p.suffix == ".app"]
    if len(apps) != 1:
        c.fail(f"Payload 内应恰好有 1 个 .app，实际 {len(apps)} 个")
        return report(c)

    app = apps[0]
    print(f"  app: {app.name}")

    # ---- 1. 名字 ----
    if app.stem == args.app_name:
        c.ok(f".app 目录名 = {args.app_name}")
    else:
        c.fail(f".app 目录名是 {app.stem}，期望 {args.app_name}")

    # ---- 2. Info.plist ----
    info_path = app / "Info.plist"
    if not info_path.is_file():
        c.fail("缺少 Info.plist —— 这会让 CFBundleExecutable 取不到，"
               "装机时报 `Unknown.app/(null): Bad file descriptor`")
        return report(c)

    info = plistlib.loads(info_path.read_bytes())
    c.ok("Info.plist 存在")

    for key in ("CFBundleExecutable", "CFBundleIdentifier", "CFBundlePackageType",
                "MinimumOSVersion", "UIDeviceFamily", "CFBundleVersion"):
        if key in info:
            c.ok(f"{key} = {info[key]}")
        else:
            c.fail(f"Info.plist 缺少 {key}")

    exe_name = info.get("CFBundleExecutable") or ""
    if not exe_name:
        c.fail("CFBundleExecutable 为空")
    elif exe_name != args.app_name:
        c.fail(f"CFBundleExecutable={exe_name} 与 app 名 {args.app_name} 不一致")
    else:
        c.ok("CFBundleExecutable 与 app 名一致")

    if info.get("CFBundleIdentifier") != args.bundle_id:
        c.fail(f"CFBundleIdentifier={info.get('CFBundleIdentifier')} 期望 {args.bundle_id}")
    else:
        c.ok("Bundle ID 正确")

    display = info.get("CFBundleDisplayName")
    if display:
        c.ok(f"桌面显示名 = {display}")
    else:
        c.fail("缺少 CFBundleDisplayName —— 桌面会显示成 .app 文件名")

    if info.get("CFBundlePackageType") != "APPL":
        c.fail("CFBundlePackageType 必须是 APPL")

    # ---- 3. 主可执行文件 ----
    if not exe_name:
        return report(c)
    exe = app / exe_name
    if not exe.is_file():
        c.fail(f"主可执行文件不存在：{exe_name}")
        return report(c)

    size = exe.stat().st_size
    if size < 100_000:
        c.fail(f"主可执行文件只有 {size} 字节，不可能是完整 App")
    else:
        c.ok(f"主可执行文件 {size / 1_048_576:.1f} MB")

    zip_mode = zip_modes.get(f"Payload/{app.name}/{exe_name}", 0)
    has_exec = bool(zip_mode & 0o111) if zip_mode else bool(exe.stat().st_mode & 0o111)
    if not has_exec:
        c.fail("主可执行文件没有执行位（zip 条目与磁盘都没有）——"
               "装机后 posix_spawn 会报 Bad file descriptor")
    else:
        c.ok("主可执行文件带执行位")

    cputype = macho_cputype(exe)
    if cputype is None:
        c.fail("主可执行文件不是 64 位 Mach-O")
    elif cputype != CPU_TYPE_ARM64:
        c.fail(f"cputype=0x{cputype:08x}，不是 arm64")
    else:
        c.ok("主可执行文件是 arm64 Mach-O")

    # ---- 4. Go 网关是否真的链进来了 ----
    if args.require_go:
        blob = exe.read_bytes()
        # cgo 导出的符号名固定为 _WildWorkStart；同时用 Go buildid 兜底判断
        if b"WildWorkStart" not in blob:
            c.fail("二进制里找不到 WildWorkStart —— Go 网关没有被链接进来，"
                   "启动时必然卡在「服务启动失败」")
        else:
            c.ok("二进制包含 Go 网关入口 WildWorkStart")
        if b"Go build ID" in blob or b"go1." in blob:
            c.ok("二进制包含 Go runtime 标记")
        else:
            c.note("未在二进制里找到明显 Go runtime 字符串（静态链接可能被裁剪，非致命）")

    # ---- 5. 图标 ----
    # 两条合法路径，满足任一即可：
    #   (a) asset catalog 编译产物 Assets.car（actool 正常）
    #   (b) 传统 CFBundleIconFiles + bundle 根目录的 AppIcon*.png（inject_icons.py 兜底）
    has_car = (app / "Assets.car").is_file()
    icons = info.get("CFBundleIcons", {})
    primary = icons.get("CFBundlePrimaryIcon", {}) if isinstance(icons, dict) else {}
    icon_files = primary.get("CFBundleIconFiles") or info.get("CFBundleIconFiles") or []

    best = 0
    for png in app.glob("AppIcon*.png"):
        sz = png_dimensions(png)
        if sz:
            best = max(best, min(sz))

    if has_car:
        c.ok(f"Assets.car 存在（{(app / 'Assets.car').stat().st_size / 1024:.0f} KB）")
    if icon_files:
        c.ok(f"Info.plist 图标键 = {icon_files}")
    if best >= args.min_icon:
        c.ok(f"bundle 内有 {best}px 图标")

    if has_car or (icon_files and best >= args.min_icon):
        if not has_car:
            c.note("走传统 CFBundleIconFiles 路径（actool 未产出 Assets.car，"
                   "可能 xcodegen 配置或 Xcode 版本异常；图标仍会显示）")
    else:
        if not has_car:
            c.fail("缺少 Assets.car —— actool 没有执行")
        if not icon_files:
            c.fail("Info.plist 没有 CFBundleIconFiles")
        if best < args.min_icon:
            c.fail(f"找不到 ≥{args.min_icon}px 的图标资源")
        c.fail("桌面图标会是空白")

    # ---- 6. 不该出现的东西 ----
    if (app / "Frameworks").is_dir():
        c.note("存在 Frameworks/ 目录：LiveContainer 需要逐个重签里面的 dylib，"
               "本工程不使用动态库，出现即说明配置异常")
    if (app / "gateway").exists():
        c.note("bundle 根目录有裸 gateway 二进制：iOS 无法 exec 未签名文件，已无用途")

    return report(c)


def report(c: Check) -> int:
    print()
    for n in c.notes:
        print(f"  · {n}")
    if c.errors:
        print(f"\n\033[31m校验失败：{len(c.errors)} 项\033[0m")
        for e in c.errors:
            print(f"  - {e}")
        return 1
    print("\n\033[32m全部通过\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
