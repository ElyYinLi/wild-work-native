# 维护手册

目标是：**上游 wild-work 更新后，能让本项目自动跟上，且后续接手者（含 AI）读这一份就够。**

---

## 0. 一句话架构

> 本项目不改上游一行代码，只是**把上游的 Go 网关编进 App**，用 WebView 打开它自己提供的面板。

```
手机 App
 ├─ WebView ──────────────► 127.0.0.1:7863（上游原版面板）
 ├─ Android：子进程       ──► lib/arm64-v8a/libgateway.so --no-tray
 └─ iOS：静态库（无子进程）──► libwildwork.a → WildWorkStart()
```

由此推出的三条铁律：

1. **网关永远是上游原样编译**。任何「为了适配手机改网关源码」的念头都要打住——一旦改了，就变成 fork，上游每次更新都要重放补丁。
2. **UI 不做二次开发**。面板本身已带响应式布局，手机宽度下可用。要改体验应该给上游提 PR，而不是在本项目里重写一套。
3. **iOS 绝不 spawn 子进程**。原因见第 4 节，这是踩过坑的硬约束。

**两个平台为什么实现不同**：Android 允许 exec 自己私有目录里的文件（借 `lib/*.so` 通道拿到执行位）；
iOS 只允许 exec「带有效签名、且被 bundle 声明为可执行」的 Mach-O，未签名 IPA 经 LiveContainer /
SideStore 重签时并不会签 Resources 里的裸二进制。所以 iOS 走静态库，见第 4 节。


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
      ├─► GOOS=android GOARCH=arm64 CGO_ENABLED=0
      │        └─► libgateway.so ─► jniLibs/arm64-v8a/ ─► assembleRelease ─► .apk
      │
      └─► prepare_ios_bridge.py（main→runMain、systray 换 stub）
               │
               └─► GOOS=ios CGO_ENABLED=1 -buildmode=c-archive
                        └─► libwildwork.a ─► 链接进主 Mach-O ─► .app ─► .ipa
```

iOS 侧多出来的 `prepare_ios_bridge.py` 只做三件机械替换（不碰业务逻辑）：

| 动作 | 原因 |
|------|------|
| `func main()` → `func runMain()` | c-archive 的入口是 `//export` 函数，不是 `main()` |
| 注入 `zz_ios_bridge.go` | 提供 `WildWorkStart(home, port)`：设 `WILDWORK_HOME`、日志落盘、`--no-tray`、后台跑 `runMain()` |
| `internal/systray` 换 stub | iOS 没有托盘，且原实现依赖 macOS AppKit，链接必炸 |

上游若改了 `main()` 的形态，脚本会在 `func main() {` 匹配失败时**明确报错退出**，不会静默产出坏包。


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

## 4. iOS 为什么必须用静态库

### 踩过的坑（2024-11）

第一版把 `gateway` 当资源放进 bundle，运行时复制到 Documents 再 `posix_spawn`。结果：

```
Failed to map /var/mobile/Containers/Data/Application/<UUID>/Documents/Applications/Unknown.app/(null):
Bad file descriptor
```

两个独立故障叠在一起：

1. **包本身是坏的**。`xcodebuild ... | tail -60` 让管道把退出码换成了 `tail` 的 0，
   构建失败也照常打包 → IPA 里只有一个 `gateway` 文件，**没有 Info.plist**。
   LiveContainer 取不到 `CFBundleExecutable`（日志里的 `(null)`），于是 app 名显示 `Unknown`、
   图标空白、映射失败。
2. **就算包是好的，子进程也起不来**。iOS 内核只允许 exec「带有效签名、且位于 app bundle 内」
   的 Mach-O。LiveContainer 重签只签主可执行文件，Resources 里的裸二进制不会被签，
   `posix_spawn` 一律 `Bad file descriptor` / 启动即 SIGKILL。这不是配置问题，是平台限制。

### 现在的做法

```
Go 源码 ──(c-archive)──► libwildwork.a + libwildwork.h
                                    │
Swift: WildWorkStart(home, port) ───┘  直接链进主 Mach-O
```

- **没有子进程**，重签与否都不影响；
- Go 的 `//export` 函数在进程内起 goroutine 跑 `runMain()`，服务监听 `127.0.0.1:7863`；
- 日志写 `<Documents>/wildwork/gateway.log`（iOS 没有可读的 stdout），Swift 侧读增量刷新启动页。

### 构建侧的两个硬约束

- `GOOS=ios` 的 Go 产物必须是 PIE，而 `InternalLinkPIESupported("ios")` 为 `false`，
  所以 `CGO_ENABLED=0` 会直接报 `requires external (cgo) linking`。必须
  `CGO_ENABLED=1` + `CC=$(xcrun --sdk iphoneos --find clang)` + `-isysroot`。
- Go 运行时依赖 `init_array` 里的初始化器，**`DEAD_CODE_STRIPPING` 必须为 NO**，
  否则链接器会把「没人调用」的初始化代码裁掉，表现为装上去就闪退。

### 产物必须过 `verify_ipa.py`

CI 在打包后强制执行 `scripts/verify_ipa.py`，任一项不过就红：

