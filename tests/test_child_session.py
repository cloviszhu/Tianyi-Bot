"""Offline launcher/package checks. No session enable, connection or logoff."""
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from wechat_gallery_bot.management import child_session
from wechat_gallery_bot.management.common import ManagementError


class SessionLauncherTests(unittest.TestCase):
    def test_query_failure_keeps_readonly_polling_but_cannot_logoff(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        check = source.split("void Check()",1)[1].split("async void EndTrial()",1)[0]
        catch = check.split("catch(Exception ex)",1)[1]
        self.assertIn("cycle.QueryFailed=true", catch)
        self.assertNotIn("uncertain=true", catch)
        for forbidden in ["ConnectPrepared", "Native.Enable", "WTSLogoffSession"]:
            self.assertNotIn(forbidden, check)
        self.assertLess(check.index('Record("connected")'), check.index("cycle.QueryFailed=false"))
        self.assertIn("if(uncertain || cycle.QueryFailed)", source)

    def test_admin_build_has_manifest_and_distinct_cache_without_launch(self):
        normal = child_session.build_host()
        admin = child_session.build_host(require_admin=True)
        self.assertNotEqual(normal, admin)
        self.assertIn(b'requireAdministrator', admin.read_bytes())
        self.assertIn(b'uiAccess="false"', admin.read_bytes())

    def test_desktop_shortcut_targets_program_directly(self):
        script = (Path(__file__).resolve().parents[1] / "src/install_desktop_shortcuts.ps1").read_text(encoding="utf-8")
        self.assertIn("$first.TargetPath = $resolvedHost", script)
        self.assertIn("$first.Arguments = '--open-child'", script)
        for forbidden in ["powershell.exe", "-Command", "-Verb RunAs", "WindowStyle Hidden"]:
            self.assertNotIn(forbidden, script)

    def test_compiled_heartbeat_reader_rejects_stale_wrong_session_and_malformed(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([str(child_session.build_host()), "--heartbeat-selftest", folder], timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0)
            self.assertEqual((Path(folder) / "result.txt").read_text(), "heartbeat_reader_passed; simulated_reports_only")

    def test_heartbeat_does_not_operate_inputs_or_launch_programs(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        probe = source.split("class HeartbeatProbe", 1)[1].split("class TrialForm", 1)[0]
        for forbidden in ["SendInput", "SendKeys", "Clipboard", "Process.Start", "Native.Enable", "ConnectPrepared", "SetForegroundWindow"]:
            self.assertNotIn(forbidden, probe)
        self.assertIn("Native.Child()!=session", probe)
        self.assertIn("age>5", probe)
        self.assertIn("FileShare.None", probe)

    def test_compiled_state_two_cycles_without_native_session(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "cycle.txt"
            result = subprocess.run([str(child_session.build_host()), "--cycle-selftest", str(report)], timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(report.read_text(), "two_cycles_and_reentry_passed; simulated_state_only")

    def test_end_uses_nonblocking_logoff_and_transition_guard(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        end = source.split("async void EndTrial()", 1)[1].split("[STAThread]", 1)[0]
        self.assertIn("cycle.EnterTransition()", end)
        self.assertIn("timer.Stop()", end)
        self.assertIn("WTSLogoffSession(IntPtr.Zero,target,false)", end)
        self.assertIn("await Task.Delay(250)", end)
        self.assertIn("rdp=null", end)
        self.assertIn("cycle.Complete()", end)

    def test_check_reentry_and_start_reset_are_guarded(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        check = source.split("void Check()",1)[1].split("async void EndTrial()",1)[0]
        self.assertIn("!cycle.CanPoll", check)
        self.assertIn("checking", check)
        begin = source.split("void Begin()",1)[1].split("void Check()",1)[0]
        self.assertLess(begin.index("cycle.ResetForStart()"),begin.index("active=true"))

    def test_launch_opens_only_our_host_without_shell_or_elevation(self):
        with patch.object(child_session, "build_host", return_value=Path("C:/test/host.exe")), patch.object(child_session.subprocess, "Popen") as launch:
            child_session.launch_trial()
            self.assertEqual(launch.call_args.args[0], ["C:\\test\\host.exe"])
            self.assertNotIn("shell", launch.call_args.kwargs)

    def test_missing_compiler_does_not_download(self):
        with patch.object(child_session.Path, "is_file", return_value=False), patch.object(child_session.subprocess, "run") as run:
            with self.assertRaises(ManagementError):
                child_session.build_host()
            run.assert_not_called()

    def test_build_failure_is_not_launched(self):
        with patch.object(child_session, "build_host", side_effect=ManagementError("failed")), patch.object(child_session.subprocess, "Popen") as launch:
            with self.assertRaises(ManagementError):
                child_session.launch_trial()
            launch.assert_not_called()

    def test_packaged_source_is_present(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs")
        self.assertTrue(source.is_file())
        project = Path(__file__).resolve().parents[1] / "src/wechat_gallery_bot/management/ChildSessionHost.cs"
        self.assertEqual(source.read_bytes(), project.read_bytes())

    def test_host_static_safety_contract(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        for required in ['"Server","localhost"', '"RedirectClipboard",false', '"RedirectDrives",false', '"EnableAutoReconnect",false', 'FileShare.None', 'File.Replace', 'current!=owned', 'IsInRole(WindowsBuiltInRole.Administrator)']:
            self.assertIn(required, source)
        for forbidden in ['Process.Start(', 'SendInput(', 'SetForegroundWindow(', 'ClearTextPassword', 'Registry.SetValue', 'ServiceController.Stop']:
            self.assertNotIn(forbidden, source)

    def test_constructor_does_not_enable_or_connect(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        constructor = source.split("public TrialForm()", 1)[1].split("void Record", 1)[0]
        self.assertNotIn("Native.Enable(", constructor)
        self.assertNotIn("rdp.ConnectLocal(", constructor)

    def test_prepare_precedes_system_enable(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        begin = source.split("void Begin()", 1)[1].split("void Check()", 1)[0]
        self.assertLess(begin.index("rdp.PrepareLocal()"), begin.index("Native.Enable(true)"))
        self.assertLess(begin.index("Native.Enable(true)"), begin.index("rdp.ConnectPrepared()"))

    def test_preflight_cannot_connect_or_enable(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        probe = source.split('args[0]=="--preflight"', 1)[1].split("return;", 1)[0]
        self.assertIn("host.PrepareLocal()", probe)
        self.assertNotIn("Native.", probe)
        self.assertNotIn("ConnectPrepared", probe)
        self.assertNotIn("ConnectLocal", probe)

    def test_error_unwraps_and_records_code_not_raw_message(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        detail = source.split("public static string ErrorDetail", 1)[1].split("readonly Label", 1)[0]
        self.assertIn("ex.InnerException", detail)
        self.assertIn("ex.HResult", detail)
        self.assertNotIn("ex.Message", detail)

    def test_credssp_set_and_read_back(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        self.assertIn('Set(advanced,"EnableCredSspSupport",true)', source)
        self.assertIn('Get(advanced,"EnableCredSspSupport")', source)
        self.assertIn('get_Property("ConnectToChildSession")', source)

    def test_connect_rechecks_effective_configuration(self):
        source = Path(child_session.__file__).with_name("ChildSessionHost.cs").read_text(encoding="utf-8")
        connect = source.split("public void ConnectPrepared()",1)[1].split("public void PrepareLocal()",1)[0]
        self.assertLess(connect.index("ValidatePrepared()"), connect.index('Call("Connect")'))


if __name__ == "__main__":
    unittest.main()
