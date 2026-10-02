"""Title block, border, and page furniture shared by both build paths."""

from __future__ import annotations

from copy import copy

from build123d import Align, Compound, Edge, Location, Vector
from build123d_drafting.helpers import (
    TitleBlock,
    TitleBlockCell,
    TitleBlockLayout,
    draft_preset,
    format_drawing_scale,
)

from draftwright._core import (
    _PAGE_SIZES,
    _TB_LINE_WIDTH,
    Analysis,
    _analysis_margins,
    _fmt,
    _font_safe_text,
    _frame_margins,
    _title_margins,
    place_annotation,
)
from draftwright.fonts import PLEX_SANS_CONDENSED


def _attribution_author(drawn_by: str | None) -> str:
    """ISO 7200 "drawn by" value: the human author and draftwright, or just
    draftwright when no author was supplied."""
    author = (drawn_by or "").strip()
    return f"{author} / draftwright" if author else "draftwright"


# ISO 5457 zone-grid letters (vertical edges): A.. skipping I and O (confusable with 1 / 0).
_ZONE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"

# ISO 5457 reference-grid division counts (columns=numbers × rows=letters) per A-series page,
# keyed by the full (width, height) so a same-width custom page doesn't borrow the count.
_ZONE_DIVISIONS = {
    (297, 210): (6, 4),
    (420, 297): (8, 6),
    (594, 420): (12, 8),
    (841, 594): (16, 12),
    (1189, 841): (24, 16),
}


def _zone_divisions(page_w: float, page_h: float) -> tuple[int, int]:
    """``(columns, rows)`` for the ISO 5457 zone grid (#768) — the standard count for an
    A-series page (matched on BOTH dimensions), else ~50 mm zones for a custom page. Rows are
    clamped to the available letters (I/O skipped) so a very tall custom page can't over-index."""
    std = _ZONE_DIVISIONS.get((int(round(page_w)), int(round(page_h))))
    if std is not None:
        return std
    cols = max(2, round(page_w / 49.5))
    rows = min(len(_ZONE_LETTERS), max(2, round(page_h / 49.5)))
    return (cols, rows)


#: The title block's row height, matching `TitleBlock`'s own `cell_height`
#: default. Used to find the block's drawn top edge without building it.
_TB_ROW_H = 8.0
#: Gap between the drawn title block and the furniture sitting above it.
_TB_FURNITURE_GAP = 3.0
#: Horizontal space the ISO 5456-2 projection glyph reserves at the right-hand end
#: of the band above the title block, so the scale note placed to its left cannot
#: collide with it. Measured from the glyph's own extents at the default font.
_PROJECTION_BAND_W = 16.0

#: What the general-tolerance cell states when no tolerance was authored or sourced.
#: A blank cell is indistinguishable from an oversight and invites a shop to assume its
#: own house standard, so the absent case is named rather than left empty.
#: The cell is not tight — 48 mm on A4's 120 mm block, 60 mm above it, against 16.9 mm of
#: ink here — so this is a wording choice, not a width-forced one. The paired test still
#: measures it against the block's own `cell_bbox` on A4, the narrowest supported width,
#: because a longer replacement (a sentence, say) would overflow there and nowhere else.
_TOLERANCE_UNSPECIFIED = "UNSPECIFIED"


def _sheet_format(page_w: float, page_h: float) -> str:
    """The title-block format for an effective page, independent of orientation.

    Named and dimension-specified pages converge on the same physical sheet in
    :class:`Analysis`, so derive this from its settled dimensions rather than retaining
    the caller's spelling. Non-standard pages state their actual size; the neighbouring
    UNITS cell supplies the unit.
    """
    for name, (width, height) in _PAGE_SIZES.items():
        if (page_w, page_h) in ((width, height), (height, width)):
            return name
    return f"{_fmt(page_w)}x{_fmt(page_h)}"


