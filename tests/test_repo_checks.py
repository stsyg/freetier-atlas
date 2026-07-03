import tempfile
import unittest
from pathlib import Path

from tools import repo_checks


class RepoCheckTests(unittest.TestCase):
    def test_secret_patterns_detect_controlled_fixture(self) -> None:
        line = "token=" + "fta_test_secret_" + "1234567890abcdef1234567890abcdef"
        self.assertTrue(any(pattern.search(line) for pattern in repo_checks.SECRET_PATTERNS))

    def test_env_dump_patterns_detect_unsafe_commands(self) -> None:
        self.assertTrue(any(pattern.search("printenv") for pattern in repo_checks.ENV_DUMP_PATTERNS))
        self.assertTrue(any(pattern.search("Get-ChildItem Env:") for pattern in repo_checks.ENV_DUMP_PATTERNS))

    def test_json_parser_rejects_malformed_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.json"
            path.write_text('{"broken": true,,}', encoding="utf-8")
            with self.assertRaises(Exception):
                repo_checks.json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
