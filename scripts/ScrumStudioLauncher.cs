using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class ScrumStudioLauncher
{
    [STAThread]
    private static void Main()
    {
        const string title = "Scrum Studio Desktop";
        try
        {
            var output = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            string root = output.Parent.Parent.FullName;
            string python = Path.Combine(root, @"data\desktop-venv\Scripts\pythonw.exe");
            string app = Path.Combine(root, "desktop.py");
            if (!File.Exists(python) || !File.Exists(app))
                throw new InvalidOperationException("Build the app from your Scrum Studio checkout and launch it from data\\desktop. See the Desktop setup in README.md.");

            using (Process.Start(new ProcessStartInfo(python, "\"" + app + "\"")
            {
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            })) { }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, title, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
