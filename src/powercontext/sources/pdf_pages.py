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
import unicodedata
import zlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator
from pypdf import PageObject, PdfReader
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    NameObject,
    NullObject,
    StreamObject,
)

from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.runtime import GetMemoryEntryRequest, MemoryEntryRecord, SearchMemoryRequest
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
    (re.compile(r"legal|litigation|lawsuit|battles"), ("legal proceedings", "litigation", "legal matters")),
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
        re.compile(r"ebitda|depreciation|amortization|\bd\s*&\s*a\b"),
        ("depreciation and amortization", "operating profit", "operating income"),
    ),
    (re.compile(r"restructur"), ("restructuring and impairment", "restructuring charges")),
    (re.compile(r"dividend"), ("dividends paid", "cash dividends")),
    (re.compile(r"repurchase|buyback"), ("share repurchase", "treasury stock")),
    (re.compile(r"effective tax|tax rate"), ("effective tax", "income taxes")),
    (re.compile(r"goodwill"), ("goodwill",)),
    (re.compile(r"gross margin|gross profit"), ("gross profit", "cost of sales")),
    (re.compile(r"\brevenues?\b|\b(?:net )?sales\b|\bturnover\b"), ("net revenue", "net sales", "total revenue")),
    (re.compile(r"interest expense|long-term debt"), ("interest expense", "long-term debt")),
    (re.compile(r"inventor"), ("inventories",)),
    (re.compile(r"receivable"), ("accounts receivable", "receivables")),
    (re.compile(r"\blease"), ("operating lease",)),
    (re.compile(r"pension"), ("pension", "retiree medical")),
    (re.compile(r"employee|workforce|headcount"), ("number of employees", "employees")),
    (
        re.compile(r"working capital|current ratio|quick ratio"),
        ("working capital", "current assets", "current liabilities"),
    ),
    (re.compile(r"free cash|\bfcf\b"), ("free cash flow", "cash flows", "capital expenditure")),
    (re.compile(r"return on assets|\broa\b"), ("net income", "total assets")),
    (re.compile(r"return on equity|\broe\b"), ("net income", "stockholders equity", "shareholders equity")),
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
_LEXICAL_TERMS = re.compile(r"[a-z]+|\d+")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_SPACED_GLYPHS = re.compile(r"\b(?:[A-Za-z0-9][^\S\r\n]+){3,}[A-Za-z0-9]\b")
# A query can need several primary statements whose rows use different wording.
# Heading detection excludes narrative references and requires numeric table content.
_STATEMENT_INTENTS = (
    (
        re.compile(r"income|earnings|revenue|sales|margin|profit|ebitda|p&l|tax|cost of|return on|\bro[ae]\b"),
        re.compile(
            r"^\s*(?:(?:consolidated|combined|condensed|group|unaudited)\s+)*"
            r"(?:statements?\s+of\s+(?:consolidated\s+)?(?:income|operations|earnings|profit)|income\s+statements?)\b",
            re.MULTILINE,
        ),
    ),
    (
        re.compile(r"cash.?flow|ebitda|\bfcf\b|capex|capital (?:expenditure|spending)|depreciation|amortization|d&a"),
        re.compile(
            r"^\s*(?:(?:consolidated|combined|condensed|group|unaudited)\s+)*"
            r"(?:statements?\s+of\s+(?:consolidated\s+)?cash\s+flows?|cash\s+flow\s+statements?)\b",
            re.MULTILINE,
        ),
    ),
    (
        re.compile(
            r"balance sheet|financial position|asset|liabilit|equity|debt|working capital|receivable|inventor|ratio|return on|\bro[ae]\b"
        ),
        re.compile(
            r"^\s*(?:(?:consolidated|combined|condensed|group|unaudited)\s+)*"
            r"(?:balance\s+sheets?|statements?\s+of\s+(?:financial\s+)?(?:position|condition))\b",
            re.MULTILINE,
        ),
    ),
)


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
        pages = _document_pages(reader)
        validated: set[int] = set()
        extracted = tuple((index, _extract_page_text(page, validated)) for index, page in enumerate(pages, start=1))
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


