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

## 容器注入层（移动端 UI 适配）

> 一句话：**不改上游一行代码**，所有手机端适配都是 WebView 加载完面板后由容器注入的一段 JS/CSS。
> 上游 master 每次更新，这套注入自动作用于新面板，不需要重放补丁。

### 两份脚本，必须逐行一致

| 平台 | 位置 | 注入时机 |
|------|------|---------|
| iOS | `ios/WildWorkNative/RootView.swift` → `PanelTweaks.script` | `WKUserScript(.atDocumentEnd)` + `didFinish` 兜底各一次（幂等） |
| Android | `android/.../PanelTweaks.java` → `SCRIPT` | `WebViewClient.onPageFinished` → `evaluateJavascript` |

改动**任意一处就必须同步另一处**。CI 两个 workflow 的第一步都会跑
`python3 scripts/check_panel_tweaks.py` 做逐行比对 + 关键项检查，不同步直接让构建失败。
脚本本身用 `"""` 三引号块提取，别把三引号改成别的引号形式。

注入做了四件事（顺序即脚本里的编号）：

1. **安全区**：把 viewport meta 补上 `viewport-fit=cover`，注入
   `body{padding-top:env(safe-area-inset-top)}` 与 `#app` 的底部 padding。
   iOS 侧 `PanelView` 必须 `.ignoresSafeArea(.all)`；Android 无安全区，`env()` 为 0，天然无害。
2. **虚拟账号说明**：给 `.acct-card[data-na="1"]` 加纯 CSS `::after` 常驻标签。
   手机没有 hover，上游把「固定账号，不可删除」放在 `title` 里，用户永远看不到，
   会误以为是删不掉的僵尸账号。用 CSS 伪元素而不是 MutationObserver——上游每秒都在改
   `[data-fb-epoch]` 文本，常驻观察器反而会拖慢页面。
3. **桌面文案清理**：TreeWalker 遍历文本节点，去掉 footer 的「· 右键托盘图标退出」，
   并把帮助第 8/9 条的「exe 目录」「托盘菜单」「右键托盘退出」改成移动端说法。
4. **首次启动收起**：清一次上游的 `acct-show-na`（`SHOW_NA_KEY`）。上游会持久化
   「展开虚拟账号」的状态，手机误触一次就再也收不回去。用自建的 `ww-mobile-booted`
   保证只清首次，用户之后主动展开仍被尊重。

### 关于那两个「删不掉的虚拟账户」

**不是脏数据，也不是 bug。** `oczen`（OpenCodeZen 匿名通道）与 `kilo`（Kilo 免费池）
是两条**免密免费渠道**，上游会各自生成一个虚拟账号占位；后端
`RemoveAccount` / `DisableAccount` 对它们硬拒（`internal/app/app.go` 明确写了「需求约束」）。
删掉等于让整条渠道瘫痪。所以容器层只做两件事：**解释清楚** + **首次启动默认收起**，
不要试图调 API 去删。上游自己也有「不适用 / 展开」开关，用户想看随时能展开。

### 主题色铁律（顶部黑边的根因）

上游面板是**浅色**主题（`style.css: --bg: #f5f6f8`）。容器/状态栏必须与它同色：

- iOS：`panelBackground`、`UIStatusBarStyleDarkContent`、`.preferredColorScheme(.light)`
- Android：`PANEL_BG` + `SYSTEM_UI_FLAG_LIGHT_STATUS_BAR`，且 `res/values/themes.xml`
  必须是**浅色**（`windowBackground` / `navigationBarColor` / `windowLightStatusBar`）。
  只改代码不改主题资源的话，启动会闪一下黑屏、底部导航栏仍是黑的、Button 文字是白的。
- WebView：`isOpaque = true`，背景色同为 `#f5f6f8`

早期版本容器用了 `#0A0D12` 深色底，状态栏安全区露出深色，用户看到的就是「上方信号/电池那一块是黑的」。
**外壳版仓库（`wild-work-mobile`）正好相反**：`mobile-ui` 是深色 `#0a0d12`，容器也保持深色 +
`UIStatusBarStyleLightContent`，别把两边的主题色串了。外壳版 UI 自带
`viewport-fit=cover` 与 `--safe-top/--safe-bottom`，只要 WebView `.ignoresSafeArea(.all)` 即可。

### 下拉刷新与卡顿

