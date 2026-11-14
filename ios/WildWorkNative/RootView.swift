import SwiftUI
import WebKit

struct RootView: View {
    @EnvironmentObject private var runner: GatewayRunner
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ZStack {
            Color(red: 0.039, green: 0.051, blue: 0.071).ignoresSafeArea()

            switch runner.phase {
            case .idle, .starting:
                BootView(log: runner.logTail)
            case .ready:
                PanelView(url: runner.panelURL)
                    .ignoresSafeArea(edges: .bottom)
            case .failed(let msg):
                FailureView(message: msg) { runner.boot() }
            }
        }
        .onAppear {
            if runner.phase == .idle { runner.boot() }
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active { runner.ensureAlive() }
        }
    }
}

/// 启动页：体现「服务在本机跑」，而不是一个空白 WebView
private struct BootView: View {
    let log: String

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .fill(LinearGradient(colors: [Color(red: 0.31, green: 0.55, blue: 0.99),
                                                  Color(red: 0.49, green: 0.42, blue: 1.0)],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
                    .frame(width: 64, height: 64)
                Text("W")
                    .font(.system(size: 30, weight: .heavy))
                    .foregroundColor(.white)
            }
            .padding(.top, 140)

            Text("WildWork")
                .font(.system(size: 21, weight: .bold))
                .foregroundColor(.white)
                .padding(.top, 16)

            Text("正在启动本地服务…")
                .font(.system(size: 13))
                .foregroundColor(Color(red: 0.58, green: 0.64, blue: 0.72))
                .padding(.top, 20)

            ProgressView()
                .progressViewStyle(.circular)
                .tint(.white)
                .padding(.top, 18)

            if !log.isEmpty {
                Text(log)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(Color(red: 0.29, green: 0.33, blue: 0.41))
                    .multilineTextAlignment(.center)
                    .lineLimit(6)
                    .padding(.horizontal, 24)
                    .padding(.top, 22)
            }

            Spacer()
        }
    }
}

private struct FailureView: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 34, weight: .semibold))
                .foregroundColor(Color(red: 0.97, green: 0.44, blue: 0.44))

            Text("服务启动失败")
                .font(.system(size: 17, weight: .bold))
                .foregroundColor(.white)

            Text(message)
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(Color(red: 0.58, green: 0.64, blue: 0.72))
                .multilineTextAlignment(.center)
                .lineLimit(10)
                .padding(.horizontal, 24)

            Button(action: retry) {
                Text("重试")
                    .font(.system(size: 15, weight: .bold))
                    .padding(.horizontal, 34)
                    .padding(.vertical, 12)
                    .background(Capsule().fill(Color(red: 0.31, green: 0.55, blue: 0.99)))
                    .foregroundColor(.white)
            }
            .padding(.top, 6)
        }
        .padding(20)
    }
}

/// 包一层 WKWebView，只做必要配置：允许本机明文 HTTP、非本机链接走系统浏览器
private struct PanelView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        let wv = WKWebView(frame: .zero, configuration: cfg)
        wv.isOpaque = false
        wv.backgroundColor = UIColor(red: 0.039, green: 0.051, blue: 0.071, alpha: 1)
        wv.scrollView.contentInsetAdjustmentBehavior = .never
        wv.navigationDelegate = context.coordinator
        wv.allowsBackForwardNavigationGestures = true
        wv.load(URLRequest(url: url))
        return wv
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, WKNavigationDelegate {
        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let target = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }
            let host = target.host ?? ""
            if host == "127.0.0.1" || host == "localhost" {
                decisionHandler(.allow)
            } else if target.scheme == "http" || target.scheme == "https" {
                UIApplication.shared.open(target)
                decisionHandler(.cancel)
            } else {
                decisionHandler(.allow)
            }
        }
    }
}
