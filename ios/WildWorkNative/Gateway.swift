import Foundation

/// iOS 网关控制器。
///
/// 与 Android 的 GatewayService 语义一致（同一个 Go 网关、同一份数据目录布局），
/// 但**实现方式完全不同**：
///
///   Android：把 `libgateway.so`（aarch64 PIE）复制到私有目录后 exec。
///   iOS    ：把网关编成静态库（`-buildmode=c-archive`）直接链进主 Mach-O，
///            由 `WildWorkStart()` 在进程内拉起 goroutine。
///
/// 为什么 iOS 不能照搬子进程方案：iOS 内核只允许执行「带有效签名、且位于
/// app bundle 内被声明为可执行」的 Mach-O。未签名 IPA 经 LiveContainer /
/// SideStore 重签时，只会签主可执行文件，Resources 里的裸二进制既不会被签、
/// 也无法通过 `posix_spawn` 的校验，一律 `Bad file descriptor` / SIGKILL。
/// 静态库方案没有子进程，重签与否都不影响，这是唯一可靠的路子。
final class Gateway {

    /// 与桌面版默认端口一致，面板里写死的 upstream 地址也是这个。
    static let port: Int32 = 7863

    private let logLock = NSLock()
    private var tail: [String] = []
    private var onLog: ((String) -> Void)?
    private var started = false
    private var logOffset: UInt64 = 0

    /// 沙盒内可写目录。bundle 只读且重装即清空，配置与账号必须放这里。
    var dataDir: URL {
        let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("wildwork", isDirectory: true)
    }

    private var logURL: URL { dataDir.appendingPathComponent("gateway.log") }

    func setLogHandler(_ h: @escaping (String) -> Void) {
        onLog = h
    }

    /// 端口可连通即视为服务活着（静态库与 App 同生命周期，无法被单独杀掉）。
    var isRunning: Bool { started && probePort() }

    /// 启动网关。已就绪则直接返回 true。
    @discardableResult
    func start() -> Bool {
        if started && probePort() { return true }

        do {
            try FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
        } catch {
            appendLog("[gateway] 创建数据目录失败：\(error.localizedDescription)")
            return false
        }

        // 首次启动把日志游标移到文件末尾，避免把上一次运行的日志当成本次输出
        if let attrs = try? FileManager.default.attributesOfItem(atPath: logURL.path),
           let size = attrs[.size] as? UInt64 {
            logOffset = size
        }

        let rc = dataDir.path.withCString { path in
            WildWorkStart(UnsafeMutablePointer(mutating: path), Gateway.port)
        }
        guard rc == 0 else {
            appendLog("[gateway] WildWorkStart 失败，返回码 \(rc)")
            return false
        }
        started = true
        appendLog("[gateway] 内嵌网关已启动，等待端口 \(Gateway.port)…")

        for _ in 0..<120 {  // 最多 30s：首次启动要建目录、扫账号、初始化调度器
            pumpLog()
            if probePort() { return true }
            Thread.sleep(forTimeInterval: 0.25)
        }
        appendLog("[gateway] 端口未就绪")
        return false
    }

    /// 静态库与 App 同生命周期，无需（也无法）主动 stop。
    /// 保留方法是为了与 Android 侧接口对齐。
    func stop() {
        // no-op
    }

    /// 读 `<home>/gateway.log` 的增量内容回调给 UI。
    /// iOS 没有可读的 stdout，Go 侧把日志同时写进这个文件。
    func pumpLog() {
        guard let handle = try? FileHandle(forReadingFrom: logURL) else { return }
        defer { try? handle.close() }
        let size = (try? handle.seekToEnd()) ?? 0
        guard size > logOffset else { return }
        try? handle.seek(toOffset: logOffset)
        let data = (try? handle.readToEnd()) ?? Data()
        logOffset = size
        guard let text = String(data: data, encoding: .utf8) else { return }
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            appendLog(String(line))
        }
    }

    private func probePort() -> Bool {
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = UInt16(Gateway.port).bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        let r = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return r == 0
    }

    /// 读 /api/state 判断 HTTP 层是否真的就绪（端口通 ≠ 服务可用）
    func fetchState() -> String? {
        guard let url = URL(string: "http://127.0.0.1:\(Gateway.port)/api/state") else { return nil }
        var result: String?
        let sem = DispatchSemaphore(value: 0)
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        URLSession.shared.dataTask(with: req) { data, resp, _ in
            if let http = resp as? HTTPURLResponse, http.statusCode == 200, let d = data {
                result = String(data: d, encoding: .utf8)
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 10)
        return result
    }

    private func appendLog(_ line: String) {
        logLock.lock()
        tail.append(line)
        if tail.count > 200 { tail.removeFirst() }
        let snapshot = tail.suffix(8).joined(separator: "\n")
        logLock.unlock()
        let cb = onLog
        DispatchQueue.main.async { cb?(snapshot) }
    }

    func recentLog() -> String {
        logLock.lock()
        defer { logLock.unlock() }
        return tail.suffix(8).joined(separator: "\n")
    }
}
