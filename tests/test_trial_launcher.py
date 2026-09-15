import hashlib
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TrialLauncherTests(unittest.TestCase):
    def test_verified_adjacent_package_only(self):
        source = Path(__file__).resolve().parents[1] / "src" / "launch_child.pyw"
        code = compile(source.read_text(encoding="utf-8"), str(source), "exec")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "wechat_gallery_bot-0.7.0-py3-none-any.whl"
            wheel.write_bytes(b"test package")
            manifest = {"wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
            (root / "release.json").write_text(json.dumps(manifest))
            namespace = {"__name__": "test_launcher", "__file__": str(root / "launch_child.pyw")}
            exec(code, namespace)
            with patch("sys.path", []), patch.dict(os.environ), patch.object(runpy, "run_module") as launch:
                namespace["main"]()
                self.assertEqual(os.environ["PYTHONPATH"], str(wheel))
                launch.assert_called_once_with("wechat_gallery_bot.management.child_binding", run_name="__main__")
            wheel.write_bytes(b"changed")
            with patch.object(runpy, "run_module") as launch, self.assertRaises(ValueError):
                namespace["main"]()
            launch.assert_not_called()
