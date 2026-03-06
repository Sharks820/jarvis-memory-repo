"""Handler classes for the Jarvis Command Bus (adapter-shim pattern).

All handler classes live in their respective submodules (e.g.
``memory_handlers``, ``voice_handlers``).  Import them directly::

    from jarvis_engine.handlers.memory_handlers import IngestHandler

This package intentionally does NOT re-export handler classes at the
top level so that importing the package stays lightweight.
"""
