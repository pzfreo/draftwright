# Archived frame export measurements — #1537

Measured the six original exports under
[`whistle-key@76ccdc2/drawings/titanium`](https://github.com/pzfreo/whistle-key/tree/76ccdc2/drawings/titanium)
on macOS with Python 3.14.7 and ezdxf 1.4.4. These are the field-report artifacts, not regenerated
output from current Draftwright. The archived generator and dependency record determine their
generation environment; the analyzer's environment determines only these measurements.

## Results

| Sheet | DXF bytes | SVG bytes | PDF bytes | DXF gzip bytes |
|---|---:|---:|---:|---:|
| Features | 8,548,578 | 1,479,246 | 565,358 | 667,005 |
| General | 8,743,378 | 1,519,262 | 561,741 | 682,691 |

| DXF entity | Features count / bytes | General count / bytes |
|---|---:|---:|
| SPLINE | 20,265 / 6,273,619 | 20,311 / 6,308,932 |
| LINE | 13,752 / 2,206,445 | 14,841 / 2,365,004 |
| ELLIPSE | 162 / 36,730 | 162 / 36,722 |
| ARC | 76 / 12,495 | 76 / 12,147 |
| CIRCLE | 30 / 3,327 | 42 / 4,611 |

The raw DXF section-byte partition accounts for every byte, including headers, classes,
tables, blocks, objects and section delimiters. Entity counts independently agree with
`ezdxf.readfile(path).modelspace()`. These fixtures have no extra paper-space entities; the
analyzer refuses a census mismatch rather than silently ignoring a different layout.

The `dims` layer holds 31,999 / 32,697 entities and 7,860,774 / 7,952,882 bytes. Part geometry
accounts for 159,119 / 170,899 bytes and hidden geometry for 512,723 / 603,635 bytes. There are
no native TEXT/MTEXT entities. Of the splines, 20,075 / 20,069 are degree 2. This identifies
annotation vector data as the dominant storage cost; it does not attribute every annotation
curve specifically to a font glyph.

SVG path-data strings account for 1,301,474 / 1,323,976 bytes. Parsed XML element counts and
path commands are recorded in the result JSON. This byte count covers decoded path-attribute
values, not all XML syntax. gzip sizes for SVG are 306,272 / 300,672 bytes and for PDF are
460,903 / 458,069 bytes. gzip is measured with `mtime=0`; compressed size depends on the
compressor version. Every compressed artifact was decompressed and compared with its original
bytes. No exporter or drawing geometry was changed.

## Repeat the measurement

Download the six files from the pinned directory into a scratch directory. Verify their hashes
against [the recorded result](1537-export-size.json), then run from the Draftwright checkout:

```sh
uv run python docs/research/1537-measure-exports.py \
  /path/to/frame-exports > /tmp/frame-export-measurements.json
```

The analyzer requires exactly the named six files and reads their bytes. It measures ASCII DXF
tags and parses SVG; it neither imports STEP geometry nor runs recognition, lint or rendering.
It is a bounded research tool, not a general CAD-file validator or an export-size CI threshold.
Malformed/unexpected DXF record layouts fail rather than being repaired for counting.

## Decision and next useful work

Retain the exported vector fidelity and use lossless packaging for transport. The
[artifact workflow](../reference/generated-artifacts.md) keeps generators and evidence
reviewable while preventing generated graphics from dominating text diffs. The measurements
do not establish an exporter defect that warrants removing curves or adding a lossy option.

If uncompressed DXF size becomes a demonstrated downstream limit, the next investigation is
repeated annotation geometry: measure opportunities for exact block reuse and the receiving
CAD systems' support before changing the representation. It must preserve physical curves,
annotation membership, labels, transformations and downstream compatibility. The current
evidence does not prove such a change safe. #1255's portable generator paths and PDF background
work are separate from compression.

ADRs 1–5: this delivery adds no compiler, layout, recognition or intent path. The bundle retains
existing evidence without merging its identities, changing coverage or implying readiness.
