#!/usr/bin/env python3
"""Generate #267 inputs with Specify v0.1.8 (never a draftwright dependency).

Run in an OCP environment. --source is Specify's committed
 tests/fixtures/revisions/gramil_assembly.new.step.gz. The real assembly and its
BOM remain in --work. Only the small synthetic box assembly is committed; its
names, quantities and answered fields follow the real BOM, without gramil geometry.
"""
import argparse
import gzip
import json
from pathlib import Path
import subprocess


def run(binary, *args):
    reply = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True, check=True)
    return json.loads(reply.stdout)


def write_bom(binary, source, directory, *, reduced=False):
    directory.mkdir(parents=True, exist_ok=True)
    rows = run(binary, 'bom', source)
    pairs = []
    for row in rows:
        i = row['part']
        name = row['name']['value']
        lower = name.lower()
        analysis, answers = directory / f'{i}.analysis.json', directory / f'{i}.answers.json'
        run(binary, 'analyse', source, '--part', i, '-o', analysis)
        material = ('Brass' if lower == 'spacer' else
                    'Copper sheet (annealed)' if 'retainer' in lower else
                    'O1 tool steel, hardened ~60 HRC' if lower.startswith('grm-07') else
                    'Mild steel, bright-drawn' if lower == 'push rod' else
                    'Stainless steel' if 'washer' in lower or 'pan-head' in lower else
                    'Steel' if lower.startswith('grub') else 'Brass H62 (CuZn40)')
        values = {'bom.make_buy': 'made' if lower.startswith('grm-') or lower in ('push rod', 'spacer') else 'bought',
                  'bom.description': ('Bone-shaped copper blade retainer' if 'retainer' in lower else
                                      'Steel push rod, cut to length' if lower == 'push rod' else
                                      'Channel-width shim, 1 mm' if lower == 'spacer' else name),
                  'bom.stock': 'from stock' if lower in ('push rod', 'spacer') else 'to be made',
                  'bom.optional': 'yes' if lower == 'spacer' else 'no',
                  'part.material': material}
        if lower.startswith('grm-'):
            values['bom.part_number'] = name.split()[0]
        # A revision and a distinct id exercise the title-block/CSV contract too.
        if reduced and i == 0:
            values['bom.revision'] = 'B'
        answers.write_text(json.dumps(values))
        pairs.extend([analysis, answers])
    output = directory / 'written.step'
    run(binary, 'write', source, *pairs, '-o', output, '--accept-defaults', '--bom')
    bom = run(binary, 'bom', output)
    (directory / 'bom.json').write_text(json.dumps(bom, indent=2) + '\n')
    return output, bom


def shapes(source, target, rows=None):
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.collections import Sequence_TDF_Label as TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Compound
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString('MDTV-XCAF'))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    if rows is None:
        reader = STEPCAFControl_Reader()
        reader.SetNameMode(True)
        assert reader.ReadFile(str(source)) == 1
        assert reader.Transfer(doc)
        roots = TDF_LabelSequence()
        tool.GetFreeShapes(roots)
        assert roots.Length() == 1
        root = roots.Value(1)
        entries = [('spacer', 1)]
    else:
        compound = TopoDS_Compound()
        BRep_Builder().MakeCompound(compound)
        root = tool.AddShape(compound, True)
        entries = [(r['name']['value'], r['quantity']['value']) for r in rows]
        TDataStd_Name.Set_s(root, TCollection_ExtendedString('Synthetic BOM assembly'))
    count = 0
    for i, (name, qty) in enumerate(entries):
        shape = BRepPrimAPI_MakeBox(8 + i, 6, 3).Shape() if rows is not None else BRepPrimAPI_MakeBox(1, 3.6, 23).Shape()
        label = tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        for _ in range(qty):
            tr = gp_Trsf()
            tr.SetTranslation(gp_Vec((count % 5) * 30, (count // 5) * 20, 0))
            tool.AddComponent(root, label, TopLoc_Location(tr))
            count += 1
    tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    assert writer.Transfer(doc, STEPControl_AsIs)
    assert writer.Write(str(target)) == 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--specify', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    original = a.work / 'gramil.step'
    original.write_bytes(gzip.decompress(a.source.read_bytes()))
    # Use the committed revision fixture, rather than assuming that another
    # snapshot of gramil has the same leaf order or licences.
    original_written, original_rows = write_bom(a.specify, original, a.work / 'original')
    variant = a.work / 'gramil-spacer.step'
    shapes(original, variant)
    variant_written, variant_rows = write_bom(a.specify, variant, a.work / 'variant')
    assert len(original_rows) == 12 and len(variant_rows) == 13
    assert [r['item_number']['value'] for r in variant_rows] == list(range(1, 14))
    assert [r['item_number']['value'] for r in variant_rows if r['stock']['value'] == 'from stock'] == [12, 13]
    assert [r['item_number']['value'] for r in variant_rows if r['optional']['value']] == [13]
    synthetic = a.work / 'synthetic.step'
    shapes(None, synthetic, variant_rows)
    written, rows = write_bom(a.specify, synthetic, a.work / 'reduced', reduced=True)
    assert [r['quantity']['value'] for r in rows] == [1, 1, 4, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    # Reduce the written file to original geometry plus the public BOM/material
    # property graphs. Private history and default PMI are deliberately outside
    # this fixture's scope; Specify's real gramil writes remain intact in --work.
    import re
    data = written.read_text()
    records = dict((int(m[0]), m[1]) for m in re.findall(r'#(\d+)\s*=\s*(.*?);', data, re.S))
    original_ids = {int(i) for i in re.findall(r'#(\d+)\s*=', synthetic.read_text())}
    public = {i for i, r in records.items() if r.startswith('PROPERTY_DEFINITION(')
              and any(f"'{n}'" in r for n in ('specify bom', 'material property'))}
    incoming = {i for i, r in records.items()
                if r.startswith(('PROPERTY_DEFINITION_REPRESENTATION(', 'GENERAL_PROPERTY_ASSOCIATION('))
                and any(int(v) in public for v in re.findall(r'#(\d+)', r))}
    keep = set(original_ids)
    todo = list(public | incoming)
    while todo:
        i = todo.pop()
        if i in keep:
            continue
        keep.add(i)
        todo.extend(int(v) for v in re.findall(r'#(\d+)', records[i]))
    data = re.sub(r'#(\d+)\s*=\s*.*?;', lambda m: m[0] if int(m[1]) in keep else '', data, flags=re.S)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_bytes(gzip.compress(data.encode(), mtime=0))
    print(json.dumps({'original': str(original_written), 'variant': str(variant_written), 'fixture': str(a.output), 'bytes': a.output.stat().st_size}))


if __name__ == '__main__':
    main()
