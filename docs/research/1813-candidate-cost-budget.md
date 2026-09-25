# #1813 candidate-first cost budget (pre-rollout)

This sets numerical stop criteria **before** changing a public default. It is a
budget, not evidence that the candidate is safe or fast on supported platforms.
Measure the same parts with the same caller page, scale, and policy on CI-class
Linux, macOS, and Windows, separately for typical and large CAD. A baseline
worker is an **offline comparator**; it is not part of an ordinary candidate
request. Report each platform/cohort's sample count, machine/core/RAM, source
SHA, median, and nearest-rank p95. Do not pool platforms or hide a failed or
timed-out worker inside a successful latency sample.
An 18-core development-host run may supplement but cannot replace the three
supported-platform results.

| Measure | Required budget on **each** platform and size cohort |
| --- | --- |
| Candidate-only end-to-end process time | Median and p95 each at most **1.25×** the corresponding baseline statistic. |
| Candidate-only peak RSS | Median and p95 each at most **1.25×** baseline; configured CAD concurrency × candidate p95 RSS at most **75%** of physical RAM. |
| Interim safety-fallback frequency | At most **5%** of representative requests after independent admission is calibrated; report the numerator, denominator, and every reason. A missing rate is a failed gate, not zero. |
| Interim total end-to-end time, including fallback | Median and p95 each at most **1.25×** baseline. Count both builds and any failed candidate time in fallback requests. |

Fallback, if used during the transition, must replay the same resolved caller
page, scale, and policy; a larger sheet or relaxed scale cannot buy a cost pass.
These ratios are ceilings, not targets. A semantic loss, introduced required
blocker, unverified safety admission, or unreviewed visual regression fails its
own gate even if cost is within budget. The eventual no-fallback path must be
measured again; the interim fallback rate is not evidence that removal is safe.
If a platform or large-part result exceeds a ceiling, retain the baseline
default and review the budget or implementation **before** any switch, with the
reason recorded rather than relaxing a threshold after seeing a failing run.

The **25%** time/RSS allowance leaves room above the observed Linux fixed15
candidate median overhead (~16%) while still requiring a meaningful reduction
from the opt-in paired `best` mode's cost; it is a policy ceiling, not a
statistical confidence interval. The **5%** fallback ceiling keeps second-build
work exceptional rather than turning the interim policy back into routine
paired evaluation. The **75%** RAM cap reserves headroom for the interpreter,
export, OS, and workload variance so a core-count-based job limit cannot swap a
CI-class machine into apparent slowness. None of these choices is retroactive
evidence of a platform pass.

## Existing Linux observation, not a budget pass

The saved v4 fixed15 report from source
`4ab713e52834b5852416bbbdad428c62a9b28605` (Linux x86_64, four
physical/logical cores, 7,941 MiB RAM; full local report SHA-256
`c4e4bbc3f90e86710b44ad5befed0c652dd38da524c41e11284ba904281b20a4`)
contains 15 offline candidate/baseline pairs. Candidate process median/p95
were 46.21/278.21 s, versus baseline 39.94/274.54 s: **1.16×/1.01×**.
Candidate peak RSS median/p95 were 537.27/877.68 MiB, versus baseline
536.39/878.98 MiB: **1.00×/1.00×**. Baseline RSS figures here were recomputed
from the existing per-case worker samples, not from a new CAD run. The
[versioned v4 trial summary](1813-bounded-chooser-trial-v4.md) records the
other values and its safety/visual limitations.

This is one development host and a fixed15 mixed-case sample, with no
fallback path. It cannot establish macOS/Windows, large/user-part, or interim
fallback budgets. The manual three-platform cost workflow added in #1825 is
not dispatchable until that workflow reaches the default branch. No default
switch or fallback removal is authorized by this document.
