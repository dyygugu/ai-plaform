using Microsoft.Win32;
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

namespace Aidp.LocalHelperSetup
{
    internal static class Program
    {
        private const string ProductName = "AIDP 本机助手";
        private const string InstallerTitle = "AIDP 本机助手安装向导";

        [STAThread]
        private static void Main(string[] args)
        {
            SetupOptions options = SetupOptions.Parse(args);
            if (options.Uninstall)
            {
                int code = InstallerActions.Uninstall(options);
                Environment.Exit(code);
                return;
            }

            if (options.QuietInstall)
            {
                int code = InstallerActions.Install(options);
                Environment.Exit(code);
                return;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm(options));
        }

        internal static string Title
        {
            get { return InstallerTitle; }
        }
    }

    internal sealed class SetupOptions
    {
        public bool QuietInstall;
        public bool Uninstall;
        public bool CreateDesktopShortcut = true;
        public bool CreateStartMenuShortcut = true;
        public bool EnableAutostart;
        public bool LaunchAfterInstall = true;
        public bool KeepConfig = true;
        public string InstallRoot = InstallerPaths.DefaultInstallRoot;

        public static SetupOptions Parse(string[] args)
        {
            var options = new SetupOptions();
            for (int i = 0; args != null && i < args.Length; i++)
            {
                string arg = args[i] ?? string.Empty;
                string lower = arg.ToLowerInvariant();
                if (lower == "--install" || lower == "/install" || lower == "--quiet")
                {
                    options.QuietInstall = true;
                }
                else if (lower == "--uninstall" || lower == "/uninstall")
                {
                    options.Uninstall = true;
                }
                else if (lower == "--install-root" && i + 1 < args.Length)
                {
                    options.InstallRoot = args[++i];
                }
                else if (lower == "--no-desktop-shortcut")
                {
                    options.CreateDesktopShortcut = false;
                }
                else if (lower == "--no-start-menu-shortcut")
                {
                    options.CreateStartMenuShortcut = false;
                }
                else if (lower == "--autostart")
                {
                    options.EnableAutostart = true;
                }
                else if (lower == "--no-launch")
                {
                    options.LaunchAfterInstall = false;
                }
                else if (lower == "--remove-config")
                {
                    options.KeepConfig = false;
                }
                else if (lower == "--keep-config")
                {
                    options.KeepConfig = true;
                }
            }

            string registryRoot = InstallerActions.ReadInstallRootFromRegistry();
            if (options.Uninstall && !HasInstallRootArg(args) && !string.IsNullOrEmpty(registryRoot))
            {
                options.InstallRoot = registryRoot;
            }
            return options;
        }

