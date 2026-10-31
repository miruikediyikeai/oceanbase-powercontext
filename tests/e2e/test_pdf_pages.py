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
import unicodedata

import pytest

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
            historical = next(
                record.citation
                for record in (await memory.list()).entries
                if record.entry.sources[0].source_id == observations[0].name
            )
            assert await search_pdf_pages(memory, "Revenue", limit=0) == ()
            assert await search_pdf_pages(memory, "Revenue", limit=-1) == ()
            assert await search_pdf_pages(memory, "  ") == ()
            saved = None
            for limit in (1, 2, 20):
                citations = await search_pdf_pages(memory, "What is ACME revenue in 2022?", limit=limit)
                page_locations = {(page.document_id, page.page_number) for page in page_by_source.values()}
                assert len(citations) == min(limit, len(page_locations))
                locations = set()
                for citation in citations:
                    record = await memory.get(GetMemoryEntryRequest(citation=citation))
                    assert len(record.entry.sources) == 1
                    reference = record.entry.sources[0]
                    assert reference.source_type == "pdf-page"
                    page = page_by_source[reference.source_id]
                    assert record.entry.text == f"{page.document_id}\n{page.text}".strip()
                    locations.add((page.document_id, page.page_number))
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
            original = await runtime.memory.for_scope(scope.scope_id).get(GetMemoryEntryRequest(citation=historical))
            assert original.entry.text == f"ACME_2022_10K\n{'Revenue ' * 80}".strip()

    asyncio.run(scenario())


