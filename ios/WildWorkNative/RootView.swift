import SwiftUI
import UIKit
import WebKit

/// 上游面板是**浅色**主题（style.css: `--bg: #f5f6f8`）。
/// 容器必须同色：早期版本用了 #0A0D12 深色底，状态栏安全区露出深色，
/// 用户看到的就是「顶部信号/电池那一块是黑的」。
private let panelBackground = UIColor(red: 0.961, green: 0.965, blue: 0.973, alpha: 1)
private let inkColor = Color(red: 0.12, green: 0.14, blue: 0.17)
private let dimColor = Color(red: 0.42, green: 0.46, blue: 0.52)

struct RootView: View {
    @EnvironmentObject private var runner: GatewayRunner
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ZStack {
            Color(panelBackground).ignoresSafeArea()

            switch runner.phase {
            case .idle, .starting:
                BootView(log: runner.logTail)
            case .ready:
                PanelView(url: runner.panelURL)
                    .ignoresSafeArea(.all) // 铺满含状态栏区域；网页侧用 env(safe-area-inset-*) 留白
            case .failed(let msg):
                FailureView(message: msg) { runner.boot() }
            }
        }
        // 浅色面板配深色状态栏文字（Info.plist 另有 UIStatusBarStyleDarkContent）
        .preferredColorScheme(.light)
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
                .foregroundColor(inkColor)
                .padding(.top, 16)

            Text("正在启动本地服务…")
                .font(.system(size: 13))
                .foregroundColor(dimColor)
                .padding(.top, 20)

            ProgressView()
                .progressViewStyle(.circular)
                .tint(Color(red: 0.15, green: 0.39, blue: 0.92))
                .padding(.top, 18)

