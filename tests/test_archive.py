from dataclasses import FrozenInstanceError
import sqlite3

from test_ingest import WeatherCase, field, packet, uint, chunks
from weather.archive.imports import Archive
from weather.archive.schema import initialize_archive, ConflictError


class TestArchive(WeatherCase):
    def test_rollback_replay_reopen(self):
        _, binding, wire = self.register_sample()
        receipt = self.archive.ingest("old", binding, [wire])
        old = self.archive.read_observations("old")
        before = self.counts()
        self.archive.db.execute(
            "CREATE TEMP TRIGGER reject_receipt BEFORE INSERT ON receipts "
            "WHEN NEW.batch_id='failed' BEGIN SELECT RAISE(ABORT,'receipt storage failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.archive.ingest("failed", binding, chunks(wire, 1))
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.archive.read_observations("old"), old)
        for table in ("batches", "observations", "receipts"):
            self.assertEqual(self.archive.db.execute(
                "SELECT count(*) FROM " + table + " WHERE batch_id='failed'").fetchone()[0], 0)
        self.assertEqual(self.archive.ingest("old", binding, chunks(wire, 2)), receipt)
        self.assertEqual(self.counts(), before)
        with self.assertRaises(ConflictError):
            self.archive.ingest("old", binding, [wire[:-1] + bytes([wire[-1] ^ 128])])
        self.assertEqual(self.archive.read_observations("old"), old)
        with self.assertRaises(FrozenInstanceError):
            old[0].raw = 99
        initialize_archive(self.archive.db)
        self.assertEqual(self.counts(), before)
        custom = self.archive.register_template("USER", 7, [field(width=8)])
        self.archive.ingest("user", custom, [packet(uint(17, 8), template_id="USER", revision=7)])
        user = self.archive.read_observations("user")
        expected_counts = self.counts()
        self.archive.close()
        self.archive = Archive(self.path)
        initialize_archive(self.archive.db)
        self.assertEqual(self.counts(), expected_counts)
        self.assertEqual(self.archive.catalog.get_binding("USER", 7), custom)
        # History reads need no template definition, even on a reopened database.
        def authorizer(action, table, column, database, source):
            return sqlite3.SQLITE_DENY if (
                action == sqlite3.SQLITE_READ and table == "templates") else sqlite3.SQLITE_OK
        self.archive.db.set_authorizer(authorizer)
        self.assertEqual(self.archive.read_observations("old"), old)
        self.assertEqual(self.archive.read_observations("user"), user)
        self.archive.db.set_authorizer(None)
        self.assertEqual(self.archive.ingest("old", binding, [wire]), receipt)
        self.assertEqual(self.counts(), expected_counts)

    def test_payload_and_binding_digest(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        wire = packet(uint(10, 8))
        first = self.archive.ingest("batch", binding, [wire])
        self.assertEqual(self.archive.ingest("batch", binding, chunks(wire, 1)), first)
        before = self.counts()
        with self.assertRaises(ConflictError):
            self.archive.ingest("batch", binding, [packet(uint(11, 8))])
        second = self.archive.register_template("T", 2, [field(width=8)])
        with self.assertRaises(ConflictError):
            self.archive.ingest("batch", second, [packet(uint(10, 8), revision=2)])
        self.assertEqual(self.archive.read_observations("batch")[0].raw, 10)
        self.assertEqual(self.counts()[1:], before[1:])

    def test_schema_version_and_reinitialize(self):
        initialize_archive(self.archive.db)
        self.archive.register_template("user", 1, [field(width=8)])
        initialize_archive(self.archive.db)
        self.assertEqual(self.archive.catalog.get_binding("user", 1).template_id, "user")
        with self.archive.db:
            self.archive.db.execute("UPDATE archive_meta SET version=2")
        with self.assertRaises(ValueError):
            initialize_archive(self.archive.db)
        with self.archive.db:
            self.archive.db.execute("UPDATE archive_meta SET version=1")
        self.assertEqual(self.archive.catalog.get_binding("user", 1).revision, 1)
