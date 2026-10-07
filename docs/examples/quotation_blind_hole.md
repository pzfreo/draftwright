# Quotation blind-hole comparison (#2233)

The original quotation STEP file and edited drawing are not available in this repository.
`quotation_blind_hole.py` therefore uses a public, reproducible 50 mm turned shaft with a
24 mm deep axial blind bore. It exercises the reported drawing decision without claiming to
reproduce the connector or agent that produced the original quotation.

Run from the repository root:

```bash
python docs/examples/quotation_blind_hole.py --out-dir /tmp/draftwright-2233 \
  --date 2026-10-07 --sheet 1/1
```

The supplied date and sheet number appear in the title block. Omit them if unknown; the
script does not invent approval, material, revision, or release status. It writes three
PDF/PNG pairs, full drawing reports, and `comparison.json`:

- `baseline`: default planner, which does not request an axial section for this X-axis
  blind bore (`section_decision: not_warranted`). This is a planner-policy choice, not a
  failed placement of a required section.
- `section-with-iso`: declared required axial section, with the existing hidden-line ISO.
- `section-visible-iso`: the same section and ISO, with
  `sheet.add_view("iso").hidden_lines(False)`.

The required section is either placed or the script fails before exporting it. The
comparison also requires the same measured-fact ledger and supplied H7 bore fit in all
three variants. On this fixture the first two ISOs contain 10 hidden edges; the last
contains none while retaining its visible edges and orientation view. The section shows
the blind-hole profile and bottom. Full lint and audited completeness accompany each
PDF; a clean lint alone is not treated as proof of manufacturing completeness.

The printed `30` overall height and `⌀30` head diameter describe the same radial extent
on this fixture. The printed 8 and 42 axial lengths do **not** form a closed three-value
chain because no 50 overall length is printed. This is a review observation, not a
general redundancy deletion rule; functional priority remains an author decision (#941).
