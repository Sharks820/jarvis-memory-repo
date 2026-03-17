// Copyright (c) 2026 Conner McCarthy. All rights reserved.
// Unity Editor Bridge — JSON-RPC 2.0 response model.

using Newtonsoft.Json;

namespace Jarvis.EditorBridge.Models
{
    /// <summary>
    /// Represents a JSON-RPC 2.0 response sent back to the Python Jarvis agent.
    /// Exactly one of <see cref="Result"/> or <see cref="Error"/> will be non-null
    /// in any given response per the JSON-RPC 2.0 specification.
    /// </summary>
    public class JsonRpcResponse
    {
        /// <summary>JSON-RPC protocol version — always "2.0".</summary>
        [JsonProperty("jsonrpc")]
        public string Jsonrpc { get; set; } = "2.0";

        /// <summary>
        /// Echoes the request Id so the caller can correlate the response.
        /// Null for notifications originated by the bridge (e.g. compile_errors).
        /// </summary>
        [JsonProperty("id", NullValueHandling = NullValueHandling.Include)]
        public string Id { get; set; }

        /// <summary>
        /// Result payload on success. May be null for void methods.
        /// Serialized as-is — can be any JSON-serializable value.
        /// </summary>
        [JsonProperty("result", NullValueHandling = NullValueHandling.Ignore)]
        public object Result { get; set; }

        /// <summary>
        /// Error payload on failure. Null on success.
        /// </summary>
        [JsonProperty("error", NullValueHandling = NullValueHandling.Ignore)]
        public JsonRpcError Error { get; set; }

        // ── Factory methods ────────────────────────────────────────────────────

        /// <summary>Creates a successful response.</summary>
        /// <param name="id">Request identifier to echo.</param>
        /// <param name="result">Return value (may be null for void methods).</param>
        public static JsonRpcResponse Success(string id, object result)
        {
            return new JsonRpcResponse
            {
                Id = id,
                Result = result ?? "null"
            };
        }

        /// <summary>Creates an error response.</summary>
        /// <param name="id">Request identifier to echo.</param>
        /// <param name="code">JSON-RPC error code (e.g. -32603 for internal error).</param>
        /// <param name="message">Human-readable error description.</param>
        public static JsonRpcResponse Failure(string id, int code, string message)
        {
            return new JsonRpcResponse
            {
                Id = id,
                Error = new JsonRpcError { Code = code, Message = message }
            };
        }
    }

    /// <summary>
    /// JSON-RPC 2.0 error object included in failure responses.
    /// </summary>
    public class JsonRpcError
    {
        /// <summary>
        /// Numeric error code. Standard codes:
        ///   -32700  Parse error
        ///   -32600  Invalid request
        ///   -32601  Method not found
        ///   -32602  Invalid params
        ///   -32603  Internal error
        /// </summary>
        [JsonProperty("code")]
        public int Code { get; set; }

        /// <summary>Short description of the error.</summary>
        [JsonProperty("message")]
        public string Message { get; set; }
    }
}
