"""Handler semantics: fragment replacement, rejection and syntax safety."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.local import LocalEnvironment
from tools.handlers import HandlerContext, dispatch
from tools.specs import TOOL_SPECS
from tools.toolsets import tool_names


CONFIG = "def resolve_timeout(config_value=None, cli_value=None):\n    return 5\n"


class ReplaceTextHandlerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-handlers-")
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "config.py").write_bytes(CONFIG.encode("utf-8"))
        self.env = LocalEnvironment(self.workspace, scratch=root / "scratch")
        specs = {name: TOOL_SPECS[name] for name in tool_names("toy-v2")}
        self.ctx = HandlerContext(specs=specs, allowed=set(specs),
                                  outputs=root / "outputs",
                                  python=sys.executable, test_timeout=90)
        self.ctx.outputs.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_unique_fragment_is_replaced(self):
        observation = dispatch("replace_text", self.env, self.ctx,
                               {"path": "config.py", "old": "    return 5\n",
                                "new": "    return 6\n"})
        self.assertTrue(observation["ok"], observation)
        self.assertIn("return 6", (self.workspace / "config.py").read_text(encoding="utf-8"))

    def test_missing_fragment_is_rejected(self):
        observation = dispatch("replace_text", self.env, self.ctx,
                               {"path": "config.py", "old": "not present",
                                "new": "anything"})
        self.assertFalse(observation["ok"])
        self.assertIn("exactly one", observation["error"])

    def test_syntax_error_is_rejected_without_writing(self):
        before = (self.workspace / "config.py").read_bytes()
        observation = dispatch("replace_text", self.env, self.ctx,
                               {"path": "config.py", "old": "    return 5\n",
                                "new": "    return (\n"})
        self.assertFalse(observation["ok"])
        self.assertIn("SyntaxError", observation["error"])
        self.assertEqual((self.workspace / "config.py").read_bytes(), before)
