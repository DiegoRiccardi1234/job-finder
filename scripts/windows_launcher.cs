using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

// A small, windowless entry point. The actual app and its helpers stay in app/.
static class Launcher {
    [STAThread]
    static int Main() {
        string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string program = Path.Combine(root, "app", "JobFinder.exe");
        string marker = Path.Combine(root, "app", ".jobfinder-layout.json");
        if (!File.Exists(program) || !File.Exists(marker)) {
            MessageBox.Show("Installazione incompleta. Conserva JobFinder.exe insieme alla cartella app.",
                "Job Finder", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        try {
            var start = new ProcessStartInfo(program);
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            using (Process child = Process.Start(start)) {
                // Preserve lifetime/exit status for diagnostics and automation.
                // The updater waits for the child; its existing copy retries
                // also cover this launcher's final, short-lived image lock.
                child.WaitForExit();
                return child.ExitCode;
            }
        } catch (Exception error) {
            MessageBox.Show("Non riesco ad avviare Job Finder:\n" + error.Message,
                "Job Finder", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
