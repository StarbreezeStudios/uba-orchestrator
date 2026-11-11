import io
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "orchestrator"))

from app.store import Store, now


class IncrementalPersistenceTests(unittest.TestCase):
    def setUp(self):
        capture = redirect_stdout(io.StringIO())
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = str(Path(directory.name) / "history.db")
        self.store = Store(self.database)
        self.addCleanup(self.store.close)
        self.helper = self.store.register_helper({"hostname": "helper", "address": "127.0.0.2", "cores": 8})
        self.request = {"initiator_id": "build", "initiator_address": "127.0.0.3",
                        "initiator_port": 1345, "target_core_count": 8}
        timestamp = now().isoformat()
        self.store._connection.executemany(
            "INSERT INTO leases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(f"historical-{i}", "old-build", "127.0.0.3", 1345, 8,
              "released" if i % 2 else "expired", timestamp, timestamp) for i in range(12000)])
        self.store._connection.executemany("INSERT INTO lease_helpers VALUES (?, ?)",
                                          [(f"historical-{i}", self.helper.helper_id) for i in range(12000)])
        self.store._connection.commit()

    def history(self):
        return [tuple(row) for row in self.store._connection.execute(
            "SELECT * FROM leases WHERE lease_id LIKE 'historical-%' ORDER BY lease_id")]

    def test_heartbeat_and_reads_do_not_rewrite_or_scan_history(self):
        original = self.history()
        connection = self.store._connection
        before = connection.total_changes
        # Abort if a hot-path operation starts scanning the retained history.
        calls = 0

        def progress():
            nonlocal calls
            calls += 1
            return int(calls > 50)

        connection.set_progress_handler(progress, 100)
        try:
            self.store.heartbeat_helper(self.helper.helper_id, {"agent_ready": False})
            self.assertEqual(connection.total_changes - before, 1)
            before = connection.total_changes
            self.store.list_helpers()
            self.store.list_initiators()
            self.store.reap()
            self.assertEqual(connection.total_changes, before)
            self.assertEqual(self.store.leases, {})
        finally:
            connection.set_progress_handler(None, 0)
        self.assertEqual(self.history(), original)
        self.assertEqual(connection.execute("SELECT count(*) FROM lease_helpers").fetchone()[0], 12000)

    def test_historical_lookup_heartbeat_and_release_preserve_current_assignment(self):
        active = self.store.create_lease(self.request)
        view = self.store.lease_view("historical-0")
        self.assertEqual(view["state"], "expired")
        self.assertEqual(view["helpers"][0]["helper_id"], self.helper.helper_id)
        self.assertEqual(self.store.heartbeat_lease("historical-0").state, "expired")
        self.store.release_lease("historical-0")
        self.assertEqual(self.store.list_helpers()[0]["lease_id"], active.lease_id)
        self.assertEqual(self.store.lease_view(active.lease_id)["state"], "pending")
        self.assertNotIn("historical-0", self.store.leases)

    def test_lease_renewal_does_not_rewrite_membership_or_history(self):
        active = self.store.create_lease(self.request)
        before = self.store._connection.total_changes
        self.store.heartbeat_lease(active.lease_id)
        self.assertEqual(self.store._connection.total_changes - before, 1)
        self.store.release_lease(active.lease_id)
        before = self.store._connection.total_changes
        self.store.release_lease(active.lease_id)
        self.assertEqual(self.store._connection.total_changes, before)

    def test_restart_preserves_terminal_history_without_loading_it(self):
        original = self.history()
        active = self.store.create_lease(self.request)
        self.store.close()
        self.store = Store(self.database)
        self.addCleanup(self.store.close)
        self.assertEqual(self.history(), original)
        self.assertEqual(self.store.lease_view(active.lease_id)["state"], "expired")
        self.store.list_helpers()
        self.assertEqual(self.store.leases, {})
        self.assertEqual(self.store._connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_partial_write_failure_rolls_back_state_and_events(self):
        original = self.history()
        self.store._connection.executescript(
            """CREATE TRIGGER reject_lease BEFORE INSERT ON leases BEGIN
                   SELECT RAISE(ABORT, 'injected failure');
               END;""")
        events = self.store.list_events()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_lease(self.request)
        self.assertFalse(self.store._connection.in_transaction)
        self.assertEqual(self.store.list_helpers()[0]["state"], "idle")
        self.assertEqual(self.store.list_events(), events)
        self.assertEqual(self.history(), original)
        self.store._connection.execute("DROP TRIGGER reject_lease")
        self.assertIsNotNone(self.store.create_lease(self.request))

    def test_failed_reload_releases_sqlite_transaction(self):
        with patch.object(self.store, "_load", side_effect=RuntimeError("read failed")):
            with self.assertRaises(RuntimeError):
                self.store.list_helpers()
        self.assertFalse(self.store._connection.in_transaction)
        self.assertEqual(len(self.store.list_helpers()), 1)

    def test_concurrent_requests_with_history_preserve_exclusive_allocation(self):
        original = self.history()
        # Separate connections retain SQLite's allocation exclusion guarantees.
        stores = [self.store] + [Store(self.database) for _ in range(3)]
        for store in stores[1:]:
            self.addCleanup(store.close)

        def request(index):
            store = stores[index % len(stores)]
            store.heartbeat_helper(self.helper.helper_id, {})
            lease = store.create_lease({**self.request, "initiator_id": f"build-{index}"})
            store.list_helpers()
            store.list_initiators()
            if lease:
                store.heartbeat_lease(lease.lease_id)
            return lease

        with ThreadPoolExecutor(max_workers=48) as executor:
            leases = list(executor.map(request, range(96)))
        self.assertEqual(sum(lease is not None for lease in leases), 1)
        self.assertEqual(self.history(), original)
        self.assertEqual(self.store._connection.execute("PRAGMA foreign_key_check").fetchall(), [])
