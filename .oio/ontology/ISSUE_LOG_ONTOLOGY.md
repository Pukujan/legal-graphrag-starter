# OIO Issue-Log Ontology

This map is the default vocabulary for **observational and operational issue logs**. OIO packages it with every supported installation. An adopter may add project-specific terms and priority meanings in its project ontology; it may not silently change the shared definitions here.

The machine-readable source is [`default.json`](default.json). The installer validates project extensions against OIO's schemas. If this explanation and the JSON disagree, treat the versioned JSON and schemas as the validation contract and file a correction against OIO.

## What the log describes

- An **observational** log records something seen, including a discrepancy or possible weakness that may not be causing active harm.
- An **operational** log records a capability or workflow that is currently broken or degraded.
- The log names the affected application, adopter, users, or systems; explains the observed consequence and any workaround; separates evidence from interpretation; and records uncertainty.

This ontology standardizes issue-log vocabulary. It does not grant permission to implement, change another repository, or release software.

## Who authored and authorized the filing

Record author/source separately from permission and authenticated GitHub actor:

| Filer origin | Queue class | Meaning |
| --- | ---: | --- |
| `human-direct` | 1 | Human authored and filed the log directly. The adopter's owner-maintained account policy determines account authority. |
| `human-via-agent` | 2 | A human explicitly instructed an agent to file it. Record the human account and instruction evidence; apply the human account's authority only when independently attributable. |
| `agent-proposed` | 3 | An agent authored a log for human consideration. This is a filing class, not permission to implement. |
| `agent-initiated` | 4 | An agent initiated the log within a destination repository's allowed filing scope. This is the lowest normal queue class. |

Record these independently: authenticated GitHub actor, claimed content author, filer origin, directing human account when applicable, authorization evidence and whether it is verified/claimed/unknown, session reference when available, and the exact destination repository. Triage uses the authenticated GitHub actor ID and owner-maintained account map to derive account authority; reporters cannot choose their own account rank. A text field or prompt stamp alone is only a claim. An authorized mapped account can separately attest to `human-direct` or `human-via-agent` by applying `oio-auth:human-direct` or `oio-auth:human-via-agent`. Triage verifies the current label against GitHub's issue event history and writes a durable bot comment containing the event ID and SHA-256 of the exact issue body. The label remains as a visible record. Any body edit invalidates the old digest and the stale label is removed; a mapped account must review the edited body and apply the matching label again. This records which GitHub account acted, not who typed a prompt when credentials are shared. The structured state is `verified-account-attestation`; it does not claim to prove a human typed the prompt or authorize implementation.

Permission to file, queue order, permission to implement, and release readiness are separate decisions. An unapproved proposal must never silently become an implementation instruction. Repository owners define where agent-initiated logs are allowed. In this workspace, OIO, ACS, CGM, and PCM are protected: agents may keep a proposal as a local draft but may not submit it or write repository files without explicit human direction naming the destination and action. Adopting OIO does not grant cross-repository write authority.

## Queue order and risk-versus-product review

The review queue compares **source class first**, then the directing/authenticated account's owner-maintained authority (`owner`, approved collaborator, community, unknown), then the adopter's project priority path using numeric components. A review lane breaks ties only after those queue dimensions, so urgency does not silently override source class, account authority, or project priority. Human-direct and human-via-agent ranks require the separate GitHub account attestation above; a body field claiming `verified` never raises the class. Agent-proposed is class 3; agent-initiated is class 4. The project priority path is meaningful only inside the project that defined it.

Risk and speed to product outcome are a parallel review lane, never a rewrite of source authority. Use the following decision matrix as an explanation guide; complete the evidence dimensions before assigning a lane.

