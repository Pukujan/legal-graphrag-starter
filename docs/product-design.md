# Product design document

## Goal and ownership
The research workbench lets a reader inspect a legal authority, follow citation paths, and compare retrieval methods with source-visible evidence. Root owns this design, system contracts and independent evaluation. Luna implements the engine and installation. ABA owns all application generation and components. The complete PRD-agent workflow owns application copywriting.

## Reader tasks
1. Find an authority and expand incoming or outgoing citations.
2. Inspect the original authority identifier, source, edge kind and path.
3. Compare original citation retrieval, extracted annotations and a decision-filtered view.
4. Inspect why a decision was made, including source offsets, model version and full answer distribution.
5. Read measured retrieval results and operational costs.

## Application design
Use the selected Editorial Atlas reference, dark navy by default, blue interaction colors and restrained gold accents. Sparse graph, readable inspector, serif headings and compact sans text. ABA should use a ready-made bounded 3D graph component with a 2D/table fallback. The graph and table consume identical records. Never show the full starter graph by default. Sample passages and illustrative visuals must be labeled.

## Acceptance
Original identifiers and citation facts remain intact. Every displayed annotation has inspectable evidence and provider provenance. Default expansion is one hop; each request allows at most three hops, 500 nodes and 1000 edges. Partial results show explicit truncation reasons. Retrieval benefit is reported only against frozen questions and relevance labels. Transport success and synthetic examples are not legal quality evidence.

## Dependencies and evidence
The clean fork is Pukujan/legal-graphrag-starter. Original baseline has 119770 nodes and 160793 edges and ran successfully before implementation. Early Jev transport smoke returned HTTP 200; legal effectiveness remains unmeasured. Existing Octo storage APIs are consumed without provisioning a new database. Hosted FalkorDB use depends on safe existing access and supported projection ownership. The PRD-agent repository currently lacks its planned executable runtime and ABA requires its supported runtime; these are observed delivery gaps, not permission to replace their ownership.
