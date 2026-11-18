from dataclasses import dataclass
from datetime import date
import json

from weather.archive.schema import ConflictError
from weather.archive.codecs import fraction_record, fraction_from_record
from weather.daily.plans import DailyInputBinding, freeze_inputs, binding_fingerprint
from weather.daily.rules import compile_quality_rule
from weather.daily.aggregate import DailyGroup, aggregate_daily


@dataclass(frozen=True)
class DailyResult:
    result_id: str
    input_binding: DailyInputBinding
    groups: tuple


def group_record(group):
    data = dict(vars(group))
    data["day"] = group.day.isoformat()
    data["exact_sum"] = fraction_record(group.exact_sum)
    data["mean"] = fraction_record(group.mean)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def group_from_record(record):
    data = json.loads(record)
    data["day"] = date.fromisoformat(data["day"])
    data["exact_sum"] = fraction_from_record(data["exact_sum"])
    data["mean"] = fraction_from_record(data["mean"])
    return DailyGroup(**data)


class DailyResults:
    def __init__(self, connection, queries):
        self.connection = connection
        self.queries = queries

    def read_daily(self, result_id):
        row = self.connection.execute(
            "SELECT binding FROM daily_results WHERE result_id=?", (result_id,)).fetchone()
        if row is None:
            raise KeyError(result_id)
        data = json.loads(row[0])
        binding = DailyInputBinding(tuple(tuple(pair) for pair in data["batches"]),
                                    compile_quality_rule(data["rule"]))
        groups = tuple(group_from_record(record[0]) for record in self.connection.execute(
            "SELECT record FROM daily_groups WHERE result_id=? ORDER BY position", (result_id,)))
        return DailyResult(result_id, binding, groups)

    def build_daily(self, result_id, batch_ids, quality_rule=None):
        if not isinstance(result_id, str) or not result_id:
            raise ValueError("result id must be nonempty text")
        binding = freeze_inputs(self.queries, batch_ids, quality_rule)
        fingerprint = binding_fingerprint(binding)
        row = self.connection.execute(
            "SELECT fingerprint FROM daily_results WHERE result_id=?", (result_id,)).fetchone()
        if row is not None:
            if row[0] != fingerprint:
                raise ConflictError("daily result id already has different inputs or rule")
            return self.read_daily(result_id)
        observations = (item for batch_id, _ in binding.batches
                        for item in self.queries.read_observations(batch_id))
        groups = aggregate_daily(observations, binding.rule)
        with self.connection:
            self.connection.execute("INSERT INTO daily_results VALUES (?,?,?)",
                                    (result_id, fingerprint, json.dumps(binding.record(),
                                     sort_keys=True, separators=(",", ":"))))
            self.connection.executemany("INSERT INTO daily_groups VALUES (?,?,?)",
                                        ((result_id, index, group_record(group))
                                         for index, group in enumerate(groups)))
        return self.read_daily(result_id)
