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
from copy import copy
from dataclasses import dataclass
from itertools import chain, pairwise
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator
from pypdf import PageObject, PdfReader
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    NameObject,
    NullObject,
    StreamObject,
    TextStringObject,
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
        "analyst details report reported directly explicitly outlined quantity less unadjusted fy "
        "disclose disclosed disclosure regarding provide provided"
    ).split()
)
_GEOGRAPHY_QUESTION = re.compile(r"geograph|countries|regions|primarily operates")
_NUMERIC_QUESTION = re.compile(
    r"margin|income|revenue|expense|cost|ebitda|capex|cash flow|earnings|profit|tax|debt|dividend|amount|"
    r"quantity|million|balance sheet|impairment"
)
_OPERATING_RATIO_PATTERN = (
    r"(?:asset|inventory|inventories|receivables?|payables?|working capital) turnover|"
    r"(?:cash conversion|cash operating|operating) cycle|"
    r"days (?:sales|receivables?|payables?|inventory|inventories) outstanding|\b(?:dso|dpo|dio)\b"
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
    (re.compile(r"segments?|business units|business divisions"), ("reportable segments", "operating segments")),
    (re.compile(r"legal|litigation|lawsuit|battles"), ("legal proceedings", "litigation", "legal matters")),
    (
        re.compile(r"income statement|p&l|p l statement|profit and loss"),
        ("statement of income", "statements of income", "statements of operations", "operating profit"),
    ),
    (
        re.compile(r"cash flow"),
        ("statement of cash flows", "statements of cash flows", "depreciation and amortization"),
    ),
    (
        re.compile(r"capex|capital expenditure|fixed assets|purchas\w* (?:land|buildings|equipment)|expand.*facilit"),
        ("purchases of property", "capital expenditure", "capital spending", "property plant and equipment"),
    ),
    (
        re.compile(r"ebitda|depreciation|amortization|\bd\s*&\s*a\b"),
        ("depreciation and amortization", "operating profit", "operating income"),
    ),
    (
        re.compile(r"restructur|downsiz|clos(?:ing|ure of) facilit"),
        ("restructuring and impairment", "restructuring charges", "restructuring costs"),
    ),
    (re.compile(r"dividend"), ("dividends paid", "cash dividends")),
    (re.compile(r"repurchas|buyback|buy(?:ing)? back|bought back"), ("share repurchase", "treasury stock")),
    (re.compile(r"effective tax|tax rate"), ("effective tax", "income taxes")),
    (re.compile(r"goodwill"), ("goodwill",)),
    (re.compile(r"gross margin|gross profit"), ("gross profit", "cost of sales")),
    (re.compile(r"\brevenues?\b|\b(?:net )?sales\b|\bturnover\b"), ("net revenue", "net sales", "total revenue")),
    (
        re.compile(r"interest expense|long.term debt|borrowings?|debt maturit|repay\w* (?:loans?|debt)"),
        ("interest expense", "long-term debt", "debt maturities"),
    ),
    (re.compile(r"inventor"), ("inventories",)),
    (re.compile(r"receivable|credit sales|collect\w*.*customers"), ("accounts receivable", "trade receivables")),
    (re.compile(r"payables?|pay\w*.*suppliers|suppliers.*pay"), ("accounts payable", "trade payables")),
    (re.compile(r"\blease|\brent(?:ed|al|ing)?\b"), ("operating lease", "lease payments")),
    (re.compile(r"pension|retire\w* benefits"), ("pension", "retirement benefits", "retiree medical")),
    (re.compile(r"stock options?|option.*awarded|option.*valu"), ("stock options", "black scholes", "share-based")),
    (
        re.compile(r"uncertain.*tax|tax.*uncertain|deductions.*tax returns"),
        ("unrecognized tax benefits", "uncertain tax positions"),
    ),
    (re.compile(r"committ\w* purchas|purchase obligations?"), ("purchase commitments", "purchase obligations")),
    (re.compile(r"currenc(?:y|ies)|exchange rates?"), ("foreign currency translation", "exchange rates")),
    (re.compile(r"employee|workforce|headcount"), ("number of employees", "employees")),
    (
        re.compile(r"working capital|current ratio|quick ratio"),
        ("working capital", "current assets", "current liabilities"),
    ),
    (re.compile(r"free cash|\bfcf\b"), ("free cash flow", "cash flows", "capital expenditure")),
    (re.compile(r"return on assets|\broa\b"), ("net income", "total assets")),
    (re.compile(r"return on equity|\broe\b"), ("net income", "stockholders equity", "shareholders equity")),
    (
        re.compile(_OPERATING_RATIO_PATTERN),
        ("net sales", "cost of sales", "accounts receivable", "inventories", "accounts payable", "total assets"),
    ),
    (re.compile(r"interest coverage|times interest earned"), ("operating income", "interest expense")),
    (re.compile(r"earnings per share|\beps\b|dilut\w*.*(?:earnings|shares?)"), ("earnings per share", "antidilutive")),
    (re.compile(r"selling general|\bsg&a\b|\bsga\b"), ("selling general and administrative",)),
    (re.compile(r"research and development|develop\w* new products"), ("research and development",)),
    (re.compile(r"\blifo\b|last.in.*first.out|most recently purchased"), ("lifo", "last in first out")),
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
_TEXT_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})
_WORD_PARTICLES = frozenset({
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "if",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "per",
    "the",
    "to",
    "was",
    "we",
    "with",
})
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_FISCAL_YEAR = re.compile(r"\b(?:fy\s*|fiscal\s+year\s+(?:ended\s+)?)(19\d{2}|20\d{2})\b")


