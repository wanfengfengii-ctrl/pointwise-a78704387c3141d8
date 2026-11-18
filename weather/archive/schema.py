SCHEMA_VERSION = 1


class ConflictError(ValueError):
    pass


def initialize_archive(connection):
    if connection.in_transaction:
        raise ValueError("initialize requires an idle connection")
    connection.execute("PRAGMA foreign_keys=ON")
    statements = (
        "CREATE TABLE IF NOT EXISTS archive_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)",
        "CREATE TABLE IF NOT EXISTS templates (template_id TEXT NOT NULL, revision INTEGER NOT NULL, definition TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY(template_id,revision))",
        "CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, template_id TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL, station TEXT NOT NULL, day TEXT NOT NULL, FOREIGN KEY(template_id,revision) REFERENCES templates(template_id,revision))",
        "CREATE TABLE IF NOT EXISTS observations (batch_id TEXT NOT NULL REFERENCES batches(batch_id), sequence INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(batch_id,sequence))",
        "CREATE TABLE IF NOT EXISTS receipts (batch_id TEXT PRIMARY KEY REFERENCES batches(batch_id), digest TEXT NOT NULL, observation_count INTEGER NOT NULL CHECK(observation_count>=0))",
        "CREATE TABLE IF NOT EXISTS daily_results (result_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, binding TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS daily_groups (result_id TEXT NOT NULL REFERENCES daily_results(result_id), position INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(result_id,position))",
    )
    with connection:
        connection.execute(statements[0])
        row = connection.execute("SELECT version FROM archive_meta WHERE singleton=1").fetchone()
        if row is not None and row[0] != SCHEMA_VERSION:
            raise ValueError("unsupported archive schema version")
        if row is None:
            connection.execute("INSERT INTO archive_meta VALUES (1,?)", (SCHEMA_VERSION,))
        for statement in statements[1:]:
            connection.execute(statement)
