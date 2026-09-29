"""Typed build-owned state carried by a finished Drawing (ADR 1 / ADR 3)."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quiddity import RecognitionResult
    from quiddity.evidence import RecognitionEvidence

    from draftwright.recognition_ownership import RecognitionOwnership

from draftwright._core import Analysis
from draftwright.linting import LintIssue
from draftwright.recognition_cache import RecognitionCache

# ``None`` is a cacheable failed mesh, distinct from a mesh not yet attempted.
_MATERIAL_MESH_UNSET = object()


@dataclass
class BuildState:
    """The build context a finished :class:`Drawing` carries (ADR 1 (was 0005 §2) / #639).

    One typed home for what used to be four loose private attributes:

    - ``analysis`` — the pipeline's :class:`Analysis` namespace.
    - ``part_model`` — the detected/declared ADR 1 (was 0008) PartModel (read surface for
      semantic edits, #397).
    - ``recognition`` — the ADR 3 (was 0017) aggregate reused by model detection and critique.
    - ``recognition_ownership`` — same-run represented, grouped/pattern, nested, conditional
      aggregate, and ownerless occurrence outcomes captured while conversion makes the decision;
      provider references never enter the IR waist.
    - ``view_edge_cache`` — lint's per-view edge bboxes, keyed on id(view shape)
      (helpers #143/#164).
    - ``ann_box_cache`` — lint's annotation bounding boxes (#602): identity- AND
      location-token-checked entries (see ``_ann_box``), pruned by ``lint()``.
    - ``principal_profile_cache`` — solid-derived unsupported principal-profile issues
      reused by repeated physical critique (#1058).
    - ``trace`` — the opt-in solve-trace recorder (#736,
      :class:`~draftwright.annotations._common.SolveTrace`), or ``None`` (default:
      tracing off). Carried here so the finalize path traces like the auto pass.
    - ``detail_view`` — the resolved ``build_drawing(detail_view=...)`` setting,
      persisted so the finalize drain gates the prismatic detail request exactly
      as the auto pass does (#661) — on the ``auto_dims=False`` path the flag
      would otherwise be consumed nowhere.

    The builder assembles it at one site; ``recognition`` may also be filled once by
    :meth:`ensure_recognition` for declared-path critique. The compat properties on
    ``Drawing`` read through it, so ``dwg._analysis``-style test inspection keeps working.
    """

    analysis: Analysis | None = None
    recognition_cache: RecognitionCache = dataclasses_field(default_factory=RecognitionCache)
    recognition_ownership: RecognitionOwnership | None = None
    part_model: object | None = None
    view_edge_cache: dict = dataclasses_field(default_factory=dict)
    ann_box_cache: dict = dataclasses_field(default_factory=dict)
    #: The title block's deterministic page-space footprint, measured before it is
    #: drawn so strip placement can avoid it (#1593). None until the builder sets it.
    pending_title_block_box: tuple | None = None
    #: Constructed title blocks shared by assembly and measured repack passes of this build.
    title_block_cache: dict = dataclasses_field(default_factory=dict)
    #: Imported document default selected as the title-block tolerance carrier. ``None``
    #: when the caller supplied any explicit value, even identical display text.
    general_tolerance_source: object | None = None
    #: Imported document-wide surface finish rendered as title-block furniture.
    default_surface_finish_source: object | None = None
    #: Per-view filled projected material (#798) as ``{id(view_shape): (shape, field)}``.
    #: Keyed by shape identity because the projected shapes carry no view label (lint
    #: takes their names from ``Drawing.views`` since #1196), and holding the shape
    #: alongside lets a reused ``id`` be detected — the same guard
    #: ``_view_edge_entries`` carries (#143).
    material_fields: dict = dataclasses_field(default_factory=dict)
    #: The one tessellation behind those fields, or ``None`` once it has been attempted
    #: and failed. Memoised separately so an unmeshable part is not re-meshed on every
    #: lint, and so views added by later stages can be lowered without redoing it.
    material_mesh: Any = _MATERIAL_MESH_UNSET
    principal_profile_cache: tuple[object, bool, tuple[LintIssue, ...]] | None = None
    trace: Any = None
    detail_view: bool = False
    #: The ADR 2 (was 0018) :class:`~draftwright.view_plan.ResolvedViewPlan` — which views this drawing
    #: has and where their blocks sit. ONE typed attachment, filled once by the builder at the
    #: same site it creates the views, because the alternative the ADR names explicitly is what
    #: the topology was before: the answer spread across `Analysis` fields, three hardcoded
    #: `_add_view` calls and a docstring, with no single thing to read or replace.
    view_plan: Any = None
    #: The compiler's :class:`~draftwright.model.compiled.Omission` records — every
    #: measurement it considered and did not approve, with the rule that stopped it (#996).
    #: The compiled plan was a local in the orchestrator: built, read by the renderers, and
    #: dropped. So the one place recording WHY a dimension is absent did not outlive the
    #: build, and absence had to be inferred from a finished sheet — which is how a wrong
    #: suppression rule produced four issue reports before anyone found the rule (#997).
    omissions: tuple = ()

    @property
    def recognition(self) -> RecognitionResult | None:
        """The immutable result held by Draftwright's consumer-owned lifecycle cache."""

        return self.recognition_cache.result

    @recognition.setter
    def recognition(self, value: RecognitionResult | None) -> None:
        self.recognition_cache.seed(value)
        self.recognition_ownership = None

    def attach_recognition(
        self,
        result: RecognitionResult | None,
        *,
        evidence: RecognitionEvidence | None = None,
        cache: RecognitionCache | None = None,
        ownership: RecognitionOwnership | None = None,
    ) -> None:
        """Attach one coherent acquisition at the builder's single fill site.

        A rebuilt drawing either receives the prior run's complete cache or a result/evidence
        pair from its current analysis. Mixing both sources would make run ownership ambiguous
        and therefore fails closed.
        """

        if cache is not None:
            if result is not None or evidence is not None or ownership is not None:
                raise ValueError("cannot attach both a recognition cache and a new acquisition")
            self.recognition_cache = cache
            self.recognition_ownership = None
            return
        if ownership is not None and ownership.evidence is not evidence:
            raise ValueError("recognition ownership and evidence must come from the same run")
        self.recognition_cache.seed(result, evidence=evidence)
        self.recognition_ownership = ownership

    @property
    def recognition_evidence(self) -> RecognitionEvidence | None:
        """Run-scoped provider evidence paired with :attr:`recognition`, when available."""

        return self.recognition_cache.evidence

    def clear_geometry_caches(self) -> None:
        """The one invalidation seam (finalize rollback): view edges + annotation
        boxes together — a rolled-back drawing must re-measure everything."""
        self.view_edge_cache.clear()
        self.ann_box_cache.clear()
        self.material_fields.clear()
        self.material_mesh = _MATERIAL_MESH_UNSET

    def ensure_recognition(self, part, *, cylinders=None) -> RecognitionResult:
        """The run's recognition aggregate, recognising *part* once if nothing has yet.

        A declared build performs no recognition (ADR 4 (was 0011) / #1022), so critique on that path
        has no inventory to judge against and must produce one.  It is built **here**, in the
        typed build state, and at most once per drawing: a lint-side or ``Drawing``-side memo
        would make critique a second recognition owner, contrary to ADR 3.

        On a detected build ``recognition`` is already filled by the builder, so this returns
        it and recognises nothing.
        """
        return self.recognition_cache.ensure(part, cylinders=cylinders)
