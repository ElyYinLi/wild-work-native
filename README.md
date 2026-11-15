# wild-work-native

[`rockswang/wild-work`](https://github.com/rockswang/wild-work) 的**纯移动端版本**：网关直接打包进 App，
装上就能用——不需要电脑、不需要 Docker、不需要外接服务。

与「外壳版」的区别：

| | 外壳版（wild-work-mobile） | 本仓库（纯移动端） |
|---|---|---|
| 运行方式 | App 连接电脑上的 wild-work | 网关跑在手机里 |
| 需要电脑 | 需要，且电脑要一直开着 | **不需要** |
| 功能 | 自研的简化移动 UI | 与原版完全一致（同一份服务端代码） |
| 包体积 | ~70 KB | ~15–20 MB（内置网关） |

## 实现原理

```
┌────────────────────────────────────────────────┐
│ 手机 App（本仓库）                              │
│                                                │
│  ┌────────────────┐   127.0.0.1:7863           │
│  │ WebView        │ ──────────────┐            │
│  │  原版面板 UI   │               ▼            │
│  └────────────────┘      ┌────────────────────┐ │
│                          │ Go 网关（上游原样） │ │
│                          │ 数据落在沙盒私有目录 │ │
│                          └────────────────────┘ │
└────────────────────────────────────────────────┘
```

- 网关就是上游 `cmd/wild-work` 编译出的 arm64 代码，**源码与上游完全一致**，
  所以 22 个渠道、签到、积分、三种协议接口全部原样可用。
- **Android**：编成 `jniLibs/arm64-v8a/libgateway.so` 由系统解压并赋予执行权限，
  再以子进程拉起（`--no-tray`）。
- **iOS**：编成静态库 `libwildwork.a`（`-buildmode=c-archive`）直接链进主可执行文件，
  Swift 调 `WildWorkStart()` 在进程内启动。**没有子进程**——iOS 不允许 exec 未签名二进制，
  这是唯一能过 LiveContainer / SideStore 重签的方案。
- 数据目录通过 `WILDWORK_HOME` 指到 App 私有目录，`config.json` / `auths/` / `data/` 都不会丢。
- 网关只监听 `127.0.0.1`，不对局域网暴露面板。

## 下载

[Releases](https://github.com/ElyYinLi/wild-work-native/releases)

| 平台 | 文件 | 说明 |
|------|------|------|
| Android | `WildWorkNative.apk` | 需 arm64-v8a（现代手机基本都是） |
| iOS | `WildWorkNative.ipa` | 未签名，LiveContainer / SideStore / TrollStore 直接导入，或自签 |

两个平台都已实测内嵌网关，装上即可用，无需任何外部服务。

> iOS 包每次发布前都会跑 `scripts/verify_ipa.py`：校验 `Info.plist` 关键键、arm64 Mach-O、
> Go 符号是否真的链进来、桌面图标资源是否齐全。任何一项不过，构建直接失败——不会产出
> 「装上去才知道是坏包」的产物。

### 首次使用

1. 打开 App，等待启动页显示「就绪」（首次约 1–3 秒，只是解压与启动网关）
2. 进入面板 → 点渠道按钮（如 `+ WorkBuddyCN`）添加账号
3. 到「设置」里把监听地址改成 `0.0.0.0` 并**设置管理密码**——这样局域网里的其它设备
   （电脑上的 Claude Code、Cherry Studio 等）也能用手机上的 API
4. 面板顶栏会显示 API 地址与 Key，填到客户端即可

## 构建

推送到 `main` 自动构建。Android 每天 03:00 UTC 还会自动跟一次上游，保证网关是最新版。

```bash
# 本地构建 Android（需 Go 1.25+ 与 Android SDK）
curl -fsSL https://codeload.github.com/rockswang/wild-work/tar.gz/refs/heads/master | tar xz
cd wild-work-master && GOOS=android GOARCH=arm64 CGO_ENABLED=0 \
  go build -trimpath -ldflags "-s -w" \
  -o ../android/app/src/main/jniLibs/arm64-v8a/libgateway.so ./cmd/wild-work
cd .. && cd android && gradle assembleRelease

# iOS 只能在 macOS 上构建（需 Xcode 15+ 与 xcodegen）
python3 scripts/prepare_ios_bridge.py /path/to/upstream bridge
SDK=$(xcrun --sdk iphoneos --show-sdk-path)
(cd bridge && CGO_ENABLED=1 GOOS=ios GOARCH=arm64 \
  CC="$(xcrun --sdk iphoneos --find clang)" \
  CGO_CFLAGS="-isysroot $SDK -miphoneos-version-min=15.0" \
  CGO_LDFLAGS="-isysroot $SDK -miphoneos-version-min=15.0" \
  go build -buildmode=c-archive -trimpath -ldflags "-s -w" \
  -o ../ios/WildWorkNative/Resources/libwildwork.a ./cmd/wild-work)
cd ios && xcodegen generate && xcodebuild -project WildWorkNative.xcodeproj \
  -scheme WildWorkNative -configuration Release -sdk iphoneos \
  -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
# 打包成 IPA 与产物校验的完整命令见 .github/workflows/ios.yml 最后两步
```

维护说明见 [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md)。
