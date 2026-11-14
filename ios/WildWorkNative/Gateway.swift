import Foundation
import UIKit

/// 网关进程管理（iOS）。
///
/// 与 Android 的 GatewayService 对应：把 Go 网关作为子进程拉起，数据目录指到沙盒。
///
/// iOS 的特殊约束：
///   - App 沙盒内**允许** exec 自己 bundle 里的可执行文件（App Store 审核不允许，自签自用没问题）；
///   - bundle 内容不可写，所以 WILDWORK_HOME 必须指到沙盒 Documents；
///   - 进程随 App 退到后台可能被挂起，回到前台时需检查并在必要时重启。
final class Gateway {

    static let port = 7863

    private var process: Process?
    private let logLock = NSLock()
    private var tail: [String] = []
    private var onLog: ((String) -> Void)?

    var dataDir: URL {
        let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("wildwork", isDirectory: true)
    }

    func setLogHandler(_ h: @escaping (String) -> Void) {
        onLog = h
    }

    private var binaryPath: String? {
        let name = "gateway"
        if let p = Bundle.main.path(forResource: name, ofType: nil) {
            return p
        }
        return Bundle.main.bundlePath + "/" + name
    }

    var isRunning: Bool {
        guard let p = process else { return false }
        return p.isRunning
    }

    /// 启动网关。已完成启动则直接返回 true。
    @discardableResult
    func start() -> Bool {
        if isRunning && probePort() { return true }
        stop()

        guard let bin = binaryPath, FileManager.default.fileExists(atPath: bin) else {
            appendLog("[gateway] 缺少内置可执行文件 gateway")
            return false
        }
        // 兜底：从 bundle 拷到 Documents 再 exec，规避个别签名的执行限制
        var exec = bin
        if !FileManager.default.isExecutableFile(atPath: bin) {
            let tmp = dataDir.appendingPathComponent("gateway-bin")
            try? FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: tmp)
            do {
                try FileManager.default.copyItem(atPath: bin, toPath: tmp.path)
                exec = tmp.path
            } catch {
                appendLog("[gateway] 复制可执行文件失败：\(error.localizedDescription)")
            }
        }

        let fm = FileManager.default
        try? fm.createDirectory(at: dataDir, withIntermediateDirectories: true)

        // 首次运行写入最小配置：只监听本机
        let cfgURL = dataDir.appendingPathComponent("config.json")
        if !fm.fileExists(atPath: cfgURL.path) {
            let json = "{\n  \"listen\": { \"host\": \"127.0.0.1\", \"port\": \(Gateway.port) }\n}\n"
            try? json.write(to: cfgURL, atomically: true, encoding: .utf8)
        }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: exec)
        p.arguments = ["--no-tray"]
        p.currentDirectoryURL = dataDir
        var env = ProcessInfo.processInfo.environment
        env["WILDWORK_HOME"] = dataDir.path
        env["HOME"] = dataDir.path
        env["TMPDIR"] = FileManager.default.temporaryDirectory.path
        p.environment = env

        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard !d.isEmpty, let s = String(data: d, encoding: .utf8) else { return }
            for line in s.split(separator: "\n", omittingEmptySubsequences: true) {
                self?.appendLog(String(line))
            }
        }
        p.terminationHandler = { [weak self] proc in
            self?.appendLog("[gateway] 进程退出 code=\(proc.terminationStatus)")
        }

        do {
            try p.run()
            process = p
            appendLog("[gateway] 已启动 pid=\(p.processIdentifier)")
        } catch {
            appendLog("[gateway] 启动失败：\(error.localizedDescription)")
            return false
        }

        for _ in 0..<80 {
            if probePort() { return true }
            if !p.isRunning { break }
            Thread.sleep(forTimeInterval: 0.25)
        }
        return p.isRunning && probePort()
    }

    func stop() {
        guard let p = process else { return }
        process = nil
        if p.isRunning {
            p.terminate()
            // Go 默认把 SIGTERM 转成退出；2 秒不退则强杀
            DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
                if p.isRunning { kill(p.processIdentifier, SIGKILL) }
            }
        }
    }

    private func probePort() -> Bool {
        var ok = false
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = UInt16(Gateway.port).bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        defer { close(fd) }
        let r = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        ok = (r == 0)
        return ok
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
        logLock.unlock()
        let cb = onLog
        DispatchQueue.main.async { cb?(line) }
    }

    func recentLog() -> String {
        logLock.lock()
        defer { logLock.unlock() }
        return tail.suffix(8).joined(separator: "\n")
    }
}
