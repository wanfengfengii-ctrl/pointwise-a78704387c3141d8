"""Independent handover audit for the WOBS2 column extension.

This is a deliberately small, self-contained check that does not reuse
columns_cases.py.  It hand-builds one two-subset packet with a fixed
replication and a tail quality block, then reconciles every observation,
quality state, whole-batch sequence, subset station/day, daily accounting and
a real database reopen.  Run with:  python3.11 -B columns_audit.py
"""
from fractions import Fraction
from pathlib import Path
import tempfile

from weather.archive.imports import Archive

DAY = "2026-11-17"


def uint(value, width):
    assert 0 <= value < 1 << width
    return format(value, "0" + str(width) + "b")


def scalar(width, active_ordered, base, k, deltas=()):
    bits = uint(base, width) + uint(k, 6)
    assert len(deltas) == len(active_ordered) if k else not deltas
    if k:
        bits += "".join(uint(delta, k) for delta in deltas)
    return bits


def pack(bits):
    padded = bits + "0" * (-len(bits) % 8)
    return bytes(int(padded[i:i + 8], 2) for i in range(0, len(padded), 8))


def header(subsets, template_id, revision):
    head = b"WOBS\x02" + bytes([len(template_id)]) + template_id.encode("ascii")
    head += revision.to_bytes(4, "big") + bytes([len(subsets)])
    for station, day in subsets:
        encoded = station.encode("ascii")
        head += bytes([len(encoded)]) + encoded + day.encode("ascii")
    return head


def main():
    # Template: one fixed replication of two rounds, each with one width-8 wind
    # field and a tail 2-bit quality block (two direct members total), followed
    # by a standalone root width-8 field with its own quality block.
    descriptors = [
        dict(kind="fixed", count=2, children=[
            dict(kind="field", measurement="wind_speed", width=8, unit="m/s"),
            dict(kind="quality", width=2)]),
        dict(kind="field", measurement="wind_speed", width=8, unit="m/s"),
        dict(kind="quality", width=2),
    ]
    subsets = (("A", DAY), ("B", DAY))
    bits = ""
    # Fixed group round 0 field: base 10 + deltas (0, 3) -> 10, 13.
    bits += scalar(8, (0, 1), 10, 4, (0, 3))
    # Fixed group round 1 field: subset0 raw 20; subset1 raw 255 (all ones).
    bits += scalar(8, (0, 1), 20, 8, (0, 235))
    # Tail quality length column: both subsets have two direct members.
    bits += scalar(32, (0, 1), 2, 0)
    # Selection position 0: subset0 unselected (0), subset1 selected (1).
    bits += scalar(1, (0, 1), 0, 1, (0, 1))
    # Selection position 1: both subsets have the member; subset0 unselected,
    # subset1 selected.
    bits += scalar(1, (0, 1), 0, 1, (0, 1))
    # Quality words follow all selections; only subset1 selected.
    # Position 0 word code 2; position 1 all-ones word => quality MISSING.
    bits += scalar(2, (1,), 2, 0)
    bits += scalar(2, (1,), 3, 0)
    # Root standalone field: base 4 + deltas (0, 5) -> 4, 9.
    bits += scalar(8, (0, 1), 4, 4, (0, 5))
    # Root quality length 1 for both, both selected, both word 0.
    bits += scalar(32, (0, 1), 1, 0)
    bits += scalar(1, (0, 1), 1, 0)
    bits += scalar(2, (0, 1), 0, 2, (0, 0))

    wire = header(subsets, "AUD", 1) + len(bits).to_bytes(4, "big") + pack(bits)

    with tempfile.TemporaryDirectory(prefix="columns-audit-") as directory:
        database = str(Path(directory) / "audit.sqlite")
        with Archive(database) as archive:
            binding = archive.register_template("AUD", 1, descriptors)
            receipt = archive.ingest_columns("audit", binding, [wire])
            assert receipt.observation_count == 6, receipt
            items = archive.read_observations("audit")

            # Whole-batch contiguous sequence, subset order, per-subset station.
            assert [item.occurrence.sequence for item in items] == list(range(6))
            assert [item.subset_index for item in items] == [0, 0, 0, 1, 1, 1]
            assert [item.station for item in items] == ["A", "A", "A", "B", "B", "B"]

            # Subset 0: fixed members raw 10 (UNSELECTED), 20 (UNSELECTED),
            # root raw 4 (VALUE 0).
            s0 = [item for item in items if item.subset_index == 0]
            assert [item.raw for item in s0] == [10, 20, 4]
            assert [item.quality.state.value for item in s0] == [
                "UNSELECTED", "UNSELECTED", "VALUE"]
            assert [item.value for item in s0] == [
                Fraction(10), Fraction(20), Fraction(4)]

            # Subset 1: raw 13 VALUE(2) (rejected under default code [0]);
            # raw 255 is itself all-ones so it is an observation missing and is
            # excluded before its MISSING quality word is ever evaluated; root
            # raw 9 VALUE(0) is accepted.
            s1 = [item for item in items if item.subset_index == 1]
            assert [item.raw for item in s1] == [13, 255, 9]
            assert s1[0].quality.code == 2 and s1[0].value == Fraction(13)
            assert s1[1].missing and s1[1].value is None
            assert s1[1].quality.state.value == "MISSING"
            assert s1[2].quality.code == 0 and s1[2].value == Fraction(9)

            # Daily station A: root VALUE(0) accepted -> sum 4, mean 4; the two
            # fixed members are unselected.  Station B: root raw 9 accepted;
            # raw 13 is quality_rejected, raw 255 is observation_missing.
            result = archive.build_daily("audit-day", ["audit"])
            rows = {(group.station, group.day.isoformat()): group
                    for group in result.groups}
            a = rows[("A", DAY)]
            assert (a.input_count, a.accepted_count, a.observation_missing,
                    a.unselected, a.quality_rejected) == (3, 1, 0, 2, 0)
            assert (str(a.exact_sum), str(a.mean)) == ("4", "4")
            b = rows[("B", DAY)]
            assert (b.input_count, b.accepted_count, b.observation_missing,
                    b.quality_rejected) == (3, 1, 1, 1)
            assert (str(b.exact_sum), str(b.mean)) == ("9", "9")

        # Reopen: no re-decode, identities (incl. subset_index) survive.
        with Archive(database) as reopened:
            assert reopened.read_observations("audit") == items
            assert reopened.read_daily("audit-day") == result
            assert all(item.subset_index is not None
                       for item in reopened.read_observations("audit"))

    print("COLUMNS_AUDIT_OK: 6 observations, two subsets; A 3/1/unselected2/sum4; "
          "B 3/1/observation_missing1/rejected1/sum9; reopened")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
