from weather.archive.codecs import observation_from_record
from weather.archive.publication import find_receipt


class ObservationQueries:
    def __init__(self, connection):
        self.connection = connection

    def read_observations(self, batch_id):
        if find_receipt(self.connection, batch_id) is None:
            raise KeyError(batch_id)
        rows = self.connection.execute(
            "SELECT record FROM observations WHERE batch_id=? ORDER BY sequence",
            (batch_id,))
        return tuple(observation_from_record(row[0]) for row in rows)

    def read_batch_binding(self, batch_id):
        row = self.connection.execute(
            "SELECT b.template_id,b.revision,b.digest,b.station,b.day "
            "FROM batches b JOIN receipts r ON r.batch_id=b.batch_id "
            "WHERE b.batch_id=?", (batch_id,)).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return tuple(row)
