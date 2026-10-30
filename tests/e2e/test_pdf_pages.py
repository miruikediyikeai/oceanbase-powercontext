# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PDF evidence persists, stays scoped and retrieves exact page versions."""

from __future__ import annotations

import asyncio

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY
from tests.test_pdf_pages import make_pdf


class PageEvidencePipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=(
                    source.content
                    if isinstance(source, ContentSource)
                    else source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"]
                ),
                sources=(source,),
            )
            for source in request.sources
        )


async def flush_all(memory, source_count):
    for _ in range(source_count + 1):
        if not (await memory.flush()).processed:
            return
    raise AssertionError("sources did not drain")  # noqa: TRY003


def test_reopened_memory_returns_distinct_exact_scoped_pdf_citations(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pdf.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        documents = [
            PDFDocumentInput(
                document_id="ACME_2022_10K",
                content=make_pdf("Revenue " * 80, "Cash flows capital expenditure 2022 1000 2000", ""),
            ),
            PDFDocumentInput(document_id="ACME_2022_10K", content=make_pdf("Revenue revised 2022 3000")),
            PDFDocumentInput(
                document_id="OTHER_2022_10K",
                content=make_pdf("Revenue revenue revenue revenue revenue 2022 1000 2000 3000 4000"),
            ),
        ]
        observations = []
        page_by_source = {}
        for document in documents:
            for page in parse_pdf_document(document):
                source = await registry.resolve(page)
                observation = project_source_for_transport(registry, source)
                observations.append(observation)
                page_by_source[observation.name] = page
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="PDFs", summary="reports", idempotency_key="pdfs"))
            other = await runtime.scopes.create(ScopeDraft(title="Other", summary="private", idempotency_key="other"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for observation in observations:
                request = SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                await runtime.ingestion.submit(request)
                await runtime.ingestion.submit(request)
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="note", content="Revenue " * 100, metadata={})
            )
            await runtime.ingestion.submit(
                SubmitSourceObservation(scope_id=other.scope_id, observation=observations[0])
            )
        async with open_builtin_runtime(config, candidate_pipeline=PageEvidencePipeline()) as runtime:
            for scope_id in (scope.scope_id, other.scope_id):
                await flush_all(runtime.memory.for_scope(scope_id), len(observations) + 1)
        async with open_builtin_contexts(config) as contexts:
            journal = await (await contexts.get(scope.scope_id)).sources.journal.entries()
            assert len(journal) == len(observations) + 1
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            assert await search_pdf_pages(memory, "Revenue", limit=0) == ()
            assert await search_pdf_pages(memory, "Revenue", limit=-1) == ()
            assert await search_pdf_pages(memory, "  ") == ()
            saved = None
            for limit in (1, 2, 20):
                citations = await search_pdf_pages(memory, "What is ACME revenue in 2022?", limit=limit)
                assert len(citations) == min(limit, len(page_by_source))
                locations = set()
                for citation in citations:
                    record = await memory.get(GetMemoryEntryRequest(citation=citation))
                    assert len(record.entry.sources) == 1
                    reference = record.entry.sources[0]
                    assert reference.source_type == "pdf-page"
                    page = page_by_source[reference.source_id]
                    assert record.entry.text == f"{page.document_id}\n{page.text}".strip()
                    locations.add((page.document_id, page.document_sha256, page.page_number))
                assert len(locations) == len(citations)
                saved = citations[0]
                first = await memory.get(GetMemoryEntryRequest(citation=citations[0]))
                assert page_by_source[first.entry.sources[0].source_id].document_id == "ACME_2022_10K"
            assert saved is not None
            record = await memory.get(GetMemoryEntryRequest(citation=saved))
            assert "Revenue" in record.entry.text
            private = await search_pdf_pages(runtime.memory.for_scope(other.scope_id), "Revenue", limit=20)
            assert len(private) == 1
            private_record = await runtime.memory.for_scope(other.scope_id).get(
                GetMemoryEntryRequest(citation=private[0])
            )
            assert private_record.entry.sources[0].source_id == observations[0].name
        async with open_builtin_runtime(config) as runtime:
            record = await runtime.memory.for_scope(scope.scope_id).get(GetMemoryEntryRequest(citation=saved))
            assert "Revenue" in record.entry.text

    asyncio.run(scenario())
