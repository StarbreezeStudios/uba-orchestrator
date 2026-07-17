import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "orchestrator"))

from app.store import Store


class StoreTests(unittest.TestCase):
    def test_lease_selects_idle_capacity_and_release_returns_helper_to_idle(self):
        store = Store()
        helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8, "listen_port": 1346})
        lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                    "initiator_port": 1345, "target_core_count": 4})
        self.assertIsNotNone(lease)
        self.assertEqual(store.helpers[helper.helper_id].state, "reserved")
        store.release_lease(lease.lease_id)
        self.assertEqual(store.helpers[helper.helper_id].state, "idle")


    def test_lease_is_not_created_when_capacity_is_insufficient(self):
        store = Store()
        store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 2})
        self.assertIsNone(store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                               "initiator_port": 1345, "target_core_count": 4}))

    def test_helper_heartbeat_renews_active_lease(self):
        store = Store()
        helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
        lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                    "initiator_port": 1345, "target_core_count": 4})
        self.assertIsNotNone(lease)
        initial_expiry = lease.expires_at
        store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
        self.assertEqual(store.lease_view(lease.lease_id)["state"], "active")
        self.assertGreater(store.leases[lease.lease_id].expires_at, initial_expiry)

    def test_diagnostics_list_helpers_and_initiators(self):
        store = Store()
        helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
        lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                    "initiator_port": 1345, "target_core_count": 4})

        helpers = store.list_helpers()
        initiators = store.list_initiators()

        self.assertEqual(len(helpers), 1)
        self.assertEqual(helpers[0]["helper_id"], helper.helper_id)
        self.assertEqual(len(initiators), 1)
        self.assertEqual(initiators[0]["lease_id"], lease.lease_id)
        self.assertEqual(initiators[0]["helpers"][0]["hostname"], "helper-1")

    def test_helper_registration_is_idempotent_and_removes_inactive_duplicates(self):
        store = Store()
        first = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8,
                                       "listen_port": 1346})
        first.state = "offline"
        store.register_helper({"helper_id": "legacy-helper", "hostname": "helper-1", "address": "10.0.0.2",
                               "cores": 16, "listen_port": 1346})
        current = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 32,
                                         "listen_port": 1346})

        self.assertEqual(current.helper_id, "legacy-helper")
        self.assertEqual(len(store.helpers), 1)
        self.assertEqual(store.helpers[current.helper_id].cores, 32)
