import asyncio
from typing import List
from app.ai.base import OCRProvider
from app.ocr.extractor import extract_pages_from_file


class GroqOCRProvider(OCRProvider):
    """Whole-page OCR through a Groq-hosted vision model (see
    extractor._groq_ocr_image). Same trigger as the chandra/paddle engines:
    only image files and PDF pages with no text layer are sent to the
    model; digital-text PDF pages keep pdfplumber's own text."""

    async def extract_pages(self, file_bytes: bytes, filename: str) -> List[dict]:
        return await asyncio.to_thread(extract_pages_from_file, file_bytes, filename, "groq")
