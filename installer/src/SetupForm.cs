using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace MayaShieldSetup
{
    /// <summary>
    /// One sentence saying what will happen, one button to do it.  Which Mayas, for whom, and
    /// the log are a click away for those who want them, and out of the way for everyone else.
    /// </summary>
    class SetupForm : Form
    {
        const int ContentWidth = 500;
        const int CloseAfterSeconds = 3;

        readonly Options o;
        readonly Reporter report;
        readonly Engine engine;
        readonly bool uninstallMode;
        List<MayaInstall> detected = new List<MayaInstall>();
        bool busy, customShown, detailsShown;
        int countdown;
        string doneText = "";

        readonly Label headline = new Label();
        readonly Label note = new Label();
        readonly Button customLink = new Button();
        readonly TableLayoutPanel customPanel = new TableLayoutPanel();
        readonly FlowLayoutPanel versionList = new FlowLayoutPanel();
        readonly RadioButton machineRadio = new RadioButton();
        readonly RadioButton userRadio = new RadioButton();
        readonly ProgressBar bar = new ProgressBar();
        readonly Label status = new Label();
        readonly Button detailsLink = new Button();
        readonly TextBox logBox = new TextBox();
        readonly Button installButton = new Button();
        readonly Button uninstallButton = new Button();
        readonly Button logButton = new Button();
        readonly Button closeButton = new Button();
        readonly System.Windows.Forms.Timer closeTimer = new System.Windows.Forms.Timer();

        public int ExitCode { get; private set; }

        public SetupForm(Options options, Reporter reporter)
        {
            o = options;
            report = reporter;
            engine = new Engine(o, report);
            uninstallMode = o.Uninstall;
            BuildLayout();

            report.OnLog = line => Post(() => AppendLog(line));
            report.OnStatus = line => Post(() => { if (busy) status.Text = line; });
            report.OnProgress = (done, total) => Post(() => SetProgress(done, total));

            closeTimer.Interval = 1000;
            closeTimer.Tick += (sender, e) =>
            {
                countdown--;
                if (countdown <= 0)
                {
                    closeTimer.Stop();
                    Close();
                    return;
                }
                status.Text = doneText + "  " + L.T("closingIn", countdown);
            };
        }

        // ================================================================ layout
        void BuildLayout()
        {
            SuspendLayout();
            Font = SystemFonts.MessageBoxFont;
            Text = L.T("title", BuildInfo.Version);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = true;
            StartPosition = FormStartPosition.CenterScreen;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
            try { Icon = Icon.ExtractAssociatedIcon(Engine.ExePath); } catch (Exception) { }

            // One column of fixed width; the window is exactly as tall as what is showing in it.
            int width = ContentWidth + 40;
            var root = new TableLayoutPanel
            {
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                ColumnCount = 1,
                Location = new Point(0, 0),
                Margin = Padding.Empty,
                Padding = Padding.Empty,
            };
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, width));

            var header = new Panel { Size = new Size(width, 64), Margin = Padding.Empty, BackColor = SystemColors.Window };
            header.Controls.Add(new Label
            {
                Text = "MayaShield " + BuildInfo.Version,
                Font = new Font(Font.FontFamily, 13f, FontStyle.Bold),
                AutoSize = true,
                Location = new Point(20, 10),
            });
            header.Controls.Add(new Label { Text = L.T("subtitle"), AutoSize = true, Location = new Point(22, 38), ForeColor = SystemColors.GrayText });
            var rule = new Label { Size = new Size(width, 1), Margin = Padding.Empty, BackColor = SystemColors.ControlLight };

            var body = new TableLayoutPanel
            {
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                Margin = Padding.Empty,
                Padding = new Padding(20, 16, 20, 8),
                ColumnCount = 1,
            };
            body.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, ContentWidth));

            headline.AutoSize = true;
            headline.MaximumSize = new Size(ContentWidth, 0);
            headline.Font = new Font(Font.FontFamily, Font.Size + 1.5f);
            headline.Margin = new Padding(0, 0, 0, 6);
            body.Controls.Add(headline);

            note.Text = L.T("closeMaya");
            note.AutoSize = true;
            note.MaximumSize = new Size(ContentWidth, 0);
            note.ForeColor = SystemColors.GrayText;
            note.Margin = new Padding(0, 0, 0, 6);
            body.Controls.Add(note);

            customLink.Text = L.T("customize");
            LooksLikeALink(customLink);
            customLink.Click += (sender, e) => ShowCustom(!customShown);
            customLink.Visible = !uninstallMode;
            body.Controls.Add(customLink);

            customPanel.AutoSize = true;
            customPanel.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            customPanel.ColumnCount = 1;
            customPanel.Margin = new Padding(12, 2, 0, 6);
            customPanel.Visible = false;
            customPanel.Controls.Add(new Label { Text = L.T("versions"), AutoSize = true, Margin = new Padding(0, 0, 0, 2) });
            versionList.FlowDirection = FlowDirection.TopDown;
            versionList.WrapContents = false;
            versionList.AutoSize = true;
            versionList.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            versionList.Margin = new Padding(0, 0, 0, 6);
            customPanel.Controls.Add(versionList);
            machineRadio.Text = L.T("scopeMachine");
            machineRadio.AutoSize = true;
            machineRadio.Checked = o.Machine;
            userRadio.Text = L.T("scopeUser");
            userRadio.AutoSize = true;
            userRadio.Checked = !o.Machine;
            customPanel.Controls.Add(machineRadio);
            customPanel.Controls.Add(userRadio);
            body.Controls.Add(customPanel);

            bar.Style = ProgressBarStyle.Continuous;
            bar.Size = new Size(ContentWidth, 14);
            bar.Margin = new Padding(0, 8, 0, 6);
            bar.Visible = false;
            body.Controls.Add(bar);

            status.AutoSize = true;
            status.MaximumSize = new Size(ContentWidth, 0);
            status.Margin = new Padding(0, 0, 0, 4);
            status.Visible = false;
            body.Controls.Add(status);

            detailsLink.Text = L.T("details");
            LooksLikeALink(detailsLink);
            detailsLink.Click += (sender, e) => ShowDetails(!detailsShown);
            detailsLink.Visible = false;
            body.Controls.Add(detailsLink);

            logBox.Multiline = true;
            logBox.ReadOnly = true;
            logBox.ScrollBars = ScrollBars.Vertical;
            logBox.BackColor = SystemColors.Window;
            logBox.Size = new Size(ContentWidth, 150);
            logBox.Margin = new Padding(0, 0, 0, 4);
            logBox.Visible = false;
            body.Controls.Add(logBox);

            var buttons = new TableLayoutPanel
            {
                Size = new Size(width, 58),
                Margin = Padding.Empty,
                ColumnCount = 5,
                RowCount = 1,
                Padding = new Padding(16, 8, 16, 12),
            };
            buttons.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            buttons.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            buttons.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            buttons.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            buttons.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            foreach (Button button in new[] { uninstallButton, logButton, installButton, closeButton })
            {
                button.AutoSize = true;
                button.AutoSizeMode = AutoSizeMode.GrowAndShrink;
                button.Padding = new Padding(10, 3, 10, 3);
                button.MinimumSize = new Size(90, 30);
                button.UseVisualStyleBackColor = true;
            }
            installButton.Text = uninstallMode ? L.T("uninstall") : L.T("install");
            installButton.Font = new Font(Font, FontStyle.Bold);
            uninstallButton.Text = L.T("uninstall");
            logButton.Text = L.T("openLog");
            logButton.Visible = false;
            closeButton.Text = L.T("close");
            installButton.Click += (sender, e) => { if (uninstallMode) StartUninstall(); else StartInstall(); };
            uninstallButton.Click += (sender, e) => StartUninstall();
            logButton.Click += (sender, e) => OpenLog();
            closeButton.Click += (sender, e) => Close();
            buttons.Controls.Add(uninstallButton, 0, 0);
            buttons.Controls.Add(new Label { AutoSize = false, Width = 1 }, 1, 0);
            buttons.Controls.Add(logButton, 2, 0);
            buttons.Controls.Add(installButton, 3, 0);
            buttons.Controls.Add(closeButton, 4, 0);
            uninstallButton.Visible = false;   // shown once we know something is installed

            root.Controls.Add(header);
            root.Controls.Add(rule);
            root.Controls.Add(body);
            root.Controls.Add(buttons);
            Controls.Add(root);
            AcceptButton = installButton;
            CancelButton = closeButton;

            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            ResumeLayout(false);
            PerformLayout();
            UpdateButtons();
        }

        /// <summary>A real button, for the keyboard and for screen readers, drawn as a link.</summary>
        void LooksLikeALink(Button button)
        {
            button.AutoSize = true;
            button.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderSize = 0;
            button.FlatAppearance.MouseOverBackColor = SystemColors.Control;
            button.FlatAppearance.MouseDownBackColor = SystemColors.Control;
            button.BackColor = SystemColors.Control;
            button.ForeColor = SystemColors.HotTrack;
            button.Font = new Font(Font, FontStyle.Underline);
            button.Cursor = Cursors.Hand;
            button.Padding = Padding.Empty;
            button.Margin = new Padding(-4, 0, 0, 2);
            button.TextAlign = ContentAlignment.MiddleLeft;
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            try
            {
                detected = uninstallMode ? Maya.WithModule(o) : Maya.Detect(o);
            }
            catch (Exception ex)
            {
                AppendLog(L.T("failed", ex.Message));
            }
            foreach (MayaInstall maya in detected)
            {
                AppendLog(L.T("found", maya.Version, maya.Path));
                if (uninstallMode)
                    continue;
                string text = L.T("mayaItem", maya.Version);
                if (!maya.Supported)
                    text += L.T("notSupported");
                else if (maya.Installed != null)
                    text += L.T("hasVersion", maya.Installed);
                var box = new CheckBox
                {
                    Text = text,
                    AutoSize = true,
                    Tag = maya,
                    Enabled = maya.Supported,
                    Checked = maya.Supported && (o.Versions == null || o.Versions.Contains(maya.Version)),
                    Margin = new Padding(0, 1, 0, 1),
                };
                new ToolTip().SetToolTip(box, maya.Path);
                box.CheckedChanged += (sender, args) => { Describe(); UpdateButtons(); };
                versionList.Controls.Add(box);
            }
            Describe();
            UpdateButtons();
        }

        List<MayaInstall> Chosen()
        {
            return versionList.Controls.OfType<CheckBox>().Where(box => box.Checked)
                .Select(box => (MayaInstall)box.Tag).Where(maya => maya.Supported).ToList();
        }

        /// <summary>Says, in one sentence, what clicking the button will do.</summary>
        void Describe()
        {
            if (uninstallMode)
            {
                headline.Text = L.T("headUninstall");
                return;
            }
            List<MayaInstall> supported = detected.Where(m => m.Supported).ToList();
            if (supported.Count == 0)
            {
                headline.Text = L.T("headNoMaya");
                note.Visible = false;
                customLink.Visible = detected.Count > 0;
                ExitCode = Exit.NoMaya;
                return;
            }
            List<MayaInstall> chosen = Chosen();
            List<MayaInstall> shown = chosen.Count > 0 ? chosen : supported;
            bool current = shown.All(m => m.Installed == BuildInfo.Version);
            bool older = shown.Any(m => m.Installed != null && m.Installed != BuildInfo.Version);
            if (current)
                headline.Text = L.T("headInstalled", BuildInfo.Version);
            else if (older)
                headline.Text = L.T("headUpgrade", BuildInfo.Version);
            else
                headline.Text = L.T("headInstall", L.List(shown.Select(m => L.T("mayaItem", m.Version))));
            installButton.Text = current ? L.T("reinstall") : L.T("install");
            uninstallButton.Visible = detected.Any(m => m.Installed != null) || engine.UserCopyExists(detected);
        }

        void ShowCustom(bool shown)
        {
            customShown = shown;
            customPanel.Visible = shown;
            customLink.Text = shown ? L.T("customizeHide") : L.T("customize");
        }

        void ShowDetails(bool shown)
        {
            detailsShown = shown;
            logBox.Visible = shown;
            detailsLink.Text = shown ? L.T("detailsHide") : L.T("details");
            if (shown)
            {
                logBox.SelectionStart = logBox.TextLength;
                logBox.ScrollToCaret();
            }
        }

        void UpdateButtons()
        {
            installButton.Enabled = !busy && (uninstallMode || Chosen().Count > 0);
            uninstallButton.Enabled = !busy;
            closeButton.Enabled = !busy;
            customPanel.Enabled = !busy;
            customLink.Enabled = !busy;
        }

        // ================================================================ actions
        void StartInstall()
        {
            List<MayaInstall> targets = Chosen();
            if (targets.Count == 0)
            {
                ShowCustom(true);
                MessageBox.Show(this, L.T("selectOne"), Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            o.Scope = machineRadio.Checked ? "machine" : "user";
            if (!o.Machine)
            {
                MayaInstall blocked = targets.FirstOrDefault(m => m.OldCopy);
                if (blocked != null)
                {
                    MessageBox.Show(this, L.T("oldNeedsAdmin", blocked.Version), Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
            }
            if (!MayaClosed())
                return;
            Run(false, () => engine.Install(targets));
        }

        void StartUninstall()
        {
            if (MessageBox.Show(this, L.T("confirmUninstall"), Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question,
                                MessageBoxDefaultButton.Button2) != DialogResult.Yes)
                return;
            if (!MayaClosed())
                return;
            Run(true, () => engine.Uninstall(Maya.WithModule(o)));
        }

        bool MayaClosed()
        {
            while (!o.Force && Maya.IsRunning())
            {
                if (MessageBox.Show(this, L.T("mayaRunning"), Text, MessageBoxButtons.RetryCancel, MessageBoxIcon.Warning) != DialogResult.Retry)
                    return false;
            }
            return true;
        }

        void Run(bool uninstall, Func<int> work)
        {
            busy = true;
            closeTimer.Stop();
            UpdateButtons();
            ShowCustom(false);
            note.Visible = false;
            bar.Visible = true;
            status.Visible = true;
            status.ForeColor = SystemColors.ControlText;
            status.Font = Font;
            detailsLink.Visible = true;
            logButton.Visible = false;
            SetBarState(BarNormal);
            SetProgress(0, 1);
            status.Text = L.T(uninstall ? "uninstalling" : "installing");
            int warningsBefore = report.Warnings;
            var thread = new Thread(() =>
            {
                int code;
                try
                {
                    code = work();
                }
                catch (Exception ex)
                {
                    report.Log(L.T("failed", ex.Message));
                    code = Exit.Error;
                }
                Post(() => Finished(uninstall, code, report.Warnings > warningsBefore));
            });
            thread.IsBackground = true;
            thread.Start();
        }

        void Finished(bool uninstall, int code, bool warned)
        {
            busy = false;
            ExitCode = code;
            // What is on disk now decides what the window offers next.
            foreach (MayaInstall maya in detected)
                maya.Installed = Maya.InstalledVersion(System.IO.Path.Combine(maya.Path, "modules"));
            foreach (CheckBox box in versionList.Controls.OfType<CheckBox>())
            {
                var maya = (MayaInstall)box.Tag;
                if (maya.Supported)
                    box.Text = L.T("mayaItem", maya.Version) + (maya.Installed != null ? L.T("hasVersion", maya.Installed) : "");
            }
            if (report.LogPath != null)
                AppendLog(L.T("logSaved", report.LogPath));
            status.Font = new Font(Font, FontStyle.Bold);

            if (code == Exit.Ok && !warned)
            {
                // Fill the bar at once (no lagging animation), say so, then leave on our own.
                SetProgress(1, 1);
                doneText = "✔ " + L.T(uninstall ? "uninstallDone" : "installDone");
                status.ForeColor = Color.FromArgb(0, 128, 60);
                installButton.Enabled = uninstallButton.Enabled = false;
                closeButton.Enabled = true;
                countdown = CloseAfterSeconds;
                status.Text = doneText + "  " + L.T("closingIn", countdown);
                if (!uninstall)
                    AppendLog(L.T("nextStart"));
                closeTimer.Start();
                return;
            }

            // Anything short of a clean finish stays on screen, with the log one click away.
            logButton.Visible = report.LogPath != null;
            ShowDetails(true);
            if (code == Exit.Ok)
            {
                SetProgress(1, 1);
                SetBarState(BarPaused);
                status.ForeColor = Color.FromArgb(160, 100, 0);
                status.Text = "⚠ " + L.T(uninstall ? "uninstallDoneWarn" : "installDoneWarn");
                installButton.Enabled = uninstallButton.Enabled = false;
                closeButton.Enabled = true;
                return;
            }

            string message;
            switch (code)
            {
                case Exit.MayaRunning: message = L.T("mayaRunningLog"); break;
                case Exit.UacDenied: message = L.T("uacDenied"); break;
                case Exit.SelfTestFailed: message = L.T("selfTestFailedShort"); break;
                case Exit.OldNeedsAdmin: message = L.T("oldNeedsAdminShort"); break;
                default: message = L.T(uninstall ? "uninstallFailed" : "failedSeeLog"); break;
            }
            bool soft = code == Exit.UacDenied || code == Exit.SelfTestFailed;
            SetBarState(soft ? BarPaused : BarError);
            status.ForeColor = soft ? Color.FromArgb(160, 100, 0) : Color.FromArgb(190, 30, 30);
            status.Text = "⚠ " + message;
            if (code == Exit.UacDenied)
                ShowDetails(false);
            Describe();
            UpdateButtons();
        }

        void OpenLog()
        {
            try
            {
                Process.Start(new ProcessStartInfo(report.LogPath) { UseShellExecute = true });
            }
            catch (Exception) { }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            // Never leave a half-finished install behind, unless Windows itself is shutting down.
            if (busy && e.CloseReason != CloseReason.WindowsShutDown)
            {
                e.Cancel = true;
                return;
            }
            closeTimer.Stop();
            base.OnFormClosing(e);
        }

        // ================================================================ helpers
        void Post(Action action)
        {
            if (IsDisposed || !IsHandleCreated)
                return;
            try
            {
                BeginInvoke(action);
            }
            catch (InvalidOperationException) { }
        }

        void AppendLog(string line)
        {
            logBox.AppendText((logBox.TextLength > 0 ? Environment.NewLine : "") + line);
        }

        /// <summary>
        /// Windows animates a progress bar towards a higher value, so a bar set to 100% just
        /// before the window goes away looks stuck part way.  Moving it backwards draws at
        /// once, so step one past the value and come back.
        /// </summary>
        void SetProgress(int done, int total)
        {
            const int scale = 1000;
            int value = total <= 0 ? 0 : (int)((long)Math.Min(done, total) * scale / total);
            bar.Maximum = scale + 1;
            bar.Value = value + 1;
            bar.Value = value;
            bar.Maximum = scale;
            bar.Update();
        }

        const int PBM_SETSTATE = 0x0410;
        const int BarNormal = 1;
        const int BarError = 2;
        const int BarPaused = 3;

        [DllImport("user32.dll", CharSet = CharSet.Auto)]
        static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

        void SetBarState(int state)
        {
            if (bar.IsHandleCreated)
                SendMessage(bar.Handle, PBM_SETSTATE, (IntPtr)state, IntPtr.Zero);
        }
    }
}
