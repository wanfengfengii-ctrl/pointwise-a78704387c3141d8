from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest

from weather.archive.imports import Archive
from weather.decoding.bits import BitReader, DecodeError
from weather.decoding.header import decode_header
from weather.decoding.groups import expand_template
from weather.decoding.events import GroupOpened, GroupClosed, RawObservationEvent
from weather.observations.models import ObservationError


def sample():
    return json.loads((Path(__file__).resolve().parents[1] / "examples/day.json").read_text())


def field(measurement="wind_speed", width=10, unit="m/s", **properties):
    return dict(kind="field", measurement=measurement, width=width, unit=unit, **properties)


def uint(value, width):
    if not 0 <= value < 1 << width:
        raise ValueError("fixture integer outside width")
    return format(value, "0" + str(width) + "b")


def quality(bits, words=(), width=2):
    return uint(len(bits), 32) + bits + "".join(uint(word, width) for word in words)


def packet(bits, template_id="T", revision=1, station="S", day="2026-11-17"):
    # Independent fixture encoder: literal wire layout, no production encode/decode calls.
    encoded = bits + "0" * ((-len(bits)) % 8)
    payload = bytes(int(encoded[index:index + 8], 2) for index in range(0, len(encoded), 8))
    return (b"WOBS" + bytes([1, len(template_id)]) + template_id.encode("ascii")
            + revision.to_bytes(4, "big") + bytes([len(station)]) + station.encode("ascii")
            + day.encode("ascii") + len(bits).to_bytes(4, "big") + payload)


def chunks(data, size):
    return [b""] + [data[index:index + size] for index in range(0, len(data), size)] + [b""]


class WeatherCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="weather-tests-")
        self.path = str(Path(self.directory.name) / "archive.sqlite")
        self.archive = Archive(self.path)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(lambda: self.archive.close())

    def register_sample(self):
        data = sample()
        binding = self.archive.register_template(data["template_id"], data["revision"],
                                                 data["descriptors"])
        return data, binding, bytes.fromhex(data["packet_hex"])

    def counts(self):
        return tuple(self.archive.db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                     for table in ("templates", "batches", "observations", "receipts",
                                   "daily_results", "daily_groups"))


