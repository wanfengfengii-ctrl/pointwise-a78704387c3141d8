import json

from weather.templates.bindings import make_binding, canonical_descriptor_bytes
from weather.archive.schema import ConflictError, initialize_archive


class TemplateCatalog:
    def __init__(self, connection):
        self.connection = connection
        initialize_archive(connection)

    def register_template(self, template_id, revision, descriptors):
        binding = make_binding(template_id, revision, descriptors)
        definition = canonical_descriptor_bytes(binding.descriptors).decode("ascii")
        with self.connection:
            row = self.connection.execute(
                "SELECT definition FROM templates WHERE template_id=? AND revision=?",
                (template_id, revision)).fetchone()
            if row is not None:
                if row[0] != definition:
                    raise ConflictError("template revision already has different content")
            else:
                self.connection.execute(
                    "INSERT INTO templates VALUES (?,?,?,?)",
                    (template_id, revision, definition, binding.fingerprint))
        return binding

    def get_binding(self, template_id, revision):
        row = self.connection.execute(
            "SELECT definition FROM templates WHERE template_id=? AND revision=?",
            (template_id, revision)).fetchone()
        if row is None:
            raise KeyError((template_id, revision))
        return make_binding(template_id, revision, json.loads(row[0]))
