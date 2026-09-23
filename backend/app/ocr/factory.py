from app.config import settings
from app.ai.airgapped import enforce_local
from app.ai.base import OCRProvider

_ocr_provider: OCRProvider | None = None

def get_ocr_provider() -> OCRProvider:
    global _ocr_provider
    if _ocr_provider is None:
        if settings.ai_ocr_provider == 'pdfplumber':
            from app.ocr.providers.pdfplumber_provider import PdfPlumberProvider
            _ocr_provider = PdfPlumberProvider()
        elif settings.ai_ocr_provider == 'llamaparse':
            enforce_local('OCR', 'llamaparse')
            from app.ocr.providers.llamaparse_provider import LlamaParseProvider
            _ocr_provider = LlamaParseProvider(api_key=settings.llamaparse_api_key)
        elif settings.ai_ocr_provider == 'paddleocr':
            # T90 — genuinely local (no external API, no enforce_local() gate),
            # real Devanagari/Marathi support. See paddleocr_provider.py.
            from app.ocr.providers.paddleocr_provider import PaddleOCRProvider
            _ocr_provider = PaddleOCRProvider()
        elif settings.ai_ocr_provider == 'chandra':
            enforce_local('OCR', 'chandra')
            from app.ocr.providers.chandra_ocr_provider import ChandraOCRProvider
            _ocr_provider = ChandraOCRProvider()
        elif settings.ai_ocr_provider == 'groq':
            enforce_local('OCR', 'groq')
            from app.ocr.providers.groq_ocr_provider import GroqOCRProvider
            _ocr_provider = GroqOCRProvider()
        else:
            raise ValueError(f"Unknown OCR provider: {settings.ai_ocr_provider}")
    return _ocr_provider
