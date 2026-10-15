# Existing Octo snapshot storage

The adopter stores citation snapshots in its own `legal_graph` PostgreSQL
schema through the existing Octo workspace SQL endpoint. It does not use the
native epistemic graph projection. The SQL caller supplies one statement and
bound parameters per request; the reader rejects multi-statement, truncated,
error-envelope, and malformed results.

## Snapshot lifecycle

`migrations/001_legal_graph.sql` defines UUID snapshot revisions, exact text
authority IDs, JSONB source payloads, stable edge keys, and a single active
snapshot pointer. `migrations/002_snapshot_provenance.sql` adds an explicit
`source_kind` and `source_dataset`; the default is visibly synthetic, and a
future importer must identify its real source explicitly. A writer starts in
`staging`, computes a digest over its canonical input representation, and
records it with the revision. Before marking it `ready`, the importer must
compare its canonical digest and staged row counts with the manifest. The
schema itself checks row counts during the ready transition and blocks edits
to ready rows; it stores the supplied digest but does not recompute or compare
JSONB content hashes. This increment does not include the future corpus
importer. Activation uses one `INSERT … SELECT … ON CONFLICT` statement that
only selects a ready revision. A failed or incomplete stage is not activated.

The current consumer surface is read-only:

- `get_active_snapshot()` returns the active ready revision metadata.
- `get_authorities(snapshot_id, cursor, limit)` and
  `get_citation_edges(snapshot_id, cursor, limit)` return at most 1,000 items
  and use one extra row to determine whether another page exists.
- `get_authority(authority_id, snapshot_id)` retrieves one exact authority.

Every query pins to the caller's UUID snapshot. Authority IDs and cursors are
bound parameters and retain exact case, punctuation, and Unicode. JSONB payloads
are returned without lossy field mapping.

## Verification

The unchanged root-authored contract suite passes 14 tests. The existing
retrieval, ranking, decision, provider, and annotation suites also pass; the
combined run is 126/126.

A small live Octo round-trip used only synthetic records in the already
provisioned Legal RAG workspace. It inserted two one-authority/one-edge
revisions. One used the exact ID `SYNTHETIC:LegalGraph:§ 1983` and retained its
Unicode edge locator and source attributes. Both revisions were staged, count
checked, marked ready, and activated through the ready-only atomic pointer
statement. Reads returned the active revision (including its synthetic
provenance), exact authority payload, exact edge payload, and authority lookup
from that same snapshot. A deliberately incomplete staging revision expected
two authorities but contained none; it remained staging and did not become
active. Synthetic payloads are fixtures for storage behavior, not legal records
or product evidence. No corpus was imported.

The canonical digest for the Unicode sample revision is
`2ddb228bf805ac29b36e3eac80aa4c30a81c4cff7593fb7da6af4256687d40a2`.
It was computed locally from the canonical JSON sample manifest and the same
value was stored in snapshot metadata. The SQL schema stores this supplied
digest; it does not independently hash JSONB contents. This smoke test does not
establish cryptographic verification of stored rows, corpus-scale throughput,
production authorization policy, or a corpus ingestion pipeline.
