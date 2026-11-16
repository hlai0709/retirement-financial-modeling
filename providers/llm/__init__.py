"""Pluggable LLM providers for assumption research (not used for Plaid financial data)."""

from providers.llm.factory import get_llm_provider

__all__ = ["get_llm_provider"]