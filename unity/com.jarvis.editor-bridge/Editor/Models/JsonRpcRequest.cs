// Copyright (c) 2026 Conner McCarthy. All rights reserved.
// Unity Editor Bridge — JSON-RPC 2.0 request model.

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Jarvis.EditorBridge.Models
{
    /// <summary>
    /// Represents an incoming JSON-RPC 2.0 request from the Python Jarvis agent.
    /// </summary>
    public class JsonRpcRequest
    {
        /// <summary>JSON-RPC protocol version — must be "2.0".</summary>
        [JsonProperty("jsonrpc")]
        public string Jsonrpc { get; set; } = "2.0";

        /// <summary>
        /// Client-assigned request identifier. Echoed back in the response so the caller
        /// can correlate async responses. May be null for notifications (fire-and-forget).
        /// </summary>
        [JsonProperty("id")]
        public string Id { get; set; }

        /// <summary>
        /// Method to invoke — formatted as "TypeName.MethodName" (e.g. "EditorApplication.OpenProject").
        /// The ReflectionCommandDispatcher resolves this against its startup cache.
        /// </summary>
        [JsonProperty("method")]
        public string Method { get; set; }

        /// <summary>
        /// Named parameters for the method, keyed by parameter name (case-insensitive).
        /// TypeCoercer maps these to the target MethodInfo's ParameterInfo array.
        /// May be null if the method takes no arguments.
        /// </summary>
        [JsonProperty("params")]
        public JObject Params { get; set; }
    }
}