def draftwright_title_block_layout() -> TitleBlockLayout:
    """The sheet's title-block arrangement: ISO 7200 complete, plus what practice needs.

    Every ISO 7200:2004 **mandatory** data field has a cell — legal owner (5.1.2),
    identification number (5.1.3), date of issue (5.1.5), segment/sheet number
    (5.1.6), title (5.2.2), approval person (5.3.4), creator (5.3.5) and document
    type (5.3.6). All rows use one proportional grid, tuned around the standard's
    recommended capacities on the narrowest block. Its four internal lines sit at
    35%, 70%, 88% and 94%: each row spans those columns as needed, so adjacent
    dividers align instead of ending in near-miss seams.

    Four cells are not ISO 7200 title-block fields and are here anyway: ``material``,
    ``general_tolerance``, drawing ``units`` and sheet ``format`` are ordinary
    manufacturing/drawing-control information, and dropping them to reach conformance
    would make the sheet worse. ``revision`` (5.1.4) is optional in the standard and
    kept because it is near-universal.

    ``scale`` is deliberately absent. ISO 7200 §4 keeps the block to a minimum and
    presents scale and the projection symbol "outside the title block only when
    used"; :func:`_add_scale_note` draws it beside the projection glyph, and the
    ``scale_not_stated`` lint makes its presence a checked guarantee rather than an
    assumption.
    """
    return TitleBlockLayout(
        (
            (
                TitleBlockCell("legal_owner", width=0.70, label="LEGAL OWNER"),
                TitleBlockCell("document_type", width=0.30, label="DOC. TYPE"),
            ),
            (
                TitleBlockCell("title", width=0.70, label="TITLE"),
                TitleBlockCell("drawing_number", width=0.30, label="DWG NO."),
            ),
            (
                TitleBlockCell("material", width=0.35, label="MATERIAL"),
                TitleBlockCell("general_tolerance", width=0.35, label="GEN. TOL."),
                TitleBlockCell("units", width=0.18, label="UNITS"),
                TitleBlockCell("format", width=0.12, label="FORMAT"),
            ),
            (
                TitleBlockCell("designed_by", width=0.35, label="DRAWN BY"),
                TitleBlockCell("approved_by", width=0.35, label="APPROVED BY"),
                TitleBlockCell("date", width=0.18, label="DATE"),
                TitleBlockCell("revision", width=0.06, label="REV"),
                TitleBlockCell("sheet", width=0.06, label="SHEET"),
            ),
        )
    )