def _document_pages(reader: PdfReader) -> tuple[PageObject, ...]:
    root = reader.root_object["/Pages"]
    if not isinstance(root, DictionaryObject) or root["/Type"] != "/Pages":
        raise ValueError("PDF has no valid page tree")  # noqa: TRY003
    expected = _page_tree_count(root, set())
    pages = tuple(reader.pages)
    if len(pages) != expected:
        raise ValueError("PDF page tree could not be completely read")  # noqa: TRY003
    return pages


def _page_tree_count(node: object, ancestors: set[int]) -> int:
    if not isinstance(node, DictionaryObject):
        raise TypeError("PDF page tree contains an invalid node")  # noqa: TRY003
    node_type = node["/Type"]
    if node_type == "/Page":
        return 1
    if node_type != "/Pages" or id(node) in ancestors:
        raise ValueError("PDF page tree contains an invalid type or cycle")  # noqa: TRY003
    count = node["/Count"]
    children = node["/Kids"]
    if not isinstance(count, int) or count < 0 or not isinstance(children, ArrayObject):
        raise ValueError("PDF page tree has invalid children or count")  # noqa: TRY003
    ancestors.add(id(node))
    try:
        actual = sum(_page_tree_count(child.get_object(), ancestors) for child in children)
    finally:
        ancestors.remove(id(node))
    if actual != count:
        raise ValueError("PDF page count does not match its page tree")  # noqa: TRY003
    return actual


def _extract_page_text(page: PageObject, validated: set[int]) -> str:
    contents = page.get("/Contents")
    if contents is not None:
        contents = contents.get_object()
        streams = contents if isinstance(contents, ArrayObject) else (contents,)
        if any(not isinstance(stream.get_object(), (StreamObject, NullObject)) for stream in streams):
            raise ValueError("PDF page has an invalid content stream")  # noqa: TRY003
        for stream in streams:
            value = stream.get_object()
            if isinstance(value, StreamObject):
                _validate_text_stream(value, validated)
    resources = page.get("/Resources")
    if resources is not None:
        _validate_text_resources(resources.get_object(), validated)
    text = page.extract_text() or ""
    fragmented = _fragmented_glyph_count(text)
    if not fragmented:
        return text
    # Some generators position every glyph and then append a TJ advance before
    # ET. Plain extraction mistakes those unused advances for spaces inside words
    # and numbers. Layout extraction uses the actual positions instead.
    try:
        layout = page.extract_text(extraction_mode="layout", layout_mode_strip_rotated=False) or ""
    except (KeyError, TypeError, ValueError, NotImplementedError):
        return text
    if _fragmented_glyph_count(layout) < fragmented:
        characters = Counter(character for character in text if not character.isspace())
        if characters == Counter(character for character in layout if not character.isspace()):
            return "\n".join(" ".join(line.split()) for line in layout.splitlines()).strip()
    return _restore_positioned_runs(text, layout)


def _fragmented_glyph_count(text: str) -> int:
    return sum(sum(character.isalnum() for character in match[0]) for match in _SPACED_GLYPHS.finditer(text))


def _restore_positioned_runs(text: str, layout: str) -> str:
    positions = [index for index, character in enumerate(layout) if character.isalnum()]
    compact = "".join(layout[index] for index in positions)

    def restore(match: re.Match[str]) -> str:
        needle = "".join(match[0].split())
        candidates: set[str] = set()
        offset = compact.find(needle)
        while offset != -1:
            candidate = layout[positions[offset] : positions[offset + len(needle) - 1] + 1]
            if all(character.isalnum() or character.isspace() for character in candidate):
                candidates.add(" ".join(candidate.split()))
            offset = compact.find(needle, offset + 1)
        if len(candidates) == 1:
            candidate = candidates.pop()
            if _fragmented_glyph_count(candidate) < _fragmented_glyph_count(match[0]):
                return candidate
        return match[0]

    # Only restore spacing of matching character sequences, keeping the plain
    # extraction's ordering, chart labels, rotated text, and every repeated digit.
    # Layout mode can omit those items, so replacing a complete page is unsafe.
    return _SPACED_GLYPHS.sub(restore, text)


