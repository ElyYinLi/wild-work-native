package com.wildwork.nativeapp;

/**
 * 容器层对原版面板的移动端适配（运行时注入，不改上游源码）。
 *
 * 与 iOS 端 RootView.swift 里的 PanelTweaks.script **逐行一致**，改动必须同步两处。
 * 详见 docs/MAINTENANCE.md「容器注入层」一节。
 */
public final class PanelTweaks {

    private PanelTweaks() {}

    public static final String SCRIPT = """
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
        """;
}
