from hashlib import sha256
import json
import sqlite3

from weather.templates.catalog import TemplateCatalog
from weather.templates.bindings import TemplateBinding
from weather.decoding.bits import BitReader
from weather.decoding.header import decode_header, verify_header_binding
from weather.decoding.groups import expand_template
from weather.decoding.columns import decode_column_packet
from weather.observations.models import ObservationError
from weather.observations.assemble import assemble_observations
from weather.observations.validate import validate_batch_observations
from weather.archive.publication import find_receipt, require_same_batch, publish_batch
from weather.archive.queries import ObservationQueries
from weather.archive.schema import initialize_archive


def compute_input_digest(binding, packet_bytes):
    identity = json.dumps([binding.template_id, binding.revision, binding.fingerprint],
                          separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return sha256(len(identity).to_bytes(4, "big") + identity + packet_bytes).hexdigest()


class Archive:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        try:
            initialize_archive(self.db)
            self.catalog = TemplateCatalog(self.db)
            self.queries = ObservationQueries(self.db)
            from weather.daily.results import DailyResults
            self.daily = DailyResults(self.db, self.queries)
        except BaseException:
            self.db.close()
            raise

    def register_template(self, template_id, revision, descriptors):
        return self.catalog.register_template(template_id, revision, descriptors)

    def _require_registered_binding(self, template_binding):
        if (not isinstance(template_binding, TemplateBinding)
                or type(template_binding.revision) is not int
                or not isinstance(template_binding.template_id, str)):
            raise ObservationError("registered template binding required")
        try:
            registered = self.catalog.get_binding(template_binding.template_id,
                                                  template_binding.revision)
        except (KeyError, TypeError, sqlite3.InterfaceError, sqlite3.ProgrammingError) as error:
            raise ObservationError("unregistered template binding") from error
        if registered != template_binding:
            raise ObservationError("forged template binding")
        return registered

    def ingest(self, batch_id, template_binding, byte_chunks):
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError("batch id must be nonempty text")
        # Materialize privately; no database transaction is held over source iteration.
        reader = BitReader(byte_chunks)
        registered = self._require_registered_binding(template_binding)
        digest = compute_input_digest(registered, reader.data)
        existing = find_receipt(self.db, batch_id)
        if existing is not None:
            return require_same_batch(existing, digest)
        header = decode_header(reader)
        verify_header_binding(header, registered)
        events = expand_template(reader, registered)
        reader.finish_payload()
        observations = assemble_observations(events, header)
        validate_batch_observations(events, observations, registered)
        return publish_batch(self.db, batch_id, registered, digest, header, observations)

    def ingest_columns(self, batch_id, template_binding, byte_chunks):
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError("batch id must be nonempty text")
        # The whole packet is staged privately; a failing subset is never
        # published because publication happens only after every subset decodes.
        reader = BitReader(byte_chunks)
        registered = self._require_registered_binding(template_binding)
        digest = compute_input_digest(registered, reader.data)
        existing = find_receipt(self.db, batch_id)
        if existing is not None:
            return require_same_batch(existing, digest)
        header, observations = decode_column_packet(reader, registered)
        return publish_batch(self.db, batch_id, registered, digest, header, observations)

    def read_observations(self, batch_id):
        return self.queries.read_observations(batch_id)

    def build_daily(self, result_id, batch_ids, quality_rule=None):
        return self.daily.build_daily(result_id, batch_ids, quality_rule)

    def read_daily(self, result_id):
        return self.daily.read_daily(result_id)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
