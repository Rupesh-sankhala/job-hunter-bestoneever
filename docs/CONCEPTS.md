# Concepts — from first principles

This explains every term the project uses, assuming you know nothing about it. Read it
top to bottom; each idea builds on the one before. Examples use the synthetic fixture
CV (a fictional person, "Alex Morgan Rivera") so nothing here is anyone's real CV.

**Contents**

1. [The problem](#1-the-problem)
2. [Hit rate — measuring whether the problem is real](#2-hit-rate)
3. [Angle — the organizing idea](#3-angle)
4. [Domain — the second dimension](#4-domain)
5. [The bank — records, bullets, cells](#5-the-bank)
6. [Angle gaps](#6-angle-gaps)
7. [The coverage engine](#7-the-coverage-engine)
8. [The apply decision](#8-the-apply-decision)
9. [Assembly and the page budget](#9-assembly-and-the-page-budget)
10. [Verification — where the safety guarantee lives](#10-verification)
11. [The corpus pipeline — harvest, cluster, bank](#11-the-corpus-pipeline)
12. [Smaller terms, alphabetically](#12-smaller-terms)

---

## 1. The problem

You are applying to jobs. You have a handful of CV versions. For each job description
(**JD**) you must decide: which version do I send, do I edit it, and is it even worth
applying?

Doing this by hand is slow and inconsistent. Automating it naively is *worse*, because
the obvious automation — "ask a language model to rewrite my CV to match this job" —
produces text that drifts from the truth. It inflates numbers, invents adjacent
experience, and states things you cannot defend in an interview.

There is also a mechanical reason not to just stuff keywords in. Modern applicant
tracking systems do semantic matching against the JD, then a language model ranks the
survivors, then a human reads the shortlist. Keyword *stuffing* — terms with no
supporting evidence — is penalized by both the model and the human. Keyword *coverage* —
describing real work using the JD's vocabulary — is rewarded. The whole design turns on
that distinction.

So the project has one hard constraint:

> **The agent may re-select, re-order, and re-phrase content. It may never add a claim.**

Everything else follows from deciding *where to enforce that*.

## 2. Hit rate

**Hit rate** is not "how similar is my CV to this job." It is a yes/no decision, averaged:

> Out of N real job descriptions, for how many is one of my existing CV versions **good
> enough to send as-is**?

You measure it by hand. Take 10–15 real JDs, pick the version you'd send, open it, and
answer: would I send this unchanged? The fraction of yeses is the hit rate.

It matters because it decides how much software you need. A high hit rate means the
valuable tool is just "tell me which version to send and what my gaps are" — no
document generation at all. A low hit rate means you genuinely need to assemble custom
CVs, which is a much larger build.

On the sample this project was built against, the measured hit rate was **40%**, and the
60% of misses needed *different project selection and different framing* — not light
edits. That result is why both paths exist.

## 3. Angle

Here is the key finding. When a fixed CV version *didn't* fit, the mismatch wasn't random.

CV versions are conventionally cut along **role families** — ML Engineer, Product Data
Scientist, Research Data Scientist. But job descriptions don't vary along that axis. They
vary along what we call the **angle**:

> An **angle** is *what kind of value the role hires you to produce*.

It is independent of seniority, industry, and job title. Six angles cover the space:

| Angle | Hires you to | Signals in a JD |
|---|---|---|
| `decision_science` | turn data into decisions that move business metrics | causal inference, uplift, A/B tests, bandits, metric design |
| `production_ml` | ship and operate models at scale | MLOps, monitoring, CI/CD, pipelines, drift, "models as code not notebooks" |
| `agent_llm` | build LLM/agent systems and integrations | orchestration, tool calling, structured outputs, observability, APIs |
| `client_delivery` | co-build with customers and explain it | consulting, customer-facing, training, "explain to non-technical audiences" |
| `research` | produce defensible novel results | hypotheses, publications, PhD-preferred, evaluation design |
| `builder` | ship things fast, generalist | portfolio, "something you've shipped", speed, full-stack |

Two roles can both be titled "Data Scientist" and sit at opposite ends of this table.

**A JD is a blend, not a label.** A given job might be 60% `agent_llm`, 25%
`production_ml`, 15% `builder`. The parser emits weights, not a single classification.

**Why this reframing pays.** Three of the six angles had no CV version at all in the
original set, and those three accounted for four of the eight sampled JDs. That is the
40% hit rate, explained — and it points at a cheap fix (write the missing versions)
rather than an expensive one (build a generator).

### Angle resolution

If a JD is a blend, which angle's wording do you actually use? Mixing wording from
several angles in one document produces an incoherent voice. So:

> **The dominant angle owns the document.** The summary, section headings, and skills
> taxonomy all come from it. Then, per project, wording falls back:
> `dominant angle` → `strongest secondary angle` → `neutral`.

**`neutral`** is the master CV's wording. Every project has one, always. That guarantees
there is never a project the system cannot render, so the pipeline can't dead-end.

## 4. Domain

**Domain** is a *second, independent* dimension: the subject-matter framing.

This wasn't in the original design; it was forced by real data. Two CV versions —
one written for finance-risk roles, one generic — both mapped to the `production_ml`
angle. Same angle, genuinely different wording. Had domain been folded into angle, one
would have silently overwritten the other and half the authored material would have
vanished from the bank.

So wording is keyed by the **pair** `(angle, domain)`, where domain may be empty:

```
production_ml                  ← the generic production-ML version
production_ml/finance_risk     ← the finance-risk version, same angle
research                       ← the generic research version
research/quant                 ← the quant version, same angle
```

Resolution falls back `(angle, domain)` → `(angle, null)` → `neutral`.

## 5. The bank

The **bank** is the single source of truth — one YAML file holding everything any
generated CV is allowed to say. Its structure:

**Identity** — name, contact, employers, education. Immutable, byte-copied into every
render, never generated, and verified unchanged at the end.

**Record** — one project or piece of work. Not one *sentence about* it; the work itself.
`sequential_demand_ranking` is a record. It appears in six CV versions under six
different headings, and it is still one record.

**Bullet** — one line of CV text. `"CNN+BiLSTM ranking model cut manual review volume to
1/8th while retaining 75% recall on out-of-time data"` is a bullet.

**Variant** — one existing CV document, e.g. the master, or the product-angle version.

**Cell** — the intersection of a record and an `(angle, domain)` pair. This is the unit
that holds wording. One record has several cells, each with its own title and bullets:

```yaml
- id: sequential_demand_ranking
  facts: ["1/8th", "75% recall", "4M+ events"]     # numeric allowlist, see §10
  serves: [production_ml, decision_science]         # which angles show this record
  variants:
    neutral:                                        # always present
      title: "Sequential Demand Ranking for Inventory Allocation"
      bullets:
        - "CNN+BiLSTM ranking model cut manual review volume to 1/8th while retaining 75% recall"
    decision_science:
      title: "Decisioning — Inventory Allocation Ranking"
      bullets:
        - "Built a sequential ranking model cutting manual review volume to 1/8th…"
```

Note the two fields doing different jobs: **`serves`** decides *whether a record appears
at all* for a given angle; **`variants`** decides *how it is worded* once it does.

**The bank is never machine-written.** Every bullet in it was authored by a human in some
CV. The build tool populates cells from what already exists and leaves the rest empty —
it does not invent phrasing to fill a hole. That property is what makes §10 work.

## 6. Angle gaps

An **angle gap** is an empty cell: a record that has no wording for a given angle.

The build reports them as a coverage matrix — records down the side, `(angle, domain)`
cells across the top, bullet counts inside:

| record | `neutral` | `decision_science` | `production_ml` | `research` |
|---|---|---|---|---|
| `demand_ranking` | 2 | 2 | 2 | 1 |
| `anomaly_detection` | 2 | — | 2 | — |
| `account_scoring` | 2 | 2 | 1 | 1 |

Each `—` is a gap. A gap is **not a defect** — it's a to-do. It means "this project has
never been described from this angle," and filling it is a small authoring task: write
two bullets about work you already did, approve them once, and the agent can use them
forever.

Gaps cluster. If an entire angle is unauthored, every record shows a gap for it, and
that column is empty top to bottom. Those are the expensive gaps — they're the reason
whole classes of job have no CV to send.

## 7. The coverage engine

This is how the system decides whether your background matches a JD. It replaces the
obvious approach, and it's worth understanding why.

### Why not embedding similarity

The instinct is to embed the JD and the CV and take the cosine similarity. That works for
**ranking** (which of my CVs is closest to this JD?) but fails for **thresholding** (is
this CV good enough to send?). Cosine between two documents of this kind lands in a narrow
band, and the band shifts with document length and writing style — so a cut-off tuned on
one JD doesn't generalize to the next. A gate needs an absolute cut-off. Cosine can't
give one.

It's also opaque. When it misjudges, there's nothing to inspect.

### What it does instead

**Step 1 — atomize the JD into requirements.** Break the posting into individually
checkable items. The splitting rules matter:

| Pattern | Example | Rule |
|---|---|---|
| **AND-list** | "monitoring, drift detection, and retraining pipelines" | split into 3 separate requirements |
| **OR-list** | "deep learning frameworks (PyTorch, TensorFlow)" | one `any_of` group — **any** member satisfies it |
| **Composition** | "deploy deep learning models to production" | **do not split** — one competency |

The composition rule is the one that protects you. "Deploy DL models to production" is a
single skill, not `deep learning` + `deployment`. Split it, and the score could be
satisfied by having the two halves separately — which is exactly the inflation this
project exists to prevent. **Split on enumeration; never split on composition.**

Requirements are also deduplicated, because postings restate the same ask under
"Responsibilities" and again under "Requirements".

**Step 2 — adjudicate each requirement** against the bank as `met`, `partial`, or
`unmet`, with one non-negotiable rule:

> **Evidence is mandatory.** A requirement can only be `met` if it names a specific
> bullet. No bullet, no credit — regardless of what the model asserts.

This single rule kills most inflated coverage.

**Step 3 — score.** Each requirement gets a weight = `kind × category`:

- kind: `must` = 3, `nice` = 1
- category: `core` 1.0 · `domain` 0.8 · `tooling` 0.6 · `credential` 0.5 · `soft` **0**

`soft` requirements ("excellent communication skills") appear in every posting,
distinguish nothing, and would only inflate the denominator, so they're dropped rather
than scored. An `any_of` group is weighted once as a group, not once per member.

```
score = Σ(weight × status) / Σ(weight)        met = 1.0, partial = 0.5, unmet = 0
```

**Worked example.** A JD wanting: 5+ yrs production ML · PyTorch **or** TensorFlow ·
monitoring, drift detection, retraining · AWS/GCP · Docker/K8s · communication · fintech:

| Requirement | Kind | Category | Weight | Status |
|---|---|---|---|---|
| 5+ yrs production ML | must | credential | 1.5 | met |
| PyTorch **or** TF | must | tooling | 1.8 | met |
| model monitoring | must | core | 3.0 | met |
| drift detection | must | core | 3.0 | met |
| retraining pipelines | must | core | 3.0 | **unmet** |
| AWS/GCP | nice | tooling | 0.6 | met |
| Docker/K8s | nice | tooling | 0.6 | **unmet** |
| fintech domain | nice | domain | 0.8 | met |
| communication | — | soft | *dropped* | — |

`10.7 / 14.3 = 0.75`, and the gap list is *retraining pipelines, Kubernetes*.

That gap list is arguably the most useful output of the whole system: it's what to study
before the interview.

### The real risk: adjudication stability

`met`/`partial`/`unmet` is a language-model judgment, and judgments drift between runs.
If the same JD scores 0.71 on one pass and 0.78 on the next, any threshold built on top
is decoration. Three mitigations, all required:

1. **Mandatory evidence** (above).
2. **Adjudicate one requirement per call** — batching the whole list drifts toward a
   uniform verdict.
3. **Measure it.** Run the evaluation set three times and report agreement *before*
   trusting any threshold.

## 8. The apply decision

Runs first and can end the pipeline immediately. Three outcomes, on two thresholds:

```
score ≥ T_ship                    → apply              (send an existing CV as-is)
T_assemble ≤ score < T_ship       → apply_with_custom  (assemble one)
score < T_assemble                → dont_apply         (report the gaps)
```

Thresholds are **calibrated against the hand-scored hit-rate baseline**, not guessed.

`dont_apply` is a first-class result, not a failure. Being told "don't bother, and here's
the three things you're missing" is often worth more than a tailored CV — it saves the
hour and tells you what to learn.

## 9. Assembly and the page budget

When a custom CV is needed:

**Select records by marginal coverage gain** — how much *new* coverage each record adds
against requirements not yet satisfied, rather than by absolute relevance. Ranking by
absolute relevance picks five records that all satisfy the same requirement and leaves the
rest uncovered.

**Order:** employers stay chronological; records reorder by relevance within each employer.

**Fit the page by measuring, not predicting.** Whether a CV fits on one page depends on
how lines wrap in the typesetter, which you cannot compute from bullet counts. So render
it, count the pages, and if it's too long apply the next step of a **degradation ladder**:

1. Demote the lowest-ranked record to its one-line form
2. Demote the next
3. Drop the lowest-ranked one-line record
4. Drop the next
5. Trim the skills section to entries that support must-have requirements
6. Stop — hand to a human with the current best

Every step taken is reported. The ladder exists because a pipeline that can only hard-fail
on "doesn't fit" dead-ends in production with no output at all.

## 10. Verification

This is where the safety guarantee actually lives, and it's the part most worth
understanding.

### The approach that doesn't work

The natural design: let a model rewrite bullets to match the JD's vocabulary, then verify
the output by comparing it to the source with string similarity. This fails in **both**
directions at once:

- Rewriting into the JD's vocabulary is the *entire point*, so every legitimate rewrite
  pushes similarity **down**.
- A fabricated inflation — `1/8th` → `1/20th`, `75% recall` → `95% recall` — changes
  almost no characters, so similarity stays **high**.

You end up tuning a threshold that trades false alarms on honest rewrites against silent
passes on the one failure the system exists to prevent. There is no good setting.

### The approach that does

Move the guarantee from **runtime** to **authoring time**. A human approves each bullet
once, per angle. At runtime the agent only *selects*.

Now verification is a **dictionary lookup**: is this exact string in the bank? No
threshold, no similarity metric, no language model anywhere in the safety path. Strictly
stronger, and far less machinery.

### What Stage 3 checks

Deterministic code only — no model judges anything here:

- **Membership (exact)** — every rendered bullet, with derived values put back as
  placeholders, must be byte-identical to a bank string.
- **Numeric allowlist** — every number in a rendered bullet must appear in that record's
  `facts` list. (`facts` is built by extracting every figure the record's bullets state.)
- **Immutables** — identity, employers, titles, dates byte-identical.
- **Layout** — compiles cleanly, exactly one page.
- **Coverage report** — requirements met with their evidence; requirements unmet, listed
  explicitly and never hidden.

One subtlety worth flagging, because it bit this project: extracting figures must try
**fractions first**. Otherwise a general number pattern matches `1` and `8` inside
`1/8th`, and the ratio never reaches the allowlist — dropping exactly the kind of claim
the allowlist exists to protect.

### Derived values

Some claims rot. `"6+ months of live monitoring"` is true when written and false six
months later, with nobody having edited anything.

So durations are **not** stored as text. The record stores a start date, and the duration
is computed at render time:

```yaml
status: {state: live_in_production, since: 2026-01}
bullets:
  - "Own the production lifecycle: scheduled scoring over 4M+ events, {live_duration} of live drift monitoring"
```

`{live_duration}` is the one exception to "bullets are copied verbatim", and it exists
precisely so a time-dependent claim cannot silently go stale.

## 11. The corpus pipeline

Before any of the above can run, the bank has to be built from CVs that already exist.
Three stages.

### Harvest

Reads every CV document into a **union inventory** of distinct bullets.

*Union*, because the role-specific versions are **not** subsets of the master. Each was
hand-rewritten and contains material the master lacks. Building the bank from the master
alone silently discards work you already did.

LaTeX sources are ground truth. For PDF-only versions, structure is recovered from
**font weight** — CV templates set headings and project titles in bold and body text in
regular. This matters more than it sounds: in extracted PDF text there is no blank line
between the end of a bullet and the next project title, so a text-only parser appends the
title onto the previous bullet and silently corrupts it. Boldness separates them.

Also handled: **dehyphenation** that preserves real compounds (a line-break hyphen in
`Stream-\nlyzer` should rejoin to `Streamlyzer`, but `signal-\nrevealing` must keep its
hyphen — resolved against a vocabulary built from the LaTeX sources); several template
families; right-aligned dates emitted on their own line; and templates that mark project
titles with a trailing colon instead of bold.

### Cluster

Groups the harvested bullets into records.

Every CV version renames its projects, so **titles cannot identify a record** — two
headings for the same work may share a single token, while two unrelated projects share
several generic ones. Bullet *content* is far more distinctive, so matching scores
**IDF-weighted** bullet overlap (rare words count for more than common ones) at 0.65
against title similarity at 0.35.

Bullets with no project heading at all — some versions compress an older role that way —
become single-bullet groups and are placed by content alone, matched against each
record's single best-matching bullet rather than its whole vocabulary, which would dilute
the signal.

Output is a **review report** for a human to check before anything reaches the bank, with
low-confidence assignments flagged.

### Build

Fills cells from the clustered bullets and reports the gaps. Never invents wording.

### Two ideas from testing worth borrowing

**Golden pair.** When a document exists as both a LaTeX source and its rendered PDF, the
two are the same document in two encodings, so the two parsers must agree exactly. That
pair is the strongest available test of the PDF parser — and the PDF parser is what every
PDF-only version's correctness depends on.

**Stale render.** If someone edits the source after the PDF was rendered, the two
genuinely differ, and an exact-text test fails even though the parser is perfectly
correct. So the assertions are split: *structural* equality (bullet counts, project
titles) is checked always, since rewording can't change how many bullets a CV has, while
*exact text* is checked except for renders explicitly declared stale. A further test
asserts the declared-stale list is exactly the set that actually differs — so a real
parser regression can never be quietly written off as "just corpus drift."

## 12. Smaller terms

**Application ledger** — append-only log of every apply decision: which JD, which version
was sent, what it claimed, which requirements were unmet. Explicitly **not** an evaluation
dataset: with realistic application volumes and outcomes dominated by referrals, timing,
and headcount, no signal about whether tailoring helped is recoverable. It exists for
operational memory — what did this company receive, did I already apply, what should I
revise before the interview.

**Corpus** — the set of existing CV documents the bank is built from.

**Corpus generation** — a batch of CVs from one period (`2026_h1`, `2026_h2`). A
superseded generation is **retired** by configuration rather than deleted, so the files
remain and the decision stays visible.

**Eval set** — a fixed set of real JDs, versioned in the repo, that every logic change is
re-scored against. Should include adversarial cases: a JD demanding skills you lack (must
report gaps, not invent), a JD from an unrelated field (must return `dont_apply`), a JD
using heavy synonym jargon.

**Facts** — a record's numeric allowlist; every figure its bullets state. Checked against
rendered output.

**Human-in-the-loop** — nothing is sent without a person approving the diff and the
coverage report. The agent proposes; the human ships.

**Marginal coverage gain** — how much *new* requirement coverage a record adds given
what's already selected, as opposed to its standalone relevance.

**Master** — the CV containing every record; the source of `neutral` wording.

**Requirement** — one atomically checkable item extracted from a JD, typed by `kind`
(must/nice) and `category` (core/domain/tooling/credential/soft).
