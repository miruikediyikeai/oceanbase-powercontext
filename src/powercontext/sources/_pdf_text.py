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

"""Conservatively exclude text wholly outside an explicit PDF clipping path."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pypdf import PageObject
from pypdf.generic import ArrayObject, DictionaryObject, StreamObject

_Rectangle = tuple[float, float, float, float]
_SHOW_TEXT = frozenset({b"Tj", b"TJ", b"'", b'"'})
_END_PATH = frozenset({b"n", b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"})
_CLIPPING_OPERATOR = re.compile(rb"(?<![a-zA-Z0-9])W\*?(?![a-zA-Z0-9])")


@dataclass(frozen=True)
class _TextPosition:
    clip: _Rectangle | None
    baseline: float
    vertical_scale: float
    aligned: bool
    rise: float

    def possibly_visible(self, bounds: tuple[float, float] | None, size: float) -> bool:
        if self.clip is None:
            return True
        left, bottom, right, top = self.clip
        if left >= right or bottom >= top:
            return False
        if bounds is None or not self.aligned or self.rise:
            return True
        low, high = sorted(self.baseline + size * self.vertical_scale * value / 1000 for value in bounds)
        return high >= bottom - 1e-6 and low <= top + 1e-6


def _font_bounds(font: DictionaryObject | None) -> tuple[float, float] | None:
    if font is None:
        return None
    encoding = font.get("/Encoding")
    encoding = encoding.get_object() if encoding is not None else None
    if font.get("/Subtype") == "/Type3" or isinstance(encoding, StreamObject) or str(encoding).endswith("-V"):
        return None
    descendants = font.get("/DescendantFonts")
    if descendants is not None:
        descendants = descendants.get_object()
        if not isinstance(descendants, ArrayObject) or not descendants:
            return None
        font = descendants[0].get_object()
    if not isinstance(font, DictionaryObject):
        return None
    descriptor = font.get("/FontDescriptor")
    descriptor = descriptor.get_object() if descriptor is not None else None
    if not isinstance(descriptor, DictionaryObject):
        return None
    box = descriptor.get("/FontBBox")
    box = box.get_object() if box is not None else None
    if not isinstance(box, ArrayObject) or len(box) != 4:
        return None
    try:
        lower, upper = float(box[1]), float(box[3])
    except (TypeError, ValueError):
        return None
    return (lower, upper) if math.isfinite(lower) and math.isfinite(upper) and lower < upper else None


class _ClippedText:
    def __init__(self) -> None:
        self.clip: _Rectangle | None = None
        self.path: _Rectangle | None = None
        self.pending_clip = False
        self.rise = 0.0
        self.stack: list[tuple[_Rectangle | None, float]] = []
        self.form_depth = 0
        self.show_active = False
        self.last_position: _TextPosition | None = None
        self.positions: set[_TextPosition | None] = set()
        self.chunks: list[tuple[str, bool]] = []
        self.fonts: dict[int, tuple[float, float] | None] = {}

    def before(self, operator: bytes, operands: list[Any], cm: Sequence[float], tm: Sequence[float]) -> None:
        if operator == b"Do":
            # Form visitors use a local coordinate system. Preserve their text;
            # the final output comparison also protects nested visitor output.
            self.form_depth += 1
            self.positions.add(None)
            return
        if self.form_depth:
            return
        if operator in _SHOW_TEXT:
            self.show_active = True
            # Quote operators advance the line inside pypdf, after this callback.
            # Their pre-operation matrix cannot establish where the text lands.
            self.last_position = (
                _TextPosition(
                    self.clip,
                    tm[4] * cm[1] + tm[5] * cm[3] + cm[5],
                    tm[2] * cm[1] + tm[3] * cm[3],
                    abs(tm[0] * cm[1] + tm[1] * cm[3]) < 1e-8 and abs(tm[2] * cm[0] + tm[3] * cm[2]) < 1e-8,
                    self.rise,
                )
                if operator in (b"Tj", b"TJ")
                else None
            )
            self.positions.add(self.last_position)
        elif operator == b"q":
            self.stack.append((self.clip, self.rise))
        elif operator == b"Q":
            if self.stack:
                self.clip, self.rise = self.stack.pop()
        elif operator == b"Ts":
            self.rise = float(operands[0])
        else:
            self._path_operation(operator, operands, cm)

    def after(self, operator: bytes, _operands, _cm, _tm) -> None:
        if operator == b"Do":
            self.form_depth -= 1
        elif operator in _SHOW_TEXT:
            self.show_active = False

    def _extend_path(self, points: Iterable[tuple[float, float]], cm: Sequence[float]) -> None:
        for x, y in points:
            x, y = x * cm[0] + y * cm[2] + cm[4], x * cm[1] + y * cm[3] + cm[5]
            if self.path is None:
                self.path = (x, y, x, y)
            else:
                left, bottom, right, top = self.path
                self.path = (min(left, x), min(bottom, y), max(right, x), max(top, y))

    def _path_operation(self, operator: bytes, operands: list[Any], cm: Sequence[float]) -> None:
        if operator in (b"m", b"l", b"c", b"v", b"y"):
            self._extend_path(((float(operands[i]), float(operands[i + 1])) for i in range(0, len(operands), 2)), cm)
        elif operator == b"re":
            x, y, width, height = (float(value) for value in operands)
            self._extend_path(((x, y), (x + width, y), (x + width, y + height), (x, y + height)), cm)
        elif operator in (b"W", b"W*"):
            self.pending_clip = True
        elif operator in _END_PATH:
            if self.pending_clip:
                box = self.path if self.path is not None else (0.0, 0.0, 0.0, 0.0)
                if self.clip is None:
                    self.clip = box
                else:
                    left, bottom, right, top = self.clip
                    self.clip = (max(left, box[0]), max(bottom, box[1]), min(right, box[2]), min(top, box[3]))
            self.pending_clip = False
            self.path = None

    def text(self, text: str, _cm, _tm, font: DictionaryObject | None, size: float) -> None:
        outside = False
        if text.strip():
            key = id(font)
            if key not in self.fonts:
                self.fonts[key] = _font_bounds(font)
            # A buffered string can span several text-show operations. Delete it
            # only if every contributing position is conclusively outside.
            outside = (
                bool(self.positions)
                and not self.form_depth
                and "\n" not in text.strip("\r\n")
                and "\r" not in text.strip("\r\n")
                and all(
                    position is not None and not position.possibly_visible(self.fonts[key], size)
                    for position in self.positions
                )
            )
            # pypdf can flush old text during the current show operation. That
            # operation's position also applies to the following text buffer.
            self.positions = {self.last_position} if self.show_active else set()
        self.chunks.append((text, outside))


def extract_page_body(page: PageObject) -> str:
    """Extract the complete body, excluding only text proven to be fully clipped."""
    contents = page.get_contents()
    if contents is None:
        return ""
    if _CLIPPING_OPERATOR.search(contents.get_data()) is None:
        return page.extract_text() or ""
    collector = _ClippedText()
    text = (
        page.extract_text(
            visitor_operand_before=collector.before,
            visitor_operand_after=collector.after,
            visitor_text=collector.text,
        )
        or ""
    )
    if not any(outside for _, outside in collector.chunks):
        return text
    # Some form resources yield both nested and outer visitor events. Only use
    # the filtered reconstruction when it exactly covers the original output.
    if "".join(value for value, _ in collector.chunks) != text:
        return text
    filtered = "".join("\n" if outside else value for value, outside in collector.chunks)
    return filtered if filtered.strip() else ""
