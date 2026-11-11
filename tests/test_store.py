import sys
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "orchestrator"))

from app.store import Store, now


class StoreTests(unittest.TestCase):
    def test_stale_lease_is_expired_without_releasing_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "orchestrator.db"))
            helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 120})
            request = {"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                       "initiator_port": 1345, "target_core_count": 32}
            original = store.create_lease(request)
            store.release_lease(original.lease_id)
            replacement = store.create_lease({**request, "initiator_id": "jenkins-2"})
            store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
            # Reproduce a stale active assignment in the authoritative runtime state.
            original.state = "active"
            store.leases[original.lease_id] = original
            store.reap()
            self.assertTrue(store.flush())
            states = dict(store._connection.execute("SELECT lease_id, state FROM leases"))
            self.assertEqual(states[original.lease_id], "expired")
            self.assertEqual(states[replacement.lease_id], "active")
            current = store.list_helpers()[0]
            self.assertEqual(current["lease_id"], replacement.lease_id)
            self.assertEqual(current["state"], "active")
            self.assertTrue(current["agent_ready"])
            store.close()

    def test_helper_timeout_expires_lease_before_helper_is_reassigned(self):
        for active in (False, True):
            for enabled in (False, True):
                with self.subTest(active=active, enabled=enabled):
                    store = Store()
                    helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 120})
                    request = {"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                               "initiator_port": 1345, "target_core_count": 32}
                    lease = store.create_lease(request)
                    if active:
                        store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
                    store.set_helper_enabled(helper.helper_id, enabled)
                    with patch("app.store.now", return_value=now() + timedelta(seconds=16)):
                        store.reap()
                        self.assertEqual(lease.state, "expired")
                        self.assertEqual(helper.state, "offline" if enabled else "disabled")
                        self.assertIsNone(helper.lease_id)
                        self.assertFalse(helper.agent_ready)
                        self.assertEqual(store.list_initiators(), [])
                        expiry = lease.expires_at
                        self.assertEqual(store.heartbeat_lease(lease.lease_id).state, "expired")
                        self.assertEqual(lease.expires_at, expiry)
                        store.heartbeat_helper(helper.helper_id, {"agent_ready": False})
                        if not enabled:
                            store.set_helper_enabled(helper.helper_id, True)
                        replacement = store.create_lease({**request, "initiator_id": "jenkins-2"})
                        self.assertIsNotNone(replacement)
                        self.assertEqual(helper.lease_id, replacement.lease_id)
                        self.assertEqual([i["lease_id"] for i in store.list_initiators()],
                                         [replacement.lease_id])
                    store.close()

    def test_lost_helper_expires_entire_lease_and_preserves_other_assignments(self):
        store = Store()
        helpers = [store.register_helper({"hostname": f"helper-{i}", "address": f"10.0.0.{i}", "cores": 8})
                   for i in range(2, 5)]
        request = {"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                   "initiator_port": 1345, "target_core_count": 16}
        lease = store.create_lease(request)
        for helper in helpers[:2]:
            store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
        other = store.create_lease({**request, "initiator_id": "jenkins-2", "target_core_count": 8})
        store.set_helper_enabled(helpers[1].helper_id, False)
        helpers[0].last_seen -= timedelta(seconds=16)
        store.reap()
        self.assertEqual(lease.state, "expired")
        self.assertEqual(helpers[0].state, "offline")
        self.assertEqual(helpers[1].state, "disabled")
        self.assertIsNone(helpers[1].lease_id)
        self.assertFalse(helpers[1].agent_ready)
        self.assertEqual(helpers[2].lease_id, other.lease_id)
        self.assertEqual(other.state, "pending")
        store.close()

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
            self.assertIn(lease.lease_id, restarted._history)
            self.assertEqual(restarted.lease_view(lease.lease_id)["state"], "expired")
            restarted.close()

    def test_existing_enabled_column_is_preserved_on_restart_and_save(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "orchestrator.db")
            store = Store(database)
            helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
            store.set_helper_enabled(helper.helper_id, False)
            store.close()

            restarted = Store(database)
            self.addCleanup(restarted.close)
            restarted.heartbeat_helper(helper.helper_id, {"agent_port": 1346})
            self.assertTrue(restarted.flush())
            row = restarted._connection.execute("SELECT enabled, listen_port FROM helpers WHERE helper_id = ?",
                                                 (helper.helper_id,)).fetchone()
            self.assertEqual(tuple(row), (0, 1346))
            new = restarted.register_helper({"hostname": "helper-2", "address": "10.0.0.3", "cores": 16})
            self.assertTrue(restarted.flush())
            row = restarted._connection.execute("SELECT enabled FROM helpers WHERE helper_id = ?",
                                                 (new.helper_id,)).fetchone()
            self.assertEqual(row["enabled"], 1)
            restarted.close()
            reopened = Store(database)
            self.addCleanup(reopened.close)
            self.assertEqual(set(reopened.helpers), {helper.helper_id, new.helper_id})
            self.assertEqual(reopened._connection.execute(
                "SELECT enabled FROM helpers WHERE helper_id = ?", (helper.helper_id,)).fetchone()["enabled"], 0)

    def test_stale_state_is_reconciled_when_store_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "orchestrator.db")
            store = Store(database)
            helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
            lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                        "initiator_port": 1345, "target_core_count": 4})
            store.leases[lease.lease_id].expires_at = store.leases[lease.lease_id].expires_at.replace(year=2020)
            store.helpers[helper.helper_id].last_seen = store.helpers[helper.helper_id].last_seen.replace(year=2020)
            store.close()

            restarted = Store(database)
            self.assertEqual(restarted.lease_view(lease.lease_id)["state"], "expired")
            self.assertEqual(restarted.helpers[helper.helper_id].state, "offline")
            self.assertIsNone(restarted.helpers[helper.helper_id].lease_id)
            restarted.close()

    def test_helper_heartbeat_revives_helper_after_orchestrator_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "orchestrator.db")
            store = Store(database)
            helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
            store.helpers[helper.helper_id].state = "offline"
            store.close()

            restarted = Store(database)
            revived = restarted.heartbeat_helper(helper.helper_id, {"agent_ready": False})
            self.assertEqual(revived.state, "idle")
            self.assertEqual(restarted.helpers[helper.helper_id].state, "idle")
            restarted.close()

    def test_memory_allocation_is_exclusive(self):
        store = Store()
        store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
        leases = []

        def allocate(initiator):
            leases.append(store.create_lease({"initiator_id": initiator, "initiator_address": "10.0.0.1",
                                              "initiator_port": 1345, "target_core_count": 8}))

        threads = [threading.Thread(target=allocate, args=(str(i),)) for i in range(48)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(lease is not None for lease in leases), 1)
        store.close()

    def test_lease_selects_idle_capacity_and_release_returns_helper_to_idle(self):
        store = Store()
        helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8, "listen_port": 1346})
        lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                    "initiator_port": 1345, "target_core_count": 4})
        self.assertIsNotNone(lease)
        self.assertEqual(store.helpers[helper.helper_id].state, "reserved")
        store.release_lease(lease.lease_id)
        self.assertEqual(store.helpers[helper.helper_id].state, "idle")

    def test_disabled_helper_is_not_selected_and_can_be_enabled(self):
        store = Store()
        helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
        store.set_helper_enabled(helper.helper_id, False)

        self.assertEqual(store.helpers[helper.helper_id].state, "disabled")
        self.assertIsNone(store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                               "initiator_port": 1345, "target_core_count": 4}))

        store.set_helper_enabled(helper.helper_id, True)
        self.assertEqual(store.helpers[helper.helper_id].state, "idle")
        self.assertIsNotNone(store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                                  "initiator_port": 1345, "target_core_count": 4}))

    def test_active_helper_drains_until_its_lease_is_released(self):
        store = Store()
        helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
        lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                    "initiator_port": 1345, "target_core_count": 4})
        store.heartbeat_helper(helper.helper_id, {"agent_ready": True})

        store.set_helper_enabled(helper.helper_id, False)
        self.assertEqual(store.helpers[helper.helper_id].state, "draining")
        self.assertEqual(store.helpers[helper.helper_id].lease_id, lease.lease_id)

        store.release_lease(lease.lease_id)
        self.assertEqual(store.helpers[helper.helper_id].state, "disabled")
        self.assertIsNone(store.helpers[helper.helper_id].lease_id)

    def test_enabling_busy_helper_preserves_reservation_until_release(self):
        for active in (False, True):
            for disable_first in (False, True):
                with self.subTest(active=active, disable_first=disable_first):
                    store = Store()
                    self.addCleanup(store.close)
                    helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
                    request = {"initiator_id": "build-1", "initiator_address": "10.0.0.1",
                               "initiator_port": 1345, "target_core_count": 8}
                    lease = store.create_lease(request)
                    if active:
                        store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
                    if disable_first:
                        store.set_helper_enabled(helper.helper_id, False)
                        self.assertEqual(helper.state, "draining")
                    store.set_helper_enabled(helper.helper_id, True)
                    self.assertEqual(helper.state, "active" if active else "reserved")
                    self.assertEqual(helper.lease_id, lease.lease_id)
                    self.assertEqual(helper.agent_ready, active)
                    self.assertIsNone(store.create_lease({**request, "initiator_id": "build-2"}))
                    self.assertEqual(store.lease_view(lease.lease_id)["state"], "active" if active else "pending")
                    store.release_lease(lease.lease_id)
                    replacement = store.create_lease({**request, "initiator_id": "build-2"})
                    self.assertIsNotNone(replacement)
                    self.assertEqual(helper.lease_id, replacement.lease_id)

    def test_concurrent_reactivation_does_not_allocate_busy_helper(self):
        store = Store()
        self.addCleanup(store.close)
        helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
        request = {"initiator_id": "original", "initiator_address": "10.0.0.1",
                   "initiator_port": 1345, "target_core_count": 8}
        original = store.create_lease(request)
        store.heartbeat_helper(helper.helper_id, {"agent_ready": True})
        barrier = threading.Barrier(16)

        def compete(index):
            barrier.wait(timeout=5)
            if index % 2:
                store.set_helper_enabled(helper.helper_id, False)
                store.set_helper_enabled(helper.helper_id, True)
                return None
            return store.create_lease({**request, "initiator_id": f"contender-{index}"})

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(compete, range(16)))
        self.assertTrue(all(lease is None for lease in results))
        self.assertEqual(helper.lease_id, original.lease_id)
        self.assertEqual(helper.state, "active")
        self.assertEqual([lease["lease_id"] for lease in store.list_initiators()], [original.lease_id])

    def test_disabled_state_survives_store_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "orchestrator.db")
            store = Store(database)
            helper = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8})
            store.set_helper_enabled(helper.helper_id, False)
            store.close()

            restarted = Store(database)
            self.assertFalse(restarted.helpers[helper.helper_id].enabled)
            self.assertEqual(restarted.helpers[helper.helper_id].state, "disabled")
            restarted.close()

    def test_existing_database_is_migrated_with_enabled_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "orchestrator.db")
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE helpers (
                    helper_id TEXT PRIMARY KEY, hostname TEXT NOT NULL, address TEXT NOT NULL,
                    cores INTEGER NOT NULL, memory_bytes INTEGER NOT NULL, platform TEXT NOT NULL,
                    uba_version TEXT NOT NULL, listen_port INTEGER NOT NULL, state TEXT NOT NULL,
                    last_seen TEXT NOT NULL, lease_id TEXT, agent_ready INTEGER NOT NULL
                );
                INSERT INTO helpers VALUES
                    ('helper-1', 'helper-1', '10.0.0.2', 8, 0, 'windows', 'unknown', 1346,
                     'idle', '2020-01-01T00:00:00+00:00', NULL, 0);
                """
            )
            connection.commit()
            connection.close()

            store = Store(database)
            self.assertTrue(store.helpers["helper-1"].enabled)
            store.close()


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

    def test_helper_registration_replaces_unleased_duplicate_when_address_changes(self):
        store = Store()
        old = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8,
                                     "listen_port": 1346})
        current = store.register_helper({"helper_id": "current-helper", "hostname": "helper-1",
                                         "address": "10.0.0.3", "cores": 32, "listen_port": 1346})

        registered = store.register_helper({"hostname": "helper-1", "address": "10.0.0.3", "cores": 32,
                                            "listen_port": 1346})

        self.assertEqual(registered.helper_id, current.helper_id)
        self.assertNotIn(old.helper_id, store.helpers)
        self.assertEqual(len(store.helpers), 1)

    def test_helper_registration_removes_historical_lease_reference_for_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "orchestrator.db")
            store = Store(database)
            old = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8,
                                         "listen_port": 1346})
            lease = store.create_lease({"initiator_id": "jenkins-1", "initiator_address": "10.0.0.1",
                                        "initiator_port": 1345, "target_core_count": 8})
            store.release_lease(lease.lease_id)
            current = store.register_helper({"helper_id": "current-helper", "hostname": "helper-1",
                                             "address": "10.0.0.3", "cores": 32, "listen_port": 1346})

            registered = store.register_helper({"hostname": "helper-1", "address": "10.0.0.3", "cores": 32,
                                                "listen_port": 1346})

            self.assertEqual(registered.helper_id, current.helper_id)
            self.assertNotIn(old.helper_id, store.helpers)
            self.assertNotIn(old.helper_id, [helper["helper_id"]
                                            for helper in store.lease_view(lease.lease_id)["helpers"]])
            store.close()

    def test_helper_registration_reuses_unleased_helper_when_address_changes(self):
        store = Store()
        previous = store.register_helper({"hostname": "helper-1", "address": "10.0.0.2", "cores": 8,
                                          "listen_port": 1346})

        registered = store.register_helper({"hostname": "helper-1", "address": "10.0.0.3", "cores": 8,
                                            "listen_port": 1346})

        self.assertEqual(registered.helper_id, previous.helper_id)
        self.assertEqual(registered.address, "10.0.0.3")
        self.assertEqual(len(store.helpers), 1)
