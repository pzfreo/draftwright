"""On-demand Spec shape and the stable, shallow MCP context outline.

These describe document structure; identity, geometry and engineering constraints
remain the model's responsibility. No nested definition feeds the outline.
"""
from copy import deepcopy

S = {'type': 'string'}
B = {'type': 'boolean'}
N = {'type': 'number'}
I = {'type': 'integer', 'minimum': 0}
SHEET = {'type': 'integer', 'minimum': 1, 'maximum': 9}

def enum(*values): return {'enum': list(values)}
def arr(item, **kw): return {'type': 'array', 'items': item, **kw}
def obj(props, required=()):
    return {'type': 'object', 'properties': props, 'additionalProperties': False,
            **({'required': list(required)} if required else {})}
def nullable(shape): return {'anyOf': [shape, {'type': 'null'}]}
def optional(props): return {k: nullable(v) for k, v in props.items()}
def ref(name): return {'$ref': '#/$defs/' + name}
def mapping(shape): return {'type': 'object', 'additionalProperties': shape}

def outline():
    """An explicit top-level contract, independent of all nested Spec definitions."""
    return {**obj({
        'schema': enum('draftwright.spec/1', 'draftwright.spec/2'),
        'part': {'type': ['string', 'integer', 'null']},
        'binding': {'type': ['object', 'null']}, 'sheet': {'type': 'object'}, 'balloons': {'type':'object'},
        'iso_scale_factor': {'type': ['number', 'null']},
        'sheets': {'type': ['object', 'null']},
        **{k: {'type': 'object'} for k in ('views', 'dimensions', 'annotations',
            'features', 'faces', 'dimension_schemes', 'precision')},
        'dimension_source': enum('auto', 'authored'),
        'pmi': enum('strict', 'first', 'ignore'),
        'process': {'type': ['string', 'null']},
        'origin': {'type': 'object'},
        'hole_table': enum('auto', 'always', 'never'),
        'envelope_tolerance': {'type': ['string', 'null']},
        **{k: {'type': 'array'} for k in ('notes', 'tables', 'gears', 'feature_declarations')},
    }), 'description': 'Typed outline only. Call spec_schema {} for the full Spec and get_spec spec_read schemas; item_options gives valid edit paths and choices.'}


