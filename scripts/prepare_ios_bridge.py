#!/usr/bin/env python3
"""把上游 wild-work 源码改造成「可作为 iOS 静态库编译」的形态。

为什么需要这一步
----------------
iOS 沙盒（尤其是 LiveContainer / 自签重签场景）**不允许** app 去 exec 一个未签名的
子进程。原方案「把 gateway 二进制放进 bundle，运行时复制到 Documents 再 fork」
在 iOS 上必然失败，即使进程起来了也会被内核以 SIGKILL 收掉。

因此 iOS 端改为：把 Go 网关编成静态库（`-buildmode=c-archive`）直接链进主 Mach-O，
Swift 侧调用导出的 `WildWorkStart()`。全程没有子进程，重签也不影响。

本脚本做四件事
--------------
1. 把 `cmd/wild-work/main.go` 的 `func main()` 改名为 `func runMain()`（供桥接层调用）；
2. 注入 `cmd/wild-work/zz_ios_bridge.go`：cgo `//export WildWorkStart`，负责设置
   WILDWORK_HOME / 日志落盘 / 参数，然后在后台 goroutine 里跑 runMain()；
3. 用 stub 替换 `internal/systray`（iOS 无托盘，且原实现依赖 macOS AppKit，链接必炸）；
4. 保留上游其余代码原样，保证功能与桌面版完全一致。

用法
----
    python3 prepare_ios_bridge.py <upstream_dir> <work_dir>

上游更新后**不需要**改本脚本：只依赖 `func main() {` 这一行和 systray 的三个导出符号。
若上游把 main 拆成别的结构，脚本会在校验阶段明确报错退出（不会静默产出坏包）。
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

BRIDGE_GO = r'''//go:build ios

// 由 scripts/prepare_ios_bridge.py 自动生成，请勿手工修改。
//
// iOS 侧入口：把上游的 runMain() 包一层，暴露成 C 函数给 Swift 调用。
// 之所以走 c-archive 而不是子进程，见脚本头部注释。
package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"io"
	"log"
	"os"
	"path/filepath"
	"sync"
)

var (
	bridgeOnce sync.Once
	bridgeExit = make(chan struct{})
)

// WildWorkStart 启动内置网关。
//
//	home  沙盒内可写目录（对应 WILDWORK_HOME），为 NULL 时返回 1
//	port  HTTP 端口，<=0 时沿用 config.json 里的值
//
// 返回 0 = 已受理（服务在后台 goroutine 中运行），非 0 = 参数/环境错误。
// 函数本身**不阻塞**，Swift 侧通过轮询端口判断就绪。
//
//export WildWorkStart
func WildWorkStart(home *C.char, port C.int) C.int {
	if home == nil {
		return 1
	}
	dir := C.GoString(home)
	if dir == "" {
		return 1
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return 2
	}

	// 数据目录：必须在任何 init 之前设置，workDir() 会在 main 开头读它。
	_ = os.Setenv("WILDWORK_HOME", dir)
	_ = os.Setenv("HOME", dir)
	_ = os.Setenv("TMPDIR", filepath.Join(dir, "tmp"))
	_ = os.MkdirAll(filepath.Join(dir, "tmp"), 0o755)

	// 日志同时写 stderr 和 <home>/gateway.log：
	// iOS 没有可读的 stdout/stderr，落盘是唯一能在 App 里排查的手段。
	logPath := filepath.Join(dir, "gateway.log")
	if f, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644); err == nil {
		log.SetOutput(io.MultiWriter(os.Stderr, f))
	}
	log.SetFlags(log.LstdFlags)

	// 首次运行写一份最小配置：只监听本机，端口由调用方指定。
	if port > 0 {
		ensurePortConfig(filepath.Join(dir, "config.json"), int(port))
	}

	// 上游 main 会解析这两个开关；--no-tray 让它走 headless 分支（阻塞等信号）。
	os.Args = []string{"wild-work", "--no-tray"}

	bridgeOnce.Do(func() {
		go func() {
			defer close(bridgeExit)
			defer func() {
				if r := recover(); r != nil {
					log.Printf("[ios] gateway panic: %v", r)
				}
			}()
			runMain()
		}()
	})
	return 0
}

// ensurePortConfig 只在配置文件不存在时写入，避免覆盖用户已改过的设置。
func ensurePortConfig(path string, port int) {
	if _, err := os.Stat(path); err == nil {
		return
	}
	body := "{\n  \"listen\": { \"host\": \"127.0.0.1\", \"port\": " +
		itoa(port) + " }\n}\n"
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		log.Printf("[ios] 写入 config.json 失败：%v", err)
	}
}

func itoa(v int) string {
	if v == 0 {
		return "0"
	}
	var buf [12]byte
	i := len(buf)
	neg := v < 0
	if neg {
		v = -v
	}
	for v > 0 {
		i--
		buf[i] = byte('0' + v%10)
		v /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
'''

SYSTRAY_STUB = r'''// Package systray iOS 占位实现（由 scripts/prepare_ios_bridge.py 注入）。
//
// iOS 没有系统托盘，且上游的 energye/systray 依赖 macOS AppKit（iOS SDK 不存在），
// 直接编译会链接失败。桥接层固定以 --no-tray 启动，故此处只需保证符号存在。
package systray

import "time"

// Actions 保留上游签名，便于 main.go 原样编译。
type Actions struct {
	OpenUI  func()
	OpenLog func()
	Quit    func()
}

// Run 在 iOS 上不应被调用（main 走 --no-tray 分支）；万一被调用则永久阻塞，
// 语义上与桌面端「托盘消息循环」一致，不会让主流程提前返回。
func Run(icon []byte, tooltip string, act Actions) {
	for {
		time.Sleep(time.Hour)
	}
}

// Quit 无托盘可摘除，直接成功。
func Quit(timeout time.Duration) bool { return true }
'''


def fail(msg: str) -> None:
    print(f"[prepare-ios-bridge] 错误：{msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if len(sys.argv) != 3:
        fail("用法：prepare_ios_bridge.py <upstream_dir> <work_dir>")

    upstream = Path(sys.argv[1]).resolve()
    work = Path(sys.argv[2]).resolve()

    if not (upstream / "cmd/wild-work/main.go").is_file():
        fail(f"{upstream} 不像 wild-work 上游（缺少 cmd/wild-work/main.go）")

    if work.exists():
        shutil.rmtree(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(upstream, work, symlinks=True)

    # 1) main() -> runMain()，让桥接层可以调用而不触发 c-archive 的入口语义
    main_go = work / "cmd/wild-work/main.go"
    src = main_go.read_text(encoding="utf-8")
    new_src, n = re.subn(r"^func main\(\) \{", "func runMain() {", src, count=1, flags=re.M)
    if n != 1:
        fail("main.go 中找不到 `func main() {`，上游结构已变，请更新 prepare_ios_bridge.py")
    # c-archive 仍要求 main 包存在 main 函数（不会被调用）
    new_src = new_src.rstrip() + "\n\n// main 仅用于满足 c-archive 对 main 包的入口要求，实际不会被执行。\nfunc main() {}\n"
    main_go.write_text(new_src, encoding="utf-8")

    # 2) 注入桥接文件
    (work / "cmd/wild-work/zz_ios_bridge.go").write_text(BRIDGE_GO, encoding="utf-8")

    # 3) systray 换成 stub
    tray_dir = work / "internal/systray"
    if not tray_dir.is_dir():
        fail("缺少 internal/systray，上游目录结构已变")
    for f in tray_dir.glob("*.go"):
        f.unlink()
    (tray_dir / "systray_stub.go").write_text(SYSTRAY_STUB, encoding="utf-8")

    # 4) 校验：确保必需的导出符号与目录都在
    checks = {
        "cmd/wild-work/zz_ios_bridge.go": "WildWorkStart",
        "internal/systray/systray_stub.go": "func Quit",
    }
    for rel, needle in checks.items():
        if needle not in (work / rel).read_text(encoding="utf-8"):
            fail(f"{rel} 校验失败（缺少 {needle}）")
    if not (work / "systrayfix").is_dir():
        fail("缺少 systrayfix 目录：go.mod 里的 replace 会失效（上游删了该补丁目录？）")

    print(f"[prepare-ios-bridge] OK -> {work}")


if __name__ == "__main__":
    main()
