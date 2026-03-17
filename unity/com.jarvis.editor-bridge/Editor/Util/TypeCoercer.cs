// Copyright (c) 2026 Conner McCarthy. All rights reserved.
// Unity Editor Bridge — JSON value to C# parameter type coercion.

using System;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace Jarvis.EditorBridge.Util
{
    /// <summary>
    /// Converts a JSON parameter bag (<see cref="JObject"/>) into a typed object array
    /// suitable for passing to <see cref="MethodInfo.Invoke"/>.
    ///
    /// Supports: string, int, long, float, double, bool, Vector2, Vector3, Color.
    /// Falls back to <see cref="JToken.ToObject(Type)"/> for other serializable types.
    /// Parameter names are matched case-insensitively against JSON keys.
    /// </summary>
    public static class TypeCoercer
    {
        /// <summary>
        /// Coerce a JSON parameter bag into a typed array aligned to <paramref name="parameters"/>.
        /// </summary>
        /// <param name="parameters">Method parameter descriptors from reflection.</param>
        /// <param name="args">Named JSON arguments from the JSON-RPC request params object.
        /// May be null if the method takes no arguments.</param>
        /// <returns>Object array ready for <see cref="MethodInfo.Invoke"/>.</returns>
        /// <exception cref="ArgumentException">
        /// Thrown when a required parameter cannot be found or coerced.
        /// </exception>
        public static object[] Coerce(ParameterInfo[] parameters, JObject args)
        {
            if (parameters == null || parameters.Length == 0)
                return Array.Empty<object>();

            var result = new object[parameters.Length];
            var argsLookup = args ?? new JObject();

            for (int i = 0; i < parameters.Length; i++)
            {
                var param = parameters[i];
                var token = FindToken(argsLookup, param.Name);

                if (token == null)
                {
                    if (param.IsOptional)
                    {
                        result[i] = param.DefaultValue;
                        continue;
                    }
                    throw new ArgumentException(
                        $"Required parameter '{param.Name}' not found in JSON args. " +
                        $"Available keys: [{string.Join(", ", argsLookup.Properties())}]");
                }

                result[i] = CoerceToken(token, param.ParameterType, param.Name);
            }

            return result;
        }

        // ── Private helpers ────────────────────────────────────────────────────

        /// <summary>Find a JSON token by parameter name (case-insensitive).</summary>
        private static JToken FindToken(JObject args, string paramName)
        {
            foreach (var prop in args.Properties())
            {
                if (string.Equals(prop.Name, paramName, StringComparison.OrdinalIgnoreCase))
                    return prop.Value;
            }
            return null;
        }

        /// <summary>Coerce a single JSON token to the target CLR type.</summary>
        private static object CoerceToken(JToken token, Type targetType, string paramName)
        {
            try
            {
                // ── Primitives ──────────────────────────────────────────────
                if (targetType == typeof(string))
                    return token.Value<string>();

                if (targetType == typeof(int))
                    return token.Value<int>();

                if (targetType == typeof(long))
                    return token.Value<long>();

                if (targetType == typeof(float))
                    return token.Value<float>();

                if (targetType == typeof(double))
                    return token.Value<double>();

                if (targetType == typeof(bool))
                    return token.Value<bool>();

                // ── Unity value types ───────────────────────────────────────
                if (targetType == typeof(Vector2))
                    return CoerceVector2(token);

                if (targetType == typeof(Vector3))
                    return CoerceVector3(token);

                if (targetType == typeof(Color))
                    return CoerceColor(token);

                // ── Fallback: Newtonsoft generic deserialization ─────────────
                return token.ToObject(targetType);
            }
            catch (Exception ex)
            {
                throw new ArgumentException(
                    $"Cannot coerce param '{paramName}' (value: {token}) to {targetType.Name}: {ex.Message}", ex);
            }
        }

        /// <summary>
        /// Deserialize a Vector2 from {"x":N,"y":N}.
        /// </summary>
        private static Vector2 CoerceVector2(JToken token)
        {
            if (token.Type != JTokenType.Object)
                throw new ArgumentException($"Expected object for Vector2, got {token.Type}");
            var obj = (JObject)token;
            return new Vector2(
                obj["x"]?.Value<float>() ?? 0f,
                obj["y"]?.Value<float>() ?? 0f
            );
        }

        /// <summary>
        /// Deserialize a Vector3 from {"x":N,"y":N,"z":N}.
        /// </summary>
        private static Vector3 CoerceVector3(JToken token)
        {
            if (token.Type != JTokenType.Object)
                throw new ArgumentException($"Expected object for Vector3, got {token.Type}");
            var obj = (JObject)token;
            return new Vector3(
                obj["x"]?.Value<float>() ?? 0f,
                obj["y"]?.Value<float>() ?? 0f,
                obj["z"]?.Value<float>() ?? 0f
            );
        }

        /// <summary>
        /// Deserialize a Color from {"r":N,"g":N,"b":N,"a":N} (0.0–1.0 range).
        /// </summary>
        private static Color CoerceColor(JToken token)
        {
            if (token.Type != JTokenType.Object)
                throw new ArgumentException($"Expected object for Color, got {token.Type}");
            var obj = (JObject)token;
            return new Color(
                obj["r"]?.Value<float>() ?? 0f,
                obj["g"]?.Value<float>() ?? 0f,
                obj["b"]?.Value<float>() ?? 0f,
                obj["a"]?.Value<float>() ?? 1f
            );
        }
    }
}
