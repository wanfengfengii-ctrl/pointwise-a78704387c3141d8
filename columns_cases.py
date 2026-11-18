"""Literal WOBS2 columns and hand-accounted outcomes; no template traversal/decoder."""
from copy import deepcopy
from fractions import Fraction
import argparse
import json
from pathlib import Path
import tempfile

DAY = "2026-11-17"


def field(width=8, scale=0):
    return dict(kind="field", measurement="wind_speed", width=width,
                scale=scale, unit="m/s")


def q():
    return dict(kind="quality", width=2)


# Column tuple: label, scalar width, active subset indexes, base, k, deltas.
# Lists are literal wire order, not produced by walking a template.
CASES = (
    dict(name="ragged", revision=1, subsets=(("S", DAY), ("S", DAY)), descriptors=[
        dict(kind="delayed", count_width=2, children=[
            dict(kind="delayed", count_width=2, children=[field(10, 2), q()]), q()])],
        columns=(
            ("outer count", 2, (0, 1), 0, 2, (0, 2)),
            ("inner count @0", 2, (1,), 1, 0, ()),
            ("value @0,0", 10, (1,), 100, 0, ()),
            ("inner length @0", 32, (1,), 1, 0, ()),
            ("inner select @0:0", 1, (1,), 1, 0, ()),
            ("inner word @0:0", 2, (1,), 0, 0, ()),
            ("inner count @1", 2, (1,), 2, 0, ()),
            ("value @1,0", 10, (1,), 300, 0, ()),
            ("value @1,1", 10, (1,), 1023, 0, ()),
            ("inner length @1", 32, (1,), 2, 0, ()),
            ("inner select @1:0", 1, (1,), 1, 0, ()),
            ("inner select @1:1", 1, (1,), 0, 0, ()),
            ("inner word @1:0", 2, (1,), 0, 0, ()),
            ("outer length", 32, (0, 1), 0, 0, ()),
        ),
        expected=(
            (1, (0, 0, 0), (0, 0), (0,), 100, "VALUE", 0),
            (1, (0, 0, 0), (1, 0), (1,), 300, "VALUE", 0),
            (1, (0, 0, 0), (1, 1), (1,), 1023, "UNSELECTED", None),
        ),
        daily=(("S", DAY, 3, 2, 1, 0, 0, 0, "4", "2"),)),
    dict(name="scopes", revision=2, subsets=(("A", DAY), ("A", DAY), ("B", "2026-11-18")),
        descriptors=[
            dict(kind="delayed", count_width=2, children=[
                field(), dict(kind="delayed", count_width=2,
                              children=[field(), q()]), q()]),
            field(), q()],
        columns=(
            ("outer count", 2, (0, 1, 2), 0, 2, (1, 2, 0)),
            ("direct @0", 8, (0, 1), 10, 4, (0, 10)),
            ("inner count @0", 2, (0, 1), 0, 1, (0, 1)),
            ("inner value @0,0", 8, (1,), 255, 0, ()),
            ("inner length @0", 32, (0, 1), 0, 1, (0, 1)),
            ("inner select @0:0", 1, (1,), 1, 0, ()),
            ("inner word @0:0", 2, (1,), 0, 0, ()),
            ("direct @1", 8, (1,), 30, 0, ()),
            ("inner count @1", 2, (1,), 2, 0, ()),
            ("inner value @1,0", 8, (1,), 40, 0, ()),
            ("inner value @1,1", 8, (1,), 50, 0, ()),
            ("inner length @1", 32, (1,), 2, 0, ()),
            ("inner select @1:0", 1, (1,), 1, 0, ()),
            ("inner select @1:1", 1, (1,), 1, 0, ()),
            ("inner word @1:0", 2, (1,), 3, 0, ()),
            ("inner word @1:1", 2, (1,), 0, 0, ()),
            ("outer length", 32, (0, 1, 2), 0, 2, (1, 2, 0)),
            ("outer select :0", 1, (0, 1), 0, 1, (0, 1)),
            ("outer select :1", 1, (1,), 1, 0, ()),
            ("outer word :0", 2, (1,), 2, 0, ()),
            ("outer word :1", 2, (1,), 0, 0, ()),
            ("root value", 8, (0, 1, 2), 7, 2, (0, 1, 2)),
            ("root length", 32, (0, 1, 2), 1, 0, ()),
            ("root select :0", 1, (0, 1, 2), 1, 0, ()),
            ("root word :0", 2, (0, 1, 2), 0, 0, ()),
        ),
        expected=(
            (0, (0, 0), (0,), (), 10, "UNSELECTED", None),
            (0, (1,), (), (), 7, "VALUE", 0),
            (1, (0, 0), (0,), (), 20, "VALUE", 2),
            (1, (0, 1, 0), (0, 0), (0,), 255, "VALUE", 0),
            (1, (0, 0), (1,), (), 30, "VALUE", 0),
            (1, (0, 1, 0), (1, 0), (1,), 40, "MISSING", None),
            (1, (0, 1, 0), (1, 1), (1,), 50, "VALUE", 0),
            (1, (1,), (), (), 8, "VALUE", 0),
            (2, (1,), (), (), 9, "VALUE", 0),
        ),
        daily=(("A", DAY, 8, 4, 1, 1, 1, 1, "95", "95/4"),
               ("B", "2026-11-18", 1, 1, 0, 0, 0, 0, "9", "9"))),
)


