from fractions import Fraction
import json
from pathlib import Path
import tempfile

from weather.archive.imports import Archive
from columns_cases import CASES, packet as columns_packet
from copy import deepcopy


def run_v1(database):
    example = json.loads((Path(__file__).resolve().parent / "examples/day.json").read_text())
    wire = bytes.fromhex(example["packet_hex"])
    with Archive(database) as archive:
        binding = archive.register_template(example["template_id"], example["revision"],
                                            example["descriptors"])
        receipt = archive.ingest("morning", binding, [wire[i:i + 3] for i in range(0, len(wire), 3)])
        observations = archive.read_observations("morning")
        result = archive.build_daily("daily", ["morning"])
        expected = example["expected"]
        actual = {
            "raw": [item.raw for item in observations],
            "paths": [list(item.occurrence.template_path) for item in observations],
            "occurrences": [list(item.occurrence.occurrence_path) for item in observations],
            "group_parents": [list(item.occurrence.group.parent_occurrence) for item in observations],
            "values": [None if item.value is None else str(item.value) for item in observations],
            "quality": [[item.quality.state.value, item.quality.code] for item in observations],
        }
        downstream = dict(input_count=result.groups[0].input_count,
                          accepted_count=result.groups[0].accepted_count,
                          exact_sum=str(result.groups[0].exact_sum), mean=str(result.groups[0].mean))
        observation_expected = {key: expected[key] for key in actual}
        daily_expected = {key: expected[key] for key in downstream}
        assert actual == observation_expected
        assert downstream == daily_expected
        assert receipt.observation_count == 3
        assert result.groups[0].mean == Fraction(2)
        assert archive.ingest("morning", binding, [wire]) == receipt
    with Archive(database) as reopened:
        assert reopened.read_observations("morning") == observations
        assert reopened.read_daily("daily") == result
    return observation_expected, downstream, example


def run_columns(database):
    """Real WOBS2 ingest: unequal replication and same-shape subsets."""
    saved = {}
    with Archive(database) as archive:
        for case in CASES:
            binding = archive.register_template(
                "COL", case["revision"], deepcopy(case["descriptors"]))
            wire = columns_packet(case)
            receipt = archive.ingest_columns(case["name"], binding, [wire])
            # Replay with one-byte chunks still fully consumes the source.
            assert archive.ingest_columns(
                case["name"], binding,
                (bytes([byte]) for byte in wire)) == receipt
            observations = archive.read_observations(case["name"])
            daily = archive.build_daily(case["name"] + "-daily", [case["name"]])
            saved[case["name"]] = (binding, observations, daily, receipt)
        # Unequal replication: ragged only produces observations in subset 1.
        ragged = saved["ragged"][1]
        assert [item.subset_index for item in ragged] == [1, 1, 1]
        assert [item.occurrence.sequence for item in ragged] == [0, 1, 2]
        # Same-shape subsets 0 and 1 share station A/day but stay independent.
        scopes = saved["scopes"][1]
        station_a = [item for item in scopes if item.station == "A"]
        assert len(station_a) == 8
        assert [item.occurrence.sequence for item in scopes] == list(range(9))
    with Archive(database) as reopened:
        for name, (_binding, observations, daily, _receipt) in saved.items():
            assert reopened.read_observations(name) == observations
            assert reopened.read_daily(name + "-daily") == daily
            assert all(item.subset_index is not None
                       for item in reopened.read_observations(name))
    summary = {}
    for name, (_binding, observations, daily, _receipt) in saved.items():
        summary[name] = {
            "observation_count": len(observations),
            "subset_indexes": [item.subset_index for item in observations],
            "stations": sorted({(item.station, item.day.isoformat())
                                for item in observations}),
            "daily": [(group.station, group.day.isoformat(), group.input_count,
                       group.accepted_count, str(group.exact_sum), str(group.mean))
                      for group in daily.groups],
        }
    return summary


def main():
    with tempfile.TemporaryDirectory(prefix="weather-demo-") as directory:
        database = str(Path(directory) / "archive.sqlite")
        observation_expected, downstream, example = run_v1(database)
        columns_summary = run_columns(database)
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "V3.dynamic_paths+V4.scoped_members+V8",
            "input": {"template": example["descriptors"], "packet_hex": example["packet_hex"]},
            "expected": observation_expected, "actual": observation_expected,
            "downstream_expected": downstream, "downstream_actual": downstream,
            "code_paths": ["weather/decoding/groups.py", "weather/observations/assemble.py",
                           "weather/observations/quality.py", "weather/archive/publication.py",
                           "weather/daily/aggregate.py", "weather/daily/results.py"],
            "oracle": example["oracle"],
        }, sort_keys=True))
        print("COLUMNS_SCENARIO " + json.dumps(columns_summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
