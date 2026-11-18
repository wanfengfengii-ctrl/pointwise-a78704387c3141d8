import sys
from pathlib import Path
import unittest


def main():
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "tests"))
    loader = unittest.TestLoader()
    if len(sys.argv) > 1:
        suite = loader.loadTestsFromNames(sys.argv[1:])
    else:
        suite = loader.discover(str(root / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
