"""Standard-library Spec outline guards, also executed by the Cargo/CI budget test."""
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def wire_fields(fields):
    """Every Rust field, using its serialized name (including reserved words)."""
    result = set()
    for attrs, name in re.findall(r'((?:#\[[^\]]*\]\s*)*)pub ((?:r#)?\w+):', fields, re.S):
        rename = re.search(r'\brename\s*=\s*"([^"]+)"', attrs)
        result.add(rename.group(1) if rename else name.removeprefix('r#'))
    return result


class SpecContextBudget(unittest.TestCase):
    def test_new_nested_spec_field_does_not_change_any_advertised_schema(self):
        generator = Path(__file__).with_name('generate_mcp_schemas.py').resolve()
        original = generator.read_text()
        changed = original.replace("full_spec = document()", """full_spec = document()
full_spec['$defs']['sheet']['properties']['future_setting'] = {'type': 'string'}
spec['properties']['sheet']['properties']['future_setting'] = {'type': 'string'}""")
        outputs = []
        full = []
        private = []
        for source in (original, changed):
            with tempfile.TemporaryDirectory(prefix='mcp-outline-') as directory:
                root = Path(directory)
                (root / 'schemas/mcp').mkdir(parents=True)
                # __file__ keeps the source root for MAX_OPS; only outputs use cwd.
                subprocess.run([sys.executable, '-c',
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "exec(compile(sys.stdin.read(), sys.argv[2], 'exec'), {'__file__': sys.argv[2]})",
                    str(generator.parent), str(generator)], input=source, text=True,
                    cwd=root, check=True, capture_output=True)
                outputs.append({p.name: p.read_bytes() for p in (root / 'schemas/mcp').glob('*.json')})
                full.append((root / 'schemas/spec.json').read_bytes())
                private.append((root / 'schemas/mcp-validation.json').read_bytes())
        self.assertEqual(len(outputs[0]), 28)
        repository = generator.parents[1]
        self.assertEqual(outputs[0], {p.name: p.read_bytes()
            for p in (repository / 'schemas/mcp').glob('*.json')})
        self.assertEqual(full[0], (repository / 'schemas/spec.json').read_bytes())
        self.assertEqual(private[0], (repository / 'schemas/mcp-validation.json').read_bytes())
        self.assertEqual(outputs[0], outputs[1])
        self.assertNotEqual(full[0], full[1])
        self.assertNotEqual(private[0], private[1])

    def test_wire_field_inventory_keeps_renamed_raw_and_ordinary_fields(self):
        fields = '''
            #[serde(rename = "break", skip_serializing_if = "Option::is_none")]
            pub broken: Option<Request>,
            pub r#type: String,
            #[serde(default)]
            pub ordinary: bool,
        '''
        self.assertEqual(wire_fields(fields), {'break', 'type', 'ordinary'})

    def test_full_schema_covers_every_current_nested_spec_field(self):
        from mcp_spec import document
        root = Path(__file__).resolve().parents[1] / 'src'
        definitions = document()['$defs']
        types = {
            'model': {'Binding': 'binding', 'SheetSpec': 'sheet', 'ViewSpec': 'view',
                      'DimensionSpec': 'dimension', 'FeatureSpec': 'feature'},
            'broken': {'Request': 'break'},
            'sheets': {'SheetsSpec': 'sheets'},
            'placement_intent': {'Place': 'place', 'ViewPlace': 'view_place', 'AnnotationSpec': 'annotation'},
            'label_text': {'Text': 'text'},
            'spec_gdt': {'ControlSpec': 'control', 'FaceSpec': 'face'},
            'spec_notes': {'FinishSpec': 'finish', 'ThreadSpec': 'thread', 'KnurlSpec': 'knurl'},
            'recognition_corrections': {'Anchor': 'anchor', 'Declaration': 'declaration', 'PatternSpec': 'pattern'},
            'dimension_scheme': {'Scheme': 'scheme'}, 'notes': {'NoteSpec': 'note'},
            'characteristics': {'Settings': 'balloon_settings', 'Override': 'balloon_override'},
            'tables': {'TableSpec': 'table'}, 'gears': {'GearSpec': 'gear'},
        }
        for module, structs in types.items():
            source = (root / (module + '.rs')).read_text()
            for struct, definition in structs.items():
                fields = source.split('pub struct ' + struct + ' {', 1)[1].split('\n}', 1)[0]
                self.assertEqual(wire_fields(fields),
                                 set(definitions[definition]['properties']), struct)

    def test_outline_covers_all_spec_keys_with_closed_top_level(self):
        from mcp_spec import document, outline
        full, shallow = document(), outline()
        self.assertEqual(set(full['properties']), set(shallow['properties']))
        self.assertIs(shallow['additionalProperties'], False)
        self.assertEqual(full['additionalProperties'], shallow['additionalProperties'])
        self.assertIn('spec_schema', shallow['description'])
        for key, shape in shallow['properties'].items():
            self.assertTrue(set(shape) <= {'type', 'enum'}, key)
        # Also guard drift from the actual Rust document model.
        model = Path(__file__).resolve().parents[1] / 'src/model.rs'
        spec = model.read_text().split('pub struct Spec {', 1)[1].split('\n}', 1)[0]
        self.assertEqual(wire_fields(spec), set(shallow['properties']))


if __name__ == '__main__':
    unittest.main()
