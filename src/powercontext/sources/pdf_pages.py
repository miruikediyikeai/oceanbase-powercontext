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
import math
import re
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator
from pypdf import PageObject, PdfReader
from pypdf.generic import ArrayObject, NullObject, StreamObject

from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
_QUERY_STOPWORDS = frozenset({
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
})
_QUERY_NOISE = frozenset(
    analyze_text(
        "what which who how when where does did has have had any the a an and or of to for from in on by with "
        "is are was were be been being this that these those it its their our not no if then else state answer "
        "respond question calculate define using give only use shown within assuming perspective investment "
        "analyst details report reported directly explicitly outlined quantity less unadjusted fy"
    ).split()
)
_GEOGRAPHY_QUESTION = re.compile(r"geograph|countries|regions|primarily operates")
_NUMERIC_QUESTION = re.compile(
    r"margin|income|revenue|expense|cost|ebitda|capex|cash flow|earnings|profit|tax|debt|dividend|amount|"
    r"quantity|million|balance sheet|impairment"
)
_REGION_NAMES = (
    "north america",
    "latin america",
    "europe",
    "asia pacific",
    "middle east",
    "africa",
    "united states",
    "canada",
)
# Analyst wording rarely appears on the statement or note page that actually answers it.
_EXPANSIONS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (_GEOGRAPHY_QUESTION, ("reportable segments", "organized into", "geographic")),
    (re.compile(r"legal|litigation|lawsuit|battles"), ("legal proceedings",)),
    (
        re.compile(r"income statement|p&l|p l statement|profit and loss"),
        ("statement of income", "statements of income", "statements of operations", "operating profit"),
    ),
    (
        re.compile(r"cash flow"),
        ("statement of cash flows", "statements of cash flows", "depreciation and amortization"),
    ),
    (re.compile(r"capex|capital expenditure"), ("purchases of property", "capital expenditure", "capital spending")),
    (
        re.compile(r"ebitda|depreciation|amortization"),
        ("depreciation and amortization", "operating profit", "operating income"),
    ),
    (re.compile(r"restructur"), ("restructuring and impairment", "restructuring charges")),
    (re.compile(r"dividend"), ("dividends paid", "cash dividends")),
    (re.compile(r"repurchase|buyback"), ("share repurchase", "treasury stock")),
    (re.compile(r"effective tax|tax rate"), ("effective tax", "income taxes")),
    (re.compile(r"goodwill"), ("goodwill",)),
    (re.compile(r"gross margin|gross profit"), ("gross profit", "cost of sales")),
    (re.compile(r"\brevenue\b|net sales"), ("net revenue", "net sales")),
    (re.compile(r"interest expense|long-term debt"), ("interest expense", "long-term debt")),
    (re.compile(r"inventor"), ("inventories",)),
    (re.compile(r"receivable"), ("accounts receivable", "receivables")),
    (re.compile(r"\blease"), ("operating lease",)),
    (re.compile(r"pension"), ("pension", "retiree medical")),
    (re.compile(r"employee|workforce|headcount"), ("number of employees", "employees")),
    (re.compile(r"working capital"), ("working capital",)),
    (re.compile(r"free cash"), ("free cash flow",)),
    (re.compile(r"earnings per share|\beps\b"), ("earnings per share",)),
    (re.compile(r"selling general|\bsg&a\b|\bsga\b"), ("selling general and administrative",)),
    (re.compile(r"research and development"), ("research and development",)),
    (re.compile(r"contingenc"), ("commitments and contingencies",)),
    (re.compile(r"subsequent event"), ("subsequent events",)),
    (re.compile(r"share-based|stock-based|share based|stock based"), ("share-based compensation",)),
    (re.compile(r"balance sheet|financial position"), ("total assets", "financial position")),
    (re.compile(r"comprehensive income"), ("comprehensive income",)),
    (re.compile(r"stockholders|shareholders. equity"), ("stockholders equity", "shareholders equity")),
    (re.compile(r"auditor|independent registered"), ("independent registered public accounting",)),
    (re.compile(r"risk factor"), ("risk factors",)),
    (re.compile(r"impairment"), ("impairment",)),
    (re.compile(r"acquisition|divestiture"), ("acquisition", "divestiture")),
)
_PHRASE_BONUS = 8.0
_NEIGHBOR_BONUS = 6.0
_PRECISE_PHRASE_LIMIT = 6
_AMOUNT_PATTERN = re.compile(r"\d[\d,]{2,}")


