# 维护手册

目标是：**上游 wild-work 更新后，能让本项目自动跟上，且后续接手者（含 AI）读这一份就够。**

---

## 0. 一句话架构

> 本项目不改上游一行代码，只是**把上游的 Go 网关编译进 App**，用 WebView 打开它自己提供的面板。

```
手机 App
 ├─ WebView ──► 127.0.0.1:7863  (Go 网关子进程提供的原版面板)
 └─ 进程管理 ──► 启动/守护/日志
```

由此推出的两条铁律：

1. **网关永远是上游原样编译**。任何「为了适配手机改网关源码」的念头都要打住——一旦改了，就变成 fork，上游每次更新都要重放补丁。
2. **UI 不做二次开发**。面板本身已带响应式布局，手机宽度下可用。要改体验应该给上游提 PR，而不是在本项目里重写一套。

---

## 1. 上游更新如何同步

### 自动：什么都不用做

| 机制 | 说明 |
|------|------|
| 每次 push 到 `main` | 现场拉上游 `master` 最新源码，重新编译网关并出包 |
| 每天 03:00 UTC 定时 | Android 流水线自动重建一次，保证 Release 里的包不超过 24 小时 |
| 上游发新版 | 下次构建自动带上，Release 说明里写明网关版本 |

也就是说：**上游更新 → 最迟 24 小时内你的 APK 自动变成新版**。前提是仓库有新的 commit 触发，或等定时任务。

### 手动立即同步

```bash
# 任意空提交即可触发重建
git commit --allow-empty -m "sync: 跟随上游最新" && git push
```

或到 Actions 页面点 **Run workflow**。

---

## 2. 构建产物是怎么来的

```
上游源码 tarball
      │
      ▼  GOOS/GOARCH 交叉编译
libgateway.so (android/arm64)  ──┐
gateway (ios/arm64)              │
      │                          │
      ▼                          ▼
 jniLibs/arm64-v8a/         Resources/gateway
      │                          │
      ▼                          ▼
   assembleRelease          xcodebuild → .app → zip → .ipa
```

### 关键决策：为什么安卓端叫 `libgateway.so`

Android 只在 `lib/<abi>/` 目录下解压 native 库，**解压后自带可执行位**，且位于可执行目录。
放进 `assets/` 的话：没有执行位、`chmod` 在部分 ROM 的 data 分区（noexec）上也无效。
所以借用 `.so` 的打包通道——它不是真正的共享库，系统也不会 `dlopen` 它，只是被当成文件解压出来。

对应 Gradle 配置（`android/app/build.gradle.kts`）：

```kotlin
packaging { jniLibs { useLegacyPackaging = true } }  // 必须解压出来才可执行
```

### 关键决策：数据目录

网关用 `WILDWORK_HOME` 环境变量决定 `config.json` / `auths/` / `data/` 落在哪：

- Android：`context.getFilesDir()/ww`
- iOS：`Documents/wildwork`

**不设置的话会落到可执行文件旁边的只读目录，启动即失败。**

### 首次启动的 config.json

网关只监听 `127.0.0.1`，不设管理密码（面板不出本机，暴露风险为零）：

```json
{ "listen": { "host": "127.0.0.1", "port": 7863 } }
```

若想改成让局域网别的设备也能用这个网关的 API，需要改成 `0.0.0.0` **并同时设 `admin_password`**，
否则网关会拒绝启动（上游的安全设计）。

---

## 3. 上游 API 变更时怎么排查

本项目**不直接调用上游 API**（面板自己调），所以接口变更通常不会影响本项目。
但以下情况需要改原生代码：

| 上游变化 | 影响 | 改哪里 |
|---------|------|--------|
| 新增 `--no-tray` 等启动参数变化 | 启动失败 | `GatewayService.start()` / `Gateway.start()` 的 `arguments` |
| `WILDWORK_HOME` 环境变量改名 | 数据写不进去 | 同上 `environment` 设置 |
| 默认端口 7863 变化 | 打不开 | 三处常量：`GatewayService.PORT`、`Gateway.port`、README |
| 二进制体积暴涨 | 超过 Play 分发包限制、启动变慢 | 检查上游是否引入大资源；通常无需处理 |
| 启动日志格式变化 | 启动页日志不显示 | 不影响功能，可不改 |