def _statement_heading(*titles: str) -> re.Pattern[str]:
    # PDF text objects can split a title within a word or put its last letter
    # on another line. This affects retrieval only, not the captured page text.
    alternatives = (
        r"\s+".join(r"\s*".join(re.escape(character) for character in word) for word in title.split())
        for title in titles
    )
    return re.compile(
        r"^\s*(?:(?:consolidated|combined|condensed|group|unaudited)\s+)*(?:" + "|".join(alternatives) + r")\b",
        re.MULTILINE,
    )


# A query can need several primary statements whose rows use different wording.
# Row concepts distinguish tables from indexes and narrative title references.
_STATEMENT_INTENTS = (
    (
        re.compile(
            r"income|earnings|revenue|sales|margin|profit|ebitda|p&l|tax|cost of|return on|payout|\bro[ae]\b|"
            r"interest coverage|times interest earned|" + _OPERATING_RATIO_PATTERN
        ),
        _statement_heading(
            "statement of income",
            "statements of income",
            "statements of consolidated income",
            "statement of operations",
            "statements of operations",
            "statement of earnings",
            "statements of earnings",
            "statement of profit",
            "statements of profit",
            "income statement",
            "income statements",
        ),
        (
            r"\b(?:net (?:sales|revenue)|total (?:net )?(?:sales|revenue)|revenues?)\b",
            r"\b(?:cost of (?:sales|revenue)|operating (?:income|profit|expenses))\b",
            r"\b(?:net (?:income|earnings)|income tax|earnings per)\b",
        ),
    ),
    (
        re.compile(
            r"cash.?flow|ebitda|\bfcf\b|capex|capital (?:expenditure|spending)|depreciation|amortization|d&a|"
            r"dividend|payout|repurchas|buyback"
        ),
        _statement_heading(
            "statement of cash flow",
            "statements of cash flow",
            "statement of cash flows",
            "statements of cash flows",
            "statements of consolidated cash flows",
            "cash flow statement",
            "cash flow statements",
            "cash flows statements",
        ),
        (
            r"\b(?:operating activities|cash (?:provided by|from) (?:operating|operations))\b",
            r"\binvesting activities\b",
            r"\bfinancing activities\b",
            r"\b(?:net income|depreciation|amortization)\b",
        ),
    ),
    (
        re.compile(
            r"balance sheet|financial position|asset|liabilit|equity|debt|working capital|receivable|inventor|"
            r"(?:current|quick|cash) ratio|leverage|return on|\bro[ae]\b|" + _OPERATING_RATIO_PATTERN
        ),
        _statement_heading(
            "balance sheet",
            "balance sheets",
            "statement of financial position",
            "statements of financial position",
            "statement of financial condition",
            "statements of financial condition",
            "statement of condition",
            "statements of condition",
        ),
        (
            r"\b(?:total assets|current assets)\b",
            r"\b(?:total liabilities|current liabilities)\b",
            r"\b(?:stockholders|shareholders|shareowners).{0,3} equity\b",
            r"\bcash and (?:cash equivalents|due from banks)\b",
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
    plain = page.extract_text() or ""
    if "\x00" in plain:
        page = _restore_actual_text_font_maps(page)
        plain = page.extract_text() or ""
    return _recover_word_boundaries(page, plain.translate(_TEXT_LIGATURES))


def _restore_actual_text_font_maps(page: PageObject) -> PageObject:
    """Recover missing glyph mappings from this PDF's own marked content.

    Some producers map ligatures to NUL in ToUnicode and supply their Unicode
    text in ActualText instead. pypdf ignores the latter. Only unambiguous
    single-glyph replacements of explicit NUL mappings are used here, and
    fonts/resources are copied so shared objects on other pages stay intact.
    """
    resources = page.get("/Resources")
    contents = page.get_contents()
    if resources is None or contents is None:
        return page
    result = copy(page)
    result[NameObject("/Resources")] = _restore_text_resource_maps(resources.get_object(), contents, page.pdf, set())
    return result


def _restore_text_resource_maps(
    resources: DictionaryObject, contents: ContentStream, reader, ancestors: set[int]
) -> DictionaryObject:
    fonts = resources.get("/Font", DictionaryObject()).get_object()
    properties = resources.get("/Properties", DictionaryObject()).get_object()
    replacements = _actual_text_glyphs(contents, properties)
    patched_fonts = DictionaryObject(fonts)
    for name in {name for name, _ in replacements}:
        if name not in fonts:
            continue
        font = fonts[name].get_object()
        cmap = font.get("/ToUnicode")
        if cmap is None:
            continue
        original = cmap.get_object().get_data()
        recovered = original
        for (key, glyph), values in replacements.items():
            if key != name or len(values) != 1:
                continue
            pattern = rb"<" + glyph.hex().encode("ascii") + rb">\s*<0000>"
            replacement = b"<" + glyph.hex().encode("ascii") + b"> <"
            replacement += next(iter(values)).encode("utf-16-be").hex().encode("ascii") + b">"
            recovered = re.sub(pattern, replacement, recovered, flags=re.IGNORECASE)
        if recovered != original:
            new_cmap = DecodedStreamObject()
            new_cmap.set_data(recovered)
            patched_font = DictionaryObject(font)
            patched_font[NameObject("/ToUnicode")] = new_cmap
            patched_fonts[NameObject(name)] = patched_font
    patched_resources = DictionaryObject(resources)
    patched_resources[NameObject("/Font")] = patched_fonts
    forms = resources.get("/XObject", DictionaryObject()).get_object()
    patched_forms = DictionaryObject(forms)
    for name, value in forms.items():
        form = value.get_object()
        if not isinstance(form, StreamObject) or form.get("/Subtype") != "/Form" or id(form) in ancestors:
            continue
        form_resources = form.get("/Resources", resources).get_object()
        patched_form = copy(form)
        patched_form[NameObject("/Resources")] = _restore_text_resource_maps(
            form_resources, ContentStream(form, reader), reader, ancestors | {id(form)}
        )
        patched_forms[NameObject(name)] = patched_form
    patched_resources[NameObject("/XObject")] = patched_forms
    return patched_resources


def _actual_text_property(value: object, properties: DictionaryObject) -> str | None:
    if isinstance(value, NameObject):
        value = properties.get(value, DictionaryObject()).get_object()
    actual = value.get("/ActualText") if isinstance(value, DictionaryObject) else None
    return str(actual) if isinstance(actual, TextStringObject) else None


def _shown_text_bytes(operands: list[object], operator: bytes) -> Iterable[bytes]:
    values = operands[0] if operator == b"TJ" and isinstance(operands[0], ArrayObject) else (operands[-1],)
    for value in values:
        if isinstance(value, TextStringObject | bytes):
            raw = value.original_bytes if isinstance(value, TextStringObject) else bytes(value)
            if raw:
                yield raw


def _actual_text_glyphs(contents: ContentStream, properties: DictionaryObject) -> dict[tuple[str, bytes], set[str]]:
    replacements: dict[tuple[str, bytes], set[str]] = {}
    font_name = ""
    saved_fonts: list[str] = []
    marked: list[tuple[str | None, list[tuple[str, bytes]]]] = []
    for operands, operator in contents.operations:
        if operator == b"Tf":
            font_name = str(operands[0])
        elif operator == b"q":
            saved_fonts.append(font_name)
        elif operator == b"Q" and saved_fonts:
            font_name = saved_fonts.pop()
        elif operator in (b"BDC", b"BMC"):
            prop = operands[-1].get_object()
            marked.append((_actual_text_property(prop, properties), []))
        elif operator in (b"Tj", b"TJ", b"'", b'"') and marked:
            for _, glyphs in marked:
                glyphs.extend((font_name, raw) for raw in _shown_text_bytes(operands, operator))
        elif operator == b"EMC" and marked:
            actual, glyphs = marked.pop()
            if actual and len(glyphs) == 1 and len(glyphs[0][1]) <= 2:
                replacements.setdefault(glyphs[0], set()).add(actual)
    return replacements


def _recover_word_boundaries(page: PageObject, plain: str) -> str:
    fragments = tuple((left, right) for left, right in re.findall(r"(?=\b([A-Za-z]+)\s+([A-Za-z]+)\b)", plain))
    if not fragments:
        return plain
    layout = (page.extract_text(extraction_mode="layout", layout_mode_strip_rotated=False) or "").translate(
        _TEXT_LIGATURES
    )
    # Never lose rotated labels or any non-whitespace character when changing
    # word and number boundaries using positions instead of drawing order.
    if Counter(c for c in plain if not c.isspace()) != Counter(c for c in layout if not c.isspace()):
        return plain
    original_numbers = Counter(re.findall(r"\d+", plain))
    layout = _restore_complete_numbers(layout, original_numbers)
    # Drawing order can concatenate an entire chart axis into one number just
    # as it can split a year into several numbers. Numeric token counts cannot
    # veto geometry; the character multiset above preserves every digit.
    before = Counter(_LEXICAL_TERMS.findall(plain.lower()))
    for left, right in fragments:
        word = left + right
        if word.lower() not in before and (
            {left.lower(), right.lower()} & _WORD_PARTICLES or (left[-1].islower() and right[0].isupper())
        ):
            # Layout occasionally joins separate labels at the ends of lines.
            # Preserve their spaces unless the combined word occurs in the PDF.
            layout = re.sub(r"\b" + re.escape(word) + r"\b", left + " " + right, layout)
    layout = _separate_joined_labels(layout, before)
    after = Counter(_LEXICAL_TERMS.findall(layout.lower()))
    if after == before:
        return plain
    return "\n".join(" ".join(line.split()) for line in layout.splitlines()).strip()


def _restore_complete_numbers(layout: str, original_numbers: Counter[str]) -> str:
    """Repair layout-only splits without joining amounts across table rows."""
    layout_numbers = Counter(re.findall(r"\d+", layout))
    for number, count in original_numbers.items():
        missing = count - layout_numbers[number]
        if not 3 <= len(number) <= 4 or missing <= 0:
            continue
        pattern = r"(?<!\d)" + r"[ \t]*".join(number) + r"(?!\d)"

        def restore(match: re.Match[str], number: str = number) -> str:
            nonlocal missing
            if missing > 0 and match[0] != number:
                missing -= 1
                return number
            return match[0]

        layout = re.sub(pattern, restore, layout)
    return layout


def _separate_joined_labels(layout: str, original_words: Counter[str]) -> str:
    """Keep table labels separate when layout coalesces adjacent text blocks.

    Only words that are independently present in both extractions are trusted.
    This avoids treating the broken fragments being repaired as complete words.
    """
    layout_words = set(_LEXICAL_TERMS.findall(layout.lower()))
    known = {word for word in original_words.keys() & layout_words if len(word) >= 3 and word.isalpha()}
    if not known:
        return layout
    parts = re.compile("|".join(re.escape(word) for word in sorted(known, key=len, reverse=True)), re.IGNORECASE)

    def separate(match: re.Match[str]) -> str:
        word = match[0]
        if word.lower() in original_words or not any(character.isupper() for character in word[1:]):
            return word
        words = parts.findall(word)
        if len(words) > 1 and "".join(words) == word:
            return " ".join(words)
        return word

    return re.sub(r"\b[A-Za-z]+\b", separate, layout)


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

    @property
    def location(self) -> tuple[str, int]:
        # The document id (literal or hashed) precedes the revision digest.
        # Revisions keep their exact citations but share a returned page slot.
        return self.source_id.rsplit(":", 2)[0], self.page_number


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


def _document_priorities(pages: tuple[_CorpusPage, ...], question: str, year: str) -> dict[str, tuple[bool, bool]]:
    names = {page.document_key: page.document_id for page in pages}
    matched = {key: re.search(_document_name_pattern(name), question) is not None for key, name in names.items()}
    if any(matched.values()):
        return {key: (flag, flag and bool(year) and year in _YEAR.findall(names[key])) for key, flag in matched.items()}

    # Document ids are caller-owned identities, not guaranteed company names.
    # Link questions to report contents before ranking their individual pages.
    covers = dict.fromkeys(names, "")
    for page in pages:
        if page.page_number <= 3:
            covers[page.document_key] += " " + page.text.partition("\n")[2].casefold()
    terms = {
        term
        for term in re.findall(r"[a-z0-9]+", question)
        if len(term) >= 2 and not term.isdigit() and term not in _QUERY_NOISE
    }
    overlaps = {key: terms & set(re.findall(r"[a-z0-9]+", text)) for key, text in covers.items()}
    frequency: Counter[str] = Counter()
    for words in overlaps.values():
        frequency.update(words)
    scores = {
        key: math.fsum(math.log(len(names) / frequency[word]) for word in words) for key, words in overlaps.items()
    }
    best = max(scores.values(), default=0.0)
    return {
        key: (
            best > 0 and score == best,
            best > 0 and score == best and bool(year) and year in _YEAR.findall(covers[key]),
        )
        for key, score in scores.items()
    }


def _rank_pages(pages: tuple[_CorpusPage, ...], question: str, limit: int) -> tuple[MemoryCitation, ...]:
    folded_question = _normalize_question(question)
    document_names = {page.document_key: page.document_id for page in pages}
    raw_question = unicodedata.normalize("NFKC", question).casefold()
    # A later maturity or target date does not identify the report's fiscal year.
    year = max(_FISCAL_YEAR.findall(raw_question) or _YEAR.findall(raw_question), default="")
    priorities = _document_priorities(pages, folded_question, year)
    identity_terms = {
        _stem(term)
        for key, name in document_names.items()
        if priorities[key][0]
        for term in _LEXICAL_TERMS.findall(name.casefold())
    }
    topical_question = folded_question
    for name in document_names.values():
        topical_question = re.sub(_document_name_pattern(name), " ", topical_question)
    phrases = _expansion_phrases(folded_question)
    query_counts = Counter(
        _stem(term)
        for term in _LEXICAL_TERMS.findall(topical_question)
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
    channel_leaders: list[str] = []
    best_priority = max(priorities.values())
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
        # A synonym can have no literal overlap at all. Fusion alone can then
        # discard even the best expanded hit behind many middling shared hits.
        leaders = (
            page.source_id
            for page in ranked
            if channel[page.source_id] > 0 and priorities[page.document_key] == best_priority
        )
        for _, source_id in zip(range(limit // 5), leaders, strict=False):
            channel_leaders.append(source_id)
    ordered = sorted(
        pages,
        key=lambda page: (
            tuple(not flag for flag in priorities[page.document_key]),
            -fused[page.source_id],
            page.page_number,
            page.source_id,
        ),
    )
    return _select_evidence_pages(
        ordered, folded_question, priorities, limit, year=year, channel_leaders=tuple(channel_leaders)
    )


def _normalize_question(question: str) -> str:
    normalized = unicodedata.normalize("NFKC", question).casefold()
    return re.sub(r"\bfy\s*(\d{4})\b", r"\1", normalized)


def _primary_statement_pages(
    ordered: list[_CorpusPage],
    bodies: dict[str, str],
    question: str,
    priorities: dict[str, tuple[bool, bool]],
    year: str,
) -> tuple[_CorpusPage, ...]:
    best_priority = max(priorities.values())
    anchors: dict[str, _CorpusPage] = {}
    for intent, heading, rows in _STATEMENT_INTENTS:
        if not intent.search(question):
            continue
        candidates = []
        for page in ordered:
            body = bodies[page.source_id]
            if priorities[page.document_key] != best_priority or len(_AMOUNT_PATTERN.findall(body)) < 8:
                continue
            match = heading.search(body)
            if match is None:
                continue
            flat = " ".join(body.split())
            row_count = sum(re.search(row, flat) is not None for row in rows)
            if not row_count:
                continue
            # Some producers draw titles after the table, so a title anywhere
            # on the same page is eligible. Actual rows outrank TOC references.
            quality = (
                -row_count,
                "consolidated" not in match[0],
                bool(year) and year not in body,
                re.search(_document_name_pattern(page.document_id), body) is None,
            )
            candidates.append((quality, page))
        candidates.sort(key=lambda candidate: candidate[0])
        for _, page in candidates[:2]:
            anchors[page.source_id] = page
    return tuple(anchors.values())


def _select_evidence_pages(
    ordered: list[_CorpusPage],
    question: str,
    priorities: dict[str, tuple[bool, bool]],
    limit: int,
    *,
    year: str,
    channel_leaders: tuple[str, ...],
) -> tuple[MemoryCitation, ...]:
    bodies = {
        page.source_id: unicodedata.normalize("NFKC", page.text.partition("\n")[2]).casefold() for page in ordered
    }
    headers = {source_id: text[:1200] for source_id, text in bodies.items()}
    anchors = {page.source_id: page for page in _primary_statement_pages(ordered, bodies, question, priorities, year)}
    locations = {(page.document_key, page.page_number): page for page in ordered}
    for anchor in tuple(anchors.values()):
        following = locations.get((anchor.document_key, anchor.page_number + 1))
        if following is not None and re.search(r"\bcontinu(?:ed|ation)\b", headers[following.source_id][:500]):
            anchors[following.source_id] = following
    # Reserve a small part of the result for each requested primary statement;
    # discussion pages remain eligible for the rest of the evidence budget.
    reserved: dict[tuple[str, int], str] = {}
    for page in anchors.values():
        reserved.setdefault(page.location, page.source_id)
    by_source = {page.source_id: page for page in ordered}
    candidates = chain(tuple(reserved.values())[: limit // 2], channel_leaders, by_source)
    selected: dict[tuple[str, int], str] = {}
    for source_id in candidates:
        if len(selected) >= limit:
            break
        selected.setdefault(by_source[source_id].location, source_id)
    selected_ids = set(selected.values())
    return tuple(page.citation for page in ordered if page.source_id in selected_ids)


def _document_name_pattern(document_id: str) -> str:
    # Finance document identifiers commonly append a year and filing type.
    # The name before that suffix is useful even on tables without a company heading.
    name = re.split(r"[_\s-](?=(?:19|20)\d{2}(?:\D|$))", document_id.casefold(), maxsplit=1)[0]
    compact_name = re.sub(r"[^a-z0-9]", "", name)
    return r"\b" + r"[\W_]*".join(compact_name) + r"\b" if len(compact_name) >= 2 else r"(?!)"


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
