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
def test_compressed_font_character_maps_preserve_unicode_or_fail_atomically(damaged):
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(make_pdf("first page", "A 2000"))))
    page = writer.pages[-1]
    resources = cast(DictionaryObject, page["/Resources"])
    fonts = cast(DictionaryObject, resources["/Font"])
    font = cast(DictionaryObject, fonts["/F1"])
    mapping = DecodedStreamObject()
    mapping.set_data(
        b"begincmap\n1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        b"1 beginbfchar\n<41> <8425>\nendbfchar\nendcmap"
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
        assert [page.text for page in parse_pdf_document(document)] == ["first page", "营 2000"]
