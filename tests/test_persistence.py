import io
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "orchestrator"))

from app.store import Store, now


class BackgroundPersistenceTests(unittest.TestCase):
    def setUp(self):
        capture = redirect_stdout(io.StringIO())
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = str(Path(directory.name) / "history.db")
        seed = Store(self.database)
        helper = seed.register_helper({"hostname": "helper", "address": "127.0.0.2", "cores": 8})
        self.assertTrue(seed.close())
        timestamp = now().isoformat()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executemany(
                "INSERT INTO leases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(f"historical-{i}", "old-build", "127.0.0.3", 1345, 8,
                  "released" if i % 2 else "expired", timestamp, timestamp) for i in range(12000)])
            connection.executemany("INSERT INTO lease_helpers VALUES (?, ?)",
                                   [(f"historical-{i}", helper.helper_id) for i in range(12000)])
        self.store = Store(self.database, persist_interval=3600)
        self.addCleanup(self.store.close)
        self.helper = self.store.heartbeat_helper(helper.helper_id, {"agent_ready": False})
        self.request = {"initiator_id": "build", "initiator_address": "127.0.0.3",
                        "initiator_port": 1345, "target_core_count": 8}
        self.assertTrue(self.store.flush())

    def history(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            return list(connection.execute(
                "SELECT * FROM leases WHERE lease_id LIKE 'historical-%' ORDER BY lease_id"))

    def test_runtime_and_historical_reads_do_not_touch_sqlite(self):
        original = self.history()
        # Any SQLite access from requests would now fail, including indexed reads.
        self.store._connection.set_authorizer(lambda *args: sqlite3.SQLITE_DENY)
        try:
            self.store.heartbeat_helper(self.helper.helper_id, {})
            lease = self.store.create_lease(self.request)
            self.store.heartbeat_lease(lease.lease_id)
            self.store.list_helpers()
            self.store.list_initiators()
            self.store.reap()
            self.assertEqual(self.store.lease_view("historical-0")["state"], "expired")
            self.store.heartbeat_lease("historical-0")
            self.store.release_lease("historical-0")
            self.assertEqual(self.store.helpers[self.helper.helper_id].lease_id, lease.lease_id)
            self.assertNotIn("historical-0", self.store.leases)
        finally:
            self.store._connection.set_authorizer(None)
        self.assertEqual(self.history(), original)
        self.assertTrue(self.store.flush())

    def test_periodic_writer_runs_without_flush_and_retains_history(self):
        original = self.history()
        self.store.close()
        self.store = Store(self.database, persist_interval=.02)
        self.addCleanup(self.store.close)
        self.store.heartbeat_helper(self.helper.helper_id, {"agent_port": 1456})
        deadline = time.monotonic() + 3
        while self.store.persistence_status()["pending"] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.store.persistence_status()["pending"])
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("SELECT listen_port FROM helpers").fetchone()[0], 1456)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.history(), original)

    def test_heartbeat_persistence_does_not_rewrite_memberships(self):
        lease = self.store.create_lease(self.request)
        self.assertTrue(self.store.flush())
        before = self.store._connection.total_changes
        for _ in range(100):
            self.store.heartbeat_lease(lease.lease_id)
        self.assertEqual(self.store._connection.total_changes, before)
        self.assertEqual(len(self.store._pending_leases), 1)
        self.assertTrue(self.store.flush())
        self.assertEqual(self.store._connection.total_changes - before, 1)

    def test_blocked_writer_does_not_block_requests_or_lose_newer_state(self):
        entered, release = threading.Event(), threading.Event()
        save = self.store._save

        def blocked(helpers, leases):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test did not release writer")
            save(helpers, leases)

        with patch.object(self.store, "_save", side_effect=blocked):
            try:
                self.store.heartbeat_helper(self.helper.helper_id, {"agent_port": 1400})
                self.assertFalse(self.store.flush(.05))
                self.assertTrue(entered.wait(1))

                def request(index):
                    self.store.heartbeat_helper(self.helper.helper_id, {})
                    lease = self.store.create_lease({**self.request, "initiator_id": f"build-{index}"})
                    self.store.list_helpers()
                    self.store.list_initiators()
                    self.store.lease_view("historical-0")
                    if lease:
                        self.store.heartbeat_lease(lease.lease_id)
                    return lease

                with ThreadPoolExecutor(max_workers=48) as executor:
                    futures = [executor.submit(request, index) for index in range(96)]
                    leases = [future.result(timeout=2) for future in futures]
                self.assertEqual(sum(lease is not None for lease in leases), 1)
                lease = next(lease for lease in leases if lease)
                self.store.release_lease(lease.lease_id)
                self.store.heartbeat_helper(self.helper.helper_id, {"agent_port": 1456})
                latest = self.store.helpers[self.helper.helper_id].last_seen.isoformat()
                self.assertEqual(len(self.store._pending_leases), 1)
                self.assertTrue(self.store._writer.is_alive())
            finally:
                release.set()
        self.assertTrue(self.store.flush())
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("SELECT listen_port, last_seen FROM helpers").fetchone(),
                             (1456, latest))
            self.assertEqual(connection.execute("SELECT state FROM leases WHERE lease_id = ?",
                                                 (lease.lease_id,)).fetchone()[0], "released")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_sqlite_lock_and_write_failure_preserve_runtime_then_retry(self):
        lease = self.store.create_lease(self.request)
        blocker = sqlite3.connect(self.database)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            self.assertFalse(self.store.flush(.05))
            self.store.heartbeat_lease(lease.lease_id)
            self.assertEqual(self.store.lease_view(lease.lease_id)["state"], "pending")
        finally:
            blocker.rollback()
            blocker.close()
        self.assertTrue(self.store.flush())
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_lease BEFORE INSERT ON leases BEGIN "
                               "SELECT RAISE(ABORT, 'injected failure'); END;")
        self.store.release_lease(lease.lease_id)
        with self.assertLogs("app.store", level="ERROR"):
            self.assertFalse(self.store.flush(.1))
        self.assertEqual(self.store.lease_view(lease.lease_id)["state"], "released")
        self.assertEqual(self.store.helpers[self.helper.helper_id].state, "idle")
        self.assertFalse(self.store._connection.in_transaction)
        self.assertEqual(self.store.persistence_status()["error"], "IntegrityError")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("SELECT state FROM leases WHERE lease_id = ?",
                                                 (lease.lease_id,)).fetchone()[0], "pending")
            connection.execute("DROP TRIGGER reject_lease")
        self.assertTrue(self.store.flush())
        self.assertIsNone(self.store.persistence_status()["error"])

    def test_close_is_bounded_and_retains_single_writer_ownership(self):
        entered, release = threading.Event(), threading.Event()
        save = self.store._save

        def blocked(helpers, leases):
            entered.set()
            release.wait(5)
            save(helpers, leases)

        with patch.object(self.store, "_save", side_effect=blocked):
            try:
                self.store.heartbeat_helper(self.helper.helper_id, {})
                self.assertFalse(self.store.flush(.05))
                self.assertTrue(entered.wait(1))
                # A change after the first snapshot must receive a final shutdown pass.
                self.store.set_helper_enabled(self.helper.helper_id, False)
                started = time.monotonic()
                self.assertFalse(self.store.close(timeout=.05))
                self.assertLess(time.monotonic() - started, .5)
                with self.assertRaises(BlockingIOError):
                    Store(self.database)
            finally:
                release.set()
            self.assertTrue(self.store.close())
        restarted = Store(self.database)
        try:
            self.assertFalse(restarted.helpers[self.helper.helper_id].enabled)
        finally:
            restarted.close()

    def test_restart_requires_new_heartbeat_before_allocation(self):
        original = self.history()
        lease = self.store.create_lease(self.request)
        self.assertTrue(self.store.close())
        self.store = Store(self.database)
        self.addCleanup(self.store.close)
        self.store.set_helper_enabled(self.helper.helper_id, False)
        self.store.set_helper_enabled(self.helper.helper_id, True)
        self.assertIsNone(self.store.create_lease(self.request))
        self.assertEqual(self.store.lease_view(lease.lease_id)["state"], "expired")
        self.assertFalse(self.store.helpers[self.helper.helper_id].agent_ready)
        self.store.heartbeat_helper(self.helper.helper_id, {"agent_ready": False})
        self.assertIsNotNone(self.store.create_lease(self.request))
        self.assertEqual(self.history(), original)

    def test_pending_terminal_history_is_bounded_without_losing_live_allocations(self):
        with patch("app.store.MAX_PENDING_HISTORY", 3):
            for _ in range(8):
                lease = self.store.create_lease(self.request)
                self.store.release_lease(lease.lease_id)
            active = self.store.create_lease(self.request)
            self.assertEqual(len(self.store._pending_leases), 4)
            self.assertIn(active.lease_id, self.store._pending_leases)
            self.assertEqual(self.store.persistence_status()["dropped_history"], 5)
        self.assertTrue(self.store.flush())
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_partial_runtime_failure_rolls_back_without_disk(self):
        before = self.store.list_helpers()
        events = self.store.list_events()
        with self.assertRaises(ValueError):
            self.store.heartbeat_helper(self.helper.helper_id, {"agent_ready": True, "agent_port": "invalid"})
        self.assertEqual(self.store.list_helpers(), before)
        self.assertEqual(self.store.list_events(), events)