| 平台 | 实现 | 为什么 |
|------|------|--------|
| iOS | `WKWebView.scrollView.refreshControl = UIRefreshControl()` | 原生手势，不与 rubber-band 冲突；`bounces` 必须为 `true` |
| Android | 自写 `PullToRefreshLayout`（两个仓库各一份） | 项目刻意零第三方依赖，没有 AndroidX 的 `SwipeRefreshLayout` |

性能上必须保持的两条：

- **缓存模式只能是 `LOAD_DEFAULT`**。早期用 `LOAD_NO_CACHE`，每次刷新都重新下载并重新解析
  上游 5000 行 `app.js`，这是手机上最明显的卡顿源。数据接口仍实时请求，不受影响。
- **`isOpaque = true`**。透明 WKWebView 每帧都要做合成混合，滚动时掉帧明显。

上游自身的常驻轮询（`loadStats` 30s / `loadAppLog` 15s / 每秒重算进行中耗时）是上游决定的，
容器层不改、也不该改。如果将来真要动，给上游提 PR，别在这里加定时器。

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
| 顶部状态栏区域一条黑边 | 容器深色底 vs 面板浅色 / WebView 未 `ignoresSafeArea(.all)` | 见「容器注入层 → 主题色铁律」；两端背景色、状态栏样式一起改 |
| 出现两个删不掉的虚拟账号 | oczen / kilo 免密免费渠道的内建占位 | 不是 bug，别删；靠常驻说明 + 首启收起处理，见「容器注入层」 |
| 下拉刷新没反应 | `bounces=false`（iOS）/ 外层容器未 attach WebView（Android） | 检查 `scrollView.refreshControl` 与 `PullToRefreshLayout.attach()` |
| 页面滚动/刷新卡顿 | `LOAD_NO_CACHE` 或 `isOpaque=false` | 改回 `LOAD_DEFAULT` / `true`，见「容器注入层」 |
| footer 仍显示「右键托盘图标退出」 | 注入脚本没跑到（页面结构变了 / 只改了一端） | 跑 `check_panel_tweaks.py`；在 App 内下拉刷新后看 `.footer` 文本 |

---

## 6. 不要做的事

1. **不要 fork 网关源码**。所有适配靠环境变量与参数，不靠改代码。
2. **不要把网关放进 `assets/`**。没有执行位，chmod 在很多 ROM 上无效。
3. **不要在 iOS 上用子进程跑网关**。未签名二进制无法 exec，见第 4 节。必须保持 c-archive 方案。
4. **不要给 `xcodebuild` 接管道**。`cmd | tail` 会把失败退出码换成 `tail` 的 0，构建失败也照样出包。
5. **不要提交二进制到仓库**。`libgateway.so` / `libwildwork.a` 已在 `.gitignore`，由 CI 构建注入。
6. **不要在 App 里硬编码端口**。三处常量必须同步改。
7. **不要重写面板 UI / fork 前端**。轻量适配只能走「容器注入层」的运行时注入；一旦复制一份 HTML/CSS 自己维护，上游每次改版都要重放补丁。
8. **不要把监听地址改成 `0.0.0.0` 而不设密码**。上游会直接拒绝启动。
9. **不要删掉 CI 里的 `verify_ipa.py`**。它是唯一能挡住「装不上的 IPA」的关卡。
10. **不要只改一侧的注入脚本**。iOS / Android 两份必须同步，`check_panel_tweaks.py` 会挡住不同步的提交。
11. **不要把 `LOAD_DEFAULT` 改回 `LOAD_NO_CACHE`**，也不要把 `isOpaque` 改回 `false`：两者都是实测过的卡顿源。
12. **不要试图删除 oczen / kilo 虚拟账号**，后端硬拒；见「容器注入层」一节。

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
| 面板主题色 | `#f5f6f8`（浅色） | 上游 `web/style.css`；容器须同色 |
| 注入脚本（两份） | `PanelTweaks.script` / `PanelTweaks.SCRIPT` | `RootView.swift` / `PanelTweaks.java` |
| 虚拟账号收起开关 | `acct-show-na`（上游）/ `ww-mobile-booted`（本仓） | 注入脚本 |
| 一致性校验 | `scripts/check_panel_tweaks.py` | 两个 workflow 的第一步 |
| 上游仓库 | `rockswang/wild-work@master` | workflows 的 `env` |
