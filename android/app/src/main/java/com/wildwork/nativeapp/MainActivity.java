package com.wildwork.nativeapp;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

/**
 * 纯移动端：网关跑在本进程内（Android 侧为子进程，iOS 侧为静态库），
 * App 只是它的窗口。功能与原版桌面程序完全一致，因为就是同一份服务端代码。
 *
 * 界面刻意做成「启动页 → 面板」两步：
 *   启动页显示网关引导日志，能让用户看到「正在启动服务」而不是干等白屏。
 *
 * 面板是上游的浅色主题（#f5f6f8），容器/状态栏必须同色，否则顶部会露出深色块。
 */
public class MainActivity extends Activity implements GatewayService.Listener {

    private static final int PANEL_BG = Color.parseColor("#F5F6F8");

    private GatewayService gateway;
    private WebView webView;
    private PullToRefreshLayout pull;
    private FrameLayout root;

    // 启动页控件
    private LinearLayout bootView;
    private TextView bootLog;
    private TextView bootStatus;
    private ProgressBar bootSpinner;
    private Button bootRetry;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private volatile boolean stopped = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // 浅色面板 → 浅色状态栏 + 深色状态栏图标
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            getWindow().setStatusBarColor(PANEL_BG);
            View decor = getWindow().getDecorView();
            decor.setSystemUiVisibility(decor.getSystemUiVisibility()
                    | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
        gateway = new GatewayService(this);
        buildUi();
        boot();
    }