其余 UI 层面的变化全部由上游自己负责，本项目无需跟进。

---

## 4. iOS 能否内嵌网关

**当前状态：可以**（`macos-latest` 上的 Xcode 链接器链可用，CI 已实测通过）。

技术背景（构建失败时用得上）：

- Go 对 `ios/arm64` 的产物必须是 **PIE**；
- 而 `platform.InternalLinkPIESupported("ios", "arm64")` 为 `false`，即 Go 不能用内部链接器产出 PIE；
- 于是 `CGO_ENABLED=0` 时直接报：
  `ios/arm64 requires external (cgo) linking, but cgo is not enabled`；
- 需要 `CGO_ENABLED=1` + iPhoneOS SDK + Xcode 的 `ld` 才能成功。

此外上游依赖的 `energye/systray` 有一处构建缺陷：`systray_darwin.go` **没有 `//go:build darwin`**，
所以 `GOOS=ios` 时也会被编进去，引入 `#cgo darwin` 指令。
若未来 CI 报 cgo 相关错误，按这个思路处理：本地复制该包、补 build tag、用 `replace` 指过去（不要改上游）。

> 万一某天两条路都失败，流水线会**自动降级为客户端版**（不内嵌网关）并照常出包，
> Release 说明里写「内置网关：false」。此时 App 需要连接局域网内的网关设备。
> 这不是故障，是设计好的降级路径。

---

## 5. 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 启动页一直转圈 | 网关没起来 | 看启动页底部日志；多半是二进制缺失或不可执行 |
| 提示「内置网关缺失」 | APK 装错架构 | 确认手机是 arm64（现代手机均是）；检查 `lib/arm64-v8a/libgateway.so` 是否存在 |
| 启动后立刻「服务已退出」 | 端口被占 / 数据目录不可写 | 看日志；确认 `WILDWORK_HOME` 指向可写目录 |
| 白屏但能返回 | 面板 JS 报错 | 长按刷新；或用手机浏览器直连 `http://127.0.0.1:7863` 看真实报错 |
| 后台一段时间后接口不通 | 系统回收子进程 | App 回前台会自动重启（`ensureAlive`） |
| iOS 装完闪退 | 子进程被签名破坏 | 换 SideStore 重签；或参照第 4 节排查看是否走了 cgo 路径 |
| CI 里 iOS job `cancelled` 且无 runner | runner 标签不可用 | 只用 `macos-latest`；`macos-14` 已下线 |

---

## 6. 不要做的事

1. **不要 fork 网关源码**。所有适配靠环境变量与参数，不靠改代码。
2. **不要把网关放进 `assets/`**。没有执行位，chmod 在很多 ROM 上无效。
3. **不要提交二进制到仓库**。`libgateway.so` / `gateway` 已在 `.gitignore`，由 CI 构建注入。
4. **不要在 App 里硬编码端口**。三处常量必须同步改。
5. **不要给面板做二次 UI 覆盖**。要改体验就推动上游，否则每次上游改版都要重新适配。
6. **不要把监听地址改成 `0.0.0.0` 而不设密码**。上游会直接拒绝启动。

---

## 7. 关键常量速查

| 项 | 值 | 位置 |
|----|-----|------|
| 网关端口 | 7863 | `GatewayService.PORT` / `Gateway.port` |
| 网关可执行文件（Android） | `lib/arm64-v8a/libgateway.so` | `android/app/src/main/jniLibs/` |
| 网关可执行文件（iOS） | `gateway`（bundle 根） | `ios/WildWorkNative/Resources/` |
| Android 包名 | `com.wildwork.nativeapp` | `app/build.gradle.kts` |
| iOS Bundle ID | `com.wildwork.nativeapp` | `ios/project.yml` |
| 数据目录（Android） | `filesDir/ww` | `GatewayService` |
| 数据目录（iOS） | `Documents/wildwork` | `Gateway.swift` |
| 上游仓库 | `rockswang/wild-work@master` | workflows 的 `env` |