def document():
    """Full draft-2020-12 author Spec, including the get_spec read shape."""
    line = optional({k: B for k in ('hidden', 'centre_marks', 'centre_lines',
                                  'thread_depiction', 'knurl_depiction')})
    definitions = {
        'binding': obj({'source_sha256': S, 'part': S, 'face_count': I,
                        'loader': S, 'part_index': nullable(I)},
                       ('source_sha256', 'part', 'face_count', 'loader')),
        'callouts': obj({'style': enum('auto', 'compact', 'full', 'note')}),
        'text': obj(optional({'prefix': S, 'suffix': S, 'template': S})),
        'place': obj({**optional({'view': S, 'side': enum('above', 'below', 'left', 'right'),
                                  'lane': I, 'along': {'anyOf': [N, enum('start', 'middle', 'end', 'outside')]},
                                  'offset': arr(N, minItems=2, maxItems=2)}), 'pinned': B}),
        'view_place': obj({'relative_to': S, 'side': enum('above', 'below', 'left', 'right'),
                           'gap': {**N, 'description': 'Clear paper distance in mm between the views’ annotation bands, not their part outlines (default 10 mm).'},
                           'align': {**enum('start', 'middle', 'end', 'outside', 'projection'),
                                     'description': 'projection keeps geometry origins aligned across side (x above/below, y left/right), only for front/plan or front/side in either direction, first or third angle. Gap stays between areas including bands. Other values retain existing alignment.'}, 'pinned': B},
                          ('relative_to', 'side')),
        'finish': obj({'ra': N, 'removal': nullable(enum('any', 'required', 'prohibited'))}, ('ra',)),
        'modelled_thread': obj(optional({
            'depiction': {**B, 'description': 'Use ISO 6410 conventional ink to depict thread extent; false restores modelled edges.'},
            'designation': {**S, 'description': 'Replace the entire modelled-thread callout verbatim. Default: ISO designation, or measured diameter and pitch; no length suffix.'}})),
        'thread': obj({'designation': S, 'length': nullable(N)}, ('designation',)),
        'knurl': obj({'pattern': enum('straight', 'diamond'), 'pitch': N}, ('pattern', 'pitch')),
        'control': obj({'characteristic': enum('straightness', 'flatness', 'circularity',
                    'cylindricity', 'profile_line', 'profile_surface', 'angularity',
                    'perpendicularity', 'parallelism', 'position', 'concentricity',
                    'symmetry', 'circular_runout', 'total_runout'), 'tolerance': N,
                    'datums': arr(S), 'diameter': nullable(B), 'modifier': nullable(enum('M', 'L'))},
                    ('characteristic', 'tolerance')),
        'anchor': obj({'faces': arr(S)}, ('faces',)),
        'pattern': obj({'arrangement': enum('linear', 'grid', 'bolt_circle'), 'members': arr(S)},
                       ('arrangement', 'members')),
        'declaration': obj({'id': S, 'kind': enum('hole', 'boss', 'turned_step', 'slot', 'pocket'),
                            'faces': arr(S)}, ('id', 'kind', 'faces')),
        'scheme': obj({'scheme': enum('chain', 'baseline', 'ordinate'), 'axis': enum('x', 'y', 'z'),
                       'targets': arr(S), 'view': nullable(enum('front', 'plan', 'side')), 'from': nullable(S)},
                      ('scheme', 'axis')),
        'dimension': obj({**optional({'presentation': {**enum('tangent_depth', 'far_extreme'), 'description': 'Cylindrical-mouth pocket depth: tangent_depth measures from the near tangent; far_extreme states the floor distance from the opposite cylinder tangent. Both are measured from the cylinder axis, radius and pocket floor. Measured values are retained; unsupported requests warn.'}, 'text': ref('text'), 'style': enum('reference', 'basic'),
                        'kind': enum('linear', 'diameter', 'radius', 'angle'),
                        'between': arr(S, minItems=1, maxItems=2), 'along': enum('x', 'y', 'z', 'true'),
                        'tolerance': S, 'fit': S, 'view': S, 'place': ref('place')}), 'omit': B}),
        'annotation': obj({**optional({'text': ref('text'), 'place': ref('place')}), 'omit': B}),
        'feature': obj({**optional({'anchor': ref('anchor'), 'reclassify': enum('hole', 'boss', 'turned_step', 'slot', 'pocket'),
                       'gusset_view': {**enum('front', 'side', 'plan'), 'description': 'Transverse view for a readable gusset group; must look along either leg. Legs remain in the triangle view.'}, 'pattern': ref('pattern'), 'fit': S, 'datum': S, 'finish': ref('finish'),
                       'modelled_thread': ref('modelled_thread'), 'spring': {'enum': ['simplified', 'full']}, 'thread': ref('thread'), 'knurl': ref('knurl'), 'note': S, 'callouts': ref('callouts')}),
                       'suppress': B, 'split': B, 'controls': arr(ref('control')), 'note_satisfies': arr(S)}),
        'face': obj({**optional({'datum': S, 'finish': ref('finish'), 'note': S,
                                 'profile_corners': {**enum('auto', 'angle', 'linear'), 'description': 'How the corners of this face\u2019s outer profile are defined: auto (default) calls out a 45\u00b0 corner chamfer by its size (C<leg>, counted when equal), locates any other cut corner\u2019s missing stations with linear dimensions, gives any other non-right corner its angle and a regular polygon one counted angle; angle states every corner\u2019s angle (a reference where linear dimensions already fix it) instead of stations; linear locates the stations of every corner, a regular polygon\u2019s too. Per corner, dimensions/<id> {} shows and omit hides an angle.'}}),
                     'controls': arr(ref('control'))}),
        'sheet': obj({'balloons': enum('off','all'), **line, **optional({'keep_layout': {**B, 'description': 'Keep explicit paper, scale and sheet count without automatic selection or view refitting. Requires sheet.size, sheet.scale and sheets.max=1. Moved views are reported on apply. A later explicit paper, scale or sheet-count edit releases an inherited guard; explicitly setting keep_layout in the same batch retains it. Remove to allow the whole sheet to be laid out again, including hand-moved placements.'}, 'uncited_datums': {**B, 'description': 'Draw every declared datum by default; false hides symbols no control frame cites, recorded as omitted_by_user. Individual symbols use annotations/<id>/omit.'}, 'zones': {**B, 'description': 'ISO 5457 grid zones and centring marks on every sheet (default true). false omits both, except zones cited by section cross-references, which stay with a sheet_zones_kept info finding.'}, 'view_gutter': {**N, 'minimum': 0, 'maximum': 1189, 'description': 'Preferred maximum blank view gutter in paper mm, default 25; shrinks to fit at the selected sheet and scale.'}, 'size': S, 'scale': S, 'preferred_max_sheets': {**I, 'minimum': 1, 'description': 'Preferred sheet count, default 2; sheets.max remains hard.'}, 'preferred_max_size': {**enum('A4', 'A3', 'A2', 'A1', 'A0'), 'description': 'Preferred largest paper, default A3. Try single sheets then preferred_max_sheets at this size, then larger paper; exceeding a preference is advisory. Explicit size or scale wins.'}, 'dimension_spacing': {**enum('normal', 'compact'), 'description': 'Dimension-line spacing. Unset: 10 mm first line, 7 mm pitch, with 7/5.5 mm compact lanes tried on the same sheet before a smaller scale or larger paper. normal forbids that fallback; compact uses it directly.'}, 'frame_gutter': {'type': 'number', 'minimum': 0, 'maximum': 20, 'description': 'Clear mm every view and annotation keeps inside the frame (default 3); spare paper widens it. 0 lets the drawing run to the frame.'}, 'fit_limits': B, 'fit_deviations': B, 'references': {**B, 'description': 'Draw redundant dimensions in parentheses (default false); pins and explicit reference style retain ink.'},
                      'projection': enum('first', 'third'), 'default_finish': ref('finish'), 'callouts': ref('callouts')}), 'title': mapping(S),
                      'table_columns': {**mapping({'type': 'integer', 'minimum': 1, 'maximum': 4}), 'description': 'Side-by-side blocks for any table by ID (t:<k>, t:radius-centres, manufacturing_requirements, gear_data_<owner>, hole_schedule_<view>, hole_table_plan): 1 never splits it. Unset splits a table taller than the sheet into the fewest blocks that fit, each repeating the header rows; rows read down then across.'}}),
        'sheets': obj({**optional({'max': SHEET, 'smallest_scale': S, 'hole_table': SHEET}),
                       'keep_together': arr(arr(S)), 'gdt_on_sheet_1': B}),
        'detail': obj({'around': {'type': ['string', 'array'], 'items': S, 'minItems': 1},
                       **optional({'from': {'type': 'string', 'pattern': r'^(front|plan|side|section_([a-h])\2)(?![\s\S])'},
                                   'scale_factor': {'type': 'number', 'exclusiveMinimum': 1}})}, ('around',)),
        'break': obj({'axis': enum('x','y','z'), 'remove': arr(N, minItems=2, maxItems=2), 'gap': {**N, 'exclusiveMinimum': 0, 'description': 'Retained gap in paper mm at every scale; default 6.'}, 'aligned': B}),
        'view': obj({**line, **optional({'break': ref('break'), 'half_section': {**B, 'description': 'ISO 128-50 half section on a profile view of a part turned whole: true forces it, false forbids it; unset draws it automatically when a coaxial hole steps inside (counterbore, spotface or countersink).'}, 'sheet': SHEET, 'place': ref('view_place'), 'detail': ref('detail')}), 'omit': B}),
        'note': obj({'id': S, 'text': S}, ('id', 'text')),
        'table': obj({'id': S, 'rows': arr(arr({'type': ['string', 'number']})),
                      'sheet': nullable(SHEET), 'prefer': nullable(enum('tr', 'tl', 'br', 'bl'))}, ('id', 'rows')),
        'gear': obj({'on': S, 'frame': obj({'origin': arr(N, minItems=3, maxItems=3), 'axis': S}, ('origin', 'axis')),
                     'tooth_count': {'type': 'integer'}, 'module': N, 'pressure_angle': N,
                     'profile_shift': N, 'face_width': N, 'tooth_thickness': N,
                     'tooth_thickness_tolerance': arr(N, minItems=2, maxItems=2),
                     'flank_tolerance_class': {'type': 'integer'}},
                    ('on', 'frame', 'tooth_count', 'module', 'pressure_angle', 'profile_shift',
                     'face_width', 'tooth_thickness', 'tooth_thickness_tolerance', 'flank_tolerance_class')),
    }
    for name in ('dimension', 'feature'):
        field = definitions[name]['properties']['fit']
        definitions[name]['properties']['fit'] = {'description': 'ISO 286 fit class. Prints independently of the supported numerical-deviation table; unavailable requested deviations warn without invented values.', **field}
    definitions['balloon_override'] = obj({'suppress': B, 'position': nullable(arr(N,minItems=2,maxItems=2))})
    definitions['balloon_settings'] = obj({'occurrences': B, 'items': mapping(ref('balloon_override'))})
    props = {'schema': enum('draftwright.spec/1', 'draftwright.spec/2'),
             'part': {'description': 'Part identity from draftwright parts: persistent key (preferred) or numeric index. Acts like --part; conflicting command/request selectors are refused. A saved Analysis must already contain this part.', **nullable({'anyOf': [{**S, 'minLength': 1}, I]})},
             'binding': nullable(ref('binding')), 'sheet': ref('sheet'),
             'balloons': ref('balloon_settings'),
             'iso_scale_factor': nullable(N), 'sheets': nullable(ref('sheets')),
             'dimension_source': enum('auto', 'authored'),
        'pmi': enum('strict', 'first', 'ignore'),
        'origin': {'description': 'Datum stations: min, max, a unique normal plane or axis:<reference> transverse to the named coordinate.', **obj({k: S for k in ('x', 'y', 'z')})}, 'hole_table': enum('auto', 'always', 'never'),
             'envelope_tolerance': nullable(S),
             'precision': mapping({'type': 'integer', 'minimum': 0, 'maximum': 15})}
    for key, name in [('views', 'view'), ('dimensions', 'dimension'), ('annotations', 'annotation'),
                      ('features', 'feature'), ('faces', 'face'), ('dimension_schemes', 'scheme')]:
        props[key] = mapping(ref(name))
    props['views']['properties'] = {'sections': {
        'description': 'Explicit longitudinal sections through named geometry. Unavailable planes retain the drawing with a specific warning.',
        **arr(obj({'id': {**S, 'pattern': '^[A-H]$'}, 'through': {**S, 'minLength': 1}}, ('id', 'through'))) }}
    for key, name in [('feature_declarations', 'declaration'), ('notes', 'note'), ('tables', 'table'), ('gears', 'gear')]:
        props[key] = arr(ref(name))
    props['process'] = {'description': 'Per-part process. Overrides file specify process UDA and material-class guess. '
                    'Printed defaults are marked CHECK; override tolerance at sheet/title/tolerance '
                    'and finish at sheet/default_finish.',
     'enum': ['machined',
              '3D printed: FDM',
              '3D printed: resin',
              '3D printed: powder',
              '3D printed: metal',
              'sheet metal',
              'moulded',
              'cast',
              None]}
    definitions['spec_read'] = obj(deepcopy(props))
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Draftwright Spec',
            'description': 'Author Spec structure. Absent fields use defaults. Geometry, references, engineering constraints and edit guidance are validated by the server. The get_spec spec value has the same shape, also named by #/$defs/spec_read.',
            **obj(props), '$defs': definitions}
