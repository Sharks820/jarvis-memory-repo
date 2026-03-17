// Copyright (c) 2026 Conner McCarthy. All rights reserved.
// Unity Editor Bridge — WebSocket server lifecycle, heartbeat, domain reload recovery.
//
// [InitializeOnLoad] ensures the static constructor runs on every domain reload,
// restarting the WebSocket server and rebuilding the reflection cache automatically.
//
// Domain reload sequence (triggered by every .cs file write):
//   1. Unity detects file change, begins compilation
//   2. beforeAssemblyReload fires -> StopServer() gracefully closes WebSocket
//   3. AppDomain torn down (all static state destroyed)
//   4. New AppDomain created, [InitializeOnLoad] fires static constructor again
//   5. BuildCache() rebuilds reflection cache (~200-400ms)
//   6. StartServer() binds port 8091 again (ReuseAddress=true handles lingering socket)
//   7. afterAssemblyReload fires -> BroadcastReady() sends {"status":"ready"}
//   8. Python client receives heartbeat, exits WAITING_FOR_BRIDGE state

using System;
using UnityEditor;
using UnityEngine;
using WebSocketSharp;
using WebSocketSharp.Server;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Jarvis.EditorBridge.Models;
using System.Collections.Generic;

namespace Jarvis.EditorBridge
{
    /// <summary>
    /// Entry point for the Jarvis Unity Editor Bridge.
    /// Automatically starts on domain load via [InitializeOnLoad].
    /// Hosts a WebSocket JSON-RPC server on ws://localhost:8091/jarvis.
    /// </summary>
    [InitializeOnLoad]
    public static class JarvisEditorBridge
    {
        // ── Configuration ──────────────────────────────────────────────────────

        private const string ServerUrl = "ws://localhost:8091";
        private const string ServicePath = "/jarvis";
        private const int RetryDelayMs = 500;

        // ── State ──────────────────────────────────────────────────────────────

        private static WebSocketServer _server;
        private static ReflectionCommandDispatcher _dispatcher;

        // ── Agent message event ────────────────────────────────────────────────

        /// <summary>
        /// Fired on the main thread when an inbound WebSocket message has a method
        /// that starts with "agent_".  JarvisPanel subscribes to this event to
        /// update its progress / approval UI without polling.
        /// </summary>
        public static event Action<string> OnAgentMessage;

        // ── Static constructor — runs on every domain reload ───────────────────

        static JarvisEditorBridge()
        {
            // Cleanup from previous domain (graceful stop before new binding)
            StopServer();

            // Build reflection cache before starting server
            _dispatcher = new ReflectionCommandDispatcher();
            _dispatcher.BuildCache();

            // Start WebSocket server
            StartServer();

            // Register lifecycle callbacks
            EditorApplication.quitting += StopServer;
            AssemblyReloadEvents.beforeAssemblyReload += StopServer;
            AssemblyReloadEvents.afterAssemblyReload += BroadcastReady;
        }

        // ── Public API ─────────────────────────────────────────────────────────

