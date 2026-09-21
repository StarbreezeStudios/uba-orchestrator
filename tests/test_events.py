import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "orchestrator"))
from app.store import Store


class EventTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.capture = redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)
        self.store = Store()
        self.addCleanup(self.store.close)

    def helper(self):
        return self.store.register_helper({"hostname": "jk-win-039", "address": "10.0.0.1", "cores": 120})

    def lease(self):
        return self.store.create_lease({"initiator_id": "JK-WIN-027", "initiator_address": "10.0.0.2",
                                        "initiator_port": 1345, "target_core_count": 32})

    def test_lifecycle_and_json_output_without_heartbeat_noise(self):
        helper = self.helper()
        lease = self.lease()
        self.store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
        count = len(self.store.list_events())
        self.store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
        self.store.heartbeat_lease(lease.lease_id)
        self.assertEqual(len(self.store.list_events()), count)
        self.store.release_lease(lease.lease_id)
        self.store.release_lease(lease.lease_id)
        events = self.store.list_events()
        self.assertEqual([e["event"] for e in reversed(events)],
                         ["helper_registered", "capacity_requested", "lease_created", "lease_active", "lease_released"])
        assigned = next(e for e in events if e["event"] == "lease_created")
        self.assertEqual(assigned["helpers"], ["jk-win-039"])
        self.assertEqual(assigned["requested_cores"], 32)
        self.assertEqual(assigned["assigned_cores"], 120)
        self.assertEqual(assigned["lease_id"], lease.lease_id)
        self.assertEqual([json.loads(line) for line in self.output.getvalue().splitlines()], list(reversed(events)))

    def test_retention_order_limits_and_snapshot_isolation(self):
        helper = self.helper()
        for index in range(205):
            self.store.set_helper_enabled(helper.helper_id, index % 2 == 1)
        events = self.store.list_events()
        self.assertEqual(len(events), 200)
        self.assertEqual(events[0]["event"], "helper_disabled")
        self.assertEqual(self.store.list_events(1), events[:1])
        events[0]["helpers"].clear()
        self.assertEqual(self.store.list_events(1)[0]["helpers"], ["jk-win-039"])
        for limit in [0, -1, 201]:
            with self.assertRaises(ValueError):
                self.store.list_events(limit)

    def test_capacity_failure_timeout_recovery_and_initiator_expiry(self):
        self.assertIsNone(self.lease())
        self.assertEqual(self.store.list_events(1)[0]["available_cores"], 0)
        helper = self.helper()
        self.lease()
        helper.last_seen -= timedelta(seconds=16)
        self.store.reap()
        self.assertEqual(self.store.list_events(1)[0]["reason"], "helper_lost")
        self.store.heartbeat_helper(helper.helper_id, {"agent_ready": False})
        self.assertEqual(self.store.list_events(1)[0]["event"], "helper_recovered")
        lease = self.lease()
        lease.expires_at -= timedelta(seconds=31)
        self.store.reap()
        self.assertEqual(self.store.list_events(1)[0]["reason"], "initiator_timeout")

    def test_failed_transaction_is_not_published_and_restart_clears_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "events.db")
            store = Store(path)
            try:
                with patch.object(store, "_save", side_effect=RuntimeError("write failed")):
                    with self.assertRaises(RuntimeError):
                        store.register_helper({"hostname": "failed", "address": "10.0.0.1", "cores": 8})
                self.assertEqual(store.list_events(), [])
                self.assertEqual(self.output.getvalue(), "")
                store.register_helper({"hostname": "registered", "address": "10.0.0.1", "cores": 8})
                store.create_lease({"initiator_id": "build", "initiator_address": "10.0.0.2",
                                    "initiator_port": 1345, "target_core_count": 4})
            finally:
                store.close()
            restarted = Store(path)
            try:
                events = restarted.list_events()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["reason"], "coordinator_restart")
            finally:
                restarted.close()