def _make_title_block(dwg, a: Analysis):
    """Construct + page-locate the title block, returning ``(tb, cell)`` where *cell* is its
    drawn-by cell bbox (for the hyperlink rect). Shared by :func:`_add_title_block` (which adds
    it, last) and :func:`_title_block_box` (which measures its footprint for GD&T avoidance, #481)
    so the two never drift."""
    title = _font_safe_text(a.title)
    number = _font_safe_text(a.number)
    tolerance = _font_safe_text(_TOLERANCE_UNSPECIFIED if a.tolerance is None else a.tolerance)
    designed_by = _font_safe_text(_attribution_author(a.drawn_by))
    material = _font_safe_text(a.material)
    # Stripped, because the TitleBlock strips these two before deciding which
    # cells to draw. Left unstripped they disagree: a whitespace revision is no
    # revision to the block (which then draws the date in the shared cell) but a
    # truthy one here, so `revision or date` recorded "  " and the drawn date
    # reached neither the PDF text layer nor the overflow lint,
    # wearing spaces. A padded date likewise measured wider than the block drew.
    date = _font_safe_text(a.date).strip()
    revision = _font_safe_text(a.revision).strip()
    # Stripped for the same reason as date/revision below: the TitleBlock
    # strips it and only creates a legal_owner cell when what is left is
    # truthy, so an unstripped "   " passed the `if not value` filter here and
    # then raised KeyError from cell_bbox(); " ACME " recorded the padded string
    # while the block drew the stripped one.
    legal_owner = _font_safe_text(a.company).strip()
    approved_by = _font_safe_text(a.approved_by).strip()
    document_type = _font_safe_text(a.document_type).strip()
    sheet = _font_safe_text(a.sheet).strip()
    units = "mm"
    sheet_format = _font_safe_text(_sheet_format(a.PAGE_W, a.PAGE_H))
    layout = draftwright_title_block_layout()
    tb = TitleBlock(
        title,
        number,
        line_width=_TB_LINE_WIDTH,
        general_tolerance=tolerance,
        designed_by=designed_by,
        material=material,
        date=date,
        revision=revision,
        legal_owner=legal_owner,
        layout=layout,
        # The three ISO 7200 mandatory fields TitleBlock has no parameter for.
        values={
            "approved_by": approved_by,
            "document_type": document_type,
            "sheet": sheet,
            "units": units,
            "format": sheet_format,
        },
        width=a.TB_W,
        # Title block renders in condensed sans (the tight ISO 7200 cells), a
        # different face from the monospace dimensions — so it carries its own
        # pinned-font draft rather than reusing dwg.draft.
        draft=draft_preset(
            font_size=dwg.draft.font_size,
            decimal_precision=dwg.draft.decimal_precision,
            font_path=PLEX_SANS_CONDENSED,
        ),
    )
    # Drawn-by cell geometry, from the block's own public cell bbox rather
    # than hardcoded column fractions, so the hyperlink rect tracks any upstream
    # TitleBlock layout change. Build-frame bbox; translated to page space below.
    cell = tb.drawn_by_cell_bbox()
    margins = _title_margins(a)
    bx, by = a.PAGE_W - a.TB_W - margins.right, margins.bottom
    tb = tb.locate(Location((bx, by, 0)))

    # Retain authoritative title-block values at their public cell centres for the PDF semantic
    # text layer.  The visible block stays path-rendered; these specs merely let export embed the
    # same bundled condensed face as invisible selectable text without parsing SVG geometry.
    # Every field below has its own cell in the layout, so each is named for
    # itself. The former shared-cell case — a date falling back into the
    # revision cell when no revision was set — is gone with the cell it worked
    # around, and leaving it in emitted the date twice.
    fields = (
        ("title", title),
        ("drawing_number", number),
        # `scale` is not here: it has no cell. ISO 7200 §4 presents it outside
        # the block, where `_add_scale_note` draws it and the
        # `scale_not_stated` lint guarantees it.
        ("material", material),
        ("units", units),
        ("format", sheet_format),
        ("approved_by", approved_by),
        ("document_type", document_type),
        ("sheet", sheet),
        ("revision", revision),
        ("date", date),
        ("general_tolerance", tolerance),
        ("designed_by", designed_by),
        ("legal_owner", legal_owner),
    )
    specs = []
    for field, value in fields:
        if not value:
            continue
        box = tb.cell_bbox(field)
        specs.append(
            (
                value,
                bx + (box["min_x"] + box["max_x"]) / 2.0,
                by + (box["min_y"] + box["max_y"]) / 2.0,
                dwg.draft.font_size,
                PLEX_SANS_CONDENSED,
            )
        )
    tb.pdf_text_specs = tuple(specs)
    # Keep the exact rendered field inputs for structural cell-overflow checks. Cell geometry
    # remains owned by TitleBlock.cell_bbox(); it is not copied into a second layout model.
    tb.title_field_specs = tuple(
        (field, value, dwg.draft.font_size, PLEX_SANS_CONDENSED)
        for field, value in fields
        if value
    )
    return tb, cell


def _cached_title_block(dwg, a: Analysis):
    """Share one constructed block across footprint, placement and build retries."""
    margins = _title_margins(a)
    key = (
        a.PAGE_W,
        a.PAGE_H,
        a.TB_W,
        a.title,
        a.number,
        a.tolerance,
        a.drawn_by,
        a.material,
        a.date,
        a.revision,
        a.company,
        a.approved_by,
        a.document_type,
        a.sheet,
        dwg.draft.font_size,
        dwg.draft.decimal_precision,
        PLEX_SANS_CONDENSED,
    )
    prototype, cell = dwg.title_block_for(key, lambda: _make_title_block(dwg, a))
    bx, by = a.PAGE_W - a.TB_W - margins.right, margins.bottom
    at = prototype.location.position
    dx, dy = bx - at.X, by - at.Y
    if dx == 0 and dy == 0:
        return prototype, cell
    tb = copy(prototype).locate(Location((bx, by, 0)))
    tb.pdf_text_specs = tuple(
        (value, x + dx, y + dy, size, font) for value, x, y, size, font in prototype.pdf_text_specs
    )
    return tb, cell


