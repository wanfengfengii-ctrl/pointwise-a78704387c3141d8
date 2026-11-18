from copy import deepcopy

from test_ingest import WeatherCase, field, packet, uint
from weather.archive.schema import ConflictError
from weather.templates.descriptors import TemplateError
from weather.templates.bindings import canonical_descriptor_bytes
from weather.decoding.bits import BitReader
from weather.decoding.header import decode_header, verify_header_binding


class TestTemplates(WeatherCase):
    def test_register_and_bind(self):
        source = [field(width=10, scale=2)]
        original = deepcopy(source)
        binding = self.archive.register_template("T", 1, source)
        frozen = canonical_descriptor_bytes(binding.descriptors)
        source[0]["width"] = 2
        second = self.archive.register_template("T", 2, source)
        self.assertNotEqual(binding.fingerprint, second.fingerprint)
        self.assertEqual(canonical_descriptor_bytes(binding.descriptors), frozen)
        self.assertEqual(self.archive.catalog.get_binding("T", 1), binding)
        self.assertEqual(self.archive.register_template("T", 1, original), binding)
        reader = BitReader([packet(uint(100, 10), station="COAST_A")])
        header = decode_header(reader)
        verify_header_binding(header, binding)
        self.assertEqual((header.template_id, header.revision, header.station,
                          header.day.isoformat(), header.payload_bits),
                         ("T", 1, "COAST_A", "2026-11-17", 10))
        self.archive.ingest("r1", binding, [packet(uint(100, 10))])
        self.assertEqual(str(self.archive.read_observations("r1")[0].value), "1")

    def test_invalid_descriptor(self):
        invalid = [
            [dict(kind="unknown")], [field(width=0)], [field(width=33)],
            [field(width=True)], [field(unit="Pa")], [field(scale=1.5)],
            [dict(kind="delayed", count_width=1, children=[])],
            [dict(kind="delayed", count_width=17, children=[])],
            [dict(kind="fixed", count=-1, children=[])],
            [dict(kind="fixed", count=True, children=[])],
            [dict(kind="quality", width=2), field()],
            [dict(kind="quality", width=2), dict(kind="quality", width=2)],
            [dict(kind="quality", width=0)], [dict(kind="field", width=2)],
            [dict(kind="fixed", count=1, children=[
                dict(kind="delayed", count_width=2, children=[
                    dict(kind="fixed", count=1, children=[field()])])])],
        ]
        before = self.counts()
        for descriptors in invalid:
            with self.subTest(descriptors=descriptors), self.assertRaises(TemplateError):
                self.archive.register_template("bad", 1, descriptors)
            self.assertEqual(self.counts(), before)
        for template_id, revision in (("", 1), ("é", 1), ("X" * 64, 1), ("T", True),
                                      ("T", -1), ("T", 2**32)):
            with self.assertRaises(TemplateError):
                self.archive.register_template(template_id, revision, [field()])
        binding = self.archive.register_template("T", 1, [field()])
        with self.assertRaises(ConflictError):
            self.archive.register_template("T", 1, [field(scale=1)])
        self.assertEqual(self.archive.catalog.get_binding("T", 1), binding)
