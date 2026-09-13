"""Opt-in folded-sheet margins and title block; run with `uv run` from the repository.

Writes dist/sergio-sheet.{svg,dxf,pdf,png}. Existing sheet defaults are unchanged.
"""

from pathlib import Path

from build123d import Box, Cylinder, Pos

from draftwright import Sheet


def main():
    blank = Box(80, 50, 20)
    bore = Pos(20, 12.5, 0) * Cylinder(4, 30)
    sheet = Sheet(
        blank - bore,
        page="A3",
        scale=1,
        frame=True,
        zones=True,
        margin_left=25,
        margin_right=10,
        margin_top=10,
        margin_bottom=10,
        title_block_width=175,
        title="MOUNTING PLATE",
        number="SERGIO-01",
    )
    sheet.authored_dimensions()
    envelope = sheet.envelope()
    hole = sheet.hole(bore)
    for feature in (envelope, hole):
        for role in feature.dimension_ids():
            sheet.dimension(feature, role)
    sheet.dimension(hole, "location")
    drawing = sheet.build()
    for issue in drawing.lint():
        print(f"{issue.severity}: {issue.code}: {issue.message}")
    Path("dist").mkdir(exist_ok=True)
    print(drawing.export("dist/sergio-sheet", formats=("svg", "dxf", "pdf", "png")))


if __name__ == "__main__":
    main()
