"""Parse source documents, keeping page/line locators in overlapping chunks."""
from __future__ import annotations

import io
import re
from pathlib import Path

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
CHUNK_SIZE = 750
CHUNK_OVERLAP = 120


class DocumentError(ValueError):
    pass


def _text_segments(text: str, page: int | None) -> list[dict]:
    """Split fixed character windows with overlap and stable source line numbers."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if not text.strip():
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):
            # Prefer paragraph/sentence boundaries without making tiny chunks.
            floor = start + CHUNK_SIZE // 2
            boundary = max(text.rfind("\n", floor, end), text.rfind("。", floor, end),
                           text.rfind(". ", floor, end))
            if boundary >= floor:
                end = boundary + 1
        raw = text[start:end]
        content = raw.strip()
        if content:
            lead = len(raw) - len(raw.lstrip())
            tail = len(raw.rstrip())
            chunks.append({"content": content, "page": page,
                           "start_line": text.count("\n", 0, start + lead) + 1,
                           "end_line": text.count("\n", 0, start + tail) + 1})
        if end >= len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks


def parse_document(name: str, payload: bytes) -> list[dict]:
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".md", ".txt"}:
        raise DocumentError("仅支持 PDF、Markdown（.md）和文本（.txt）文件。")
    if not payload:
        raise DocumentError("文件为空，请选择包含文字的文件。")
    if len(payload) > MAX_FILE_BYTES:
        raise DocumentError("单个文件不能超过 20 MB。")
    chunks = []
    if suffix == ".pdf":
        if not payload.lstrip().startswith(b"%PDF-"):
            raise DocumentError("文件不是有效的 PDF，请确认扩展名和文件内容。")
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(payload))
            if reader.is_encrypted:
                raise DocumentError("暂不支持加密 PDF，请先解除密码保护。")
            if len(reader.pages) > 2000:
                raise DocumentError("PDF 页数超过 2000 页，请拆分后上传。")
            total_chars = 0
            for index, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                total_chars += len(text)
                if total_chars > MAX_TEXT_CHARS:
                    raise DocumentError("提取文字过多，请将文件拆分后上传。")
                chunks.extend(_text_segments(text, index))
        except DocumentError:
            raise
        except Exception as exc:
            raise DocumentError("PDF 无法解析，文件可能损坏。请重新导出后上传。") from exc
        if not chunks:
            raise DocumentError("未从 PDF 提取到文字。扫描件需要先进行 OCR；本版本不包含 OCR。")
    else:
        if b"\x00" in payload:
            raise DocumentError("检测到二进制内容。请上传 UTF-8 或 GB18030 编码的纯文本。")
        text = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = payload.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise DocumentError("无法识别文本编码，请将文件另存为 UTF-8 后上传。")
        bad_controls = sum(ord(char) < 32 and char not in "\n\r\t" for char in text)
        if bad_controls > max(2, len(text) * 0.01):
            raise DocumentError("文件包含过多非文本字符，请上传纯文本文件。")
        if len(text) > MAX_TEXT_CHARS:
            raise DocumentError("提取文字过多，请将文件拆分后上传。")
        chunks = _text_segments(text, None)
        if not chunks:
            raise DocumentError("文件没有可索引的文字。")
    for ordinal, chunk in enumerate(chunks, 1):
        chunk["ordinal"] = ordinal
    return chunks


def safe_display_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:240] or "未命名文件.txt"