class TestIngest(WeatherCase):
    def test_nested_occurrences(self):
        data, binding, wire = self.register_sample()
        receipt = self.archive.ingest("nested", binding, [wire])
        records = self.archive.read_observations("nested")
        self.assertEqual(receipt.observation_count, 3)
        self.assertEqual([item.raw for item in records], [100, 300, 1023])
        self.assertEqual([item.occurrence.template_path for item in records], [(1, 0, 0)] * 3)
        self.assertEqual([item.occurrence.occurrence_path for item in records],
                         [(0, 0), (1, 0), (1, 1)])
        self.assertEqual([item.occurrence.group.parent_occurrence for item in records],
                         [(0,), (1,), (1,)])
        self.assertEqual([item.occurrence.sequence for item in records], [0, 1, 2])
        self.assertEqual([item.value for item in records], [Fraction(1), Fraction(3), None])
        self.assertTrue(records[2].missing)
        reader = BitReader([wire])
        decode_header(reader)
        events = expand_template(reader, binding)
        reader.finish_payload()
        opens = [event for event in events if isinstance(event, GroupOpened)]
        closes = [event for event in events if isinstance(event, GroupClosed)]
        self.assertEqual([event.count for event in opens], [1, 0, 2, 1, 2])
        self.assertEqual({event.key for event in opens}, {event.key for event in closes})
        self.assertEqual(len([event for event in events if isinstance(event, RawObservationEvent)]), 3)
        for size in (1, 2, 5, 11):
            self.archive.ingest("split-" + str(size), binding, chunks(wire, size))
            self.assertEqual(self.archive.read_observations("split-" + str(size)), records)

    def test_adjacent_zero_and_two(self):
        descriptors = [dict(kind="delayed", count_width=2, children=[
            field(width=5), dict(kind="quality", width=2)])] * 2
        binding = self.archive.register_template("T", 1, descriptors)
        bits = uint(0, 2) + quality("") + uint(2, 2) + uint(4, 5) + uint(9, 5)
        bits += quality("11", [0, 2])
        self.archive.ingest("adjacent", binding, chunks(packet(bits), 1))
        records = self.archive.read_observations("adjacent")
        self.assertEqual([item.occurrence.template_path for item in records], [(1, 0)] * 2)
        self.assertEqual([item.occurrence.occurrence_path for item in records], [(0,), (1,)])
        self.assertEqual([item.quality.code for item in records], [0, 2])

    def test_exact_units(self):
        descriptors = [
            field("wind_speed", 8, "m/s", scale=1),
            field("air_temperature", 8, "degF", reference=-32),
            field("air_temperature", 8, "degC", reference=-4, scale=1),
            field("pressure", 8, "hPa", scale=-1),
            field("precipitation", 8, "mm", reference=1),
            field("wind_speed", 8, "knot"),
            field("air_temperature", 8, "K"),
            field("precipitation", 8, "m"),
            field("pressure", 8, "Pa"),
        ]
        binding = self.archive.register_template("T", 1, descriptors)
        self.archive.ingest("units", binding, [packet("".join(uint(v, 8)
                            for v in (1, 64, 1, 2, 2, 1, 255, 2, 3)))])
        records = self.archive.read_observations("units")
        self.assertEqual([item.value for item in records], [
            Fraction(1, 10), Fraction(27315, 100), Fraction(5457, 20),
            Fraction(2000), Fraction(3, 1000), Fraction(463, 900), None,
            Fraction(2), Fraction(3)])
        self.assertEqual([item.unit for item in records],
                         ["m/s", "K", "K", "Pa", "m", "m/s", "K", "m", "Pa"])
        self.assertEqual(records[0].value.denominator, 10)

    def test_truncation_and_source_error(self):
        _, binding, wire = self.register_sample()
        self.archive.ingest("old", binding, [wire])
        old = self.archive.read_observations("old")
        counts = self.counts()
        for index in range(len(wire)):
            with self.subTest(cut=index), self.assertRaises(DecodeError):
                self.archive.ingest("cut", binding, [wire[:index]])
            self.assertEqual(self.counts(), counts)
        for broken in (wire + b"\x00", wire[:-1] + bytes([wire[-1] | 1]),
                       b"FAIL" + wire[4:], wire[:4] + b"\x02" + wire[5:]):
            with self.assertRaises(DecodeError):
                self.archive.ingest("broken", binding, [broken])
        for prefix, batch_id in ((wire[:8], "partial"), (wire, "complete"), (wire, "old")):
            error = RuntimeError("recorded source failed")
            def source():
                yield prefix
                raise error
            with self.assertRaises(RuntimeError) as caught:
                self.archive.ingest(batch_id, binding, source())
            self.assertIs(caught.exception, error)
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.archive.read_observations("old"), old)
        with self.assertRaises(KeyError):
            self.archive.read_observations("cut")

    def test_header_counts_and_binding(self):
        binding = self.archive.register_template("T", 1, [
            dict(kind="delayed", count_width=2, children=[field(width=5)])])
        before = self.counts()
        with self.assertRaises(DecodeError):
            self.archive.ingest("reserved", binding, [packet("11")])
        with self.assertRaises(DecodeError):
            self.archive.ingest("date", binding, [packet("00", day="2026-02-30")])
        with self.assertRaises(DecodeError):
            self.archive.ingest("extra", binding, [packet("000")])
        with self.assertRaises(DecodeError):
            self.archive.ingest("short-value", binding, [packet("01")])
        with self.assertRaises(ObservationError):
            self.archive.ingest("wrong", binding, [packet("00", revision=2)])
        with self.assertRaises(ObservationError):
            self.archive.ingest("forged", replace(binding, fingerprint="wrong"), [packet("00")])
        with self.assertRaises(ObservationError):
            self.archive.ingest("absent", replace(binding, template_id="X"), [packet("00")])
        self.assertEqual(self.counts(), before)
