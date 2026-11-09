"""Original synthetic #380 assembly; run from any directory with build123d/OCP.

/Users/paul/repos/quiddity/.venv/bin/python -I tests/fixtures/assembly/pack-copies.py
No external models. Shared under this repository's licence. XCAF retains two
placements of one plate PRODUCT, alongside independently built congruent copies.
Public Specify BOM UDAs deliberately carry distinct item numbers and defaults.
"""
import gzip
from pathlib import Path
import re
import tempfile

from build123d import Box, Cylinder, RegularPolygon, extrude
from OCP.BRep import BRep_Builder
from OCP.gp import gp_Trsf, gp_Vec
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Compound
from OCP.XCAFDoc import XCAFDoc_DocumentTool


def bolt(length):
    # Hex head (5 mm across flats), 2 mm high, and a 3 mm shank.
    head = extrude(RegularPolygon(2.5, 6, major_radius=False), amount=2)
    return head.fuse(Cylinder(1.5, length).translate((0, 0, -length / 2)))


doc = TDocStd_Document(TCollection_ExtendedString('MDTV-XCAF'))
tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
root = tool.NewShape()
empty = TopoDS_Compound()
BRep_Builder().MakeCompound(empty)
tool.SetShape(root, empty)
TDataStd_Name.Set_s(root, TCollection_ExtendedString('Copy grouping fixture'))

entries = [
    ('Enclosure', Box(80, 60, 16).cut(Box(76, 56, 16).translate((0, 0, 2))), [(0, 0, 0)]),
    ('Plate MK II (60mm) #1', Box(60, 40, 3), [(0, 0, 14), (100, 0, 14)]),
    ('Plate MK II (60mm) #2', Box(60, 40, 3), [(200, 0, 14)]),
    *[(f'M3×8 hex bolt #{i + 1}', bolt(8), [(x, y, 17)])
      for i, (x, y) in enumerate([(-25, -15), (25, -15), (-25, 15), (25, 15)])],
    ('M3×10 hex bolt', bolt(10), [(50, 0, 17)]),
]
for name, shape, positions in entries:
    label = tool.AddShape(shape.wrapped, False)
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    for position in positions:
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(*position))
        tool.AddComponent(root, label, TopLoc_Location(transform))
tool.UpdateAssemblies()
writer = STEPCAFControl_Writer()
writer.SetNameMode(True)
assert writer.Transfer(doc, STEPControl_AsIs)
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'copies.step'
    assert writer.Write(str(path)) == 1
    source = path.read_text()

# Attach public UDA graphs to each leaf product definition. Use unique native
# exporter ids and find numbers: neither states an interchangeable-part identity.
products = dict((int(i), name) for i, name in re.findall(
    r"#(\d+) = PRODUCT\('[^']*','([^']*)'", source))
formations = dict((int(i), int(product)) for i, product in re.findall(
    r"#(\d+) = PRODUCT_DEFINITION_FORMATION\('[^']*','[^']*',#(\d+)\)", source))
definitions = [(int(i), products[formations[int(formation)]]) for i, formation in re.findall(
    r"#(\d+) = PRODUCT_DEFINITION\('[^']*','[^']*',#(\d+),#\d+\)", source)
    if products[formations[int(formation)]] != 'Copy grouping fixture']
assert len(definitions) == 8, definitions
next_id = max(map(int, re.findall(r'#(\d+)\s*=', source))) + 1
records = []
for n, (owner, name) in enumerate(definitions, 1):
    buy = 'bolt' in name
    fields = [
        ('item_number', str(n)),
        ('make_buy', "'bought'" if buy else "'made'"),
        ('description', f"'Exporter suggestion {n}'"),
        ('default.description', '.T.'),
        ('optional', '.T.' if n % 2 else '.F.'),
        ('default.optional', '.T.'),
    ]
    items = ','.join(f'#{next_id + 4 + i}' for i in range(len(fields)))
    records.extend([
        f"#{next_id}=PROPERTY_DEFINITION('specify bom','',#{owner});",
        f'#{next_id + 1}=PROPERTY_DEFINITION_REPRESENTATION(#{next_id},#{next_id + 2});',
        f"#{next_id + 2}=REPRESENTATION('',({items}),#{next_id + 3});",
        f"#{next_id + 3}=REPRESENTATION_CONTEXT('','');",
    ])
    for i, (key, value) in enumerate(fields):
        kind = ('BOOLEAN' if value.startswith('.') else
                'DESCRIPTIVE' if value.startswith("'") else 'INTEGER')
        records.append(f"#{next_id + 4 + i}={kind}_REPRESENTATION_ITEM('{key}',{value});")
    next_id += 4 + len(fields)
source = source.replace('ENDSEC;\nEND-ISO-10303-21;', '\n'.join(records) + '\nENDSEC;\nEND-ISO-10303-21;')
output = Path(__file__).with_suffix('.step.gz')
output.write_bytes(gzip.compress(source.encode(), mtime=0))
print(output, output.stat().st_size)
