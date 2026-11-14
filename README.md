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
┌──────────────────────────────────────────────┐
│ 手机 App（本仓库）                            │
│                                              │
│  ┌────────────────┐   127.0.0.1:7863         │
│  │ WebView        │ ──────────────┐          │
│  │  原版面板 UI   │               │          │
│  └────────────────┘               ▼          │
│                        ┌───────────────────┐ │
│                        │ Go 网关子进程      │ │
│                        │ (上游二进制)       │ │
│                        │ 数据落在沙盒私有目录 │ │
│                        └───────────────────┘ │
└──────────────────────────────────────────────┘
```

- 网关就是上游 `cmd/wild-work` 交叉编译出的 arm64 二进制，**源码与上游完全一致**，
  所以 22 个渠道、签到、积分、三种协议接口全部原样可用。
- 打包为 `jniLibs/arm64-v8a/libgateway.so`：Android 只会解压 native 库并赋予可执行权限，
  这是让 Go 二进制在 App 内可执行最稳的方式（放 assets 里没有执行位）。
- 数据目录通过 `WILDWORK_HOME` 指到 App 私有目录，`config.json` / `auths/` / `data/` 都不会丢。
- 网关只监听 `127.0.0.1`，不对局域网暴露面板。

## 下载

[Releases](https://github.com/ElyYinLi/wild-work-native/releases)

| 平台 | 文件 | 说明 |
|------|------|------|
| Android | `WildWorkNative.apk` | 需 arm64-v8a（现代手机基本都是） |
| iOS | `WildWorkNative.ipa` | 未签名，需自签；能否内嵌网关见 Release 说明 |

## 构建

推送到 `main` 自动构建。Android 每天 03:00 UTC 还会自动跟一次上游，保证网关是最新版。

```bash
# 本地构建 Android（需 Go 1.25+ 与 Android SDK）
curl -fsSL https://codeload.github.com/rockswang/wild-work/tar.gz/refs/heads/master | tar xz
cd wild-work-master && GOOS=android GOARCH=arm64 CGO_ENABLED=0 \
  go build -trimpath -ldflags "-s -w" \
  -o ../android/app/src/main/jniLibs/arm64-v8a/libgateway.so ./cmd/wild-work
cd .. && cd android && gradle assembleRelease
```

维护说明见 [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md)。