def _validate_text_stream(stream: StreamObject, validated: set[int]) -> None:
    if id(stream) in validated:
        return
    validated.add(id(stream))
    if not isinstance(stream, EncodedStreamObject):
        return
    filters = stream.get("/Filter")
    filters = filters.get_object() if filters is not None else NullObject()
    filters = (
        tuple(filters) if isinstance(filters, ArrayObject) else (() if isinstance(filters, NullObject) else (filters,))
    )
    parameters = stream.get("/DecodeParms")
    parameters = parameters.get_object() if parameters is not None else NullObject()
    if isinstance(parameters, NullObject):
        parameters = (NullObject(),) * len(filters)
    elif isinstance(parameters, ArrayObject):
        parameters = tuple(parameters)
    else:
        parameters = (parameters,)
    if len(parameters) != len(filters):
        raise ValueError("PDF filter parameters do not match its filters")  # noqa: TRY003
    # pypdf deliberately recovers broken compressed streams, even in strict
    # reader mode. Validate each compression envelope before accepting its text.
    data = stream._data
    for name, parameter in zip(filters, parameters, strict=True):
        stage = EncodedStreamObject()
        stage._data = data
        stage[NameObject("/Filter")] = name.get_object()
        stage[NameObject("/DecodeParms")] = parameter.get_object()
        decoded = stage.get_data()
        if name.get_object() in ("/FlateDecode", "/Fl"):
            _validate_flate_envelope(data)
        data = decoded
    # Cache the fully decoded chain. In pypdf 6.19 a null /DecodeParms with
    # multiple filters otherwise stops decoding after the first filter.
    decoded_stream = DecodedStreamObject()
    decoded_stream.set_data(data)
    stream.decoded_self = decoded_stream


def _validate_flate_envelope(data: bytes) -> None:
    decoder = zlib.decompressobj()
    pending = data
    while pending:
        decoder.decompress(pending, 1_048_576)
        pending = decoder.unconsumed_tail
    if not decoder.eof:
        raise ValueError("PDF contains a truncated compressed stream")  # noqa: TRY003


def _validate_text_resources(resources: object, validated: set[int]) -> None:
    if not isinstance(resources, DictionaryObject):
        raise TypeError("PDF text resources must be a dictionary")  # noqa: TRY003
    if id(resources) in validated:
        return
    validated.add(id(resources))
    fonts = resources.get("/Font", DictionaryObject()).get_object()
    for font in fonts.values():
        font = font.get_object()
        mapping = font.get("/ToUnicode")
        if mapping is not None:
            mapping = mapping.get_object()
            if isinstance(mapping, StreamObject):
                _validate_text_stream(mapping, validated)
    forms = resources.get("/XObject", DictionaryObject()).get_object()
    for form in forms.values():
        form = form.get_object()
        if isinstance(form, StreamObject) and form.get("/Subtype") == "/Form":
            _validate_text_stream(form, validated)
            nested = form.get("/Resources")
            if nested is not None:
                _validate_text_resources(nested.get_object(), validated)


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
    if not listed:
        listed = await _searched_pages(memory, question, limit)
    return _rank_pages(listed, question, limit) if listed else ()


async def _searched_pages(memory, question: str, limit: int) -> tuple[_CorpusPage, ...]:
    records: dict[tuple[str, str], MemoryEntryRecord] = {}
    for query in _search_queries(question, _question_terms(question)):
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(limit * 4, 80)))
        for hit in page.hits:
            key = (hit.entry_id, hit.entry_version_id)
            if key in records:
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            records[key] = await memory.get(GetMemoryEntryRequest(citation=citation))
    return _corpus_pages(records.values())