        /// <summary>
        /// Broadcast a ready heartbeat to all connected WebSocket sessions.
        /// Called by afterAssemblyReload callback after domain reload completes.
        /// Python UnityTool receives this and exits WAITING_FOR_BRIDGE state.
        /// </summary>
        public static void BroadcastReady()
        {
            if (_server == null || !_server.IsListening) return;

            try
            {
                var sessions = _server.WebSocketServices[ServicePath].Sessions;
                var readyPayload = JsonConvert.SerializeObject(new { status = "ready" });
                sessions.Broadcast(readyPayload);
                Debug.Log("[Jarvis] Editor Bridge sent ready heartbeat to all clients");
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[Jarvis] BroadcastReady failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Send a JSON-RPC notification (no id) to all connected WebSocket sessions.
        /// Used for unsolicited events: compilation errors, asset import results, etc.
        /// </summary>
        /// <param name="method">Notification method name (e.g. "compile_errors").</param>
        /// <param name="data">Data payload — will be serialized as the "params" field.</param>
        public static void SendNotification(string method, object data)
        {
            if (_server == null || !_server.IsListening) return;

            try
            {
                var notification = new
                {
                    jsonrpc = "2.0",
                    method = method,
                    @params = data
                };
                var payload = JsonConvert.SerializeObject(notification);

                var sessions = _server.WebSocketServices[ServicePath].Sessions;
                sessions.Broadcast(payload);
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[Jarvis] SendNotification('{method}') failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Access the dispatcher for direct dispatch from tests or other Editor scripts.
        /// </summary>
        internal static ReflectionCommandDispatcher Dispatcher => _dispatcher;

        /// <summary>
        /// Send a JSON-RPC response payload to all connected clients.
        /// Used by JarvisPanel to reply to approval requests from the Python agent.
        /// </summary>
        /// <param name="payload">Pre-serialized JSON string.</param>
        public static void BroadcastRaw(string payload)
        {
            if (_server == null || !_server.IsListening) return;

            try
            {
                var sessions = _server.WebSocketServices[ServicePath].Sessions;
                sessions.Broadcast(payload);
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[Jarvis] BroadcastRaw failed: {ex.Message}");
            }
        }

        /// <summary>
        /// Raise the OnAgentMessage event on Unity's main thread so that
        /// EditorWindow subscribers (JarvisPanel) can safely call Repaint().
        /// Called from JarvisBridgeService.OnMessage for agent_* methods.
        /// </summary>
        internal static void RaiseAgentMessage(string rawMessage)
        {
            // WebSocket messages arrive on a background thread.
            // EditorApplication.delayCall is posted to the main thread.
            EditorApplication.delayCall += () => OnAgentMessage?.Invoke(rawMessage);
        }

        // ── Private server lifecycle ───────────────────────────────────────────

        private static void StartServer()
        {
            try
            {
                _server = new WebSocketServer(ServerUrl);
                _server.ReuseAddress = true;  // Prevents port binding failure on domain reload (Pitfall 2)
                _server.AddWebSocketService<JarvisBridgeService>(ServicePath,
                    () => new JarvisBridgeService(_dispatcher));
                _server.Start();
                Debug.Log($"[Jarvis] Editor Bridge started on {ServerUrl}{ServicePath}");
            }
            catch (System.Net.Sockets.SocketException ex)
            {
                Debug.LogWarning(
                    $"[Jarvis] Editor Bridge failed to bind {ServerUrl} ({ex.Message}). " +
                    $"Retrying in {RetryDelayMs}ms...");

                // Retry once via delayCall — gives OS time to release the socket
                EditorApplication.delayCall += RetryStartServer;
            }
            catch (Exception ex)
            {
                Debug.LogError($"[Jarvis] Editor Bridge failed to start: {ex}");
            }
        }

        private static void RetryStartServer()
        {
            // Delay helper: called by EditorApplication.delayCall after 500ms
            System.Threading.Thread.Sleep(RetryDelayMs);
            try
            {
                _server = new WebSocketServer(ServerUrl);
                _server.ReuseAddress = true;
                _server.AddWebSocketService<JarvisBridgeService>(ServicePath,
                    () => new JarvisBridgeService(_dispatcher));
                _server.Start();
                Debug.Log($"[Jarvis] Editor Bridge started (retry) on {ServerUrl}{ServicePath}");
            }
            catch (Exception ex)
            {
                Debug.LogError($"[Jarvis] Editor Bridge retry failed: {ex}");
            }
        }

        private static void StopServer()
        {
            if (_server != null && _server.IsListening)
            {
                try
                {
                    _server.Stop();
                    Debug.Log("[Jarvis] Editor Bridge stopped");
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[Jarvis] StopServer error (safe to ignore on reload): {ex.Message}");
                }
            }
            _server = null;
        }

        // ── Inner service class ────────────────────────────────────────────────

        /// <summary>
        /// WebSocket service handler — one instance per connected client session.
        /// Handles the JSON-RPC request/response lifecycle for each connection.
        /// </summary>
        private class JarvisBridgeService : WebSocketBehavior
        {
            private readonly ReflectionCommandDispatcher _dispatcher;

            public JarvisBridgeService(ReflectionCommandDispatcher dispatcher)
            {
                _dispatcher = dispatcher;
            }

            protected override void OnOpen()
            {
                // Send ready heartbeat immediately so freshly-connected Python client
                // knows the bridge is live and the reflection cache is built
                var readyPayload = JsonConvert.SerializeObject(new { status = "ready" });
                Send(readyPayload);
                Debug.Log($"[Jarvis] Client connected: {ID}");
            }

            protected override void OnMessage(MessageEventArgs e)
            {
                JsonRpcRequest request = null;

                try
                {
                    // ── Deserialize ────────────────────────────────────────────
                    request = JsonConvert.DeserializeObject<JsonRpcRequest>(e.Data);

                    if (request == null)
                    {
                        Send(JsonConvert.SerializeObject(
                            JsonRpcResponse.Failure(null, -32700, "Parse error: null message")));
                        return;
                    }

                    if (string.IsNullOrWhiteSpace(request.Method))
                    {
                        Send(JsonConvert.SerializeObject(
                            JsonRpcResponse.Failure(request.Id, -32600,
                                "Invalid request: 'method' field is required")));
                        return;
                    }

                    // ── Forward agent_* messages to UI listeners ────────────────
                    if (request.Method.StartsWith("agent_", StringComparison.Ordinal))
                        JarvisEditorBridge.RaiseAgentMessage(e.Data);

                    // ── Defense-in-depth: static analysis for write operations ──
                    // Only run if params contain a string that looks like C# code
                    if (request.Params != null && IsWriteOperation(request.Method))
                    {
                        var contentToken = request.Params["content"] ?? request.Params["code"];
                        if (contentToken != null)
                        {
                            var code = contentToken.Value<string>() ?? string.Empty;
                            var violations = StaticAnalysisGuard.ScanForDangerousPatterns(code);
                            if (violations.Count > 0)
                            {
                                var violationMsg = string.Join("; ", violations);
                                Send(JsonConvert.SerializeObject(
                                    JsonRpcResponse.Failure(request.Id, -32602,
                                        $"Static analysis blocked: {violationMsg}")));
                                Debug.LogWarning(
                                    $"[Jarvis] StaticAnalysisGuard blocked '{request.Method}': {violationMsg}");
                                return;
                            }
                        }
                    }

                    // ── Dispatch ───────────────────────────────────────────────
                    var result = _dispatcher.Dispatch(request.Method, request.Params);

                    // Convert void (null) result to a descriptive string
                    object responseResult = result ?? $"{request.Method} completed";

                    Send(JsonConvert.SerializeObject(
                        JsonRpcResponse.Success(request.Id, responseResult)));
                }
                catch (KeyNotFoundException knfe)
                {
                    Send(JsonConvert.SerializeObject(
                        JsonRpcResponse.Failure(request?.Id, -32601, knfe.Message)));
                }
                catch (UnauthorizedAccessException uae)
                {
                    Debug.LogError($"[Jarvis] Path jail violation: {uae.Message}");
                    Send(JsonConvert.SerializeObject(
                        JsonRpcResponse.Failure(request?.Id, -32602, uae.Message)));
                }
                catch (System.Reflection.AmbiguousMatchException ame)
                {
                    Send(JsonConvert.SerializeObject(
                        JsonRpcResponse.Failure(request?.Id, -32602, ame.Message)));
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[Jarvis] Dispatch error for '{request?.Method}': {ex}");
                    Send(JsonConvert.SerializeObject(
                        JsonRpcResponse.Failure(request?.Id, -32603,
                            $"Internal error: {ex.Message}")));
                }
            }

            protected override void OnError(ErrorEventArgs e)
            {
                Debug.LogError($"[Jarvis] WebSocket error on session {ID}: {e.Message}");
            }

            protected override void OnClose(CloseEventArgs e)
            {
                Debug.Log($"[Jarvis] Client disconnected: {ID} (code={e.Code}, reason={e.Reason})");
            }

            // ── Helpers ────────────────────────────────────────────────────────

            private static bool IsWriteOperation(string method)
            {
                var writeKeywords = new[]
                {
                    "Write", "Create", "Save", "Export", "WriteAllText", "WriteAllBytes"
                };
                foreach (var kw in writeKeywords)
                {
                    if (method.IndexOf(kw, StringComparison.OrdinalIgnoreCase) >= 0)
                        return true;
                }
                return false;
            }
        }
    }
}
