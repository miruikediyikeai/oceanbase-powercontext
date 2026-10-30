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
import hashlib
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

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
