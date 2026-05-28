from pathlib import Path
import unittest


class Python39CompatibilityTests(unittest.TestCase):
    def test_backend_runtime_annotations_are_python39_compatible(self) -> None:
        app_root = Path(__file__).resolve().parents[1] / "app"
        offenders = []
        for path in app_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "| None" in text or "None |" in text:
                offenders.append(str(path.relative_to(app_root.parent)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