class PDFDocumentInput(BaseModel):
    """One complete original PDF addressed by a caller-stable document id."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    """Extracted text for one physical page. Page numbers start at 1."""

    document_id: str
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value


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
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF requires a password")  # noqa: TRY003, TRY301
        extracted = tuple((index, _extract_page_text(page)) for index, page in enumerate(reader.pages, start=1))
    except Exception as error:
        # pypdf can also raise codec, decompression and recursion errors when a
        # later page is damaged. None may escape as a partially accepted PDF.
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


def _extract_page_text(page: PageObject) -> str:
    contents = page.get("/Contents")
    if contents is not None:
        contents = contents.get_object()
        streams = contents if isinstance(contents, ArrayObject) else (contents,)
        if any(not isinstance(stream.get_object(), (StreamObject, NullObject)) for stream in streams):
            raise ValueError("PDF page has an invalid content stream")  # noqa: TRY003
    return page.extract_text() or ""


class PDFPageSourceAdapter:
    """Resolve one parsed page into a captured Source and read that page back."""

    input_class: type[PDFPageInput] = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class: type[PDFPageSource] = PDFPageSource

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
    source_class: type[PDFPageSource] = PDFPageSource
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
    listed = await _listed_pages(memory)
    if listed:
        return _rank_pages(listed, question, limit)
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
            if len(entry.entry.sources) != 1 or entry.entry.sources[0].source_type != PDF_PAGE_SOURCE_NAME:
                continue
            page_key = entry.entry.sources[0].source_id
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
        # A separate namespace keeps hashed ids distinct from literal ids.
        document_key = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        source_id = f"pdf-sha256:{document_key}:{document_sha256}:{page_number}"
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
    queries.extend(f"{left} {right}" for left, right in pairwise(ranked))
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


@dataclass
class _CorpusPage:
    source_id: str
    citation: MemoryCitation
    text: str
    page_number: int
    document_key: str
    document_id: str


async def _listed_pages(memory) -> tuple[_CorpusPage, ...]:
    list_entries = getattr(memory, "list", None)
    if list_entries is None:
        return ()
    listed = await list_entries()
    grouped: dict[str, _CorpusPage] = {}
    for record in listed.entries:
        entry = record.entry
        if len(entry.sources) != 1 or entry.sources[0].source_type != PDF_PAGE_SOURCE_NAME or not entry.text.strip():
            continue
        source_id = entry.sources[0].source_id
        current = grouped.get(source_id)
        if current is None:
            grouped[source_id] = _CorpusPage(
                source_id=source_id,
                citation=record.citation,
                text=entry.text,
                page_number=_trailing_page_number(source_id),
                document_key=source_id.rsplit(":", 1)[0],
                document_id=entry.text.partition("\n")[0],
            )
        else:
            current.text = f"{current.text}\n{entry.text}"
    return tuple(grouped.values())


def _rank_pages(pages: tuple[_CorpusPage, ...], question: str, limit: int) -> tuple[MemoryCitation, ...]:
    folded_question = re.sub(r"\bfy\s*(\d{4})\b", r"\1", question.casefold())
    phrases = _expansion_phrases(folded_question)
    query_counts = Counter(
        _stem(term) for term in analyze_fts_query(folded_question).split() if term not in _QUERY_NOISE
    )
    token_rows = [Counter(_stem(term) for term in analyze_text(page.text).split()) for page in pages]
    lengths = [sum(counts.values()) for counts in token_rows]
    document_frequency: Counter[str] = Counter()
    for counts in token_rows:
        document_frequency.update(counts.keys())
    total = len(pages)
    average = (sum(lengths) / total) if total else 1.0
    numeric = _NUMERIC_QUESTION.search(folded_question) is not None
    geography = _GEOGRAPHY_QUESTION.search(folded_question) is not None
    folded_pages = [" ".join(page.text.casefold().split()) for page in pages]
    scores = {
        page.source_id: _lexical_score(
            counts,
            length,
            folded,
            query_counts=query_counts,
            document_frequency=document_frequency,
            total=total,
            average=average,
            phrases=phrases,
            numeric=numeric,
            geography=geography,
        )
        for page, counts, length, folded in zip(pages, token_rows, lengths, folded_pages, strict=True)
    }
    document_names = {page.document_key: page.document_id for page in pages}
    document_matches = {key: _matches_document_name(name, folded_question) for key, name in document_names.items()}
    for page in pages:
        if document_matches[page.document_key]:
            scores[page.source_id] += 24.0
    _add_neighbor_bonus(pages, folded_pages, phrases, scores)
    ordered = sorted(pages, key=lambda page: (-scores[page.source_id], page.page_number, page.source_id))
    return tuple(page.citation for page in ordered[:limit])


def _matches_document_name(document_id: str, question: str) -> bool:
    # Finance document identifiers commonly append a year and filing type.
    # The name before that suffix is useful even on tables without a company heading.
    name = re.split(r"[_\s-](?=(?:19|20)\d{2}(?:\D|$))", document_id.casefold(), maxsplit=1)[0]
    compact_name = re.sub(r"[^a-z0-9]", "", name)
    pattern = r"\b" + r"[\W_]*".join(compact_name) + r"\b"
    return len(compact_name) >= 2 and re.search(pattern, question) is not None


def _lexical_score(
    counts: Counter[str],
    length: int,
    folded: str,
    *,
    query_counts: Counter[str],
    document_frequency: Counter[str],
    total: int,
    average: float,
    phrases: tuple[str, ...],
    numeric: bool,
    geography: bool,
) -> float:
    score = 0.0
    norm = 0.25 + 0.75 * (length / average if average else 1.0)
    for term, query_frequency in query_counts.items():
        frequency = counts.get(term, 0)
        if not frequency:
            continue
        seen = document_frequency[term]
        idf = math.log((total - seen + 0.5) / (seen + 0.5) + 1.0)
        score += idf * (frequency * 2.2) / (frequency + 1.2 * norm) * (1.0 + math.log(query_frequency))
    for phrase in phrases:
        if phrase in folded:
            score += _PHRASE_BONUS
    if numeric:
        amounts = len(_AMOUNT_PATTERN.findall(folded))
        if amounts >= 8:
            score += min(amounts, 36) / 4.0
    if geography:
        region_hits = sum(region in folded for region in _REGION_NAMES)
        if region_hits >= 3:
            score += 4.0 + region_hits
    return score


def _add_neighbor_bonus(
    pages: tuple[_CorpusPage, ...],
    folded_pages: list[str],
    phrases: tuple[str, ...],
    scores: dict[str, float],
) -> None:
    location = {(page.document_key, page.page_number): page.source_id for page in pages if page.page_number > 0}
    precise: set[tuple[str, int]] = set()
    for phrase in phrases:
        matched = [
            (page.document_key, page.page_number)
            for page, folded in zip(pages, folded_pages, strict=True)
            if page.page_number > 0 and phrase in folded
        ]
        if 0 < len(matched) <= _PRECISE_PHRASE_LIMIT:
            precise.update(matched)
    for document_key, number in precise:
        for neighbor in (number - 1, number + 1):
            source_id = location.get((document_key, neighbor))
            if source_id is not None:
                scores[source_id] += _NEIGHBOR_BONUS


def _expansion_phrases(folded_question: str) -> tuple[str, ...]:
    phrases: list[str] = []
    for pattern, candidates in _EXPANSIONS:
        if pattern.search(folded_question) is None:
            continue
        for phrase in candidates:
            if phrase not in phrases:
                phrases.append(phrase)
    return tuple(phrases)


def _stem(token: str) -> str:
    if token.isdigit() or len(token) < 6:
        return token
    return token[:6]


def _trailing_page_number(source_id: str) -> int:
    tail = source_id.rsplit(":", 1)[-1]
    if tail.isdecimal():
        return int(tail)
    return 0
