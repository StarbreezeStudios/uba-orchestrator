import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "orchestrator"))

from app.store import Store


class StoreTests(unittest.TestCase):
    def test_state_survives_store_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "orchestrator.db")
            store = Store(database)
            helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8,
                                            "listen_port": 1346})
            lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                        "initiator_port": 1345, "target_core_count": 4})
            store.close()

            restarted = Store(database)
            self.assertIn(helper.helper_id, restarted.helpers)
            self.assertIn(lease.lease_id, restarted.leases)
            self.assertEqual(restarted.lease_view(lease.lease_id)["state"], "pending")
            restarted.close()

    def test_stale_state_is_reconciled_when_store_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "orchestrator.db")
            store = Store(database)
            helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
            lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                        "initiator_port": 1345, "target_core_count": 4})
            store.leases[lease.lease_id].expires_at = store.leases[lease.lease_id].expires_at.replace(year=2020)
            store.helpers[helper.helper_id].last_seen = store.helpers[helper.helper_id].last_seen.replace(year=2020)
            store._connection.execute("UPDATE leases SET expires_at = ? WHERE lease_id = ?",
                                      (store.leases[lease.lease_id].expires_at.isoformat(), lease.lease_id))
            store._connection.execute("UPDATE helpers SET last_seen = ? WHERE helper_id = ?",
                                      (store.helpers[helper.helper_id].last_seen.isoformat(), helper.helper_id))
            store._connection.commit()
            store.close()

            restarted = Store(database)
            self.assertEqual(restarted.leases[lease.lease_id].state, "expired")
            self.assertEqual(restarted.helpers[helper.helper_id].state, "offline")
            self.assertIsNone(restarted.helpers[helper.helper_id].lease_id)
            restarted.close()

    def test_sqlite_allocation_is_transactionally_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "orchestrator.db")
            first = Store(database)
            first.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
            second = Store(database)
            leases = []

            def allocate(store, initiator):
                leases.append(store.create_lease({"initiator_id": initiator, "initiator_address": "10.0.0.1",
                                                  "initiator_port": 1345, "target_core_count": 8}))

            threads = [threading.Thread(target=allocate, args=(first, "one")),
                       threading.Thread(target=allocate, args=(second, "two"))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(sum(lease is not None for lease in leases), 1)
            first.close()
            second.close()

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

    def test_helper_heartbeat_activates_lease_without_renewing_it(self):
        store = Store()
        helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
        lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                    "initiator_port": 1345, "target_core_count": 4})
        self.assertIsNotNone(lease)
        initial_expiry = lease.expires_at
        store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
        self.assertEqual(store.lease_view(lease.lease_id)["state"], "active")
        self.assertEqual(store.leases[lease.lease_id].expires_at, initial_expiry)

    def test_initiator_heartbeat_renews_active_lease(self):
        store = Store()
        helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
        lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                    "initiator_port": 1345, "target_core_count": 4})
        self.assertIsNotNone(lease)
        initial_expiry = lease.expires_at
        store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
        store.heartbeat_lease(lease.lease_id)
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