def _title_block_box(dwg, a: Analysis):
    """The title block's real page-space bbox ``(x0, y0, x1, y1)``. GD&T placement avoids it
    (#481): the block is added last, so strip placement can't see it, but it's deterministic."""
    tb, _ = _cached_title_block(dwg, a)
    b = tb.bounding_box()
    return (b.min.X, b.min.Y, b.max.X, b.max.Y)


def _add_title_block(dwg, a: Analysis):
    """Add the title block annotation."""
    prototype, cell = _cached_title_block(dwg, a)
    # Candidate drawings may share the build cache. The placed annotation owns a
    # separate wrapper so edits/removal on one candidate cannot alter another.
    tb = copy(prototype)

    # Record that cell's page-space rectangle so export() can place a clickable
    # draftwright hyperlink over the "… / draftwright" author text. The build-frame
    # cell corners are offset by the block's page location (bx, _TB_CLEAR). The
    # rect rides the title-block annotation itself (like ``covers_diameters`` /
    # ``is_centerline`` riders), NOT an expando poked onto the drawing — the
    # drawing is not the state bus (ADR 1); export reads it back via
    # ``get_annotation("title_block")``, so a removed block drops its link too.
    margins = _title_margins(a)
    bx = a.PAGE_W - a.TB_W - margins.right
    tb.draftwright_link_rect = (
        bx + cell["min_x"],
        margins.bottom + cell["min_y"],
        bx + cell["max_x"],
        margins.bottom + cell["max_y"],
    )
    place_annotation(
        dwg.registry,
        dwg.items,
        tb,
        "title_block",
        feature=dwg.general_tolerance_source,
    )


def _make_sheet_frame(a: Analysis) -> Compound:
    """The sheet border rectangle (#767) — a closed outline at the ``_MARGIN`` inset (the old
    drawable boundary). Content clears it because ``a.margin`` is the reserved content margin.
    Carries an ``is_sheet_frame`` rider (like ``is_centerline``) so lint skips its page-spanning
    box, and so ``get_annotation`` / a removed frame drop it cleanly."""
    x0, y0, x1, y1 = _frame_margins(a).bounds(a.PAGE_W, a.PAGE_H)
    frame = Compound(
        children=[
            Edge.make_line(Vector(x0, y0, 0), Vector(x1, y0, 0)),
            Edge.make_line(Vector(x1, y0, 0), Vector(x1, y1, 0)),
            Edge.make_line(Vector(x1, y1, 0), Vector(x0, y1, 0)),
            Edge.make_line(Vector(x0, y1, 0), Vector(x0, y0, 0)),
        ]
    )
    frame.is_sheet_frame = True  # furniture, not a dimension/view — exempt from overlap lint
    return frame


def _add_sheet_frame(dwg, a: Analysis):
    """Add the sheet border (#767), drawn last like the title block. No-op is the caller's
    (gated on ``a.frame``)."""
    place_annotation(dwg.registry, dwg.items, _make_sheet_frame(a), "sheet_frame")


def _title_block_top(a: Analysis) -> float:
    """Page y of the drawn title block's top edge.

    The block reserves `_TB_H` but draws `rows x cell_height`, so the furniture
    above it should sit against the block rather than at the top of the reserved
    band — otherwise the gap is whatever slack the band happens to carry.
    """
    return _title_margins(a).bottom + len(draftwright_title_block_layout().rows) * _TB_ROW_H


