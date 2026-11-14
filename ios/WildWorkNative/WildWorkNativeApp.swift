import SwiftUI

@main
struct WildWorkNativeApp: App {
    @StateObject private var runner = GatewayRunner()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(runner)
                .prefersHomeIndicatorAutoHidden(false)
        }
    }
}

/// 网关与 UI 的桥梁：负责启动、就绪判定、错误展示。
@MainActor
final class GatewayRunner: ObservableObject {

    enum Phase: Equatable {
        case idle
        case starting
        case ready
        case failed(String)
    }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var logTail: String = ""

    let gateway = Gateway()

    var panelURL: URL {
        URL(string: "http://127.0.0.1:\(Gateway.port)/")!
    }

    func boot() {
        guard phase != .starting else { return }
        phase = .starting
        gateway.setLogHandler { [weak self] line in
            guard let self = self else { return }
            Task { @MainActor in
                self.logTail = self.gateway.recentLog()
            }
        }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let started = self.gateway.start()

            var state: String?
            if started {
                for _ in 0..<20 {
                    state = self.gateway.fetchState()
                    if state != nil { break }
                    Thread.sleep(forTimeInterval: 0.3)
                }
            }

            let log = self.gateway.recentLog()
            Task { @MainActor in
                if state != nil {
                    self.phase = .ready
                } else if !started {
                    self.phase = .failed(log.isEmpty ? "网关启动失败" : log)
                } else {
                    self.phase = .failed(log.isEmpty ? "网关无响应" : log)
                }
            }
        }
    }

    /// 回到前台时确保网关还活着（系统可能已回收子进程）
    func ensureAlive() {
        if phase == .ready && !gateway.isRunning {
            phase = .idle
            boot()
        }
    }

    func shutdown() {
        gateway.stop()
    }
}
