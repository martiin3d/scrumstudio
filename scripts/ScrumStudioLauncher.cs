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
            string root = Path.Combine(output.FullName, "app");
            string python = Path.Combine(output.FullName, @"runtime\pythonw.exe");
            if (!File.Exists(python) && !Directory.Exists(root) && output.Parent != null && output.Parent.Parent != null)
            {
                root = output.Parent.Parent.FullName;
                python = Path.Combine(root, @"data\desktop-venv\Scripts\pythonw.exe");
            }
            string app = Path.Combine(root, "desktop.py");
            if (!File.Exists(python) || !File.Exists(app))
                throw new InvalidOperationException("Extract the whole Scrum Studio ZIP before opening the executable. For a source checkout, see Desktop setup in README.md.");

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
