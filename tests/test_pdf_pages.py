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

"""Behavior of complete PDF capture and stable physical-page identities."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import re
from collections import Counter
from typing import cast

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NullObject,
    NumberObject,
    StreamObject,
    TextStringObject,
)

from powercontext.sources import SourceDefinitionRegistry, SourceMaterialization, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def make_pdf(*texts: str, password: str | None = None, broken_last_page: bool = False) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for text in texts:
        page = writer.add_blank_page(width=600, height=800)
        if text:
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 12 Tf 20 750 Td ({escaped}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if broken_last_page:
        # A real malformed content object on a later page, after valid text.
        writer.pages[-1][NameObject("/Contents")] = NameObject("/InvalidContentStream")
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner-secret")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("password", [None, ""])
def test_extract_all_physical_pages_with_numbers_and_permissions(password):
    raw = make_pdf("Revenue 2022 1,234.56", "", "Cash flows 2021 987.65", password=password)
    pages = parse_pdf_document(PDFDocumentInput(document_id="sample", content=raw))
    assert [(page.page_number, page.text) for page in pages] == [
        (1, "Revenue 2022 1,234.56"),
        (2, ""),
        (3, "Cash flows 2021 987.65"),
    ]
    assert all(page.document_id == "sample" for page in pages)
    assert all(page.document_sha256 == hashlib.sha256(raw).hexdigest() for page in pages)


@pytest.mark.parametrize("form_depth", [0, 2])
def test_document_words_recover_inferred_spaces_and_preserve_literal_word_boundaries(form_depth):
    vocabulary = "Participant Retirement December derivatives groundwater regulations asset ABCD " * 4
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf(vocabulary, "", "placeholder"))))
    stream = DecodedStreamObject()
    # The numeric TJ adjustments create inferred spaces inside words. Actual
    # spaces in the strings remain authoritative, even for common compounds.
    stream.set_data(
        b"BT /F1 12 Tf 20 700 Td "
        b"[(Participan) -300 (t Retir) -300 "
        b"(ement groundwater groundwater ground water Regulation S as set 1000 1000)] TJ "
        b"0 -30 Td (D e c e m b e r) Tj "
        b"0 -30 Td (d e r i v a t i v e s) Tj "
        b"0 -30 Td (A B C D) Tj ET"
    )
    page = writer.pages[-1]
    resources = page["/Resources"]
    for _ in range(form_depth):
        stream[NameObject("/Type")] = NameObject("/XObject")
        stream[NameObject("/Subtype")] = NameObject("/Form")
        stream[NameObject("/BBox")] = ArrayObject([NumberObject(n) for n in (0, 0, 600, 800)])
        stream[NameObject("/Resources")] = resources
        resources = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Form1"): writer._add_object(stream)})
        })
        stream = DecodedStreamObject()
        stream.set_data(b"/Form1 Do")
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="fragmented", content=buffer.getvalue()))
    assert len(pages) == 3 and pages[1].text == ""
    assert Counter(re.findall(r"[a-z]+|\d+", pages[-1].text.lower())) == Counter([
        "participant",
        "retirement",
        "groundwater",
        "groundwater",
        "ground",
        "water",
        "regulation",
        "s",
        "as",
        "set",
        "1000",
        "1000",
        "december",
        "derivatives",
        "a",
        "b",
        "c",
        "d",
    ])


@pytest.mark.parametrize("named_property", [False, True])
@pytest.mark.parametrize("form_depth", [0, 1, 2])
def test_actual_text_restores_missing_font_characters_and_preserves_repeated_numbers(named_property, form_depth):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("Participants 1000 1000"))))
    page = writer.pages[0]
    resources = cast(DictionaryObject, page["/Resources"])
    font = cast(DictionaryObject, cast(DictionaryObject, resources["/Font"])["/F1"])
    mapping = DecodedStreamObject()
    mapping.set_data(b"begincmap\n1 beginbfchar\n<00> <0000>\nendbfchar\nendcmap")
    font[NameObject("/ToUnicode")] = writer._add_object(mapping)
    properties = DictionaryObject({NameObject("/ActualText"): TextStringObject("ti")})
    if named_property:
        resources[NameObject("/Properties")] = DictionaryObject({NameObject("/Replacement"): properties})
    marked = b"/Replacement" if named_property else b"<< /ActualText (ti) >>"
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 700 Td (Par) Tj /Span " + marked + b" BDC <00> Tj EMC (cipants 1000 1000) Tj ET")
    for _ in range(form_depth):
        stream[NameObject("/Type")] = NameObject("/XObject")
        stream[NameObject("/Subtype")] = NameObject("/Form")
        stream[NameObject("/BBox")] = ArrayObject([NumberObject(n) for n in (0, 0, 600, 800)])
        stream[NameObject("/Resources")] = resources
        resources = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Form1"): writer._add_object(stream)})
        })
        stream = DecodedStreamObject()
        stream.set_data(b"/Form1 Do")
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    parsed = parse_pdf_document(PDFDocumentInput(document_id="actual-text", content=buffer.getvalue()))
    assert Counter(re.findall(r"[a-z]+|\d+", parsed[0].text.lower())) == Counter({"participants": 1, "1000": 2})


@pytest.mark.parametrize("numbers", ["1000 1000", "1000 1000 2022 2021 2020 234 456 678 891 102 982"])
def test_geometric_extraction_repairs_midword_line_breaks_without_changing_table_numbers(numbers):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("Revenue 1000 1000"))))
    page = writer.pages[0]
    stream = DecodedStreamObject()
    # TJ adjustments affect pypdf's line-break inference although the following
    # text object explicitly restores the same baseline and adjacent position.
    stream.set_data(
        b"BT /F1 12 Tf 1 0 0 1 20 700 Tm [(Consolida) 0 -10000] TJ 0 30 Td ET\n"
        b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (ted) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 20 680 Tm (Revenue " + numbers.encode("ascii") + b") Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    parsed = parse_pdf_document(PDFDocumentInput(document_id="line-break", content=buffer.getvalue()))
    assert "Consolidated" in parsed[0].text
    assert Counter(re.findall(r"\d+", parsed[0].text)) == Counter(numbers.split())


def test_positioned_glyphs_preserve_words_and_repeated_values():
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Courier"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    lines = ("Revenue 2022 1000 1000", "Operating income 500 400", "Cash flow 2022 2021")
    stream = DecodedStreamObject()
    # Each glyph has an explicit position. A trailing TJ advance has no visible
    # effect before ET, but plain extraction incorrectly inserts a word space.
    stream.set_data(
        "\n".join(
            f"BT /F1 12 Tf 1 0 0 1 {20 + 7.2 * column:.2f} {750 - 20 * row} Tm [<{ord(character):02x}> -1000] TJ ET"
            for row, line in enumerate(lines)
            for column, character in enumerate(line)
        ).encode("ascii")
        + b"\nBT /F1 12 Tf 0 1 -1 0 400 20 Tm (Rotated label) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    raw = buffer.getvalue()
    parsed = parse_pdf_document(PDFDocumentInput(document_id="positioned", content=raw))
    expected = Counter(re.findall(r"[a-z]+|\d+", ("\n".join(lines) + "\nRotated label").lower()))
    assert Counter(re.findall(r"[a-z]+|\d+", parsed[0].text.lower())) == expected
    assert Counter(character for character in parsed[0].text if not character.isspace()) == Counter(
        character for character in "\n".join(lines) + "\nRotated label" if not character.isspace()
    )


@pytest.mark.parametrize("year", ["2022", "1934", "2026"])
def test_three_digit_fragments_do_not_prevent_year_recovery(year):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("Fiscal year"))))
    page = writer.pages[0]
    fonts = cast(DictionaryObject, cast(DictionaryObject, page["/Resources"])["/Font"])
    cast(DictionaryObject, fonts["/F1"])[NameObject("/BaseFont")] = NameObject("/Courier")
    prefix = f"Fiscal year {year[:-1]}"
    stream = DecodedStreamObject()
    stream.set_data(
        (
            f"BT /F1 12 Tf 1 0 0 1 20 700 Tm [({prefix}) -1000] TJ ET\n"
            f"BT /F1 12 Tf 1 0 0 1 {20 + len(prefix) * 7.2:.2f} 700 Tm ({year[-1]}) Tj ET\n"
            "BT /F1 12 Tf 20 680 Td (Revenue 1000 1000) Tj ET"
        ).encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="split-year", content=buffer.getvalue()))
    assert Counter(re.findall(r"\d+", pages[0].text)) == Counter({year: 1, "1000": 2})


def test_chart_labels_drawn_in_reverse_order_remain_separate_numbers():
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("Chart labels"))))
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 20 750 Td (Chart labels) Tj ET\n"
        b"BT /F1 12 Tf 300 700 Td (2022) Tj ET\n"
        b"BT /F1 12 Tf 200 700 Td (2021) Tj ET\n"
        b"BT /F1 12 Tf 100 700 Td (2020) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="chart", content=buffer.getvalue()))
    assert Counter(re.findall(r"\d+", pages[0].text)) == Counter({"2020": 1, "2021": 1, "2022": 1})


def test_geometric_word_repair_preserves_overlapping_table_labels():
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("labels"))))
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 20 750 Td (Asset Category Total) Tj ET\n"
        b"BT /F1 12 Tf 20 720 Td (Asset Category Total ) Tj ET\n"
        b"BT /F1 12 Tf 20 720 Td (Asset Category Total ) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 20 680 Tm [(Consolida) 0 -10000] TJ 0 30 Td ET\n"
        b"BT /F1 12 Tf 1 0 0 1 72 680 Tm (ted) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    parsed = parse_pdf_document(PDFDocumentInput(document_id="labels", content=buffer.getvalue()))
    assert Counter(re.findall(r"[a-z]+", parsed[0].text.lower())) == Counter({
        "asset": 3,
        "category": 3,
        "total": 3,
        "consolidated": 1,
    })


@pytest.mark.parametrize("path", ["0 0 600 100 re", "0 0 m 600 0 l 600 100 l 0 100 l h"])
@pytest.mark.parametrize("transform", ["", "0.5 0 0 0.5 100 100 cm"])
def test_extractable_text_is_preserved_with_clipping_and_repeated_values(path, transform):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("", "Revenue 1000"))))
    page = writer.pages[-1]
    resources = cast(DictionaryObject, page["/Resources"])
    fonts = cast(DictionaryObject, resources["/Font"])
    font = cast(DictionaryObject, fonts["/F1"])
    font[NameObject("/FontDescriptor")] = DictionaryObject({
        NameObject("/Type"): NameObject("/FontDescriptor"),
        NameObject("/FontName"): NameObject("/Helvetica"),
        NameObject("/FontBBox"): ArrayObject([NumberObject(n) for n in (-170, -225, 1000, 931)]),
    })
    stream = DecodedStreamObject()
    stream.set_data(
        (
            f"q {transform}\nBT /F1 12 Tf 20 700 Td (Revenue 1000) Tj ET\n"
            f"q {path} W n\nBT /F1 12 Tf 20 650 Td (Clipped duplicate revenue 1000) Tj ET Q\n"
            "BT /F1 12 Tf 20 600 Td (Revenue 1000) Tj ET Q"
        ).encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="clipped", content=buffer.getvalue()))
    assert [page.page_number for page in pages] == [1, 2]
    assert pages[0].text == ""
    assert Counter(re.findall(r"[a-z]+|\d+", pages[1].text.lower())) == Counter({
        "revenue": 3,
        "1000": 3,
        "clipped": 1,
        "duplicate": 1,
    })


def test_clipping_preserves_visible_buffered_raised_and_rotated_text():
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("Visible 1000"))))
    page = writer.pages[0]
    resources = cast(DictionaryObject, page["/Resources"])
    fonts = cast(DictionaryObject, resources["/Font"])
    font = cast(DictionaryObject, fonts["/F1"])
    font[NameObject("/FontDescriptor")] = DictionaryObject({
        NameObject("/FontBBox"): ArrayObject([NumberObject(n) for n in (-170, -225, 1000, 931)]),
    })
    stream = DecodedStreamObject()
    stream.set_data(
        b"q 0 0 600 800 re W n BT /F1 12 Tf 20 700 Td (Visible 1000) Tj "
        b"q 0 0 600 100 re W n (Clipped) Tj Q ET Q\n"
        b"q 0 500 600 300 re W n BT /F1 12 Tf 20 100 Td 600 Ts (Raised 2000) Tj ET Q\n"
        b"q 0 0 600 800 re W n BT /F1 12 Tf 0 1 -1 0 400 100 Tm (Rotated 3000) Tj ET Q"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="visible", content=buffer.getvalue()))
    counts = Counter(re.findall(r"[a-z]+|\d+", pages[0].text.lower()))
    assert counts >= Counter({"visible": 1, "1000": 1, "raised": 1, "2000": 1, "rotated": 1, "3000": 1})


def test_extractable_text_in_an_empty_clipping_region_and_blank_physical_page_are_preserved():
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("Clipped", ""))))
    stream = DecodedStreamObject()
    stream.set_data(b"q 0 0 0 0 re W n BT /F1 12 Tf 20 700 Td (Clipped) Tj ET Q")
    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="empty-clip", content=buffer.getvalue()))
    assert [(page.page_number, page.text) for page in pages] == [(1, "Clipped"), (2, "")]


@pytest.mark.parametrize("show_text", ["(Shifted 1000) '", '0 0 (Shifted 1000) "'])
def test_clipping_preserves_text_moved_into_view_by_next_line_operator(show_text):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("Shifted 1000"))))
    page = writer.pages[0]
    resources = cast(DictionaryObject, page["/Resources"])
    font = cast(DictionaryObject, cast(DictionaryObject, resources["/Font"])["/F1"])
    font[NameObject("/FontDescriptor")] = DictionaryObject({
        NameObject("/FontBBox"): ArrayObject([NumberObject(n) for n in (-170, -225, 1000, 931)]),
    })
    stream = DecodedStreamObject()
    stream.set_data(f"q 0 0 600 200 re W n BT /F1 12 Tf 600 TL 20 700 Td {show_text} ET Q".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="next-line", content=buffer.getvalue()))
    assert "Shifted 1000" in pages[0].text


@pytest.mark.parametrize("raw", [b"", b"not a PDF", b"%PDF-1.7\ntruncated"])
def test_invalid_documents_raise_value_error(raw):
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="sample", content=raw))


def test_late_damage_truncation_and_password_fail_without_partial_pages():
    for raw in (
        make_pdf("valid first page", "broken last page", broken_last_page=True),
        make_pdf("valid page").split(b"%%EOF")[0],
        make_pdf("protected page", password="required"),  # noqa: S106
        make_pdf(),
    ):
        with pytest.raises(ValueError):
            parse_pdf_document(PDFDocumentInput(document_id="sample", content=raw))


@pytest.mark.parametrize("document_id", ["", " ", "\t\n"])
def test_blank_document_ids_are_rejected(document_id):
    with pytest.raises(ValueError):
        PDFDocumentInput(document_id=document_id, content=make_pdf("text"))
    with pytest.raises(ValueError):
        PDFPageInput(document_id=document_id, document_sha256="a" * 64, page_number=1, text="text")


def test_source_identity_projection_and_revision_readback():
    async def scenario():
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        assert PDF_PAGE_SOURCE_DEFINITION.name == "pdf-page"
        assert PDF_PAGE_SOURCE_DEFINITION.version == "1"
        assert PDF_PAGE_SOURCE_DEFINITION.input_class is PDFPageInput
        names = set()
        for document_id in ("sample", "sample:a", "sample%3Aa", "财报" * 200, "财报" * 199 + "表"):
            for raw in (make_pdf("original", ""), make_pdf("revised", "")):
                for page in parse_pdf_document(PDFDocumentInput(document_id=document_id, content=raw)):
                    source = await registry.resolve(page)
                    assert source.name == (await registry.resolve(page)).name
                    assert source.name not in names
                    assert len(source.name) <= 256
                    assert source.materialization == SourceMaterialization.CAPTURED
                    names.add(source.name)
                    assert await registry.read(source) == page
                    evidence = project_source_for_transport(registry, source).projection(TEXT_EVIDENCE_PROJECTION_KEY)
                    assert evidence == {
                        "source_type": "pdf-page",
                        "source_id": source.name,
                        "content": f"{document_id}\n{page.text}",
                        "metadata": {
                            "document_id": document_id,
                            "document_sha256": page.document_sha256,
                            "page_number": page.page_number,
                        },
                    }

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["page-count", "null-child", "page-type", "truncated-stream", "stream-checksum"])
def test_damaged_later_pages_cannot_be_silently_recovered(damage):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("first page 1000", "last page 2000"))))
    tree = cast(DictionaryObject, writer.root_object["/Pages"])
    if damage == "page-count":
        tree[NameObject("/Count")] = NumberObject(3)
    elif damage == "null-child":
        cast(ArrayObject, tree["/Kids"]).append(NullObject())
        tree[NameObject("/Count")] = NumberObject(3)
    elif damage == "page-type":
        writer.pages[-1][NameObject("/Type")] = NameObject("/NotAPage")
    else:
        stream = cast(StreamObject, writer.pages[-1]["/Contents"]).flate_encode()
        if damage == "truncated-stream":
            stream._data = stream._data[:-2]
        else:
            stream._data = stream._data[:-1] + bytes([stream._data[-1] ^ 1])
        writer.pages[-1][NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="damaged", content=buffer.getvalue()))


def test_page_tree_allows_indirect_scalar_objects():
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("first page", "last page"))))
    tree = cast(DictionaryObject, writer.root_object["/Pages"])
    tree[NameObject("/Count")] = writer._add_object(NumberObject(2))
    tree[NameObject("/Type")] = writer._add_object(NameObject("/Pages"))
    writer.pages[-1][NameObject("/Type")] = writer._add_object(NameObject("/Page"))
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="indirect", content=buffer.getvalue()))
    assert [page.text for page in pages] == ["first page", "last page"]


@pytest.mark.parametrize("password", [None, ""])
@pytest.mark.parametrize("encoding", ["flate", "chained", "chained-null-parameters"])
def test_compressed_text_preserves_all_pages_and_empty_password_access(password, encoding):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("Revenue 1,234", "", "Cash flows 5,678"))))
    for page in (writer.pages[0], writer.pages[2]):
        stream = cast(StreamObject, page["/Contents"]).flate_encode()
        if encoding != "flate":
            stream._data = base64.a85encode(stream._data) + b"~>"
            stream[NameObject("/Filter")] = ArrayObject([NameObject("/ASCII85Decode"), NameObject("/FlateDecode")])
            if encoding == "chained-null-parameters":
                stream[NameObject("/DecodeParms")] = NullObject()
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner-secret")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="compressed", content=buffer.getvalue()))
    assert [(page.page_number, page.text) for page in pages] == [
        (1, "Revenue 1,234"),
        (2, ""),
        (3, "Cash flows 5,678"),
    ]


@pytest.mark.parametrize("damaged", [False, True])
def test_text_in_form_resources_is_preserved_or_fails_atomically(damaged):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("first page", "form revenue 2000"))))
    page = writer.pages[-1]
    form = cast(StreamObject, page["/Contents"]).flate_encode()
    form[NameObject("/Type")] = NameObject("/XObject")
    form[NameObject("/Subtype")] = NameObject("/Form")
    form[NameObject("/BBox")] = ArrayObject([NumberObject(n) for n in (0, 0, 600, 800)])
    form[NameObject("/Resources")] = page["/Resources"]
    if damaged:
        form._data = form._data[:-2]
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Fm"): writer._add_object(form)}),
    })
    content = DecodedStreamObject()
    content.set_data(b"q /Fm Do Q")
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    document = PDFDocumentInput(document_id="form", content=buffer.getvalue())
    if damaged:
        with pytest.raises(ValueError):
            parse_pdf_document(document)
    else:
        assert [page.text for page in parse_pdf_document(document)] == ["first page", "form revenue 2000"]


@pytest.mark.parametrize("damaged", [False, True])
@pytest.mark.parametrize(("unicode_hex", "expected"), [("8425", "营"), ("FB01", "fi")])
def test_compressed_font_character_maps_preserve_unicode_or_fail_atomically(damaged, unicode_hex, expected):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("first page", "A 2000"))))
    page = writer.pages[-1]
    resources = cast(DictionaryObject, page["/Resources"])
    fonts = cast(DictionaryObject, resources["/Font"])
    font = cast(DictionaryObject, fonts["/F1"])
    mapping = DecodedStreamObject()
    mapping.set_data(
        b"begincmap\n1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        + f"1 beginbfchar\n<41> <{unicode_hex}>\nendbfchar\nendcmap".encode("ascii")
    )
    compressed = mapping.flate_encode()
    if damaged:
        compressed._data = compressed._data[:-2]
    font[NameObject("/ToUnicode")] = writer._add_object(compressed)
    buffer = io.BytesIO()
    writer.write(buffer)
    document = PDFDocumentInput(document_id="font-map", content=buffer.getvalue())
    if damaged:
        with pytest.raises(ValueError):
            parse_pdf_document(document)
    else:
        assert [page.text for page in parse_pdf_document(document)] == ["first page", f"{expected} 2000"]
