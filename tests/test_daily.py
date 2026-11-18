from fractions import Fraction
import sqlite3

from test_ingest import WeatherCase, field, packet, uint, quality
from weather.archive.imports import Archive
from weather.archive.schema import ConflictError


class TestDaily(WeatherCase):
    def test_exact_quality_filtered_mean(self):
        data, binding, wire = self.register_sample()
        self.archive.ingest("coast", binding, [wire])
        result = self.archive.build_daily("daily", ["coast"])
        group = result.groups[0]
        self.assertEqual((group.input_count, group.accepted_count, group.exact_sum, group.mean),
                         (3, 2, Fraction(4), Fraction(2)))
        self.assertEqual((group.observation_missing, group.unselected,
                          group.quality_missing, group.quality_rejected), (1, 0, 0, 0))
        excluded = self.archive.build_daily("excluded", ["coast"], {"accepted_codes": []})
        self.assertEqual((excluded.groups[0].exact_sum, excluded.groups[0].mean),
                         (Fraction(0), None))
        self.assertEqual(excluded.groups[0].input_count, 3)
        self.assertEqual(excluded.groups[0].quality_rejected, 2)

    def test_fixed_inputs(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        for batch, value in (("b1", 1), ("b2", 99)):
            self.archive.ingest(batch, binding, [packet(uint(value, 8))])
        rule = {"allow_unselected": True, "accepted_codes": [2, 0, 2]}
        first = self.archive.build_daily("one", ["b1", "b1"], rule)
        self.assertEqual(first.groups[0].mean, Fraction(1))
        self.assertEqual([pair[0] for pair in first.input_binding.batches], ["b1"])
        self.assertEqual(self.archive.build_daily("one", ["b1"], {
            "accepted_codes": [0, 2], "allow_unselected": True}), first)
        self.archive.ingest("b3", binding, [packet(uint(200, 8))])
        self.assertEqual(self.archive.build_daily("one", ["b1"], rule), first)
        fresh = self.archive.build_daily("fresh", ["b1"], rule)
        self.assertEqual(fresh.groups, first.groups)
        both = self.archive.build_daily("both", ["b2", "b1", "b2"], rule)
        self.assertEqual(both.groups[0].mean, Fraction(50))
        self.assertEqual(self.archive.build_daily("both", ["b1", "b2"], rule), both)
        before = self.counts()
        with self.assertRaises(ConflictError):
            self.archive.build_daily("one", ["b1", "b2"], rule)
        with self.assertRaises(ConflictError):
            self.archive.build_daily("one", ["b1"], {})
        with self.assertRaises(KeyError):
            self.archive.build_daily("missing", ["unknown"])
        self.assertEqual(self.counts(), before)
        self.archive.close()
        self.archive = Archive(self.path)
        self.assertEqual(self.archive.read_daily("one"), first)
        self.assertEqual(self.archive.read_daily("both"), both)
        with self.assertRaises(KeyError):
            self.archive.read_daily("unknown")

    def test_quality_filter_and_rule_types(self):
        binding = self.archive.register_template("T", 1, [
            dict(kind="fixed", count=5, children=[
                field(width=8), dict(kind="quality", width=2)])])
        bits = "".join(uint(value, 8) for value in (255, 2, 3, 4, 5))
        bits += quality("10111", [0, 3, 0, 2])
        self.archive.ingest("input", binding, [packet(bits)])
        group = self.archive.build_daily("strict", ["input"]).groups[0]
        self.assertEqual((group.input_count, group.accepted_count, group.mean), (5, 1, Fraction(4)))
        self.assertEqual((group.observation_missing, group.unselected,
                          group.quality_missing, group.quality_rejected), (1, 1, 1, 1))
        all_allowed = self.archive.build_daily("allowed", ["input"], {
            "allow_unselected": True, "allow_quality_missing": True, "accepted_codes": [0, 2]})
        self.assertEqual((all_allowed.groups[0].accepted_count, all_allowed.groups[0].exact_sum,
                          all_allowed.groups[0].mean), (4, Fraction(14), Fraction(7, 2)))
        only_unselected = self.archive.build_daily("unselected", ["input"], {
            "accepted_codes": [], "allow_unselected": True}).groups[0]
        only_missing = self.archive.build_daily("qmissing", ["input"], {
            "accepted_codes": [], "allow_quality_missing": True}).groups[0]
        self.assertEqual((only_unselected.accepted_count, only_unselected.mean), (1, Fraction(2)))
        self.assertEqual((only_missing.accepted_count, only_missing.mean), (1, Fraction(3)))
        before = self.counts()
        for rule in ({"extra": True}, {"accepted_codes": [True]},
                     {"accepted_codes": [-1]}, {"accepted_codes": [0.0]},
                     {"accepted_codes": "0"}, {"allow_unselected": 1},
                     {"allow_quality_missing": None}, []):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                self.archive.build_daily("invalid", ["input"], rule)
        self.assertEqual(self.counts(), before)

    def test_exact_groups_empty_and_sorting(self):
        binding = self.archive.register_template("T", 1, [
            dict(kind="fixed", count=2, children=[field(width=8, scale=1)])])
        wire = packet(uint(1, 8) + uint(2, 8))
        self.archive.ingest("fraction", binding, [wire])
        group = self.archive.build_daily("fractions", ["fraction"], {
            "allow_unselected": True}).groups[0]
        self.assertEqual((group.exact_sum, group.mean), (Fraction(3, 10), Fraction(3, 20)))
        empty_binding = self.archive.register_template("EMPTY", 1, [
            dict(kind="fixed", count=0, children=[field(width=8)])])
        self.archive.ingest("zero", empty_binding, [packet("", template_id="EMPTY")])
        self.assertEqual(self.archive.build_daily("zero-result", ["zero"]).groups, ())
        self.assertEqual(self.archive.build_daily("no-input", []).groups, ())
        multi = self.archive.register_template("MULTI", 1, [
            field("pressure", 8, "Pa"), field(width=8)])
        for batch, station, day in (("z", "Z", "2026-11-17"),
                                    ("a-late", "A", "2026-11-18"),
                                    ("a-early", "A", "2026-11-17")):
            self.archive.ingest(batch, multi, [packet(uint(2, 8) + uint(4, 8),
                template_id="MULTI", station=station, day=day)])
        result = self.archive.build_daily("sorted", ["z", "a-late", "a-early"], {
            "allow_unselected": True})
        keys = [(g.station, g.measurement, g.day.isoformat()) for g in result.groups]
        self.assertEqual(keys, [
            ("A", "pressure", "2026-11-17"), ("A", "pressure", "2026-11-18"),
            ("A", "wind_speed", "2026-11-17"), ("A", "wind_speed", "2026-11-18"),
            ("Z", "pressure", "2026-11-17"), ("Z", "wind_speed", "2026-11-17")])

    def test_daily_atomic_publication(self):
        binding = self.archive.register_template("T", 1, [field(width=8)])
        self.archive.ingest("b", binding, [packet(uint(2, 8))])
        old = self.archive.build_daily("old", ["b"], {"allow_unselected": True})
        before = self.counts()
        self.archive.db.execute(
            "CREATE TEMP TRIGGER reject_daily BEFORE INSERT ON daily_groups "
            "WHEN NEW.result_id='failed' BEGIN SELECT RAISE(ABORT,'daily storage failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.archive.build_daily("failed", ["b"], {"allow_unselected": True})
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.archive.read_daily("old"), old)
        with self.assertRaises(KeyError):
            self.archive.read_daily("failed")
        self.archive.close()
        self.archive = Archive(self.path)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.archive.read_daily("old"), old)
