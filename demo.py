from fractions import Fraction
import json
from pathlib import Path
import tempfile

from weather.archive.imports import Archive


def main():
    example = json.loads((Path(__file__).resolve().parent / "examples/day.json").read_text())
    wire = bytes.fromhex(example["packet_hex"])
    with tempfile.TemporaryDirectory(prefix="weather-demo-") as directory:
        database = str(Path(directory) / "archive.sqlite")
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
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "V3.dynamic_paths+V4.scoped_members+V8",
            "input": {"template": example["descriptors"], "packet_hex": example["packet_hex"]},
            "expected": observation_expected, "actual": actual,
            "downstream_expected": daily_expected, "downstream_actual": downstream,
            "code_paths": ["weather/decoding/groups.py", "weather/observations/assemble.py",
                           "weather/observations/quality.py", "weather/archive/publication.py",
                           "weather/daily/aggregate.py", "weather/daily/results.py"],
            "oracle": example["oracle"],
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
