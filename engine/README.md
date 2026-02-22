# Jarvis Engine (Codex Bootstrap)

This is a minimal executable scaffold for the Jarvis local-first engine.

## Quick Start
```powershell
cd engine
python -m jarvis_engine.main status
```

From repo root:
```powershell
python -m engine.src.jarvis_engine.main status
```

## What Exists
- Config loader from `.planning/config.json`
- Local JSONL event memory
- Basic policy gate for command allowlists
- Model router stub (local vs cloud burst)
- CLI commands: `status`, `log`, `route`

## Next Build Targets
1. Ollama adapter and structured tool execution layer
2. MCP server allowlist policy definitions
3. Action audit signing/checksum integrity

