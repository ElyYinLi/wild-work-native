package com.wildwork.nativeapp;

import android.content.Context;
import android.os.Build;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * 内置网关的进程管理。
 *
 * 设计要点：
 *   - 网关就是上游 wild-work 的 Go 二进制，交叉编译成 android/arm64 静态二进制后随 APK 打包；
 *     运行时解压到应用私有目录并 chmod 755（APK 内 assets 没有可执行位）。
 *   - 数据目录通过 WILDWORK_HOME 指定到 filesDir，避免往只读的 APK 内写。
 *   - 网关自己会提供 Web 面板，所以 App 直接加载它，UI 与上游完全一致，不需要另做一套。
 */
public final class GatewayService {

    public static final int PORT = 7863;

    /**
     * 网关二进制打包为 jniLibs/arm64-v8a/libgateway.so。
     *
     * 为什么叫 .so：Android 只在 lib/<abi>/ 下解压 native 库，且解压后自带可执行权限、
     * 位于可执行目录。放进 assets 的话既没有执行位，也无法 chmod（data 分区部分机型挂载 noexec），
     * 这是最稳的做法。它并不是真正的共享库，只是借用打包通道。
     */
    private static final String LIB_NAME = "libgateway.so";

    private final Context ctx;
    private Process process;
    private Thread logThread;
    private final List<String> tailLog = new ArrayList<>();

    public GatewayService(Context ctx) {
        this.ctx = ctx.getApplicationContext();
    }

    public interface Listener {
        void onLog(String line);
        void onExit(int code);
    }

    /** 定位系统解压好的可执行文件；不存在时尝试用 ApplicationInfo.nativeLibraryDir。 */
    private File resolveBinary() throws IOException {
        String dir = ctx.getApplicationInfo().nativeLibraryDir;
        File bin = new File(dir, LIB_NAME);
        if (bin.exists()) {
            // 系统解压时已带执行位，这里再兜底一次
            if (!bin.canExecute()) {
                //noinspection ResultOfMethodCallIgnored
                bin.setExecutable(true, false);
            }
            if (bin.canExecute()) return bin;
        }
        throw new IOException("内置网关缺失，请确认安装包 ABI 为 arm64-v8a（当前 nativeLibraryDir="
                + dir + "，存在=" + bin.exists() + "）");
    }

    public boolean isRunning() {
        if (process == null) return false;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            return process.isAlive();
        }
        try {
            process.exitValue();
            return false;
        } catch (IllegalThreadStateException e) {
            return true;
        }
    }

    /** 启动网关并等待端口就绪。阻塞调用，须在工作线程执行。 */
    public synchronized boolean start(Listener listener) throws Exception {
        if (isRunning()) return true;

        File bin = resolveBinary();
        File home = new File(ctx.getFilesDir(), "ww");
        if (!home.exists() && !home.mkdirs()) {
            throw new IOException("无法创建数据目录");
        }

        // 首次启动前写入最小配置：只监听本机 + 固定端口。
        // 面板鉴权保持关闭——App 只在本机回环访问，端口不对局域网暴露。
        File cfg = new File(home, "config.json");
        if (!cfg.exists()) {
            writeAll(cfg, "{\n" +
                    "  \"listen\": { \"host\": \"127.0.0.1\", \"port\": " + PORT + " }\n" +
                    "}\n");
        }

        ProcessBuilder pb = new ProcessBuilder(bin.getAbsolutePath(), "--no-tray");
        Map<String, String> env = pb.environment();
        // workDir() 优先读 WILDWORK_HOME，确保 config.json / auths / data 都落在私有目录
        env.put("WILDWORK_HOME", home.getAbsolutePath());
        env.put("HOME", home.getAbsolutePath());
        env.put("TMPDIR", ctx.getCacheDir().getAbsolutePath());
        pb.directory(home);
        pb.redirectErrorStream(true);

        process = pb.start();
        final Process proc = process;

        logThread = new Thread(() -> {
            try (BufferedReader r = new BufferedReader(new InputStreamReader(proc.getInputStream()))) {
                String line;
                while ((line = r.readLine()) != null) {
                    synchronized (tailLog) {
                        tailLog.add(line);
                        if (tailLog.size() > 200) tailLog.remove(0);
                    }
                    if (listener != null) listener.onLog(line);
                }
            } catch (IOException ignored) {
            }
            int code = -1;
            try {
                code = proc.waitFor();
            } catch (InterruptedException ignored) {
            }
            if (listener != null) listener.onExit(code);
        }, "ww-gateway-log");
        logThread.setDaemon(true);
        logThread.start();

        // 等到端口可连（全新启动首次会生成配置，通常 1 秒内）
        for (int i = 0; i < 60; i++) {
            if (probePort()) return true;
            if (!isRunning()) break;
            Thread.sleep(250);
        }
        return isRunning() && probePort();
    }

    private boolean probePort() {
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress("127.0.0.1", PORT), 400);
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    /** 读一次 /api/state，用于快速判定网关是否真的可用（端口通 ≠ 服务已就绪）。 */
    public String fetchState() {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://127.0.0.1:" + PORT + "/api/state").openConnection();
            c.setConnectTimeout(3000);
            c.setReadTimeout(8000);
            if (c.getResponseCode() != 200) return null;
            StringBuilder sb = new StringBuilder();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), "UTF-8"))) {
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
            }
            return sb.toString();
        } catch (Exception e) {
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    public synchronized void stop() {
        Process p = process;
        process = null;
        if (p == null) return;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                p.destroy();
                if (!p.waitFor(3, TimeUnit.SECONDS)) p.destroyForcibly();
            } else {
                p.destroy();
            }
        } catch (Exception ignored) {
        }
        // Go 不响应 SIGTERM 时 ensure 干净退出（会话文件句柄释放）
        try {
            Process k = new ProcessBuilder("killall", "-9", LIB_NAME).redirectErrorStream(true).start();
            k.waitFor(2, TimeUnit.SECONDS);
        } catch (Exception ignored) {
        }
    }

    public String recentLog() {
        synchronized (tailLog) {
            StringBuilder sb = new StringBuilder();
            int from = Math.max(0, tailLog.size() - 40);
            for (int i = from; i < tailLog.size(); i++) {
                sb.append(tailLog.get(i)).append('\n');
            }
            return sb.toString();
        }
    }

    /** 数据目录，供「导出数据」使用 */
    public File dataDir() {
        return new File(ctx.getFilesDir(), "ww");
    }


    private static void writeAll(File f, String content) throws IOException {
        try (OutputStream os = new FileOutputStream(f)) {
            os.write(content.getBytes("UTF-8"));
        }
    }
}
