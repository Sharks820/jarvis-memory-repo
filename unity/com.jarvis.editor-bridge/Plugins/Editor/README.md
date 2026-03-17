# Plugins/Editor — websocket-sharp DLL

This directory is the location for the `websocket-sharp.dll` native plugin.
The DLL is NOT committed to the repository.

## Installation

1. Open NuGet: https://www.nuget.org/packages/WebSocketSharp.Standard/1.0.3
2. Download the package (`.nupkg` file).
3. Rename the file extension to `.zip` and extract it.
4. Locate `lib/netstandard2.0/websocket-sharp.dll` inside the extracted archive.
5. Copy `websocket-sharp.dll` into this directory:
   `unity/com.jarvis.editor-bridge/Plugins/Editor/websocket-sharp.dll`
6. In Unity, the `.dll` will be detected automatically by the `.asmdef`'s
   `precompiledReferences` entry.

## Why not UPM?

The `com.websocket-sharp` UPM variant is unmaintained. The NuGet DLL
(WebSocketSharp.Standard 1.0.3 targeting .NET Standard 2.0) is the confirmed
working option for Unity 6.3's Mono runtime, as used by mcp-unity and similar
tools.

## Version

**WebSocketSharp.Standard 1.0.3** — required minimum.
