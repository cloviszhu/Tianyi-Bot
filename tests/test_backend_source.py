"""Optional installed-source inspection, never imports wxauto4 or starts UI."""
import ast
import importlib.metadata
import unittest
from pathlib import Path


class InstalledSourceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.distribution = importlib.metadata.distribution("wxauto4")
        except importlib.metadata.PackageNotFoundError:
            self.skipTest("Optional free WeChat backend is not installed")

    def tree(self, path):
        return ast.parse(Path(self.distribution.locate_file(path)).read_text(encoding="utf-8-sig"))

    def method(self, path, cls, method):
        tree = self.tree(path)
        owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
        return next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == method)

    def test_installed_listen_send_and_context_signatures(self):
        cases = [
            ("WeChat", "AddListenChat", ["self", "nickname", "callback"]),
            ("WeChat", "StopListening", ["self", "remove"]),
            ("Chat", "ChatInfo", ["self"]),
            ("Chat", "SendMsg", ["self", "msg", "who", "clear", "at", "exact"]),
            ("Chat", "SendFiles", ["self", "filepath", "who", "exact"]),
        ]
        for cls, method, args in cases:
            self.assertEqual([a.arg for a in self.method("wxauto4/wx.py", cls, method).args.args], args)

    def test_installed_preview_contract(self):
        method = self.method("wxauto4/ui/component.py", "WeChatImage", "save")
        self.assertEqual([arg.arg for arg in method.args.args], ["self", "dir_path", "timeout"])
        self.method("wxauto4/ui/component.py", "Menu", "select")
        self.method("wxauto4/msgs/base.py", "HumanMessage", "click")
        self.method("wxauto4/msgs/base.py", "BaseMessage", "roll_into_view")
        tree = self.tree("wxauto4/msgs/mtype.py")
        image = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ImageMessage")
        self.assertNotIn("download", [n.name for n in image.body if isinstance(n, ast.FunctionDef)])

    def test_installed_code_matches_project_sources(self):
        # In a fresh installation this catches accidentally testing stale builds.
        import wechat_gallery_bot
        installed = Path(wechat_gallery_bot.__file__).parent
        source = Path(__file__).resolve().parents[1] / "src" / "wechat_gallery_bot"
        for path in source.rglob("*.py"):
            self.assertEqual(path.read_bytes(), (installed / path.relative_to(source)).read_bytes(), str(path.relative_to(source)))
