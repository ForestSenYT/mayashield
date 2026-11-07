using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace MayaShieldSetup
{
    class SetupForm : Form
    {
        const int CloseDelayMs = 1500;     // long enough to read "complete", short enough to feel automatic

        readonly Options o;
        readonly Reporter report;
        readonly Engine engine;
        readonly bool uninstallMode;
        List<MayaInstall> detected = new List<MayaInstall>();
        bool busy;

        readonly Label introLabel = new Label();
        readonly CheckedListBox mayaList = new CheckedListBox();
        readonly RadioButton machineRadio = new RadioButton();
        readonly RadioButton userRadio = new RadioButton();
        readonly ProgressBar bar = new ProgressBar();
        readonly Label statusLabel = new Label();
        readonly TextBox logBox = new TextBox();
        readonly Button installButton = new Button();
        readonly Button uninstallButton = new Button();
        readonly Button closeButton = new Button();
        readonly System.Windows.Forms.Timer closeTimer = new System.Windows.Forms.Timer();

        public int ExitCode { get; private set; }

        class Item
        {
            public MayaInstall Maya;
            public override string ToString()
            {
                string text = L.T("mayaItem", Maya.Version, Maya.Path);
                if (!Maya.Supported)
                    return text + L.T("notSupported");
                return Maya.OldCopy ? text + L.T("oldCopyNote") : text;
            }
        }

        public SetupForm(Options options, Reporter reporter)
        {
            o = options;
            report = reporter;
            engine = new Engine(o, report);
            uninstallMode = o.Uninstall;
            BuildLayout();

            report.OnLog = line => Post(() => AppendLog(line));
            report.OnStatus = line => Post(() => statusLabel.Text = line);
            report.OnProgress = (done, total) => Post(() => SetProgress(done, total));

            UpdateButtons();   // Install stays off until OnShown has found the Mayas
            closeTimer.Interval = CloseDelayMs;
            closeTimer.Tick += (sender, e) =>
            {
                closeTimer.Stop();
                Close();
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
            ClientSize = new Size(600, 500);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }

            var header = new Panel { Dock = DockStyle.Top, Height = 64, BackColor = SystemColors.Window };
            var title = new Label
            {
                Text = "MayaShield " + BuildInfo.Version,
                Font = new Font(Font.FontFamily, 13f, FontStyle.Bold),
                AutoSize = true,
                Location = new Point(16, 10),
            };
            var subtitle = new Label { Text = L.T("subtitle"), AutoSize = true, Location = new Point(18, 38), ForeColor = SystemColors.GrayText };
            header.Controls.Add(title);
            header.Controls.Add(subtitle);
            var rule = new Label { Dock = DockStyle.Top, Height = 1, BackColor = SystemColors.ControlDark };

            var body = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(16, 12, 16, 12), ColumnCount = 1 };
            body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));

            introLabel.Text = uninstallMode ? L.T("introUninstall") : L.T("intro");
            introLabel.AutoSize = true;
            introLabel.MaximumSize = new Size(560, 0);
            introLabel.Margin = new Padding(0, 0, 0, 8);
            body.Controls.Add(introLabel);

            mayaList.CheckOnClick = true;
            mayaList.IntegralHeight = false;
            mayaList.Dock = DockStyle.Fill;
            mayaList.Height = 110;
            mayaList.HorizontalScrollbar = true;
            mayaList.ItemCheck += (sender, e) =>
            {
                var item = mayaList.Items[e.Index] as Item;
                if (item != null && !item.Maya.Supported)
                    e.NewValue = CheckState.Unchecked;
            };
            mayaList.ItemCheck += (sender, e) => BeginInvoke((MethodInvoker)UpdateButtons);

            var scope = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, Dock = DockStyle.Fill, WrapContents = false, Margin = new Padding(0, 8, 0, 4) };
            machineRadio.Text = L.T("scopeMachine");
            machineRadio.AutoSize = true;
            machineRadio.Checked = o.Machine;
            userRadio.Text = L.T("scopeUser");
            userRadio.AutoSize = true;
            userRadio.Checked = !o.Machine;
            scope.Controls.Add(machineRadio);
            scope.Controls.Add(userRadio);

            if (!uninstallMode)
            {
                body.RowStyles.Add(new RowStyle(SizeType.AutoSize));
                body.RowStyles.Add(new RowStyle(SizeType.Absolute, 110f));
                body.RowStyles.Add(new RowStyle(SizeType.AutoSize));
                body.Controls.Add(mayaList);
                body.Controls.Add(scope);
            }
            else
            {
                body.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            }

            bar.Style = ProgressBarStyle.Continuous;
            bar.Dock = DockStyle.Fill;
            bar.Height = 18;
            bar.Margin = new Padding(0, 8, 0, 4);
            bar.Minimum = 0;
            bar.Maximum = 100;
            statusLabel.Text = L.T("ready");
            statusLabel.AutoEllipsis = true;
            statusLabel.Dock = DockStyle.Fill;
            statusLabel.Height = 20;
            logBox.Multiline = true;
            logBox.ReadOnly = true;
            logBox.ScrollBars = ScrollBars.Vertical;
            logBox.Dock = DockStyle.Fill;
            logBox.BackColor = SystemColors.Window;
            logBox.Margin = new Padding(0, 4, 0, 0);

            body.RowStyles.Add(new RowStyle(SizeType.Absolute, 30f));
            body.RowStyles.Add(new RowStyle(SizeType.Absolute, 24f));
            body.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
            body.Controls.Add(bar);
            body.Controls.Add(statusLabel);
            body.Controls.Add(logBox);

            var buttons = new TableLayoutPanel { Dock = DockStyle.Bottom, Height = 48, ColumnCount = 4, Padding = new Padding(12, 8, 12, 8), BackColor = SystemColors.Control };
            buttons.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            buttons.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            buttons.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            buttons.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            foreach (Button button in new[] { uninstallButton, installButton, closeButton })
            {
                button.AutoSize = true;
                button.MinimumSize = new Size(96, 28);
                button.UseVisualStyleBackColor = true;
            }
            installButton.Text = uninstallMode ? L.T("uninstall") : L.T("install");
            uninstallButton.Text = L.T("uninstall");
            closeButton.Text = L.T("close");
            installButton.Click += (sender, e) => { if (uninstallMode) StartUninstall(); else StartInstall(); };
            uninstallButton.Click += (sender, e) => StartUninstall();
            closeButton.Click += (sender, e) => Close();
            buttons.Controls.Add(uninstallButton, 0, 0);
            buttons.Controls.Add(new Label(), 1, 0);
            buttons.Controls.Add(installButton, 2, 0);
            buttons.Controls.Add(closeButton, 3, 0);
            uninstallButton.Visible = !uninstallMode;

            Controls.Add(body);
            Controls.Add(buttons);
            Controls.Add(rule);
            Controls.Add(header);
            AcceptButton = installButton;
            CancelButton = closeButton;

            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            ResumeLayout(false);
            PerformLayout();
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            try
            {
                detected = Maya.Detect(o);
            }
            catch (Exception ex)
            {
                AppendLog(L.T("failed", ex.Message));
            }
            foreach (MayaInstall maya in detected)
            {
                bool wanted = maya.Supported && (o.Versions == null || o.Versions.Contains(maya.Version));
                mayaList.Items.Add(new Item { Maya = maya }, wanted);
                AppendLog(L.T("found", maya.Version, maya.Path));
            }
            if (!uninstallMode && !detected.Any(m => m.Supported))
            {
                introLabel.Text = L.T("noMaya");
                statusLabel.Text = L.T("noMaya");
                ExitCode = Exit.NoMaya;
            }
            UpdateButtons();
        }

        void UpdateButtons()
        {
            installButton.Enabled = !busy && (uninstallMode || mayaList.CheckedItems.Count > 0);
            uninstallButton.Enabled = !busy;
            closeButton.Enabled = !busy;
            mayaList.Enabled = !busy;
            machineRadio.Enabled = userRadio.Enabled = !busy;
        }

        // ================================================================ actions
        void StartInstall()
        {
            List<MayaInstall> targets = mayaList.CheckedItems.Cast<Item>().Select(item => item.Maya).Where(m => m.Supported).ToList();
            if (targets.Count == 0)
            {
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
            List<MayaInstall> all = detected;
            Run(true, () => engine.Uninstall(all));
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
            UpdateButtons();
            SetBarState(BarNormal);
            SetProgress(0, 1);
            statusLabel.Text = L.T(uninstall ? "uninstalling" : "installing");
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
                Post(() => Finished(uninstall, code));
            });
            thread.IsBackground = true;
            thread.Start();
        }

        void Finished(bool uninstall, int code)
        {
            busy = false;
            ExitCode = code;
            if (code == Exit.Ok)
            {
                // Fill the bar at once (no lagging animation), say so, then leave on our own.
                SetProgress(1, 1);
                statusLabel.Text = L.T(uninstall ? "uninstallDone" : "installDone");
                if (!uninstall)
                    AppendLog(L.T("nextStart"));
                installButton.Enabled = uninstallButton.Enabled = false;
                closeButton.Enabled = true;
                closeTimer.Start();
                return;
            }

            SetBarState(BarError);
            string message;
            switch (code)
            {
                case Exit.MayaRunning: message = L.T("mayaRunningLog"); break;
                case Exit.UacDenied: message = L.T("uacDenied"); break;
                case Exit.SelfTestFailed: message = L.T("selfTestFailedShort"); break;
                case Exit.OldNeedsAdmin: message = L.T("failedSeeLog"); break;
                default: message = L.T(uninstall ? "uninstallFailed" : "failedSeeLog"); break;
            }
            statusLabel.Text = message;
            UpdateButtons();
            MessageBox.Show(this, message, Text, MessageBoxButtons.OK,
                            code == Exit.UacDenied ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (busy)
            {
                e.Cancel = true;   // never leave a half-finished install behind
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

        [DllImport("user32.dll", CharSet = CharSet.Auto)]
        static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

        void SetBarState(int state)
        {
            if (bar.IsHandleCreated)
                SendMessage(bar.Handle, PBM_SETSTATE, (IntPtr)state, IntPtr.Zero);
        }
    }
}
