import importlib.util
import unittest
from pathlib import Path


def load_helper_agent():
    path = Path(__file__).parents[1] / "helper-agent" / "agent.py"
    spec = importlib.util.spec_from_file_location("helper_agent", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HelperCapacityTests(unittest.TestCase):
    def test_calculates_capacity_from_logical_cores(self):
        calculate = load_helper_agent().calculate_helper_cores
        self.assertEqual(calculate(16), 14)
        self.assertEqual(calculate(32), 28)
        self.assertEqual(calculate(128), 120)


if __name__ == "__main__":
    unittest.main()