def _add_scale_note(dwg, a: Analysis):
    """Draw the sheet scale beside the projection glyph, above the title block.

    ISO 7200 §4 keeps the title block to a minimum and presents the remaining
    fields "outside the title block only when used, e.g. scale, projection
    symbol". The projection glyph already lives in the band the block reserves
    but does not draw into (`_TB_H` minus the block's own height); the scale
    sits to its left, in the same band and the same condensed face.

    Unconditional, unlike the projection symbol: a drawing that does not state
    its scale cannot be measured off, so there is no mode in which omitting it
    is right. The `scale_not_stated` lint checks the result rather than trusting
    this function.
    """
    from build123d_drafting import Note

    note = Note(
        f"SCALE {format_drawing_scale(a.SCALE)}",
        (0, 0),
        draft=draft_preset(
            font_size=dwg.draft.font_size,
            decimal_precision=dwg.draft.decimal_precision,
            font_path=PLEX_SANS_CONDENSED,
        ),
    )
    b = note.bounding_box()
    bx, by = (b.min.X + b.max.X) / 2, (b.min.Y + b.max.Y) / 2
    w, h = b.max.X - b.min.X, b.max.Y - b.min.Y
    # Left of the projection glyph, which reserves `_PROJECTION_BAND_W` at the
    # right-hand end of the same band.
    right = (
        a.PAGE_W
        - max(_title_margins(a).right + 3, _analysis_margins(a).right)
        - _PROJECTION_BAND_W
    )
    cx = right - w / 2
    cy = _title_block_top(a) + _TB_FURNITURE_GAP + h / 2
    note = note.locate(Location((cx - bx, cy - by, 0)))
    note.is_scale_note = True
    place_annotation(dwg.registry, dwg.items, note, "scale_note")


def _add_default_surface_finish(dwg, a: Analysis):
    """Draw an imported document-wide ISO 1302 requirement without a leader.

    The reserved band above the title block is sheet furniture space.  A document default
    belongs there: hanging it from model geometry would falsely narrow its scope to one face.
    """
    requirement = dwg.default_surface_finish_source
    if requirement is None:
        return

    from build123d_drafting import SurfaceFinish

    symbol = SurfaceFinish(
        f"Ra {requirement.ra}",
        (0, 0),
        draft=draft_preset(
            font_size=dwg.draft.font_size,
            decimal_precision=dwg.draft.decimal_precision,
            font_path=PLEX_SANS_CONDENSED,
        ),
    )
    box = symbol.bounding_box()
    width, height = box.max.X - box.min.X, box.max.Y - box.min.Y
    label = symbol.label_bbox
    label_x = (label[0] + label[2] - box.min.X - box.max.X) / 2
    label_y = (label[1] + label[3] - box.min.Y - box.max.Y) / 2
    # Occupy the left end of the title-block furniture band. Scale and projection method use
    # its right end, leaving independent document controls readable on every standard sheet.
    cx = a.PAGE_W - a.TB_W - _title_margins(a).right + width / 2
    cy = _title_block_top(a) + _TB_FURNITURE_GAP + height / 2
    symbol = symbol.locate(Location((cx - box.min.X - width / 2, cy - box.min.Y - height / 2, 0)))
    symbol.pdf_text_relative_specs = (
        (
            symbol.label,
            label_x,
            label_y,
            dwg.draft.font_size,
            PLEX_SANS_CONDENSED,
            "IBM Plex Sans Condensed",
            "REGULAR",
            "center",
            "middle",
        ),
    )
    symbol.is_default_surface_finish = True
    place_annotation(
        dwg.registry,
        dwg.items,
        symbol,
        "default_surface_finish",
        feature=requirement,
    )


def _add_projection_symbol(dwg, a: Analysis):
    """Place the ISO 5456-2 projection-method glyph (#769) in the reserved title-block band,
    just above the drawn title block (deterministic empty space — the block reserves _TB_H but
    draws shorter). Registered ``projection_symbol`` with an ``is_projection_symbol`` identity
    rider. Unlike the page-spanning frame it is NOT lint-exempt: it's a small, well-placed glyph,
    so lint covers it and a future mispositioning is caught. Suppressed only by
    ``projection_symbol=False``."""
    if not a.projection_symbol:
        return

    from build123d_drafting import ProjectionSymbol

    sym = ProjectionSymbol(
        a.projection_convention,
        draft=draft_preset(
            font_size=dwg.draft.font_size,
            decimal_precision=dwg.draft.decimal_precision,
            font_path=PLEX_SANS_CONDENSED,
        ),
    )
    b = sym.bounding_box()
    bx, by = (b.min.X + b.max.X) / 2, (b.min.Y + b.max.Y) / 2
    w, h = b.max.X - b.min.X, b.max.Y - b.min.Y
    # Right side of the title-block column, near the top of its reserved band.
    # Sheet frames reserve an inner content margin; keep furniture inside it too.
    cx = a.PAGE_W - max(_title_margins(a).right + 3, _analysis_margins(a).right) - w / 2
    cy = _title_block_top(a) + _TB_FURNITURE_GAP + h / 2
    sym = sym.locate(Location((cx - bx, cy - by, 0)))
    sym.is_projection_symbol = True
    place_annotation(dwg.registry, dwg.items, sym, "projection_symbol")