        private static bool HasInstallRootArg(string[] args)
        {
            for (int i = 0; args != null && i < args.Length; i++)
            {
                if (string.Equals(args[i], "--install-root", StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        }
    }

    internal sealed class SetupForm : Form
    {
        private readonly SetupOptions options;
        private readonly TextBox installRootText;
        private readonly CheckBox desktopShortcut;
        private readonly CheckBox startMenuShortcut;
        private readonly CheckBox autostart;
        private readonly CheckBox launchAfterInstall;
        private readonly TextBox logBox;
        private readonly Button installButton;

        public SetupForm(SetupOptions options)
        {
            this.options = options;
            Text = Program.Title;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(720, 520);
            MinimumSize = new Size(720, 520);
            Font = new Font("Microsoft YaHei UI", 9F);

            var title = new Label();
            title.Text = "AIDP 本机助手安装向导";
            title.Font = new Font(Font.FontFamily, 18F, FontStyle.Bold);
            title.AutoSize = true;
            title.Location = new Point(26, 22);
            Controls.Add(title);

            var description = new Label();
            description.Text = "安装后可通过桌面或开始菜单打开本机助手，支持托盘常驻、开机自启动和一键卸载。";
            description.AutoSize = true;
            description.ForeColor = Color.FromArgb(90, 100, 116);
            description.Location = new Point(30, 66);
            Controls.Add(description);

            var pathLabel = new Label();
            pathLabel.Text = "安装位置";
            pathLabel.AutoSize = true;
            pathLabel.Location = new Point(30, 108);
            Controls.Add(pathLabel);

            installRootText = new TextBox();
            installRootText.Text = options.InstallRoot;
            installRootText.Location = new Point(30, 132);
            installRootText.Width = 560;
            Controls.Add(installRootText);

            var browse = new Button();
            browse.Text = "浏览";
            browse.Location = new Point(604, 130);
            browse.Width = 80;
            browse.Click += delegate { BrowseInstallRoot(); };
            Controls.Add(browse);

            desktopShortcut = NewCheckBox("创建桌面快捷方式", 30, 182, options.CreateDesktopShortcut);
            startMenuShortcut = NewCheckBox("创建开始菜单入口", 30, 216, options.CreateStartMenuShortcut);
            autostart = NewCheckBox("安装后开启开机自启动", 30, 250, options.EnableAutostart);
            launchAfterInstall = NewCheckBox("安装完成后立即启动本机助手", 30, 284, options.LaunchAfterInstall);

            logBox = new TextBox();
            logBox.Multiline = true;
            logBox.ReadOnly = true;
            logBox.ScrollBars = ScrollBars.Vertical;
            logBox.Location = new Point(30, 330);
            logBox.Size = new Size(654, 110);
            logBox.Text = "准备安装。\r\n";
            Controls.Add(logBox);

            installButton = new Button();
            installButton.Text = "开始安装";
            installButton.Location = new Point(490, 462);
            installButton.Size = new Size(92, 32);
            installButton.Click += delegate { Install(); };
            Controls.Add(installButton);

            var cancel = new Button();
            cancel.Text = "取消";
            cancel.Location = new Point(592, 462);
            cancel.Size = new Size(92, 32);
            cancel.Click += delegate { Close(); };
            Controls.Add(cancel);
        }

        private CheckBox NewCheckBox(string text, int x, int y, bool isChecked)
        {
            var box = new CheckBox();
            box.Text = text;
            box.Checked = isChecked;
            box.AutoSize = true;
            box.Location = new Point(x, y);
            Controls.Add(box);
            return box;
        }

        private void BrowseInstallRoot()
        {
            using (var dialog = new FolderBrowserDialog())
            {
                dialog.Description = "选择 AIDP 本机助手安装目录";
                dialog.SelectedPath = installRootText.Text;
                if (dialog.ShowDialog(this) == DialogResult.OK)
                {
                    installRootText.Text = dialog.SelectedPath;
                }
            }
        }

        private void Install()
        {
            installButton.Enabled = false;
            options.InstallRoot = installRootText.Text;
            options.CreateDesktopShortcut = desktopShortcut.Checked;
            options.CreateStartMenuShortcut = startMenuShortcut.Checked;
            options.EnableAutostart = autostart.Checked;
            options.LaunchAfterInstall = launchAfterInstall.Checked;

            try
            {
                int code = InstallerActions.Install(options, AppendLog);
                if (code == 0)
                {
                    AppendLog("安装完成。");
                    MessageBox.Show(this, "AIDP 本机助手已安装完成。", "安装完成", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    Close();
                }
                else
                {
                    installButton.Enabled = true;
                    MessageBox.Show(this, "安装未完成，请查看安装日志。", "安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
            catch (Exception ex)
            {
                installButton.Enabled = true;
                AppendLog("安装失败：" + ex.Message);
                MessageBox.Show(this, "安装失败：" + ex.Message, "安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void AppendLog(string message)
        {
            logBox.AppendText(message + "\r\n");
        }
    }

    internal static class InstallerActions
    {
        private const string ProductName = "AIDP 本机助手";
        private const string LauncherExe = "AIDP 本机助手.exe";
        private const string UninstallerExe = "AIDP 本机助手卸载.exe";
        private const string StartupCmd = "AIDP 本机助手.cmd";
        private const string UninstallRegistryPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\AIDP Local Helper";

        public static int Install(SetupOptions options)
        {
            return Install(options, null);
        }

        public static int Install(SetupOptions options, Action<string> log)
        {
            try
            {
                string installRoot = Path.GetFullPath(options.InstallRoot);
                log = log ?? delegate { };
                log("正在准备安装目录：" + installRoot);
                Directory.CreateDirectory(installRoot);

                StopExistingHelper(installRoot, log);

                string tempRoot = Path.Combine(Path.GetTempPath(), "aidp-setup-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(tempRoot);
                try
                {
                    log("正在解压安装包。");
                    string suiteRoot = ExtractEmbeddedPayloadZip(tempRoot);
                    CopyDirectory(suiteRoot, installRoot);

                    string currentExe = Assembly.GetExecutingAssembly().Location;
                    File.Copy(currentExe, Path.Combine(installRoot, UninstallerExe), true);

                    if (options.CreateDesktopShortcut)
                    {
                        CreateDesktopShortcut(installRoot);
                        log("已创建桌面快捷方式。");
                    }
                    else
                    {
                        DeleteFileIfExists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), ProductName + ".lnk"));
                    }

                    if (options.CreateStartMenuShortcut)
                    {
                        CreateStartMenuShortcuts(installRoot);
                        log("已创建开始菜单入口。");
                    }
                    else
                    {
                        DeleteDirectoryIfExists(InstallerPaths.StartMenuFolder);
                    }

                    SetAutostart(installRoot, options.EnableAutostart);
                    WriteUninstallRegistry(installRoot);
                    WriteInstallLog(installRoot, "installed");

                    if (options.LaunchAfterInstall)
                    {
                        Process.Start(new ProcessStartInfo(Path.Combine(installRoot, LauncherExe)) { UseShellExecute = true });
                        log("已启动本机助手。");
                    }

                    return 0;
                }
                finally
                {
                    DeleteDirectoryIfExists(tempRoot);
                }
            }
            catch (Exception ex)
            {
                try { WriteInstallLog(options.InstallRoot, "install_failed: " + ex.Message); } catch { }
                if (!options.QuietInstall)
                {
                    MessageBox.Show("安装失败：" + ex.Message, "AIDP 本机助手安装向导", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return 1;
            }
        }

        public static int Uninstall(SetupOptions options)
        {
            try
            {
                string installRoot = Path.GetFullPath(options.InstallRoot);
                if (!Directory.Exists(installRoot))
                {
                    RemoveShortcutsAndRegistry();
                    return 0;
                }

                bool keepConfig = options.KeepConfig;
                if (!options.QuietInstall)
                {
                    DialogResult answer = MessageBox.Show(
                        "是否保留本机配置、日志和平台地址？\r\n\r\n选择“是”会保留配置；选择“否”会完整删除。",
                        "卸载 AIDP 本机助手",
                        MessageBoxButtons.YesNoCancel,
                        MessageBoxIcon.Question);
                    if (answer == DialogResult.Cancel) return 2;
                    keepConfig = answer == DialogResult.Yes;
                }

                StopExistingHelper(installRoot, null);
                RemoveShortcutsAndRegistry();

                if (keepConfig)
                {
                    PreserveConfigAndDeleteInstallRoot(installRoot);
                }
                else
                {
                    if (IsSafeInstallRoot(installRoot)) DeleteDirectoryIfExists(installRoot);
                    if (Directory.Exists(installRoot)) ScheduleSelfCleanup(installRoot);
                }
                return 0;
            }
            catch (Exception ex)
            {
                if (!options.QuietInstall)
                {
                    MessageBox.Show("卸载失败：" + ex.Message, "卸载 AIDP 本机助手", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return 1;
            }
        }

        public static string ReadInstallRootFromRegistry()
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallRegistryPath))
                {
                    return Convert.ToString(key == null ? "" : key.GetValue("InstallLocation", ""));
                }
            }
            catch
            {
                return "";
            }
        }

        public static string ExtractEmbeddedPayloadZip(string tempRoot)
        {
            string exePath = Assembly.GetExecutingAssembly().Location;
            string zipPath = Path.Combine(tempRoot, "aidp-local-suite-payload.zip");
            EmbeddedPayload.ExtractToFile(exePath, zipPath);
            string extractRoot = Path.Combine(tempRoot, "suite");
            Directory.CreateDirectory(extractRoot);
            ZipFile.ExtractToDirectory(zipPath, extractRoot);
            return extractRoot;
        }

        private static void StopExistingHelper(string installRoot, Action<string> log)
        {
            log = log ?? delegate { };
            string launcher = Path.Combine(installRoot, LauncherExe);
            if (File.Exists(launcher))
            {
                try
                {
                    var process = Process.Start(new ProcessStartInfo(launcher, "--exit") { UseShellExecute = false, CreateNoWindow = true });
                    if (process != null) process.WaitForExit(8000);
                    log("已停止旧版本机助手。");
                }
                catch
                {
                }
            }
        }

        private static void CopyDirectory(string source, string destination)
        {
            foreach (string dir in Directory.GetDirectories(source, "*", SearchOption.AllDirectories))
            {
                Directory.CreateDirectory(dir.Replace(source, destination));
            }
            foreach (string file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
            {
                string target = file.Replace(source, destination);
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                File.Copy(file, target, true);
            }
        }

        private static void CreateDesktopShortcut(string installRoot)
        {
            string shortcutPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), ProductName + ".lnk");
            CreateShortcut(shortcutPath, Path.Combine(installRoot, LauncherExe), "", installRoot, ProductName);
        }

        private static void CreateStartMenuShortcuts(string installRoot)
        {
            Directory.CreateDirectory(InstallerPaths.StartMenuFolder);
            CreateShortcut(Path.Combine(InstallerPaths.StartMenuFolder, ProductName + ".lnk"), Path.Combine(installRoot, LauncherExe), "", installRoot, ProductName);
            CreateShortcut(Path.Combine(InstallerPaths.StartMenuFolder, "卸载 AIDP 本机助手.lnk"), Path.Combine(installRoot, UninstallerExe), "--uninstall", installRoot, "卸载 AIDP 本机助手");
        }

        private static void CreateShortcut(string shortcutPath, string targetPath, string arguments, string workingDirectory, string description)
        {
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            object shell = Activator.CreateInstance(shellType);
            object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { shortcutPath });
            Type shortcutType = shortcut.GetType();
            shortcutType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetPath });
            shortcutType.InvokeMember("Arguments", BindingFlags.SetProperty, null, shortcut, new object[] { arguments });
            shortcutType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workingDirectory });
            shortcutType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { description });
            shortcutType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { targetPath + ",0" });
            shortcutType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
        }