            if !log.isEmpty {
                Text(log)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(dimColor)
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
                .foregroundColor(Color(red: 0.86, green: 0.15, blue: 0.15))

            Text("服务启动失败")
                .font(.system(size: 17, weight: .bold))
                .foregroundColor(inkColor)

            Text(message)
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(dimColor)
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

/// 包一层 WKWebView：全屏铺满 + 原生下拉刷新 + 容器层注入（不改上游源码）。
private struct PanelView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        // atDocumentEnd：文档已解析完，head 存在，可安全创建 <style> 与遍历文本节点。
        // didFinish 里会再跑一次（幂等），覆盖面板 SPA 首屏重渲染后的节点。
        cfg.userContentController.addUserScript(
            WKUserScript(source: PanelTweaks.script,
                         injectionTime: .atDocumentEnd,
                         forMainFrameOnly: true))

        let wv = WKWebView(frame: .zero, configuration: cfg)
        // isOpaque=false 会让 WKWebView 每帧做透明合成（滚动时尤其明显）。
        // 面板本身不透明，背景色一致即可直接关掉 blending —— 这是移动端的主要卡顿源之一。
        wv.isOpaque = true
        wv.backgroundColor = panelBackground
        wv.scrollView.backgroundColor = panelBackground
        wv.scrollView.contentInsetAdjustmentBehavior = .never
        wv.navigationDelegate = context.coordinator
        wv.allowsBackForwardNavigationGestures = true

        // 原生下拉刷新：比在网页里模拟手势可靠，不与 iOS 的 rubber-band 冲突
        let refresh = UIRefreshControl()
        refresh.tintColor = UIColor(red: 0.15, green: 0.39, blue: 0.92, alpha: 1)
        refresh.addTarget(context.coordinator,
                          action: #selector(Coordinator.doRefresh(_:)),
                          for: .valueChanged)
        wv.scrollView.refreshControl = refresh
        wv.scrollView.bounces = true

        context.coordinator.webView = wv
        wv.load(URLRequest(url: url))
        return wv
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, WKNavigationDelegate {
        weak var webView: WKWebView?

        @objc func doRefresh(_ sender: UIRefreshControl) {
            webView?.reload()
            // reload 很快（LOAD_DEFAULT / WKWebView 缓存静态资源）；兜底超时防止转圈不消失
            DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak sender] in
                if sender?.isRefreshing == true { sender?.endRefreshing() }
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            webView.evaluateJavaScript(PanelTweaks.script, completionHandler: nil)
            if let r = webView.scrollView.refreshControl, r.isRefreshing {
                r.endRefreshing()
            }
        }

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

/// 容器层对原版面板的移动端适配。
///
/// 铁律：不改上游 Go 源码、不 fork 面板 HTML/CSS/JS。所有调整都是**运行时注入**，
/// 上游每次更新自动作用于新面板，不需要重放补丁。
/// Android 侧 MainActivity 里有一份内容相同的常量，改动必须同步两处（见 MAINTENANCE.md）。
enum PanelTweaks {
    static let script = """
    (function(){
      // 1) 安全区：viewport-fit=cover 让 env(safe-area-inset-*) 返回真实值。
      //    顶部由 body padding 让开状态栏，底部给 #app 补足 Home 指示条高度。
      //    Android WebView 没有安全区，env() 为 0，这条 CSS 天然无害。
      var CSS_ID = 'ww-mobile-css';
      var css = [
        'html,body{background:#f5f6f8 !important;}',
        'body{padding-top:env(safe-area-inset-top,0px) !important;}',
        '#app{padding-bottom:calc(60px + env(safe-area-inset-bottom,0px)) !important;}',
        // 2) 虚拟账号（oczen/kilo）在手机上没有 hover，title 里的「固定账号，不可删除」
        //    永远看不到，用户会误以为是删不掉的僵尸账号。用纯 CSS 伪元素补一条常驻说明，
        //    SPA 重渲染自动生效，零 JS 常驻开销。
        '.acct-card[data-na="1"]{position:relative;padding-bottom:28px !important;}',
        '.acct-card[data-na="1"]::after{content:"内置免费通道 · 系统自动管理，无需登录/删除";position:absolute;left:12px;bottom:7px;font-size:11px;color:#8a919f;line-height:1.3;}'
      ].join('');
      var old = document.getElementById(CSS_ID); if (old) old.remove();
      var st = document.createElement('style'); st.id = CSS_ID; st.textContent = css;
      (document.head || document.documentElement).appendChild(st);

      var m = document.querySelector('meta[name="viewport"]');
      if (m) m.setAttribute('content',
        'width=device-width, initial-scale=1.0, viewport-fit=cover, maximum-scale=1.0, user-scalable=no');

      // 3) 桌面专属文案 → 移动端说法（含底部 footer 的「右键托盘图标退出」）
      var R = [
        [' · 右键托盘图标退出', ''],
        ['9. 退出程序：右键托盘图标选择"退出"。', '9. 退出程序：切到后台即可，服务随 App 保持运行。'],
        ['8. 日志文件位于 exe 目录下', '8. 日志文件位于 App 文档目录下'],
        ['可从托盘菜单"查看日志"打开。', '可在 App 的「文件」中查看。']
      ];
      try {
        var w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
        var n, list = [];
        while ((n = w.nextNode())) list.push(n);
        list.forEach(function(t){
          var s = t.nodeValue, o = s;
          R.forEach(function(p){ if (s.indexOf(p[0]) >= 0) s = s.split(p[0]).join(p[1]); });
          if (s !== o) t.nodeValue = s;
        });
      } catch (e) {}

      // 4) 首次启动把虚拟账号恢复为「默认收起」。
      //    上游 SHOW_NA_KEY='acct-show-na' 会持久化展开态，手机误触一次就再也收不回去。
      //    只清一次；用户之后主动点「不适用」展开仍被尊重。
      try {
        if (!localStorage.getItem('ww-mobile-booted')) {
          localStorage.removeItem('acct-show-na');
          localStorage.setItem('ww-mobile-booted', '1');
        }
      } catch (e) {}
    })();
    """
}