| 检查项 | 对应事故 |
|--------|---------|
| `Payload/` 下恰好一个 `.app` | 目录结构不合规，装机工具直接解析失败 |
| `Info.plist` 存在且 `CFBundleExecutable` 与 app 名一致 | `Unknown.app/(null)` |
| `CFBundleDisplayName` / `CFBundleIdentifier` 正确 | 桌面名字不对、覆盖安装冲突 |
| 主可执行文件 ≥100KB、有执行位、arm64 Mach-O | 空壳包 / `posix_spawn` 失败 |
| 二进制含 `WildWorkStart` | 网关没链进来，启动卡在「服务启动失败」 |
| `Assets.car` 存在或有 `CFBundleIconFiles` | 桌面图标空白 |

`scripts/inject_icons.py` 是图标兜底：actool 正常时什么都不做，异常时补传统
`CFBundleIconFiles` + 标准命名 PNG（`AppIcon60x60@2x.png` 等）。


---

## 5. 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 启动页一直转圈 | 网关没起来 | 看启动页底部日志（iOS 直接读 `Documents/wildwork/gateway.log`） |
| 提示「内置网关缺失」 | APK 装错架构 | 确认手机是 arm64（现代手机均是）；检查 `lib/arm64-v8a/libgateway.so` 是否存在 |
| 启动后立刻「服务已退出」 | 端口被占 / 数据目录不可写 | 看日志；确认 `WILDWORK_HOME` 指向可写目录 |
| 白屏但能返回 | 面板 JS 报错 | 长按刷新；或用手机浏览器直连 `http://127.0.0.1:7863` 看真实报错 |
| 后台一段时间后接口不通 | 系统挂起 App | 回前台自动恢复（`ensureAlive` 探活 + 重新拉起） |
| iOS 装机报 `Unknown.app/(null): Bad file descriptor` | IPA 缺 Info.plist，或主二进制无执行位 | 跑 `python3 scripts/verify_ipa.py <ipa> ...` 定位；CI 已强制校验 |
| iOS 装完桌面无图标、App 内正常 | 缺 Assets.car / CFBundleIcons | 同上；`inject_icons.py` 已做兜底 |
| iOS 桌面名字显示为 `Unknown` | `CFBundleExecutable` 为空 | 同上，根因是 Info.plist 缺失 |
| iOS 装完秒退 | 静态库被 dead-strip / 签名被破坏 | 确认 `DEAD_CODE_STRIPPING: NO`；换 SideStore 重签试试 |
| CI 报 `缺少 systrayfix 目录` | 上游已把 energye/systray 修好，删掉了补丁目录 | 脚本会自动清掉 go.mod 里指向它的 `replace` 行，正常现象 |
| `prefersHomeIndicatorAutoHidden` 编译错误 | 它是 UIViewController 的属性，不是 SwiftUI View 的修饰符 | 已从 `WildWorkNativeApp.swift` 移除 |
| CI 里 iOS job `cancelled` 且无 runner | runner 标签不可用 / 私有仓库未绑支付方式 | 只用 `macos-latest`；Free 账号的私有仓库跑 macOS runner 需要已验证的付款方式 |

---

## 6. 不要做的事

1. **不要 fork 网关源码**。所有适配靠环境变量与参数，不靠改代码。
2. **不要把网关放进 `assets/`**。没有执行位，chmod 在很多 ROM 上无效。
3. **不要在 iOS 上用子进程跑网关**。未签名二进制无法 exec，见第 4 节。必须保持 c-archive 方案。
4. **不要给 `xcodebuild` 接管道**。`cmd | tail` 会把失败退出码换成 `tail` 的 0，构建失败也照样出包。
5. **不要提交二进制到仓库**。`libgateway.so` / `libwildwork.a` 已在 `.gitignore`，由 CI 构建注入。
6. **不要在 App 里硬编码端口**。三处常量必须同步改。
7. **不要给面板做二次 UI 覆盖**。要改体验就推动上游，否则每次上游改版都要重新适配。
8. **不要把监听地址改成 `0.0.0.0` 而不设密码**。上游会直接拒绝启动。
9. **不要删掉 CI 里的 `verify_ipa.py`**。它是唯一能挡住「装不上的 IPA」的关卡。

---

## 7. 关键常量速查

| 项 | 值 | 位置 |
|----|-----|------|
| 网关端口 | 7863 | `GatewayService.PORT` / `Gateway.port` |
| 网关可执行文件（Android） | `lib/arm64-v8a/libgateway.so` | `android/app/src/main/jniLibs/` |
| 网关静态库（iOS） | `libwildwork.a` + `libwildwork.h` | `ios/WildWorkNative/Resources/` |
| iOS 导出函数 | `int WildWorkStart(char* home, int port)` | `cmd/wild-work/zz_ios_bridge.go` |
| iOS 显示名 / bundle 名 | `WildWork` | `ios/project.yml` 的 `PRODUCT_NAME` / `CFBundleDisplayName` |
| Android 包名 | `com.wildwork.nativeapp` | `app/build.gradle.kts` |
| iOS Bundle ID | `com.wildwork.nativeapp` | `ios/project.yml` |
| 数据目录（Android） | `filesDir/ww` | `GatewayService` |
| 数据目录（iOS） | `Documents/wildwork` | `Gateway.swift` |
| 上游仓库 | `rockswang/wild-work@master` | workflows 的 `env` |