        private static void SetAutostart(string installRoot, bool enabled)
        {
            string startupPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), StartupCmd);
            if (enabled)
            {
                string launcher = Path.Combine(installRoot, LauncherExe);
                string content = "@echo off\r\nstart \"AIDP 本机助手\" \"" + launcher + "\" --minimized\r\n";
                File.WriteAllText(startupPath, content, new UTF8Encoding(false));
            }
            else
            {
                DeleteFileIfExists(startupPath);
            }
        }

        private static void WriteUninstallRegistry(string installRoot)
        {
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallRegistryPath))
            {
                key.SetValue("DisplayName", ProductName);
                key.SetValue("DisplayVersion", "0.9.1");
                key.SetValue("Publisher", "AIDP");
                key.SetValue("InstallLocation", installRoot);
                key.SetValue("DisplayIcon", Path.Combine(installRoot, LauncherExe));
                key.SetValue("UninstallString", "\"" + Path.Combine(installRoot, UninstallerExe) + "\" --uninstall");
                key.SetValue("QuietUninstallString", "\"" + Path.Combine(installRoot, UninstallerExe) + "\" --uninstall --quiet --remove-config");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
        }

        private static void RemoveShortcutsAndRegistry()
        {
            DeleteFileIfExists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), ProductName + ".lnk"));
            DeleteFileIfExists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), StartupCmd));
            DeleteDirectoryIfExists(InstallerPaths.StartMenuFolder);
            try { Registry.CurrentUser.DeleteSubKeyTree(UninstallRegistryPath, false); } catch { }
        }

        private static void PreserveConfigAndDeleteInstallRoot(string installRoot)
        {
            if (!IsSafeInstallRoot(installRoot)) return;
            string temp = Path.Combine(Path.GetTempPath(), "aidp-keep-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(temp);
            string config = Path.Combine(installRoot, "local-agent", "config");
            string logs = Path.Combine(installRoot, "local-agent", "logs");
            if (Directory.Exists(config)) CopyDirectory(config, Path.Combine(temp, "config"));
            if (Directory.Exists(logs)) CopyDirectory(logs, Path.Combine(temp, "logs"));
            DeleteDirectoryIfExists(installRoot);
            if (Directory.Exists(Path.Combine(temp, "config"))) CopyDirectory(Path.Combine(temp, "config"), Path.Combine(installRoot, "local-agent", "config"));
            if (Directory.Exists(Path.Combine(temp, "logs"))) CopyDirectory(Path.Combine(temp, "logs"), Path.Combine(installRoot, "local-agent", "logs"));
            DeleteDirectoryIfExists(temp);
        }

        private static bool IsSafeInstallRoot(string installRoot)
        {
            string full = Path.GetFullPath(installRoot).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string root = Path.GetPathRoot(full).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            if (string.Equals(full, root, StringComparison.OrdinalIgnoreCase)) return false;
            if (full.Length < 12) return false;
            return true;
        }

        private static void WriteInstallLog(string installRoot, string message)
        {
            try
            {
                Directory.CreateDirectory(installRoot);
                string line = DateTime.Now.ToString("s") + " " + message + "\r\n";
                File.AppendAllText(Path.Combine(installRoot, "install.log"), line, new UTF8Encoding(false));
            }
            catch
            {
            }
        }

        private static void ScheduleSelfCleanup(string installRoot)
        {
            try
            {
                if (!IsSafeInstallRoot(installRoot)) return;
                string scriptPath = Path.Combine(Path.GetTempPath(), "aidp-uninstall-cleanup-" + Guid.NewGuid().ToString("N") + ".cmd");
                string content =
                    "@echo off\r\n" +
                    "ping 127.0.0.1 -n 4 > nul\r\n" +
                    "rmdir /s /q \"" + installRoot + "\"\r\n" +
                    "del \"%~f0\"\r\n";
                File.WriteAllText(scriptPath, content, new UTF8Encoding(false));
                Process.Start(new ProcessStartInfo("cmd.exe", "/c \"" + scriptPath + "\"") { UseShellExecute = false, CreateNoWindow = true });
            }
            catch
            {
            }
        }

        private static void DeleteFileIfExists(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); } catch { }
        }

        private static void DeleteDirectoryIfExists(string path)
        {
            try { if (Directory.Exists(path)) Directory.Delete(path, true); } catch { }
        }
    }

    internal static class InstallerPaths
    {
        public static readonly string DefaultInstallRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AIDP", "local-agent");

        public static readonly string StartMenuFolder = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.Programs),
            "AIDP 本机助手");
    }

    internal static class EmbeddedPayload
    {
        private static readonly byte[] Marker = Encoding.ASCII.GetBytes("AIDP_SETUP_PAYLOAD_V1");

        public static void ExtractToFile(string setupExePath, string destinationZip)
        {
            byte[] all = File.ReadAllBytes(setupExePath);
            if (all.Length < Marker.Length + 8) throw new InvalidOperationException("安装器不包含内置套件。");

            long lengthOffset = all.Length - 8;
            long payloadLength = BitConverter.ToInt64(all, (int)lengthOffset);
            long markerOffset = lengthOffset - Marker.Length;
            if (markerOffset < 0 || payloadLength <= 0) throw new InvalidOperationException("内置套件长度无效。");

            for (int i = 0; i < Marker.Length; i++)
            {
                if (all[markerOffset + i] != Marker[i]) throw new InvalidOperationException("安装器内置套件标记无效。");
            }

            long payloadOffset = markerOffset - payloadLength;
            if (payloadOffset < 0) throw new InvalidOperationException("内置套件位置无效。");

            using (var output = File.Create(destinationZip))
            {
                output.Write(all, (int)payloadOffset, (int)payloadLength);
            }
        }
    }
}
