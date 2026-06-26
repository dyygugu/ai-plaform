using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Net;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace Aidp.LocalHelperLauncher
{
    internal static class Program
    {
        private const string MutexName = "Local\\AIDP_LOCAL_HELPER_WINDOWS_APP";

        [STAThread]
        private static void Main(string[] args)
        {
            if (HasArg(args, "--exit"))
            {
                LauncherOperations.StopHelperService();
                return;
            }

            bool createdNew;
            using (var mutex = new Mutex(true, MutexName, out createdNew))
            {
                if (!createdNew)
                {
                    LauncherOperations.OpenConsole();
                    return;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new LauncherContext(args));
            }
        }

        private static bool HasArg(string[] args, string expected)
        {
            if (args == null) return false;
            for (int i = 0; i < args.Length; i++)
            {
                if (string.Equals(args[i], expected, StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        }
    }

    internal sealed class LauncherContext : ApplicationContext
    {
        private readonly bool startMinimized;
        private readonly NotifyIcon tray;
        private readonly ToolStripMenuItem helperStatusItem;
        private readonly ToolStripMenuItem platformStatusItem;
        private readonly ToolStripMenuItem enableAutostartItem;
        private readonly ToolStripMenuItem disableAutostartItem;
        private readonly System.Windows.Forms.Timer statusTimer;
        private Process helperProcess;
        private bool exiting;
        private bool recovering;

        public LauncherContext(string[] args)
        {
            startMinimized = HasArg(args, "--minimized") || HasArg(args, "/minimized");

            helperStatusItem = new ToolStripMenuItem("本机助手启动中") { Enabled = false };
            platformStatusItem = new ToolStripMenuItem("平台连接检测中") { Enabled = false };
            enableAutostartItem = new ToolStripMenuItem("开启开机自启动", null, delegate { EnableAutostart(); });
            disableAutostartItem = new ToolStripMenuItem("关闭开机自启动", null, delegate { DisableAutostart(); });

            tray = new NotifyIcon();
            tray.Icon = SystemIcons.Application;
            tray.Text = "AIDP 本机助手";
            tray.Visible = true;
            tray.DoubleClick += delegate { LauncherOperations.OpenConsole(); };
            tray.ContextMenuStrip = BuildMenu();

            EnsureHelperStarted();
            if (!startMinimized)
            {
                LauncherOperations.OpenConsole();
            }

            statusTimer = new System.Windows.Forms.Timer();
            statusTimer.Interval = 5000;
            statusTimer.Tick += delegate { RefreshStatus(); };
            statusTimer.Start();
            RefreshStatus();
        }

        private ContextMenuStrip BuildMenu()
        {
            var menu = new ContextMenuStrip();
            menu.Items.Add(new ToolStripMenuItem("打开控制台", null, delegate { LauncherOperations.OpenConsole(); }));
            menu.Items.Add(helperStatusItem);
            menu.Items.Add(platformStatusItem);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("测试平台连接", null, delegate { TestPlatformConnection(); }));
            menu.Items.Add(new ToolStripMenuItem("检查更新", null, delegate { CheckUpdates(); }));
            menu.Items.Add(new ToolStripMenuItem("重启本机助手", null, delegate { RestartHelper(); }));
            menu.Items.Add(enableAutostartItem);
            menu.Items.Add(disableAutostartItem);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("退出本机助手", null, delegate { ExitApplication(); }));
            return menu;
        }

        private static bool HasArg(string[] args, string expected)
        {
            if (args == null) return false;
            for (int i = 0; i < args.Length; i++)
            {
                if (string.Equals(args[i], expected, StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        }

        private void EnsureHelperStarted()
        {
            if (LauncherOperations.IsHelperHealthy()) return;

            try
            {
                helperProcess = LauncherOperations.StartHelperProcess();
                if (!LauncherOperations.WaitForHelper(25))
                {
                    ShowBalloon("启动提醒", "本机助手正在启动中，如控制台暂时打不开，请稍后再试。", ToolTipIcon.Info);
                }
            }
            catch (Exception ex)
            {
                ShowBalloon("启动失败", "本机助手启动失败：" + ex.Message, ToolTipIcon.Error);
            }
        }

        private void RefreshStatus()
        {
            bool healthy = LauncherOperations.IsHelperHealthy();
            helperStatusItem.Text = healthy ? "本机助手运行中" : "本机助手未运行";
            helperStatusItem.ForeColor = healthy ? Color.SeaGreen : Color.Firebrick;
            platformStatusItem.Text = healthy ? "平台连接请在控制台查看" : "平台连接不可检测";
            enableAutostartItem.Enabled = !LauncherOperations.IsAutostartEnabled();
            disableAutostartItem.Enabled = LauncherOperations.IsAutostartEnabled();

            if (!healthy && !exiting && !recovering)
            {
                recovering = true;
                try
                {
                    helperStatusItem.Text = "正在恢复本机助手";
                    EnsureHelperStarted();
                }
                finally
                {
                    recovering = false;
                }
            }
        }

        private void TestPlatformConnection()
        {
            EnsureHelperStarted();
            string response = LauncherOperations.PostJson("/api/assistant/test-platform-connection", "{}");
            bool ok = response.IndexOf("\"ok\":true", StringComparison.OrdinalIgnoreCase) >= 0;
            platformStatusItem.Text = ok ? "平台连接正常" : "平台连接异常";
            platformStatusItem.ForeColor = ok ? Color.SeaGreen : Color.Firebrick;
            ShowBalloon("平台连接", ok ? "连接成功，可以正常访问平台。" : "连接失败，请在控制台检查平台地址。", ok ? ToolTipIcon.Info : ToolTipIcon.Warning);
        }

        private void CheckUpdates()
        {
            EnsureHelperStarted();
            string response = LauncherOperations.PostJson("/api/assistant/check-updates", "{}");
            string message = response.IndexOf("pending_idle", StringComparison.OrdinalIgnoreCase) >= 0
                ? "已发现新版，但当前正在执行任务。系统会等空闲后再更新。"
                : "更新检查完成，请在控制台查看详情。";
            ShowBalloon("检查更新", message, ToolTipIcon.Info);
        }

        private void RestartHelper()
        {
            StopHelper(false);
            Thread.Sleep(900);
            helperProcess = null;
            EnsureHelperStarted();
            LauncherOperations.OpenConsole();
        }

        private void EnableAutostart()
        {
            try
            {
                LauncherOperations.SetAutostart(true);
                RefreshStatus();
                ShowBalloon("开机自启动", "已开启开机自启动。", ToolTipIcon.Info);
            }
            catch (Exception ex)
            {
                ShowBalloon("开机自启动", "开启失败：" + ex.Message, ToolTipIcon.Error);
            }
        }

        private void DisableAutostart()
        {
            try
            {
                LauncherOperations.SetAutostart(false);
                RefreshStatus();
                ShowBalloon("开机自启动", "已关闭开机自启动。", ToolTipIcon.Info);
            }
            catch (Exception ex)
            {
                ShowBalloon("开机自启动", "关闭失败：" + ex.Message, ToolTipIcon.Error);
            }
        }

        private void ExitApplication()
        {
            if (exiting) return;
            string health = LauncherOperations.GetText("/api/health", 1200);
            if (health.IndexOf("\"workerRuntimeStatus\":\"running\"", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                DialogResult confirm = MessageBox.Show(
                    "当前正在执行平台任务，退出可能中断任务。确认退出吗？",
                    "退出本机助手",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning);
                if (confirm != DialogResult.Yes) return;
            }

            exiting = true;
            StopHelper(true);
            statusTimer.Stop();
            tray.Visible = false;
            tray.Dispose();
            ExitThread();
        }

        private void StopHelper(bool releasePort)
        {
            LauncherOperations.PostJson("/api/worker-runtime/stop", "{}");

            if (helperProcess != null && !helperProcess.HasExited)
            {
                try
                {
                    helperProcess.Kill();
                    helperProcess.WaitForExit(5000);
                }
                catch
                {
                }
            }

            if (releasePort)
            {
                LauncherOperations.StopHelperService();
            }
        }

        private void ShowBalloon(string title, string text, ToolTipIcon icon)
        {
            try
            {
                tray.BalloonTipTitle = title;
                tray.BalloonTipText = text;
                tray.BalloonTipIcon = icon;
                tray.ShowBalloonTip(3500);
            }
            catch
            {
            }
        }
    }

    internal static class LauncherOperations
    {
        private const int HelperPort = 8790;
        private const string HelperBaseUrl = "http://127.0.0.1:8790";
        private const string LauncherExeName = "AIDP 本机助手.exe";
        private const string StartupFileName = "AIDP 本机助手.cmd";

        public static string AppRoot
        {
            get { return AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar); }
        }

        public static void OpenConsole()
        {
            try
            {
                Process.Start(new ProcessStartInfo(HelperBaseUrl + "/") { UseShellExecute = true });
            }
            catch
            {
            }
        }

        public static bool IsHelperHealthy()
        {
            string text = GetText("/api/health", 900);
            return text.IndexOf("\"ok\":true", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        public static void StopHelperService()
        {
            PostJson("/api/worker-runtime/stop", "{}");
            KillHelperScriptProcesses();
            KillProcessesListeningOnPort(8790);
            KillLauncherProcessesInAppRoot();
        }

        public static bool WaitForHelper(int seconds)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < deadline)
            {
                if (IsHelperHealthy()) return true;
                Thread.Sleep(500);
            }
            return false;
        }

        public static Process StartHelperProcess()
        {
            string helperScript = FindHelperScript();
            if (string.IsNullOrEmpty(helperScript))
            {
                throw new FileNotFoundException("没有找到 local-agent\\host-launcher.ps1。请确认套件文件完整。");
            }

            string shell = FindPowerShell();
            string arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File \"" + helperScript + "\" -Port " + HelperPort + " -HostName 127.0.0.1";
            var info = new ProcessStartInfo();
            info.FileName = shell;
            info.Arguments = arguments;
            info.WorkingDirectory = Path.GetDirectoryName(helperScript);
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            return Process.Start(info);
        }

        public static string GetText(string path, int timeoutMs)
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(HelperBaseUrl + path);
                request.Method = "GET";
                request.Timeout = timeoutMs;
                request.ReadWriteTimeout = timeoutMs;
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var stream = response.GetResponseStream())
                using (var reader = new StreamReader(stream, Encoding.UTF8))
                {
                    return reader.ReadToEnd();
                }
            }
            catch
            {
                return string.Empty;
            }
        }

        public static string PostJson(string path, string json)
        {
            try
            {
                byte[] body = Encoding.UTF8.GetBytes(json ?? "{}");
                var request = (HttpWebRequest)WebRequest.Create(HelperBaseUrl + path);
                request.Method = "POST";
                request.Timeout = 6000;
                request.ReadWriteTimeout = 6000;
                request.ContentType = "application/json; charset=utf-8";
                request.ContentLength = body.Length;
                using (var stream = request.GetRequestStream())
                {
                    stream.Write(body, 0, body.Length);
                }
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var stream = response.GetResponseStream())
                using (var reader = new StreamReader(stream, Encoding.UTF8))
                {
                    return reader.ReadToEnd();
                }
            }
            catch (WebException ex)
            {
                try
                {
                    using (var stream = ex.Response == null ? null : ex.Response.GetResponseStream())
                    {
                        if (stream == null) return string.Empty;
                        using (var reader = new StreamReader(stream, Encoding.UTF8))
                        {
                            return reader.ReadToEnd();
                        }
                    }
                }
                catch
                {
                    return string.Empty;
                }
            }
            catch
            {
                return string.Empty;
            }
        }

        public static bool IsAutostartEnabled()
        {
            return File.Exists(GetStartupPath());
        }

        public static void SetAutostart(bool enabled)
        {
            string path = GetStartupPath();
            if (enabled)
            {
                string exe = GetCurrentLauncherPath();
                string content = "@echo off\r\nstart \"AIDP 本机助手\" \"" + exe + "\" --minimized\r\n";
                File.WriteAllText(path, content, new UTF8Encoding(false));
            }
            else if (File.Exists(path))
            {
                File.Delete(path);
            }
        }

        public static void KillProcessesListeningOnPort(int port)
        {
            try
            {
                var info = new ProcessStartInfo();
                info.FileName = "netstat.exe";
                info.Arguments = "-ano -p tcp";
                info.UseShellExecute = false;
                info.RedirectStandardOutput = true;
                info.CreateNoWindow = true;
                using (var process = Process.Start(info))
                {
                    string output = process.StandardOutput.ReadToEnd();
                    process.WaitForExit(3000);
                    string[] lines = output.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries);
                    for (int i = 0; i < lines.Length; i++)
                    {
                        string line = lines[i];
                        if (line.IndexOf("LISTENING", StringComparison.OrdinalIgnoreCase) < 0) continue;
                        if (line.IndexOf(":" + port + " ", StringComparison.OrdinalIgnoreCase) < 0) continue;
                        string[] parts = line.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                        if (parts.Length < 5) continue;
                        int pid;
                        if (!int.TryParse(parts[parts.Length - 1], out pid)) continue;
                        if (pid == Process.GetCurrentProcess().Id) continue;
                        try
                        {
                            Process.GetProcessById(pid).Kill();
                        }
                        catch
                        {
                        }
                    }
                }
            }
            catch
            {
            }
        }

        public static void KillHelperScriptProcesses()
        {
            try
            {
                using (var searcher = new ManagementObjectSearcher("SELECT ProcessId, CommandLine, Name FROM Win32_Process WHERE Name='pwsh.exe' OR Name='powershell.exe'"))
                using (var results = searcher.Get())
                {
                    foreach (ManagementObject item in results)
                    {
                        string commandLine = Convert.ToString(item["CommandLine"] ?? string.Empty);
                        if (commandLine.IndexOf("host-launcher.ps1", StringComparison.OrdinalIgnoreCase) < 0) continue;
                        if (commandLine.IndexOf("-Port 8790", StringComparison.OrdinalIgnoreCase) < 0 && commandLine.IndexOf(" 8790", StringComparison.OrdinalIgnoreCase) < 0) continue;

                        int pid;
                        if (!int.TryParse(Convert.ToString(item["ProcessId"]), out pid)) continue;
                        try
                        {
                            Process.GetProcessById(pid).Kill();
                        }
                        catch
                        {
                        }
                    }
                }
            }
            catch
            {
            }
        }

        public static void KillLauncherProcessesInAppRoot()
        {
            try
            {
                string currentId = Convert.ToString(Process.GetCurrentProcess().Id);
                using (var searcher = new ManagementObjectSearcher("SELECT ProcessId, CommandLine, Name FROM Win32_Process WHERE Name='AIDP 本机助手.exe'"))
                using (var results = searcher.Get())
                {
                    foreach (ManagementObject item in results)
                    {
                        string pidText = Convert.ToString(item["ProcessId"]);
                        if (string.Equals(pidText, currentId, StringComparison.OrdinalIgnoreCase)) continue;

                        string commandLine = Convert.ToString(item["CommandLine"] ?? string.Empty);
                        if (commandLine.IndexOf(AppRoot, StringComparison.OrdinalIgnoreCase) < 0) continue;

                        int pid;
                        if (!int.TryParse(pidText, out pid)) continue;
                        try
                        {
                            Process.GetProcessById(pid).Kill();
                        }
                        catch
                        {
                        }
                    }
                }
            }
            catch
            {
            }
        }

        private static string FindHelperScript()
        {
            string[] candidates = new[]
            {
                Path.Combine(AppRoot, "local-agent", "host-launcher.ps1"),
                Path.Combine(AppRoot, "host-launcher.ps1"),
                Path.Combine(Directory.GetParent(AppRoot) == null ? AppRoot : Directory.GetParent(AppRoot).FullName, "local-agent", "host-launcher.ps1")
            };
            for (int i = 0; i < candidates.Length; i++)
            {
                if (File.Exists(candidates[i])) return candidates[i];
            }
            return string.Empty;
        }

        private static string FindPowerShell()
        {
            string[] known = new[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs\\PowerShell\\7\\pwsh.exe"),
                "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
                "C:\\Program Files\\PowerShell\\7-preview\\pwsh.exe"
            };
            for (int i = 0; i < known.Length; i++)
            {
                if (File.Exists(known[i])) return known[i];
            }

            string path = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
            string[] roots = path.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries);
            for (int i = 0; i < roots.Length; i++)
            {
                string candidate = Path.Combine(roots[i].Trim(), "pwsh.exe");
                if (File.Exists(candidate)) return candidate;
            }
            return "powershell.exe";
        }

        private static string GetStartupPath()
        {
            string startup = Environment.GetFolderPath(Environment.SpecialFolder.Startup);
            return Path.Combine(startup, StartupFileName);
        }

        private static string GetCurrentLauncherPath()
        {
            string current = Process.GetCurrentProcess().MainModule.FileName;
            if (!string.IsNullOrEmpty(current) && File.Exists(current)) return current;
            return Path.Combine(AppRoot, LauncherExeName);
        }
    }
}
