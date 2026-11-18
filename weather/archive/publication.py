from dataclasses import dataclass

from weather.archive.schema import ConflictError
from weather.archive.codecs import observation_to_record


@dataclass(frozen=True)
class ImportReceipt:
    batch_id: str
    input_digest: str
    observation_count: int


def find_receipt(connection, batch_id):
    row = connection.execute(
        "SELECT batch_id,digest,observation_count FROM receipts WHERE batch_id=?",
        (batch_id,)).fetchone()
    return None if row is None else ImportReceipt(*row)


def require_same_batch(receipt, digest):
    if receipt.input_digest != digest:
        raise ConflictError("batch id already bound to different input")
    return receipt


def publish_batch(connection, batch_id, binding, digest, header, observations):
    with connection:
        existing = find_receipt(connection, batch_id)
        if existing is not None:
            return require_same_batch(existing, digest)
        connection.execute("INSERT INTO batches VALUES (?,?,?,?,?,?)",
                           (batch_id, binding.template_id, binding.revision, digest,
                            header.station, header.day.isoformat()))
        connection.executemany("INSERT INTO observations VALUES (?,?,?)",
                               ((batch_id, item.occurrence.sequence, observation_to_record(item))
                                for item in observations))
        connection.execute("INSERT INTO receipts VALUES (?,?,?)",
                           (batch_id, digest, len(observations)))
    return ImportReceipt(batch_id, digest, len(observations))