def uint(value, width):
    if type(value) is not int or not 0 <= value < 1 << width:
        raise ValueError("fixture integer outside width")
    return format(value, "0" + str(width) + "b")


def column_bits(column):
    _, width, active, base, k, deltas = column
    assert active and tuple(sorted(set(active))) == active
    assert 1 <= width <= 32 and 0 <= k <= width
    assert len(deltas) == (len(active) if k else 0)
    assert all(base + d < 1 << width for d in deltas)
    return uint(base, width) + uint(k, 6) + "".join(uint(d, k) for d in deltas)


def payload(case):
    return "".join(column_bits(c) for c in case["columns"])


def packet(case, bits=None):
    bits = payload(case) if bits is None else bits
    padded = bits + "0" * (-len(bits) % 8)
    data = bytes(int(padded[i:i + 8], 2) for i in range(0, len(padded), 8))
    subsets = case["subsets"]
    header = b"WOBS\x02\x03COL" + case["revision"].to_bytes(4, "big") + bytes([len(subsets)])
    for station, day in subsets:
        encoded = station.encode("ascii")
        header += bytes([len(encoded)]) + encoded + day.encode("ascii")
    return header + len(bits).to_bytes(4, "big") + data


def trace(case):
    offset = 0
    result = []
    for c in case["columns"]:
        size = len(column_bits(c))
        result.append(dict(label=c[0], active=c[2], start=offset, end=offset + size,
                           values=tuple(c[3] + d for d in c[5]) if c[4]
                           else (c[3],) * len(c[2])))
        offset += size
    return result


def fixture_check():
    # Independent arithmetic checks on the supplied literal columns, not a WOBS2 decoder.
    assert len(CASES[0]["expected"]) == 3
    assert len(CASES[1]["expected"]) == 9
    for case in CASES:
        bits = payload(case)
        assert trace(case)[-1]["end"] == len(bits)
        assert len(packet(case)) == 18 + sum(11 + len(s) for s, _ in case["subsets"]) + (len(bits) + 7) // 8
        assert all(i < len(case["subsets"]) for c in case["columns"] for i in c[2])
        print(json.dumps(dict(case=case["name"], payload_bits=len(bits),
                              packet_hex=packet(case).hex(), columns=trace(case)),
                         sort_keys=True))


def check_case(archive, case, batch):
    binding = archive.register_template("COL", case["revision"], deepcopy(case["descriptors"]))
    wire = packet(case)
    receipt = archive.ingest_columns(batch, binding, [wire])
    items = archive.read_observations(batch)
    actual = tuple((x.subset_index, x.occurrence.template_path,
                    x.occurrence.occurrence_path, x.occurrence.group.parent_occurrence,
                    x.raw, x.quality.state.value, x.quality.code) for x in items)
    assert actual == case["expected"], (case["name"], actual)
    assert [x.occurrence.sequence for x in items] == list(range(len(items)))
    assert receipt.observation_count == len(items)
    for x in items:
        assert (x.station, x.day.isoformat()) == case["subsets"][x.subset_index]
        assert x.occurrence.group.template_path == x.occurrence.template_path[:-1]
        limit, scale = (1023, 100) if case["name"] == "ragged" else (255, 1)
        assert (x.value, x.missing, x.unit) == (
            None if x.raw == limit else Fraction(x.raw, scale), x.raw == limit, "m/s")
    assert archive.ingest_columns(batch, binding, (bytes([b]) for b in wire)) == receipt
    assert archive.read_observations(batch) == items
    daily = archive.build_daily(batch + "-daily", [batch])
    actual_daily = tuple((g.station, g.day.isoformat(), g.input_count, g.accepted_count,
                          g.observation_missing, g.unselected, g.quality_missing,
                          g.quality_rejected, str(g.exact_sum), str(g.mean)) for g in daily.groups)
    assert actual_daily == case["daily"], actual_daily
    return items, daily


def business_check(path):
    from weather.archive.imports import Archive
    with Archive(path) as archive:
        saved = [(case["name"], *check_case(archive, case, case["name"])) for case in CASES]
    with Archive(path) as reopened:
        for name, items, daily in saved:
            assert reopened.read_observations(name) == items
            assert reopened.read_daily(name + "-daily") == daily
    print("COLUMNS_EXAMPLES_OK: 12 observations; ragged 3/2/4/2; scopes A 8/4/95/(95/4), B 1/1/9/9; reopened")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("fixtures", "verify", "demo"))
    args = parser.parse_args()
    fixture_check()
    if args.mode != "fixtures":
        with tempfile.TemporaryDirectory(prefix="columns-cases-") as directory:
            business_check(str(Path(directory) / "archive.sqlite"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
