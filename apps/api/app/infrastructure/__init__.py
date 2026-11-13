"""Infrastructure layer: adapters that implement domain ports.

This is the only layer allowed to import third-party SDKs (LiteLLM, Google,
database drivers, Supabase clients). It depends on ``app.domain`` and never on
``app.application``, so no adapter can call back into orchestration.
"""