def _page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    source_id = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        # A separate namespace keeps hashed ids distinct from literal ids.
        document_key = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        source_id = f"pdf-sha256:{document_key}:{document_sha256}:{page_number}"
    return source_id


def _question_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    for term in _LEXICAL_TERMS.findall(_normalize_question(question)):
        if term in _QUERY_STOPWORDS or term in _QUERY_NOISE or (len(term) <= 2 and not term.isdigit()):
            continue
        if term not in terms:
            terms.append(term)
    return tuple(terms)


def _search_queries(question: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    queries = [" ".join(terms), *_expansion_phrases(_normalize_question(question))]
    ranked = sorted(terms, key=lambda term: (term.isdigit() or len(term) >= 6, len(term)), reverse=True)
    queries.extend(ranked[:10])
    queries.extend(f"{left} {right}" for left, right in pairwise(ranked))
    unique: list[str] = []
    for query in queries:
        if query and query not in unique:
            unique.append(query)
    return tuple(unique[:32])


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
    return _corpus_pages(listed.entries)


def _corpus_pages(records: Iterable[MemoryEntryRecord]) -> tuple[_CorpusPage, ...]:
    grouped: dict[str, _CorpusPage] = {}
    for record in records:
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
    return tuple(grouped.values())


def _rank_pages(pages: tuple[_CorpusPage, ...], question: str, limit: int) -> tuple[MemoryCitation, ...]:
    folded_question = _normalize_question(question)
    document_names = {page.document_key: page.document_id for page in pages}
    year = max(_YEAR.findall(folded_question), default="")
    priorities = {
        key: (matched, matched and bool(year) and year in _YEAR.findall(name))
        for key, name in document_names.items()
        for matched in (_matches_document_name(name, folded_question),)
    }
    identity_terms = {
        _stem(term)
        for key, name in document_names.items()
        if priorities[key][0]
        for term in _LEXICAL_TERMS.findall(name.casefold())
    }
    phrases = _expansion_phrases(folded_question)
    query_counts = Counter(
        _stem(term)
        for term in _LEXICAL_TERMS.findall(folded_question)
        if term not in _QUERY_NOISE and not _YEAR.fullmatch(term) and _stem(term) not in identity_terms
    )
    literal_weights = {term: 1.0 + math.log(count) for term, count in query_counts.items()}
    query_weights = literal_weights.copy()
    for phrase in phrases:
        for term in _LEXICAL_TERMS.findall(phrase):
            if term not in _QUERY_NOISE:
                query_weights.setdefault(_stem(term), 0.35)
    normalized_pages = [unicodedata.normalize("NFKC", page.text.partition("\n")[2]).casefold() for page in pages]
    token_rows = [Counter(_stem(term) for term in _LEXICAL_TERMS.findall(text)) for text in normalized_pages]
    lengths = [sum(counts.values()) for counts in token_rows]
    document_frequency: Counter[str] = Counter()
    for counts in token_rows:
        document_frequency.update(counts.keys())
    total = len(pages)
    average = (sum(lengths) / total) if total else 1.0
    numeric = _NUMERIC_QUESTION.search(folded_question) is not None
    geography = _GEOGRAPHY_QUESTION.search(folded_question) is not None
    folded_pages = [" ".join(text.split()) for text in normalized_pages]
    scores = {
        page.source_id: _lexical_score(
            counts,
            length,
            folded,
            query_weights=query_weights,
            document_frequency=document_frequency,
            total=total,
            average=average,
            phrases=phrases,
            numeric=numeric,
            geography=geography,
        )
        for page, counts, length, folded in zip(pages, token_rows, lengths, folded_pages, strict=True)
    }
    literal_scores = {
        page.source_id: _lexical_score(
            counts,
            length,
            folded,
            query_weights=literal_weights,
            document_frequency=document_frequency,
            total=total,
            average=average,
            phrases=(),
            numeric=False,
            geography=False,
        )
        for page, counts, length, folded in zip(pages, token_rows, lengths, folded_pages, strict=True)
    }
    _add_neighbor_bonus(pages, folded_pages, phrases, scores)
    # Rank fusion preserves specific query terms even when several financial
    # expansions match a generic discussion page. Their numeric scores have
    # different scales and must not compete through unbounded phrase bonuses.
    fused: Counter[str] = Counter()
    for channel in (literal_scores, scores):
        ranked = sorted(
            pages,
            key=lambda page: (
                tuple(not flag for flag in priorities[page.document_key]),
                -channel[page.source_id],
                page.page_number,
                page.source_id,
            ),
        )
        for rank, page in enumerate(ranked, start=1):
            if channel[page.source_id] > 0:
                fused[page.source_id] += 1.0 / (20 + rank)
    ordered = sorted(
        pages,
        key=lambda page: (
            tuple(not flag for flag in priorities[page.document_key]),
            -fused[page.source_id],
            page.page_number,
            page.source_id,
        ),
    )
    return _select_evidence_pages(ordered, folded_question, priorities, limit)


def _normalize_question(question: str) -> str:
    normalized = unicodedata.normalize("NFKC", question).casefold()
    return re.sub(r"\bfy\s*(\d{4})\b", r"\1", normalized)


def _select_evidence_pages(
    ordered: list[_CorpusPage],
    question: str,
    priorities: dict[str, tuple[bool, bool]],
    limit: int,
) -> tuple[MemoryCitation, ...]:
    best_priority = max(priorities.values())
    year = max(_YEAR.findall(question), default="")
    headers = {page.source_id: page.text.partition("\n")[2][:1200].casefold() for page in ordered}
    anchors: dict[str, _CorpusPage] = {}
    for intent, heading in _STATEMENT_INTENTS:
        if not intent.search(question):
            continue
        candidates = sorted(
            (
                page
                for page in ordered
                if priorities[page.document_key] == best_priority
                and heading.search(headers[page.source_id])
                and len(_AMOUNT_PATTERN.findall(page.text)) >= 8
            ),
            key=lambda page: (
                bool(year) and year not in headers[page.source_id],
                not _matches_document_name(page.document_id, headers[page.source_id]),
            ),
        )
        for _, page in zip(range(2), candidates, strict=False):
            anchors[page.source_id] = page
    locations = {(page.document_key, page.page_number): page for page in ordered}
    for anchor in tuple(anchors.values()):
        following = locations.get((anchor.document_key, anchor.page_number + 1))
        if following is not None and re.search(r"\bcontinu(?:ed|ation)\b", headers[following.source_id][:500]):
            anchors[following.source_id] = following
    # Reserve a small part of the result for each requested primary statement;
    # discussion pages remain eligible for the rest of the evidence budget.
    selected = dict.fromkeys(tuple(anchors)[: limit // 2])
    for page in ordered:
        if len(selected) >= limit:
            break
        selected.setdefault(page.source_id)
    return tuple(page.citation for page in ordered if page.source_id in selected)


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
    query_weights: dict[str, float],
    document_frequency: Counter[str],
    total: int,
    average: float,
    phrases: tuple[str, ...],
    numeric: bool,
    geography: bool,
) -> float:
    score = 0.0
    norm = 0.25 + 0.75 * (length / average if average else 1.0)
    for term, query_weight in query_weights.items():
        frequency = counts.get(term, 0)
        if not frequency:
            continue
        seen = document_frequency[term]
        idf = math.log((total - seen + 0.5) / (seen + 0.5) + 1.0)
        score += idf * (frequency * 2.2) / (frequency + 1.2 * norm) * query_weight
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
    if len(token) > 4 and token.endswith("ies"):
        token = token[:-3] + "y"
    elif len(token) > 4 and token.endswith(("xes", "ches", "shes", "sses")):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    if token.isdigit() or len(token) < 6:
        return token
    return token[:6]


def _trailing_page_number(source_id: str) -> int:
    tail = source_id.rsplit(":", 1)[-1]
    if tail.isdecimal():
        return int(tail)
    return 0
