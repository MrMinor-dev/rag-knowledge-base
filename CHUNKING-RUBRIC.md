# Chunking Rubric

The rules and score I used to rewrite business documents so that a retrieved chunk makes sense on its own.

Retrieval hands the agent a few hundred words with no surrounding document. If those words say "the system" or "see Section 4," the agent can't use them. This rubric makes each chunk carry its own context, and it scores the result so you know when to stop.

## The 5 transformation rules

**1. Chunk independence.** Every section stands alone.

```
Before: "The process involves three steps..."
After:  "[Business name] content creation process involves three steps..."
```

**2. Context repetition.** Each major section repeats the business name, the business model, the key constraints and the phase. This breaks the DRY habit that serves human readers. Retrieval needs it.

**3. Keyword density.** The business name appears 5 to 15 times per document. Phase markers and decision-authority levels are explicit.

**4. Explicit cross-references.** Use the full document path and section title. A retrieved chunk can't follow "See Section 4."

```
Before: "See Section 4"
After:  "See automation-strategy-master.md, Section 9: Automation Graduation Framework"
```

**5. Specific headings.**

```
Before: "## Overview"
After:  "## [Business name] Content Automation Overview - Phase 0 Foundation"
```

## The 100-point chunk quality score

| Area | Points | What it asks |
|---|---|---|
| Chunk independence | 30 | Can it stand alone (10)? Is the business context present (10)? Are there broken references (10)? |
| Keyword optimization | 25 | Business name density (10), phase markers present (8), decision authority clear (7) |
| Context repetition | 20 | Is the business model repeated (10)? Are the key constraints repeated (10)? |
| Cross-reference quality | 15 | Full paths used (10), relationship types named (5) |
| Usable without a human | 10 | Could an agent act on this chunk alone? |

## The quality gates

A document is accepted only if it passes all of these.

- Every section is self-contained.
- The business name appears throughout.
- Product categories, budget and model are repeated where they matter.
- Cross-references use full paths and name the relationship.
- An agent could execute from the document without a person.
- Program or policy requirements and phase constraints are stated.
- Decision authority levels are clear.
- Categories are referenced the same way everywhere.

## 5 document tiers

Not every document deserves the same effort. I sorted them by how often an agent needs them and what breaks if they fail.

| Tier | What it holds | Target score | Access |
|---|---|---|---|
| 1 | Critical path | 85 to 90 | Daily |
| 2 | High-frequency operations | 85 to 90 | Daily or weekly |
| 3 | Strategic frameworks | 80 to 85 | Weekly or monthly |
| 4 | Reference material | 75 to 80 | Monthly or quarterly |
| 5 | Archive and history | 70 to 75 | Rarely |

Classify by criteria: business impact, how often the agent needs it during unattended operation, how many other documents point to it, and how critical the decisions it supports are.

## Classifying a large set fast

To classify 84 documents without scoring all of them:

1. Sample 4 documents from different folders and of different types.
2. Score each one.
3. Find the patterns they share: zero context, generic headings, broken references.
4. Assign every document a tier by pattern.
5. Build the rewrite plan by criticality.

## Keeping it from decaying

Documents change. Rules for keeping the score:

| Level | When | What |
|---|---|---|
| 1 | A new document or a major update | Score it before it is indexed |
| 2 | Weekly | Routine edits: recheck headings and references |
| 3 | Monthly | Quality audit and pattern review |
| 4 | Quarterly | Review the rubric itself |

## Batch rewriting

Do a few by hand first. When 4 documents scored the same by hand, I scripted the transformation and ran it tier by tier: load the batch, apply the 5 rules, check the gates, commit each document on its own, and refresh the index. Codify the rules. "Do your best" doesn't produce consistent results at scale.
