"""WOBS2 multi-subset column packet tests.

Independent literal-wire fixtures (no production encoder) cover the public
differential column rules, unequal replication, same-shape subsets, the three
quality states, replay/reopen persistence, atomic rollback and the full error
boundary from COLUMNS.md.  The hand-accounted packets in columns_cases.py are
cross-checked separately through its own verify entry point.
"""
from copy import deepcopy
from dataclasses import replace
import sqlite3
from pathlib import Path
import tempfile
import unittest

from columns_cases import CASES, packet, column_bits
from weather.archive.imports import Archive
from weather.archive.schema import ConflictError
from weather.decoding.bits import DecodeError
from weather.observations.models import ObservationError, QualityState

DAY = "2026-11-17"


def field(width=8, scale=0):
    return dict(kind="field", measurement="wind_speed", width=width,
                scale=scale, unit="m/s")


def quality(width=2):
    return dict(kind="quality", width=width)


def uint(value, width):
    if not 0 <= value < 1 << width:
        raise ValueError("fixture integer outside width")
    return format(value, "0" + str(width) + "b")


def pack_bits(bits):
    padded = bits + "0" * (-len(bits) % 8)
    return bytes(int(padded[i:i + 8], 2) for i in range(0, len(padded), 8))


def column(width, active, base, k=0, deltas=()):
    return column_bits((None, width, active, base, k, deltas))


def raw_column(width, base, k, deltas=()):
    """Build a single-active column without range checks, for malformed packets."""
    bits = uint(base, width) + uint(k, 6)
    if k:
        bits += "".join(format(d, "0" + str(k) + "b") for d in deltas)
    return bits


def make_header(subsets, template_id="COL", revision=1, version=2):
    head = b"WOBS" + bytes([version, len(template_id)]) + template_id.encode("ascii")
    head += revision.to_bytes(4, "big") + bytes([len(subsets)])
    for station, day in subsets:
        encoded = station.encode("ascii")
        head += bytes([len(encoded)]) + encoded + day.encode("ascii")
    return head


def make_packet(bits, subsets, template_id="COL", revision=1, version=2,
                declared_bits=None):
    return (make_header(subsets, template_id, revision, version)
            + (len(bits) if declared_bits is None else declared_bits).to_bytes(4, "big")
            + pack_bits(bits))


class ColumnsCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="columns-tests-")
        self.path = str(Path(self.directory.name) / "archive.sqlite")
        self.archive = Archive(self.path)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(lambda: self.archive.close())

    def counts(self):
        return tuple(self.archive.db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                     for table in ("templates", "batches", "observations", "receipts",
                                   "daily_results", "daily_groups"))

    # ----- canonical hand-accounted cases ---------------------------------

    def test_canonical_cases_match_literal_expectations(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                binding = self.archive.register_template(
                    "COL", case["revision"], deepcopy(case["descriptors"]))
                receipt = self.archive.ingest_columns(
                    case["name"], binding, [packet(case)])
                items = self.archive.read_observations(case["name"])
                actual = tuple((x.subset_index, x.occurrence.template_path,
                                x.occurrence.occurrence_path,
                                x.occurrence.group.parent_occurrence, x.raw,
                                x.quality.state.value, x.quality.code) for x in items)
                self.assertEqual(actual, case["expected"])
                self.assertEqual(receipt.observation_count, len(items))
                self.assertEqual([x.occurrence.sequence for x in items],
                                 list(range(len(items))))
                for item in items:
                    self.assertEqual((item.station, item.day.isoformat()),
                                     case["subsets"][item.subset_index])

    def test_ragged_daily_and_subset_zero_replication(self):
        case = CASES[0]
        binding = self.archive.register_template(
            "COL", case["revision"], deepcopy(case["descriptors"]))
        receipt = self.archive.ingest_columns("ragged", binding, [packet(case)])
        self.assertEqual(receipt.observation_count, 3)
        items = self.archive.read_observations("ragged")
        # All three belong to subset 1; subset 0 replicated zero times.
        self.assertEqual([x.subset_index for x in items], [1, 1, 1])
        result = self.archive.build_daily("day", ["ragged"])
        group = result.groups[0]
        self.assertEqual((group.input_count, group.accepted_count), (3, 2))
        self.assertEqual(group.observation_missing, 1)
        self.assertEqual((str(group.exact_sum), str(group.mean)), ("4", "2"))

    def test_scopes_same_shape_subsets_and_three_quality_states(self):
        case = CASES[1]
        binding = self.archive.register_template(
            "COL", case["revision"], deepcopy(case["descriptors"]))
        self.archive.ingest_columns("scopes", binding, [packet(case)])
        items = self.archive.read_observations("scopes")
        self.assertEqual(len(items), 9)
        states = [(x.subset_index, x.quality.state, x.quality.code) for x in items]
        self.assertIn((1, QualityState.MISSING, None), states)
        self.assertIn((1, QualityState.VALUE, 0), states)
        self.assertIn((0, QualityState.UNSELECTED, None), states)
        # Subsets 0 and 1 share station A/day but stay independent occurrences.
        same_station = [x for x in items if x.station == "A"]
        self.assertEqual(len(same_station), 8)
        result = self.archive.build_daily("scopes-day", ["scopes"])
        by_key = {(g.station, g.day.isoformat()): g for g in result.groups}
        group_a = by_key[("A", DAY)]
        self.assertEqual((group_a.input_count, group_a.accepted_count), (8, 4))
        self.assertEqual((group_a.observation_missing, group_a.unselected,
                          group_a.quality_missing, group_a.quality_rejected),
                         (1, 1, 1, 1))
        self.assertEqual((str(group_a.exact_sum), str(group_a.mean)),
                         ("95", "95/4"))
        group_b = by_key[("B", "2026-11-18")]
        self.assertEqual((group_b.input_count, group_b.accepted_count), (1, 1))
        self.assertEqual((str(group_b.exact_sum), str(group_b.mean)), ("9", "9"))

    # ----- differential column mechanics ----------------------------------

    def test_scalar_column_base_and_deltas(self):
        binding = self.archive.register_template("T", 1, [field(width=10)])
        # Two subsets, base=100, k=5, deltas 0 and 23 -> 100 and 123.
        bits = column(10, (0, 1), 100, 5, (0, 23))
        wire = make_packet(bits, [("A", DAY), ("B", DAY)], template_id="T")
        self.archive.ingest_columns("b", binding, [wire])
        items = self.archive.read_observations("b")
        self.assertEqual([x.raw for x in items], [100, 123])
        self.assertEqual([x.subset_index for x in items], [0, 1])
        self.assertEqual([x.station for x in items], ["A", "B"])

    def test_k_zero_reuses_base_without_deltas(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = column(8, (0, 1, 2), 42, 0)  # no delta bits at all
        wire = make_packet(bits, [("A", DAY), ("B", DAY), ("C", DAY)],
                           template_id="T")
        self.archive.ingest_columns("b", binding, [wire])
        self.assertEqual([x.raw for x in self.archive.read_observations("b")],
                         [42, 42, 42])

    def test_sixteen_subsets_boundary(self):
        subsets = tuple(("S%02d" % index, DAY) for index in range(16))
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = column(8, tuple(range(16)), 5, 8, tuple(range(16)))
        wire = make_packet(bits, subsets, template_id="T")
        self.archive.ingest_columns("b", binding, [wire])
        items = self.archive.read_observations("b")
        self.assertEqual(len(items), 16)
        self.assertEqual([x.subset_index for x in items], list(range(16)))
        self.assertEqual([x.raw for x in items], [5 + index for index in range(16)])
        self.assertEqual([x.occurrence.sequence for x in items], list(range(16)))

    def test_empty_template_zero_observations(self):
        binding = self.archive.register_template("EMPTY", 1, [])
        wire = make_packet("", [("A", DAY)], template_id="EMPTY")
        receipt = self.archive.ingest_columns("empty", binding, [wire])
        self.assertEqual(receipt.observation_count, 0)
        self.assertEqual(self.archive.read_observations("empty"), ())

    def test_missing_all_ones_keeps_identity_and_numbering(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = column(8, (0, 1), 255, 0)
        wire = make_packet(bits, [("A", DAY), ("B", DAY)], template_id="T")
        self.archive.ingest_columns("b", binding, [wire])
        items = self.archive.read_observations("b")
        self.assertTrue(all(x.missing and x.value is None for x in items))
        self.assertEqual([x.occurrence.sequence for x in items], [0, 1])

    def test_nested_replication_narrows_active_sets(self):
        # Two delayed levels; subset 1 reaches the inner call with count 0 in
        # outer round 0 and subset 2 never reaches the outer body at all.
        descriptors = [dict(kind="delayed", count_width=2, children=[
            dict(kind="delayed", count_width=2, children=[field(width=8), quality(2)]),
            quality(2)])]
        binding = self.archive.register_template("N", 3, descriptors)
        bits = column(2, (0, 1, 2), 0, 2, (2, 1, 0))      # outer counts 2,1,0
        bits += column(2, (0, 1), 0, 1, (1, 0))           # inner counts 1,0
        bits += column(8, (0,), 50, 0)                    # subset0 field only
        bits += column(32, (0, 1), 0, 1, (1, 0))          # lengths 1 and 0
        bits += column(1, (0,), 1, 0)                     # select subset0
        bits += column(2, (0,), 0, 0)                     # word 0
        bits += column(2, (0,), 1, 0)                     # outer round1 inner count
        bits += column(8, (0,), 60, 0)
        bits += column(32, (0,), 1, 0)
        bits += column(1, (0,), 1, 0)
        bits += column(2, (0,), 0, 0)
        bits += column(32, (0, 1, 2), 0, 0)               # outer: no direct members
        wire = make_packet(
            bits, [("A", DAY), ("A", DAY), ("C", DAY)],
            template_id="N", revision=3)
        receipt = self.archive.ingest_columns("nested", binding, [wire])
        items = self.archive.read_observations("nested")
        self.assertEqual(receipt.observation_count, 2)
        self.assertEqual([item.subset_index for item in items], [0, 0])
        self.assertEqual([item.raw for item in items], [50, 60])
        self.assertEqual([item.occurrence.occurrence_path for item in items],
                         [(0, 0), (1, 0)])
        self.assertEqual([item.occurrence.sequence for item in items], [0, 1])

    # ----- quality column mechanics ---------------------------------------

    def test_variable_quality_columns_unselected_missing_value(self):
        descriptors = [dict(kind="fixed", count=2, children=[field(width=8), quality(2)])]
        binding = self.archive.register_template("T", 1, descriptors)
        # One subset two members: member0 selected VALUE(0), member1 unselected.
        bits = column(8, (0,), 10, 0)
        bits += column(8, (0,), 20, 0)
        bits += column(32, (0,), 2, 0)                 # length 2
        bits += column(1, (0,), 1, 0)                  # select member0 = 1
        bits += column(1, (0,), 0, 0)                  # select member1 = 0
        bits += column(2, (0,), 0, 0)                  # word member0 = 0
        wire = make_packet(bits, [("A", DAY)], template_id="T")
        self.archive.ingest_columns("b", binding, [wire])
        items = self.archive.read_observations("b")
        self.assertEqual([(x.quality.state, x.quality.code) for x in items],
                         [(QualityState.VALUE, 0), (QualityState.UNSELECTED, None)])

    def test_quality_missing_all_ones_word(self):
        descriptors = [dict(kind="fixed", count=1, children=[field(width=8), quality(3)])]
        binding = self.archive.register_template("T", 1, descriptors)
        bits = column(8, (0,), 11, 0)
        bits += column(32, (0,), 1, 0)
        bits += column(1, (0,), 1, 0)
        bits += column(3, (0,), 7, 0)                  # all-ones word => MISSING
        wire = make_packet(bits, [("A", DAY)], template_id="T")
        self.archive.ingest_columns("b", binding, [wire])
        item = self.archive.read_observations("b")[0]
        self.assertEqual(item.quality.state, QualityState.MISSING)

    def test_no_quality_descriptor_leaves_unselected(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = column(8, (0,), 9, 0)
        wire = make_packet(bits, [("A", DAY)], template_id="T")
        self.archive.ingest_columns("b", binding, [wire])
        self.assertEqual(self.archive.read_observations("b")[0].quality.state,
                         QualityState.UNSELECTED)

    # ----- header and decode error boundary -------------------------------

    def test_header_subset_count_bounds(self):
        binding = self.archive.register_template("T", 1, [])
        for count in (0, 17):
            head = (b"WOBS\x02\x01T" + (1).to_bytes(4, "big") + bytes([count]))
            with self.assertRaises(DecodeError):
                self.archive.ingest_columns("n" + str(count), binding, [head])

    def test_header_identifier_and_date_validation(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = column(8, (0,), 1, 0)
        # Bad date (February 30).
        wire = make_packet(bits, [("A", "2026-02-30")], template_id="T")
        with self.assertRaises(DecodeError):
            self.archive.ingest_columns("date", binding, [wire])
        # Zero-length station id.
        head = b"WOBS\x02\x01T" + (1).to_bytes(4, "big") + bytes([1]) + b"\x00"
        with self.assertRaises(DecodeError):
            self.archive.ingest_columns("station", binding, [head])

    def test_version_one_rejected_by_columns_entry(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        wire = make_packet(column(8, (0,), 7, 0), [("A", DAY)],
                           template_id="T", version=1)
        with self.assertRaises(DecodeError):
            self.archive.ingest_columns("v1", binding, [wire])

    def test_column_k_wider_than_scalar_rejected(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = uint(7, 8) + uint(9, 6)              # k=9 > w=8
        wire = make_packet(bits, [("A", DAY)], template_id="T")
        before = self.counts()
        with self.assertRaises(DecodeError):
            self.archive.ingest_columns("kw", binding, [wire])
        self.assertEqual(self.counts(), before)

    def test_column_overflow_rejected(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = raw_column(8, 250, 4, (10,))       # 260 >= 256
        wire = make_packet(bits, [("A", DAY)], template_id="T")
        with self.assertRaises(DecodeError):
            self.archive.ingest_columns("ov", binding, [wire])

    def test_reserved_delayed_count_rejected(self):
        descriptors = [dict(kind="delayed", count_width=2, children=[field(width=8)])]
        binding = self.archive.register_template("T", 1, descriptors)
        bits = column(2, (0,), 3, 0)                # all-ones reserved count
        wire = make_packet(bits, [("A", DAY)], template_id="T")
        with self.assertRaises(DecodeError):
            self.archive.ingest_columns("reserved", binding, [wire])

    def test_truncation_at_every_byte_boundary(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        full = make_packet(column(8, (0,), 7, 0), [("A", DAY)], template_id="T")
        before = self.counts()
        for cut in range(len(full)):
            with self.subTest(cut=cut), self.assertRaises(DecodeError):
                self.archive.ingest_columns("cut", binding, [full[:cut]])
        self.assertEqual(self.counts(), before)
        with self.assertRaises(KeyError):
            self.archive.read_observations("cut")

    def test_nonzero_padding_and_trailing_byte(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = column(8, (0,), 7, 0)                # 14 bits -> 2 pad bits
        padded = bits + "10"
        bad = (make_header([("A", DAY)], template_id="T")
               + len(bits).to_bytes(4, "big") + pack_bits(padded))
        with self.assertRaises(DecodeError):
            self.archive.ingest_columns("pad", binding, [bad])
        full = make_packet(bits, [("A", DAY)], template_id="T")
        with self.assertRaises(DecodeError):
            self.archive.ingest_columns("trail", binding, [full + b"\x00"])

    def test_declared_payload_bit_count_must_match(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = column(8, (0,), 7, 0)                # exactly 14 payload bits
        for declared in (13, 15, 16):
            with self.subTest(declared=declared), self.assertRaises(DecodeError):
                self.archive.ingest_columns(
                    "m" + str(declared), binding,
                    [make_packet(bits, [("A", DAY)], template_id="T",
                                 declared_bits=declared)])

    def test_station_identifier_length_bounds(self):
        binding = self.archive.register_template("T", 1, [])
        for length in (0, 64):
            station = "S" * length
            head = (b"WOBS\x02" + bytes([length]) + station.encode("ascii")
                    + (1).to_bytes(4, "big") + bytes([1]))
            with self.subTest(length=length), self.assertRaises(DecodeError):
                self.archive.ingest_columns("id" + str(length), binding, [head])

    def test_quality_length_mismatch_is_observation_error(self):
        descriptors = [field(width=8), quality(2)]
        binding = self.archive.register_template("T", 1, descriptors)
        bits = column(8, (0,), 7, 0)
        bits += column(32, (0,), 2, 0)              # claims 2, only 1 member
        wire = make_packet(bits, [("A", DAY)], template_id="T")
        before = self.counts()
        with self.assertRaises(ObservationError):
            self.archive.ingest_columns("ql", binding, [wire])
        self.assertEqual(self.counts(), before)

    # ----- binding, replay, conflict, rollback ----------------------------

    def test_binding_must_be_registered_and_match_header(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        bits = column(8, (0,), 7, 0)
        with self.assertRaises(ObservationError):
            self.archive.ingest_columns(
                "forged", replace(binding, fingerprint="wrong"),
                [make_packet(bits, [("A", DAY)], template_id="T")])
        with self.assertRaises(ObservationError):
            self.archive.ingest_columns(
                "absent", replace(binding, template_id="X"),
                [make_packet(bits, [("A", DAY)], template_id="X")])
        with self.assertRaises(ObservationError):
            self.archive.ingest_columns(
                "revision", binding,
                [make_packet(bits, [("A", DAY)], template_id="T", revision=2)])

    def test_replay_returns_same_receipt_and_conflicts_on_change(self):
        case = CASES[0]
        binding = self.archive.register_template(
            "COL", case["revision"], deepcopy(case["descriptors"]))
        wire = packet(case)
        first = self.archive.ingest_columns("batch", binding, [wire])
        # Byte-by-byte chunks still fully consume the source and replay.
        again = self.archive.ingest_columns(
            "batch", binding, (bytes([byte]) for byte in wire))
        self.assertEqual(again, first)
        before = self.counts()
        mutated = bytearray(wire)
        mutated[-1] ^= 1
        with self.assertRaises(ConflictError):
            self.archive.ingest_columns("batch", binding, [bytes(mutated)])
        self.assertEqual(self.counts(), before)

    def test_source_exception_is_preserved_and_not_published(self):
        case = CASES[0]
        binding = self.archive.register_template(
            "COL", case["revision"], deepcopy(case["descriptors"]))
        wire = packet(case)
        error = RuntimeError("recorded source failed")

        def source():
            yield wire[:12]
            raise error

        before = self.counts()
        with self.assertRaises(RuntimeError) as caught:
            self.archive.ingest_columns("broken", binding, source())
        self.assertIs(caught.exception, error)
        self.assertEqual(self.counts(), before)

    def test_sqlite_publish_failure_rolls_back_whole_packet(self):
        case = CASES[0]
        binding = self.archive.register_template(
            "COL", case["revision"], deepcopy(case["descriptors"]))
        before = self.counts()
        self.archive.db.execute(
            "CREATE TEMP TRIGGER reject_receipt BEFORE INSERT ON receipts "
            "WHEN NEW.batch_id='failed' BEGIN SELECT RAISE(ABORT,'storage failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.archive.ingest_columns("failed", binding, [packet(case)])
        self.assertEqual(self.counts(), before)
        for table in ("batches", "observations", "receipts"):
            self.assertEqual(self.archive.db.execute(
                "SELECT count(*) FROM " + table + " WHERE batch_id='failed'").fetchone()[0], 0)
        # The same packet can still be published under a fresh id.
        self.assertEqual(
            self.archive.ingest_columns("ok", binding, [packet(case)]).observation_count, 3)

    def test_reopen_preserves_subset_identity_without_redecode(self):
        case = CASES[1]
        binding = self.archive.register_template(
            "COL", case["revision"], deepcopy(case["descriptors"]))
        self.archive.ingest_columns("scopes", binding, [packet(case)])
        saved = self.archive.read_observations("scopes")
        daily = self.archive.build_daily("scopes-day", ["scopes"])
        self.archive.close()
        reopened = Archive(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.read_observations("scopes"), saved)
        self.assertEqual(reopened.read_daily("scopes-day"), daily)
        # subset_index is genuinely persisted, not recomputed.
        self.assertEqual([x.subset_index for x in reopened.read_observations("scopes")],
                         [x.subset_index for x in saved])

    def test_mixed_v1_v2_daily_and_legacy_subset_index_none(self):
        import json
        example = json.loads(
            (Path(__file__).resolve().parents[1] / "examples/day.json").read_text())
        v1_binding = self.archive.register_template(
            example["template_id"], example["revision"], example["descriptors"])
        self.archive.ingest("v1", v1_binding, [bytes.fromhex(example["packet_hex"])])
        self.assertTrue(all(
            x.subset_index is None for x in self.archive.read_observations("v1")))
        case = CASES[1]
        v2_binding = self.archive.register_template(
            "COL", case["revision"], deepcopy(case["descriptors"]))
        self.archive.ingest_columns("v2", v2_binding, [packet(case)])
        result = self.archive.build_daily(
            "mixed", ["v1", "v2"], {"allow_unselected": True})
        stations = {g.station for g in result.groups}
        self.assertIn("COAST_A", stations)
        self.assertIn("A", stations)
        # Same-shape subsets are not deduplicated by the daily aggregation.
        v2_items = self.archive.read_observations("v2")
        self.assertEqual(len(v2_items), 9)


if __name__ == "__main__":
    unittest.main()