    private void buildUi() {
        root = new FrameLayout(this);
        root.setBackgroundColor(PANEL_BG);

        webView = new WebView(this);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(false);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        // LOAD_NO_CACHE 会让每次刷新重新下载并重新解析上游 5000 行 app.js —— 手机上的主要卡顿源。
        // 面板走 127.0.0.1，静态资源交给 WebView 正常缓存即可；数据接口永远实时请求。
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        webView.setBackgroundColor(PANEL_BG);
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (url == null) return false;
                if (url.startsWith("http://127.0.0.1:" + GatewayService.PORT)
                        || url.startsWith("http://localhost:" + GatewayService.PORT)) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url)));
                    return true;
                } catch (Exception e) {
                    return false;
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // 上游面板是 SPA；onPageFinished 时 DOM 已就绪，注入一次即可。
                // 内容与 iOS PanelTweaks.script 一致，见 PanelTweaks.java。
                view.evaluateJavascript(PanelTweaks.SCRIPT, null);
                pull.setRefreshing(false);
            }
        });
        webView.setWebChromeClient(new WebChromeClient());
        webView.setVisibility(View.GONE);

        pull = new PullToRefreshLayout(this);
        pull.attach(webView);
        pull.setOnRefreshListener(() -> ui.post(() -> {
            if (!stopped) webView.reload();
        }));
        root.addView(pull, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        bootView = new LinearLayout(this);
        bootView.setOrientation(LinearLayout.VERTICAL);
        bootView.setGravity(Gravity.CENTER_HORIZONTAL);
        int pad = dp(24);
        bootView.setPadding(pad, 0, pad, pad);

        TextView logo = new TextView(this);
        logo.setText("W");
        logo.setTextSize(30);
        logo.setTextColor(Color.WHITE);
        logo.setGravity(Gravity.CENTER);
        GradientDrawable logoBg = new GradientDrawable();
        logoBg.setShape(GradientDrawable.RECTANGLE);
        logoBg.setCornerRadius(dp(20));
        logoBg.setColors(new int[]{Color.parseColor("#4F8DFD"), Color.parseColor("#7C6CFF")});
        logoBg.setOrientation(GradientDrawable.Orientation.TL_BR);
        logo.setBackground(logoBg);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(64), dp(64));
        lp.topMargin = dp(120);
        bootView.addView(logo, lp);

        TextView title = new TextView(this);
        title.setText("WildWork");
        title.setTextSize(21);
        title.setTextColor(Color.parseColor("#1F2329"));
        title.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        tlp.topMargin = dp(16);
        bootView.addView(title, tlp);

        bootStatus = new TextView(this);
        bootStatus.setText("正在启动本地服务…");
        bootStatus.setTextSize(13);
        bootStatus.setTextColor(Color.parseColor("#5A6472"));
        bootStatus.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        slp.topMargin = dp(20);
        bootView.addView(bootStatus, slp);

        bootSpinner = new ProgressBar(this);
        LinearLayout.LayoutParams plp = new LinearLayout.LayoutParams(dp(26), dp(26));
        plp.topMargin = dp(18);
        bootView.addView(bootSpinner, plp);

        bootLog = new TextView(this);
        bootLog.setTextSize(10);
        bootLog.setTextColor(Color.parseColor("#6B7280"));
        bootLog.setTypeface(android.graphics.Typeface.MONOSPACE);
        bootLog.setMaxLines(6);
        bootLog.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams lglp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lglp.topMargin = dp(22);
        bootView.addView(bootLog, lglp);

        bootRetry = new Button(this);
        bootRetry.setText("重试");
        bootRetry.setVisibility(View.GONE);
        bootRetry.setOnClickListener(v -> {
            bootRetry.setVisibility(View.GONE);
            boot();
        });
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        blp.topMargin = dp(20);
        bootView.addView(bootRetry, blp);

        root.addView(bootView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        setContentView(root);
    }

    private void boot() {
        stopped = false;
        bootView.setVisibility(View.VISIBLE);
        webView.setVisibility(View.GONE);
        bootStatus.setText("正在启动本地服务…");
        bootStatus.setTextColor(Color.parseColor("#5A6472"));
        bootSpinner.setVisibility(View.VISIBLE);

        new Thread(() -> {
            try {
                boolean ok = gateway.start(this);
                if (stopped) return;
                if (!ok) {
                    ui.post(() -> showBootError("服务未能启动"));
                    return;
                }
                // 端口通不代表 HTTP 就绪，再确认一次 /api/state
                String state = null;
                for (int i = 0; i < 20 && state == null; i++) {
                    state = gateway.fetchState();
                    if (state == null) Thread.sleep(300);
                }
                if (stopped) return;
                if (state == null) {
                    String log = gateway.recentLog();
                    ui.post(() -> showBootError(log.isEmpty() ? "服务无响应" : log));
                    return;
                }
                ui.post(this::showPanel);
            } catch (final Exception e) {
                if (!stopped) ui.post(() -> showBootError(e.getMessage()));
            }
        }, "ww-boot").start();
    }

    private void showPanel() {
        bootStatus.setText("就绪");
        bootSpinner.setVisibility(View.GONE);
        bootView.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl("http://127.0.0.1:" + GatewayService.PORT + "/");
    }

    private void showBootError(String detail) {
        bootSpinner.setVisibility(View.GONE);
        bootStatus.setText("启动失败");
        bootStatus.setTextColor(Color.parseColor("#DC2626"));
        bootLog.setText(detail == null ? "" : detail);
        bootRetry.setVisibility(View.VISIBLE);
    }

    @Override
    public void onLog(String line) {
        ui.post(() -> {
            if (bootView.getVisibility() != View.VISIBLE) return;
            String cur = bootLog.getText().toString();
            String next = cur.isEmpty() ? line : (cur + "\n" + line);
            String[] lines = next.split("\n");
            StringBuilder sb = new StringBuilder();
            int from = Math.max(0, lines.length - 6);
            for (int i = from; i < lines.length; i++) {
                sb.append(lines[i]);
                if (i < lines.length - 1) sb.append('\n');
            }
            bootLog.setText(sb.toString());
        });
    }

    @Override
    public void onExit(int code) {
        if (stopped) return;
        ui.post(() -> {
            if (bootView.getVisibility() == View.VISIBLE) {
                showBootError("服务已退出（code=" + code + "）\n\n" + gateway.recentLog());
            }
        });
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.getVisibility() == View.VISIBLE && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        stopped = true;
        if (gateway != null) gateway.stop();
        if (webView != null) {
            try {
                if (pull != null) pull.removeView(webView);
                webView.destroy();
            } catch (Exception ignored) {}
        }
        super.onDestroy();
    }
}
