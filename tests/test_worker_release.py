import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from wechat_gallery_bot.management.common import ManagementError
from wechat_gallery_bot.management.worker_release import WorkerReleases


class WorkerReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.releases = WorkerReleases(self.root / "releases")

    def wheel(self, marker="one", extra=None):
        target = self.root / (marker + ".whl")
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("wechat_gallery_bot/__init__.py", "")
            archive.writestr("wechat_gallery_bot/management/__init__.py", "")
            archive.writestr("wechat_gallery_bot/management/child_intake.py",
                             "import json,sys;print(json.dumps({'marker':%r,'args':sys.argv}))" % marker)
            for name, content in (extra or {}).items():
                archive.writestr(name, content)
        return target

    def test_stage_activate_rollback_preserves_both_packages(self):
        first = self.releases.stage(self.wheel())
        self.assertIsNone(self.releases.state()["current"])
        self.releases.activate(first)
        second = self.releases.stage(self.wheel("two"))
        self.releases.activate(second)
        self.assertEqual(self.releases.state(), {"current": second, "previous": first})
        self.releases.rollback()
        self.assertEqual(self.releases.state()["current"], first)
        self.assertTrue(self.releases.verify(second).is_file())

    def test_selected_zip_really_launches_in_isolated_subprocess(self):
        digest = self.releases.stage(self.wheel("selected"))
        self.releases.activate(digest)
        command, selected = self.releases.command(sys.executable)
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"marker": "selected", "args": ["worker", "--worker"]})
        self.assertEqual(selected, digest)

    def test_corrupt_active_package_never_silently_falls_back(self):
        digest = self.releases.stage(self.wheel())
        self.releases.activate(digest)
        self.releases.path(digest).write_bytes(b"corrupt")
        with self.assertRaises(ManagementError):
            self.releases.command(sys.executable)

    def test_bad_pointer_does_not_execute_external_path(self):
        self.releases.root.mkdir()
        self.releases.pointer.write_text('{"current":"../evil","previous":null}')
        with self.assertRaises(ManagementError):
            self.releases.command(sys.executable)

    def test_invalid_archives_and_external_entries_rejected(self):
        for name in ("../evil.py", "/evil.py", "sitecustomize.py", "wechat_gallery_bot/../evil.py", "C:/evil.py"):
            with self.subTest(name=name), self.assertRaises(ManagementError):
                self.releases.stage(self.wheel(extra={name: ""}))
        with self.assertRaises(ManagementError):
            self.releases.check_blob(b"not a wheel")

    def test_no_pointer_uses_installed_worker_and_first_rollback_restores_it(self):
        self.assertEqual(self.releases.command(sys.executable)[1], "installed")
        digest = self.releases.stage(self.wheel())
        self.releases.activate(digest)
        self.releases.rollback()
        self.assertEqual(self.releases.command(sys.executable)[1], "installed")
