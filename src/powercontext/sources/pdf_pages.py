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

"""Parse every physical PDF page and retrieve exact page citations from Memory."""

from __future__ import annotations

import hashlib
import io
import re
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "fy",
        "has",
        "if",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "then",
        "to",
        "was",
        "were",
        "what",
        "which",
        "who",
        "with",
    }
)


class PDFDocumentInput(BaseModel):
    """One complete original PDF addressed by a caller-stable document id."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """Extracted text for one physical page. Page numbers start at 1."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """Captured page text. A new PDF byte string receives a new identity."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Return every physical page, or fail without a partial result."""

    if not isinstance(value.document_id, str) or value.document_id.strip() == "":
        raise ValueError("document_id must not be blank")  # noqa: TRY003
    if not value.content.startswith(b"%PDF-"):
        raise ValueError("document is not a PDF")  # noqa: TRY003
    document_sha256 = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content))
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF requires a password")  # noqa: TRY003
        extracted = tuple((index, page.extract_text() or "") for index, page in enumerate(reader.pages, start=1))
    except ValueError:
        raise
    except (PdfReadError, OSError, TypeError, KeyError, AssertionError) as error:
        raise ValueError("PDF document could not be parsed") from error  # noqa: TRY003
    if not extracted:
        raise ValueError("PDF document does not contain a page")  # noqa: TRY003
    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=document_sha256,
            page_number=page_number,
            text=text,
        )
        for page_number, text in extracted
    )


class PDFPageSourceAdapter:
    """Resolve one parsed page into a captured Source and read that page back."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = _page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose page text with the document id and physical page number."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` citations for different pages that match the question."""

    if limit < 1 or not isinstance(question, str) or question.strip() == "":
        return ()
    ranked: dict[str, tuple[int, int, MemoryCitation]] = {}
    terms = _question_terms(question)
    order = 0
    for query in _search_queries(question, terms):
        try:
            page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(limit, 20)))
        except (TypeError, ValueError):
            continue
        for hit in page.hits:
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            entry = await memory.get(GetMemoryEntryRequest(citation=citation))
            page_ids = tuple(source.source_id for source in entry.entry.sources)
            if not page_ids:
                continue
            page_key = page_ids[0]
            score = _overlap_score(entry.entry.text, terms)
            current = ranked.get(page_key)
            if current is None or score > current[0]:
                ranked[page_key] = (score, order, citation)
                order += 1
            if len(ranked) >= limit * 4:
                break
    selected = sorted(ranked.values(), key=lambda item: (-item[0], item[1]))
    return tuple(item[2] for item in selected[:limit])


def _page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    source_id = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("PDF page source id must not exceed 256 characters")  # noqa: TRY003
    return source_id


def _question_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    for term in re.findall(r"[A-Za-z0-9]+", question.casefold()):
        if term in _QUERY_STOPWORDS or (len(term) <= 2 and not term.isdigit()):
            continue
        if term not in terms:
            terms.append(term)
    return tuple(terms)


def _search_queries(question: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    queries = [question.strip()]
    ranked = sorted(terms, key=lambda term: (term.isdigit() or len(term) >= 6, len(term)), reverse=True)
    queries.extend(ranked[:10])
    queries.extend(f"{left} {right}" for left, right in zip(ranked, ranked[1:], strict=False))
    unique: list[str] = []
    for query in queries:
        if query and query not in unique:
            unique.append(query)
    return tuple(unique[:24])


def _overlap_score(text: str, terms: tuple[str, ...]) -> int:
    folded = text.casefold()
    score = 0
    for term in terms:
        if term not in folded:
            continue
        score += 4 if term.isdigit() or len(term) >= 7 else 1
    return score