def _add_zone_grid(dwg, a: Analysis):
    """Draw the ISO 5457 zone-grid border ruler (#768) — numbers 1.. along the top/bottom
    edges, letters A.. (skipping I/O) down the left/right — in the band between the frame
    (at ``_MARGIN``) and the page edge. Requires ``a.frame`` (the ticks sit on the border);
    the caller gates on ``a.zones`` and ensures the frame. Tick lines register as ``zone_grid``
    (``is_zone_grid``); each label carries an ``is_zone_label`` rider so it is exempt from the
    page-bounds lint (it legitimately sits outside the drawable) — but NOT from overlap lint."""
    from build123d_drafting import Note

    cols, rows = _zone_divisions(a.PAGE_W, a.PAGE_H)
    margins = _frame_margins(a)
    for edge in ("left", "right", "top", "bottom"):
        if getattr(margins, edge) == 0:
            raise ValueError(f"zones require a positive margin_{edge} for their labels")
    x0, y0, x1, y1 = margins.bounds(a.PAGE_W, a.PAGE_H)
    cw, rh = (x1 - x0) / cols, (y1 - y0) / rows
    draft = draft_preset(
        font_size=dwg.draft.font_size * 0.8,
        decimal_precision=dwg.draft.decimal_precision,
        font_path=PLEX_SANS_CONDENSED,
    )
    ticks = []
    for i in range(1, cols):  # interior column boundaries → ticks on top + bottom edges
        xb = x0 + i * cw
        ticks.append(
            Edge.make_line(Vector(xb, y0, 0), Vector(xb, y0 - min(3.0, margins.bottom * 0.6), 0))
        )
        ticks.append(
            Edge.make_line(Vector(xb, y1, 0), Vector(xb, y1 + min(3.0, margins.top * 0.6), 0))
        )
    for j in range(1, rows):  # interior row boundaries → ticks on left + right edges
        yb = y0 + j * rh
        ticks.append(
            Edge.make_line(Vector(x0, yb, 0), Vector(x0 - min(3.0, margins.left * 0.6), yb, 0))
        )
        ticks.append(
            Edge.make_line(Vector(x1, yb, 0), Vector(x1 + min(3.0, margins.right * 0.6), yb, 0))
        )
    grid = Compound(children=ticks)
    grid.is_zone_grid = True
    place_annotation(dwg.registry, dwg.items, grid, "zone_grid")

    def _label(text, cx, cy, name, edge):
        note = Note(text, (cx, cy), draft, align=(Align.CENTER, Align.CENTER))
        size = note.bounding_box().size
        extent = size.X if edge in ("left", "right") else size.Y
        if extent > getattr(margins, edge) + 1e-6:
            raise ValueError(f"margin_{edge} is too narrow for zone labels ({extent:.2f} mm)")
        note.is_zone_label = True
        place_annotation(dwg.registry, dwg.items, note, name)

    for i in range(cols):  # numbers 1.. left→right, in the bottom + top bands
        cx = x0 + (i + 0.5) * cw
        _label(str(i + 1), cx, y0 - margins.bottom / 2, f"zone_num_b_{i}", "bottom")
        _label(str(i + 1), cx, y1 + margins.top / 2, f"zone_num_t_{i}", "top")
    for j in range(rows):  # letters A.. top→bottom, in the left + right bands
        cy = y1 - (j + 0.5) * rh
        _label(_ZONE_LETTERS[j], x0 - margins.left / 2, cy, f"zone_ltr_l_{j}", "left")
        _label(_ZONE_LETTERS[j], x1 + margins.right / 2, cy, f"zone_ltr_r_{j}", "right")
