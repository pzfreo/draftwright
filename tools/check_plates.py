"""Check G2's eight reviewed plate parts against independently saved native analyses.

Usage: python3 -I tools/check_plates.py <quality out dir> <analysis dir>
Analyses are <part>.analysis.json from draftwright analyse; plans are quality.py's.
"""
import json
import re
import sys
from pathlib import Path

parts = ["1000", "10007", "10033", "10038", "10047", "10049", "10060",
         "nist_ftc_07_asme1_rd"]
quality, analyses = map(Path, sys.argv[1:])
print("part                         slabs direct covered thickness statements")
for name in parts:
    analysis = json.loads((analyses / f"{name}.analysis.json").read_text())
    plan = json.loads((quality / f"{name}.plan.json").read_text())
    plates = [f for f in analysis["features"] if f["kind"] == "plate"]
    assert plates, f"{name}: no recognised plates"
    direct = covered = 0
    statements = {}
    for plate in plates:
        ident = plate["id"]
        item_id = f"d:{ident}/thickness.length"
        records = [(d, m) for d in plan["dimensions"] for m in d["measurements"]
                   if m["feature"] == ident and m["parameter"] == "thickness.length"]
        if not records:
            omission = next((o for o in plan.get("omitted", []) if o["id"] == item_id), None)
            assert omission and omission["state"]["kind"] == "covered", f"{name}: {item_id} unstated"
            basis = omission["basis"].removeprefix("conveyed by ")
            owners = (basis.split("; dependencies: ", 1)[1].split(", ")
                      if "; dependencies: " in basis else
                      basis.partition(": ")[0].split(", ")
                      if omission["basis"].startswith("conveyed by ")
                      else [omission["state"]["by"]])
            drawn = [d for d in plan["dimensions"] if d["id"] in owners and d.get("placed")]
            assert len(drawn) == len(owners), f"{name}: {item_id} lacks a drawn dependency"
            statements[item_id] = "derived: " + "; ".join(
                f'{d["label"]} ({d["view"]})' for d in drawn)
            covered += 1
            continue
        assert len(records) == 1, f"{name}: {ident} stated {len(records)} times"
        dimension, measurement = records[0]
        assert dimension.get("placed") and dimension["state"]["kind"] == "shown", item_id
        value = plate["parameters"]["hi"] - plate["parameters"]["lo"]
        assert abs(measurement["value"] - value) < 1e-6, item_id
        label = re.sub(r"^\d+×\s*", "", dimension["label"])
        match = re.match(r"\d+(?:\.\d+)?", label)
        assert match, item_id
        nominal = match.group()
        precision = len(nominal.partition(".")[2])
        assert abs(float(nominal) - value) <= 0.5 * 10 ** -precision + 1e-9, item_id
        axis = plate["parameters"]["axis"]
        assert dimension["view"] in {"x": {"v:front", "v:plan"},
                                      "y": {"v:side", "v:plan"},
                                      "z": {"v:front", "v:side"}}[axis], item_id
        statements[dimension["id"]] = f'{dimension["label"]} ({dimension["view"]})'
        direct += 1
    if name == "1000":
        assert next(d for d in plan["dimensions"] if d["id"] == "d:f:plate:1/thickness.length")["view"] == "v:plan"
    if name == "10038":
        assert not any(d["id"] == "d:step_level/step_height.length.f4" for d in plan["dimensions"]), "10038: redundant underside closes a chain"
        assert next(d for d in plan["dimensions"] if d["id"] == "d:f:plate:1/thickness.length")["view"] == "v:side"
    if name == "10007":
        assert direct == 0, "10007: ladder intervals must not close dimension chains"
    print(f"{name:28} {len(plates):5} {direct:6} {covered:7} " + "; ".join(statements.values()))
print("PASS: every reviewed slab is stated once in an edge-on view or has a drawn owner")
