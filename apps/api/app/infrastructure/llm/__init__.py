"""LLM adapters (Phase 2): LiteLLM provider implementation and a test fake.

The adapter's job is translation plus resilience: provider model string from
settings, message/tool mapping both ways, timeout enforcement, error mapping onto
``LLMError`` subclasses, and a fake implementation so tests need no network and
no API key.
"""