| Demonstrated unresolved condition | Named product outcome/release relation | Review lane | Shipping interpretation |
| --- | --- | --- | --- |
| Critical/high impact, observed/likely, confirmed/supported, active/intermittent production | Any | `production-risk-review` | Human review is expedited. Release impact is decided by the owner using change risk and recovery evidence. |
| Critical/high impact with confirmed/supported evidence | Blocks a named outcome, without qualifying production exposure | `release-risk-review` | Assess the smallest safe slice and cost of delay; this label alone does not block shipment. |
| Any supported bounded issue | Affects or blocks a named outcome | `product-outcome-review` | Compare demonstrated user value and cost of delay against risk of the proposed change. |
| Low/informational impact or uncertain/unknown evidence | No demonstrated blocked core outcome | `bounded-or-uncertain-review` | Keep visible; speculative edge cases do not block a product outcome by default. |
| Other complete record | Supports, unrelated, or unknown | `normal-review` | Use the project path and review evidence in normal order. |

This is not a numeric severity score and does not make automatic release decisions. A production incident can be expedited while remaining class 4; source authority and urgency stay separate. A demonstrated critical boundary or accepted user outcome failure cannot be dismissed for speed alone. A low-confidence edge case cannot become a release blocker solely because it is imaginable.

Fresh installs prefill project priority paths `1`–`100` with generic definitions so the form is usable immediately. The adopter owns replacing those generic meanings with its product ontology. It also must maintain its GitHub account authority map for ranked human reports; unknown accounts remain at the end pending owner review.

## Priority is project-scoped

An adopter defines what its priority paths mean. The default supports roots `1` through `100` and hierarchical subdivisions such as `1.1`, `1.2`, `1.10`, and deeper paths. Paths are strings made of positive integer components, not decimals. Compare each component numerically from left to right; a parent path precedes its child. Lower paths are earlier only within that adopter's declared ordering.

Each valid ranked issue record stores its project/repository, exact priority path, matching project concept, and ontology version. Every used path must have a definition in the project extension; child paths also require declared parent paths. A parent path may be assigned directly when it has a definition, meaning the issue is ranked at that broader category without selecting a more specific child. If the path or concept is missing or mismatched, the record is explicitly `unresolved` with null path and concept ID; downstream queues must not invent a default rank. There is no fixed subdivision depth. Do not compare `1.2` in one project directly with `1.2` in another unless an explicit crosswalk defines that comparison. Do not convert the path to a floating-point number: `1.10` must remain distinct from `1.1`.

## Impact, risk, and shipping relevance

Keep separate fields for:

1. **Impact if unresolved:** who or what is affected, breadth, consequence, workaround, and an impact band. **Critical** requires evidence of severe harm, broad production failure, a security/authorization failure, or irreversible data loss; **high** means a core outcome fails for a meaningful segment with limited workaround; **moderate** is bounded with a usable workaround or recovery; **low** is limited friction without a demonstrated core outcome block; **informational** means material impairment is not demonstrated.
2. **Likelihood:** `observed`, `likely`, `plausible`, `unlikely`, or `unknown`, with evidence. “Observed” describes a recorded occurrence; it does not assert recurrence.
3. **Exposure and recovery:** production/test exposure and duration; recovery is automatic, documented, manual, difficult/irreversible, or unknown.
4. **Evidence confidence:** confirmed, supported, uncertain, or unknown.
5. **Release relevance:** the named product outcome or release criterion and whether this blocks, affects, supports, or is unrelated to it; record delay cost and the smallest useful slice when known.
6. **Risk of the proposed change:** recorded separately from risk of leaving the observed condition unresolved.

These axes are not collapsed into a universal score or fixed 4×4 grid. Project priority concepts can account for the app's actual user outcomes and the speed gained by the smallest useful product slice. Do not let speculative, low-impact edge cases block a declared product outcome by default. Do not use speed to excuse a demonstrated failure of an accepted user outcome or a critical boundary. State uncertainty instead of inflating the rating. These are issue-description rules, not deployment gates.

## Project extensions

The adopter extension has a stable project namespace and declares its repository identity, the OIO ontology version it extends, and project-specific priority concepts. Extensions may add narrower concepts and definitions. They may not reuse OIO's core identifier namespace, replace core definitions, remove required provenance, or relax protected filing boundaries. Unknown parent concepts, duplicate IDs, invalid priority paths, and incompatible versions fail validation.

The installed default is pinned to the OIO release recorded in `.oio/install-manifest.json`. Filing and validation never fetch a moving `main` branch or silently reinterpret historical records.
