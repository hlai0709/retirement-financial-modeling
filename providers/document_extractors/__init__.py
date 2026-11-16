"""Plug-in document extractors."""

from providers.document_extractors.tax_return_json import TaxReturnJsonExtractor
from providers.document_extractors.tax_return_pdf import TaxReturnPdfExtractor

__all__ = ["TaxReturnJsonExtractor"]