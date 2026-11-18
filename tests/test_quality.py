from dataclasses import replace

from test_ingest import WeatherCase, field, packet, uint, quality, chunks
from weather.decoding.bits import BitReader
from weather.decoding.header import decode_header
from weather.decoding.groups import expand_template
from weather.decoding.events import RawObservationEvent, RawQualityEvent
from weather.observations.assemble import assemble_observations
from weather.observations.validate import validate_batch_observations
from weather.observations.models import ObservationError, QualityState


class TestQuality(WeatherCase):
    def test_three_quality_states(self):
        binding = self.archive.register_template("T", 1, [
            dict(kind="fixed", count=3, children=[
                field("pressure", 8, "Pa"), dict(kind="quality", width=2)])])
        wire = packet("".join(uint(v, 8) for v in (10, 20, 30)) + quality("011", [3, 0]))
        self.archive.ingest("states", binding, chunks(wire, 1))
        records = self.archive.read_observations("states")
        self.assertEqual([(item.quality.state, item.quality.code) for item in records],
                         [(QualityState.UNSELECTED, None), (QualityState.MISSING, None),
                          (QualityState.VALUE, 0)])
        self.assertEqual([item.raw for item in records], [10, 20, 30])
        self.archive.close()
        from weather.archive.imports import Archive
        self.archive = Archive(self.path)
        self.assertEqual(self.archive.read_observations("states"), records)

    def test_scoped_members_and_missing(self):
        inner = dict(kind="delayed", count_width=2, children=[
            field(width=8), dict(kind="quality", width=2)])
        binding = self.archive.register_template("T", 1, [
            dict(kind="fixed", count=2, children=[
                field(width=8), inner, dict(kind="quality", width=2)])])
        bits = uint(5, 8) + uint(0, 2) + quality("")
        bits += uint(6, 8) + uint(1, 2) + uint(255, 8) + quality("1", [0])
        bits += quality("11", [1, 2])
        self.archive.ingest("scope", binding, [packet(bits)])
        records = self.archive.read_observations("scope")
        self.assertEqual([item.raw for item in records], [5, 6, 255])
        self.assertEqual([item.quality.code for item in records], [1, 2, 0])
        self.assertEqual([item.occurrence.occurrence_path for item in records],
                         [(0,), (1,), (1, 0)])
        self.assertEqual([item.occurrence.group.template_path for item in records],
                         [(0,), (0,), (0, 1)])
        self.assertTrue(records[2].missing)
        binding = self.archive.register_template("MID", 1, [
            dict(kind="fixed", count=3, children=[
                field(width=8), dict(kind="quality", width=2)])])
        bits = "".join(uint(v, 8) for v in (10, 255, 30)) + quality("011", [2, 0])
        self.archive.ingest("middle", binding, [packet(bits, template_id="MID")])
        middle = self.archive.read_observations("middle")
        self.assertEqual([item.quality.code for item in middle], [None, 2, 0])
        self.assertEqual([item.missing for item in middle], [False, True, False])

    def test_reference_out_of_group(self):
        group = dict(kind="fixed", count=1, children=[
            field(width=8), dict(kind="quality", width=2)])
        binding = self.archive.register_template("T", 1, [group, group])
        valid = packet(uint(10, 8) + quality("1", [0]) + uint(20, 8) + quality("1", [0]))
        self.archive.ingest("old", binding, [valid])
        before = self.counts()
        old = self.archive.read_observations("old")
        invalid = packet(uint(10, 8) + quality("1", [0])
                         + uint(20, 8) + quality("01", [0]))
        with self.assertRaises(ObservationError):
            self.archive.ingest("outside", binding, [invalid])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.archive.read_observations("old"), old)
        with self.assertRaises(KeyError):
            self.archive.read_observations("outside")

    def test_event_integrity(self):
        _, binding, wire = self.register_sample()
        reader = BitReader([wire])
        header = decode_header(reader)
        events = expand_template(reader, binding)
        records = assemble_observations(events, header)
        validate_batch_observations(events, records, binding)
        position = next(i for i, event in enumerate(events) if isinstance(event, RawQualityEvent))
        duplicate = events[:position + 1] + (events[position],) + events[position + 1:]
        with self.assertRaises(ObservationError):
            assemble_observations(duplicate, header)
        with self.assertRaises(ObservationError):
            assemble_observations(events[:-1], header)
        with self.assertRaises(ObservationError):
            validate_batch_observations(events, records[:-1], binding)
        altered = (replace(records[0], occurrence=records[1].occurrence),) + records[1:]
        with self.assertRaises(ObservationError):
            validate_batch_observations(events, altered, binding)
        index = next(i for i, event in enumerate(events) if isinstance(event, RawObservationEvent))
        identity = replace(events[index].occurrence, occurrence_path=(9, 0))
        broken = events[:index] + (replace(events[index], occurrence=identity),) + events[index + 1:]
        candidates = assemble_observations(broken, header)
        with self.assertRaises(ObservationError):
            validate_batch_observations(broken, candidates, binding)