def test_complete_large_page_survives_ingestion_restart_and_exact_citation(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'large-page.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        text = "Revenue and operating expenses 2022 1000 1000. " * 400
        document = PDFDocumentInput(
            document_id=unicodedata.normalize("NFD", "ACME_2022_10K_Café"), content=make_pdf(text, "")
        )
        pages = parse_pdf_document(document)
        assert len(pages[0].text.encode("utf-8")) > 8192
        observations = [project_source_for_transport(registry, await registry.resolve(page)) for page in pages]
        async with open_builtin_runtime(config, candidate_pipeline=PageEvidencePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="PDF", summary="large pages", idempotency_key="large"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for _ in range(2):
                for observation in observations:
                    await runtime.ingestion.submit(
                        SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                    )
            await flush_all(runtime.memory.for_scope(scope.scope_id), len(pages))
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "ACME revenue in FY2022", limit=20)
            assert len(citations) == 2
            saved = [await memory.get(GetMemoryEntryRequest(citation=citation)) for citation in citations]
            assert {record.entry.text for record in saved} == {
                f"{page.document_id}\n{page.text}".strip() for page in pages
            }
            assert {record.entry.sources[0].source_id for record in saved} == {o.name for o in observations}
            citation = saved[0].citation
        async with open_builtin_contexts(config) as contexts:
            await (await contexts.get(scope.scope_id)).artifacts.memory.rebuild_projections()
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            record = await memory.get(GetMemoryEntryRequest(citation=citation))
            assert record == saved[0]
            hits = await search_pdf_pages(SearchOnlyMemory(memory), "ACME revenue in FY2022", limit=20)
            hit_texts = [(await memory.get(GetMemoryEntryRequest(citation=hit))).entry.text for hit in hits]
            assert f"{pages[0].document_id}\n{pages[0].text}".strip() in hit_texts

    asyncio.run(scenario())


def test_document_ids_are_opaque_when_locating_a_company_statement(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'opaque.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        statement = (
            "Consolidated Statements of Income\n2022 2021 2020\n"
            "Net revenue 9000 8000 7000\nOperating income 2000 1900 1800\n"
            "Income taxes 500 400 300\nNet income 1500 1400 1300"
        )
        expected = None
        async with open_builtin_runtime(config, candidate_pipeline=PageEvidencePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Reports", summary="opaque ids", idempotency_key="ids")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for index in range(25):
                company = "Acme" if index == 24 else f"Other Company {index}"
                document = PDFDocumentInput(
                    document_id=f"input-{index:02}", content=make_pdf(f"{company}\nAnnual Report 2022", statement)
                )
                for page in parse_pdf_document(document):
                    observation = project_source_for_transport(registry, await registry.resolve(page))
                    await runtime.ingestion.submit(
                        SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                    )
                    if index == 24 and page.page_number == 2:
                        expected = observation.name
            await flush_all(runtime.memory.for_scope(scope.scope_id), 50)
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for interface in (memory, SearchOnlyMemory(memory)):
                citations = await search_pdf_pages(interface, "What was Acme's net income in FY2022?", limit=20)
                sources = {
                    source.source_id
                    for citation in citations
                    for source in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
                }
                assert expected is not None and expected in sources
                assert len(sources) == len(citations) <= 20

    asyncio.run(scenario())


@pytest.mark.parametrize("layout", ["split-word", "inverted-title", "footer-title", "payout-ratio"])
def test_primary_tables_with_layout_variations_survive_discussion_pages(tmp_path, layout):
    async def scenario():
        income_rows = (
            "2022 2021 2020\nNet sales 86000 79000 70000\nCost of sales 41000 38000 35000\n"
            "Income tax 1700 2100 1900\nNet earnings 9000 8000 7000"
        )
        cash_rows = (
            "2022 2021 2020\nNet income 9000 8000 7000\n"
            "Depreciation and amortization 2700 2600 2500\n"
            "Operating activities 10000 11000 10000\nInvesting activities 5200 4600 4200"
        )
        discussion = (
            "Management discussion: gross profit margin, effective income tax rate, net income change, "
            "dividends paid, dividend payout ratio, cash flow statement, depreciation and amortization. "
            "Operating profit operating income net revenue total revenue net sales cost of sales. "
            "2022 2021 2020 9500 9000 8500 8000 7500 7000 6500 6000. "
        )
        if layout == "inverted-title":
            target = "CASH FLOWS STATEMENTS\n" + cash_rows
            question = "What did ACME report as depreciation and amortization from the cash flow statement in FY2022?"
        elif layout == "footer-title":
            target = income_rows + "\n" + "Additional financial table row 1000 2000\n" * 40
            target += "\nConsolidated Statements of Income"
            question = "What was ACME gross profit margin in FY2022?"
        elif layout == "payout-ratio":
            target = "Consolidated Statements of Income\n" + income_rows
            question = "What is ACME's FY2022 dividend payout ratio?"
        else:
            target = "Consolidated Statement of Incom\n\ne\n" + income_rows
            question = "What was ACME gross profit margin in FY2022?"
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'layouts.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        document = PDFDocumentInput(
            document_id="ACME_2022_10K", content=make_pdf(target, *(discussion for _ in range(24)))
        )
        async with open_builtin_runtime(config, candidate_pipeline=PageEvidencePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Reports", summary="tables", idempotency_key="layouts")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            pages = parse_pdf_document(document)
            for page in pages:
                observation = project_source_for_transport(registry, await registry.resolve(page))
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            await flush_all(runtime.memory.for_scope(scope.scope_id), len(pages))
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for interface in (memory, SearchOnlyMemory(memory)):
                citations = await search_pdf_pages(interface, question, limit=20)
                found = set()
                for citation in citations:
                    record = await memory.get(GetMemoryEntryRequest(citation=citation))
                    found.add(record.entry.sources[0].source_id)
                target_source = await registry.resolve(pages[0])
                assert len(citations) == len(found) <= 20
                assert target_source.name in found

    asyncio.run(scenario())


class SearchOnlyMemory:
    """Expose search/get from a real scoped Runtime, without inventory listing."""

    def __init__(self, memory):
        self.memory = memory

    async def search(self, request):
        return await self.memory.search(request)

    async def get(self, request):
        return await self.memory.get(request)


def test_ratio_questions_retrieve_every_required_statement_after_restart(tmp_path):
    async def scenario():
        discussion = (
            "Management discusses asset turnover, cash conversion cycle, days sales outstanding, "
            "and interest coverage ratio in FY2022. Performance measures are reviewed regularly. "
        )
        income = (
            "Consolidated Statements of Income\n2022 2021 2020\n"
            "Net revenue 9000 8000 7000\nCost of sales 4000 3500 3000\n"
            "Interest expense 200 190 180\nNet income 1500 1400 1300"
        )
        balance = (
            "Consolidated Balance Sheets\n2022 2021\n"
            "Cash and cash equivalents 1200 1100\nAccounts receivable 2000 1900\nInventories 900 800\n"
            "Total assets 9000 8500\nAccounts payable 700 600\nTotal liabilities 4000 3500"
        )
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'ratios.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        document = PDFDocumentInput(
            document_id="ACME_2022_10K", content=make_pdf(*(discussion for _ in range(24)), income, balance)
        )
        pages = parse_pdf_document(document)
        observations = [project_source_for_transport(registry, await registry.resolve(page)) for page in pages]
        async with open_builtin_runtime(config, candidate_pipeline=PageEvidencePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Ratios", summary="tables", idempotency_key="ratios"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for observation in observations:
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            await flush_all(runtime.memory.for_scope(scope.scope_id), len(pages))
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for metric, required in (
                ("asset turnover", observations[-2:]),
                ("cash conversion cycle", observations[-2:]),
                ("days sales outstanding", observations[-2:]),
                ("interest coverage ratio", observations[-2:-1]),
            ):
                for interface in (memory, SearchOnlyMemory(memory)):
                    citations = await search_pdf_pages(interface, f"What was ACME's {metric} in FY2022?", limit=20)
                    records = [await memory.get(GetMemoryEntryRequest(citation=citation)) for citation in citations]
                    sources = {record.entry.sources[0].source_id for record in records}
                    assert {observation.name for observation in required} <= sources, metric
                    assert len(sources) == len(citations) <= 20

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("topic", "evidence"),
    [
        ("money spent buying back common shares", "Share repurchases\nTreasury stock acquired for 2500."),
        ("payment obligations to suppliers", "Accounts payable\nTrade payables were 3500."),
        ("future payments on rented buildings", "Operating leases\nMinimum lease payments total 4500."),
        (
            "business units used to assess financial performance",
            "Reportable segments\nSegment operating profit was 5500.",
        ),
    ],
)
@pytest.mark.parametrize(
    ("document_id", "company"), [("ACME_2022_10K", "ACME"), ("ACMEHOLDINGS_2022_10K", "Acme Holdings")]
)
def test_financial_paraphrases_retrieve_note_evidence(tmp_path, topic, evidence, document_id, company):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'paraphrases.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        question = f"What did {company} disclose about {topic} in FY2022?"
        discussion = f"Management at {company} discussed {topic}. No amounts are provided in this overview. "
        document = PDFDocumentInput(
            document_id=document_id, content=make_pdf(*(discussion for _ in range(24)), evidence)
        )
        pages = parse_pdf_document(document)
        async with open_builtin_runtime(config, candidate_pipeline=PageEvidencePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Reports", summary="notes", idempotency_key="notes"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            observations = [project_source_for_transport(registry, await registry.resolve(page)) for page in pages]
            for observation in observations:
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            await flush_all(runtime.memory.for_scope(scope.scope_id), len(pages))
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for interface in (memory, SearchOnlyMemory(memory)):
                citations = await search_pdf_pages(interface, question, limit=20)
                references = {
                    source.source_id
                    for citation in citations
                    for source in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
                }
                assert len(references) == len(citations) <= 20
                assert observations[-1].name in references

    asyncio.run(scenario())


def test_financial_statement_evidence_survives_repeated_discussion_pages(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'statements.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        income = (
            "Consolidated Statements of Income\n2022 2021 2020\n"
            "Net sales 86000 79000 70000\nOperating income 11000 10000 9000\n"
            "Income tax 1700 2100 1900\nNet earnings 9000 8000 7000"
        )
        cash = (
            "Consolidated Statements of Cash Flows\n2022 2021 2020\n"
            "Depreciation and amortization 2700 2600 2500\n"
            "Purchases of property plant and equipment 5200 4600 4200\n"
            "Cash provided by operating activities 10000 11000 10000"
        )
        discussion = (
            "Management discussion of adjusted EBITDA less capex and operating income. "
            "ACME FY2022 income statement cash flow statement depreciation and amortization. "
            "Capital spending net revenue operating profit restructuring and impairment charges. "
            "The following discussion explains non-GAAP adjustments rather than primary statements. "
        )
        documents = (
            PDFDocumentInput(
                document_id="ACME_2022_10K",
                content=make_pdf(income, cash, *(discussion + f"Adjustment {i} 2022 1000 2000" for i in range(24))),
            ),
            PDFDocumentInput(
                document_id="OTHER_2022_10K",
                content=make_pdf(*(discussion * 3 + f"Competitor adjustment {i} 4000 5000 6000" for i in range(24))),
            ),
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=PageEvidencePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Reports", summary="tables", idempotency_key="tables"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for document in documents:
                for page in parse_pdf_document(document):
                    observation = project_source_for_transport(registry, await registry.resolve(page))
                    locations[observation.name] = (page.document_id, page.page_number)
                    await runtime.ingestion.submit(
                        SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                    )
            await flush_all(runtime.memory.for_scope(scope.scope_id), len(locations))
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            question = (
                "What is ACME's FY2022 EBITDA less capex? Use operating income from the income statement "
                "and depreciation and amortization from the cash flow statement."
            )
            for interface in (memory, SearchOnlyMemory(memory)):
                citations = await search_pdf_pages(interface, question, limit=20)
                found = set()
                for citation in citations:
                    entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                    found.add(locations[entry.sources[0].source_id])
                assert len(found) == len(citations) <= 20
                assert {("ACME_2022_10K", 1), ("ACME_2022_10K", 2)} <= found

    asyncio.run(scenario())


@pytest.mark.parametrize("interference", ["other-year", "future-maturity", "statement-continuation", "specific-topic"])
def test_evidence_survives_other_years_continued_tables_and_generic_financial_terms(tmp_path, interference):
    async def scenario():
        discussion = (
            "Net revenue total revenue net sales operating profit operating income. "
            "Cash flow statement depreciation and amortization capital expenditure capital spending. "
            "2022 2021 2020 9500 9000 8500 8000 7500 7000 6500 6000. "
        )
        if interference == "other-year":
            target = "Revenue from licensing agreements was 1200 in 2022."
            documents = (
                PDFDocumentInput(document_id="ACME_2022_10K", content=make_pdf(target)),
                PDFDocumentInput(document_id="ACME_2023_10K", content=make_pdf(*(discussion for _ in range(24)))),
            )
            question = "What was ACME's revenue in FY2022?"
            expected_page = 1
        elif interference == "future-maturity":
            documents = (
                PDFDocumentInput(
                    document_id="ACME_2022_10K", content=make_pdf("Long-term debt maturing in 2030 was 1200 in 2022.")
                ),
                PDFDocumentInput(document_id="ACME_2030_10K", content=make_pdf(*(discussion for _ in range(24)))),
            )
            question = "What long-term debt maturing in 2030 did ACME report in FY2022?"
            expected_page = 1
        elif interference == "statement-continuation":
            header = (
                "Consolidated Statements of Cash Flows\n2022 2021 2020\n"
                "Operating activities 11000 10000 9000\nInvesting activities 8000 7000 6000"
            )
            continuation = "Continued\nDepreciation and amortization 1200 1100 1000"
            documents = (
                PDFDocumentInput(
                    document_id="ACME_2022_10K",
                    content=make_pdf(header, continuation, *(discussion for _ in range(24))),
                ),
            )
            question = "What was ACME's depreciation and amortization from the cash flow statement in FY2022?"
            expected_page = 2
        else:
            target = "Royalty revenue from the Meridian licensing agreement was 1200 in 2022."
            documents = (
                PDFDocumentInput(
                    document_id="ACME_2022_10K", content=make_pdf(target, *(discussion for _ in range(24)))
                ),
            )
            question = "What revenue did ACME recognize under the Meridian licensing agreement in FY2022?"
            expected_page = 1
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'recall.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=PageEvidencePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Reports", summary="tables", idempotency_key="recall"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for document in documents:
                for page in parse_pdf_document(document):
                    observation = project_source_for_transport(registry, await registry.resolve(page))
                    locations[observation.name] = (page.document_id, page.page_number)
                    await runtime.ingestion.submit(
                        SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                    )
            await flush_all(runtime.memory.for_scope(scope.scope_id), len(locations))
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for interface in (memory, SearchOnlyMemory(memory)):
                citations = await search_pdf_pages(interface, question, limit=20)
                found = set()
                for citation in citations:
                    record = await memory.get(GetMemoryEntryRequest(citation=citation))
                    found.add(locations[record.entry.sources[0].source_id])
                assert len(citations) == len(found) <= 20
                assert ("ACME_2022_10K", expected_page) in found

    asyncio.run(scenario())
