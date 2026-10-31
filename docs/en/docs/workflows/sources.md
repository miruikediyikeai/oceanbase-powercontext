---
title: Sources and capture
description: Capture evidence, inspect Source records, and configure processing.
---

# Sources and capture

Sources preserve evidence before it becomes selected knowledge. A Content Source stores captured text; other Source
types can refer to material supplied by a connector. Work Contracts and Task Outcomes also preserve Source evidence.

## Capture and inspect

1. Choose an existing Scope. The [API Quick Start](../develop/api-quickstart.md) demonstrates content capture through
   `POST /v1/sources/content` and retains the returned Source reference.
2. Use `GET /v1/scopes/{scope_id}/sources` to browse the Source journal, then
   `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` to read the selected Source.
3. To process eligible evidence into Memory, configure a generation model and either scheduled processing or an
   explicit Memory flush. See [Enable extraction and vector search](../get-started/configure-models.md).

A successful capture means evidence was stored; it does not mean extraction succeeded or that the result was worth
keeping. Use Source references to trace a generated result back to its input. Do not retry a capture with changed
content under the same identity as if it were a new observation.

## Choose a capture path

- Agent prompt hooks: use the host-specific capture switch in [Connect Agents](../integrations/index.md).
- Files and object stores: use the [OpenDAL connector](ingest-text-files-with-opendal.md).
- Subject evidence for a Scope profile: follow [Scope profiles](use-profiles.md).
- Application or evaluation events: use the application's adapter; [Bub capture](../integrations/evaluation.md) is opt-in.

Source writes and reads are subject to the selected [Scope and access policy](scopes-and-access.md).

## Capture PDF pages

`powercontext.sources.pdf_pages` provides `PDFDocumentInput`, `parse_pdf_document`,
`PDF_PAGE_SOURCE_DEFINITION`, and `search_pdf_pages`. Install the built-in Runtime dependencies and
`pypdf` to use this module; the competition environment pins `pypdf==6.19.0`.

Pass the complete original PDF bytes and a nonblank document ID to the parser. It returns every physical
page in order, starting at 1, including blank pages. Each page carries the SHA-256 of the complete PDF.
PDFs readable with an empty password are accepted; invalid, truncated, or password-protected documents
raise `ValueError` without returning partial pages. Text extraction preserves page boundaries and does
not perform OCR. The parser checks page-tree counts and Flate stream integrity, including text in
form resources and font character maps, before accepting the document.
When a font maps a glyph to NUL, unambiguous single-glyph `/ActualText` spans can restore its Unicode
mapping. Standard Unicode presentation ligatures are decoded to their letter sequences.
Geometric extraction repairs split words and numbers while preserving every non-whitespace character
and all already complete amounts and years, including repetitions. Ordinary extraction's token count
is not a quality reference: positioned glyphs can split one printed word into many tokens.
All text extractable by pypdf is retained, including text drawn under clipping paths and repeated
table values. Visual clipping is not used to delete captured content.

Resolve each returned page with a `SourceDefinitionRegistry` containing `PDF_PAGE_SOURCE_DEFINITION`,
then submit its `project_source_for_transport` observation through Runtime ingestion. Register
`manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION)` before submitting observations. The `pdf-page`
Definition is version `1`; its captured Source reads back the exact `PDFPageInput`. Its standard text
projection contains `document_id + "\n" + page.text`, with metadata containing exactly `document_id`,
`document_sha256`, and `page_number`. Long document IDs use a digest in the Source identity while
retaining the original ID in the captured page and metadata. Existing short Source identities retain
their spelling.

Use a pipeline that writes each page projection as a separate Memory entry with that Source reference.
Bodies whose Source references are exclusively PDF pages can exceed the ordinary 8192-byte Memory
entry budget, preserving complete pages through hashing, persistence, reopening, and index rebuilds.
Their stored bodies preserve Unicode spelling so opaque document IDs still match the captured projections;
content hashes retain the canonical JSON encoding.
After processing, call `await search_pdf_pages(runtime.memory.for_scope(scope_id), question, limit=20)`.
The result is a tuple of exact `MemoryCitation` values for distinct pages, which can be read through
`memory.get(GetMemoryEntryRequest(citation=citation))`, including after reopening Runtime. Nonpositive
limits and blank questions return an empty tuple. Other Source types are excluded.
Each document and physical page occupies at most one result slot across PDF revisions. The selected
citation identifies its exact stored revision; earlier citations remain readable.

Retrieval uses the scoped public Memory API and combines independent rankings of original question
terms and financial vocabulary expansions. Company names and fiscal years prioritize matching document
identities. For questions about financial statements, a small part of the result budget preserves
matching income, cash flow, and balance sheet pages identified by headings and numeric content,
including adjacent pages marked as continuations. Word inflections and fiscal-year prefixes are
normalized during scoring.
Document IDs may be opaque. When an ID does not identify the company, question terms shared with the
opening pages provide document context for ranking statements that omit the company name.
Financial paraphrases map to accounting terms, such as supplier payment obligations to accounts payable
and rented buildings to operating leases. A bounded part of the result budget preserves the strongest
hits from both the original and expanded queries, including note pages with no literal query overlap.
Statement recognition tolerates words split across PDF text objects, plural and reordered titles,
and titles drawn after the table. Financial row concepts distinguish primary tables from indexes
and narrative references. Ratio questions preserve the statements needed for both operands,
including income and cash flow statements for dividend payout ratios.
An explicit fiscal year takes precedence over other dates, such as future debt maturities,
when selecting the report revision by year.

When inventory listing is available, each query reads the current scope's PDF entries and scores their
complete texts; work and temporary storage grow with the scoped corpus size. With a search/get-only
Memory, expanded lexical queries retrieve candidates, which use the same page selection logic. Neither
path requires a model or network connection.
Keep original PDFs indexed by their full digest so the returned physical page can be checked. A changed
PDF receives a new identity; retain its previous bytes to keep earlier citations useful.
