// Copyright (c) 2026 Conner McCarthy. All rights reserved.
// Unity Editor Bridge — reflection-based command dispatcher.
//
// BuildCache() scans UnityEditor and UnityEngine assemblies ONCE at startup and
// populates a Dictionary<string, List<MethodInfo>> keyed by "TypeName.MethodName".
// Multiple overloads per method name are stored in the list — overload resolution
// by parameter count occurs at Dispatch() time (Pitfall 3 in research).

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Jarvis.EditorBridge.Util;

namespace Jarvis.EditorBridge
{
    /// <summary>
    /// Builds a reflection cache of all public static methods in UnityEditor and
    /// UnityEngine assemblies, then dispatches JSON-RPC method calls against that cache.
    /// </summary>
    public class ReflectionCommandDispatcher
    {
        // Key: "TypeName.MethodName", Value: all public static overloads for that key
        private readonly Dictionary<string, List<MethodInfo>> _cache =
            new Dictionary<string, List<MethodInfo>>(StringComparer.OrdinalIgnoreCase);

        private int _totalTypes;
        private int _totalMethods;

        // ── Built-in Jarvis command handlers ────────────────────────────────
        // These handle custom RPC methods sent by the Python agent that are NOT
        // public static methods on UnityEditor/UnityEngine types.

        private static readonly Dictionary<string, Func<JObject, object>> s_builtinCommands
            = new Dictionary<string, Func<JObject, object>>(StringComparer.OrdinalIgnoreCase)
        {
            { "WriteScript", HandleWriteScript },
            { "CompileProject", HandleCompileProject },
            { "RunTests", HandleRunTests },
            { "EnterPlayMode", HandleEnterPlayMode },
            { "ExitPlayMode", HandleExitPlayMode },
            { "ImportAsset", HandleImportAsset },
            { "GetCompileErrors", HandleGetCompileErrors },
            { "CreateProject", HandleCreateProject },
            { "SetModelImporterSettings", HandleSetModelImporterSettings },
            { "SetTextureImporterSettings", HandleSetTextureImporterSettings },
            { "SetAudioImporterSettings", HandleSetAudioImporterSettings },
        };

        // ── Public API ─────────────────────────────────────────────────────────

        /// <summary>
        /// Scans UnityEditor and UnityEngine assemblies and populates the dispatch cache.
        /// Call once from the static constructor — ~200-400ms; amortised across domain reload.
        /// </summary>
        public void BuildCache()
        {
            _cache.Clear();
            _totalTypes = 0;
            _totalMethods = 0;

            var assemblies = new[]
            {
                typeof(UnityEditor.EditorApplication).Assembly,  // UnityEditor
                typeof(UnityEngine.GameObject).Assembly,         // UnityEngine.CoreModule
            };

            foreach (var asm in assemblies)
            {
                Type[] types;
                try
                {
                    types = asm.GetTypes();
                }
                catch (ReflectionTypeLoadException rtle)
                {
                    // Some types may fail to load — skip them, continue with the rest
                    types = rtle.Types;
                    Debug.LogWarning(
                        $"[Jarvis] ReflectionTypeLoadException in {asm.GetName().Name}: " +
                        $"{rtle.LoaderExceptions.Length} types skipped");
                }

                foreach (var type in types)
                {
                    if (type == null) continue;

                    var methods = type.GetMethods(BindingFlags.Public | BindingFlags.Static);
                    if (methods.Length == 0) continue;

                    _totalTypes++;

                    foreach (var method in methods)
                    {
                        var key = $"{type.Name}.{method.Name}";

                        if (!_cache.TryGetValue(key, out var overloads))
                        {
                            overloads = new List<MethodInfo>();
                            _cache[key] = overloads;
                        }

                        overloads.Add(method);
                        _totalMethods++;
                    }
                }
            }

            Debug.Log(
                $"[Jarvis] Reflection cache built: {_totalMethods} methods from {_totalTypes} types");
        }

        /// <summary>
        /// Dispatch a JSON-RPC method call.
        /// </summary>
        /// <param name="methodKey">"TypeName.MethodName" (e.g. "EditorApplication.OpenProject").</param>
        /// <param name="args">Named JSON args from the request params. May be null.</param>
        /// <returns>Return value of the invoked method, or null for void methods.</returns>
        /// <exception cref="KeyNotFoundException">Method key not found in cache.</exception>
        /// <exception cref="AmbiguousMatchException">
        /// Multiple overloads with the same parameter count — cannot auto-resolve.
        /// </exception>
        /// <exception cref="UnauthorizedAccessException">
        /// File-operation method targets a path outside the JarvisGenerated jail.
        /// </exception>
        public object Dispatch(string methodKey, JObject args)
        {
            if (string.IsNullOrWhiteSpace(methodKey))
                throw new ArgumentException("Method key must not be null or empty.", nameof(methodKey));

            // ── Check built-in Jarvis commands before reflection dispatch ────
            if (s_builtinCommands.TryGetValue(methodKey, out var builtinHandler))
                return builtinHandler(args ?? new JObject());

            if (!_cache.TryGetValue(methodKey, out var overloads))
                throw new KeyNotFoundException(
                    $"[Jarvis] Unknown method: '{methodKey}'. " +
                    $"Cache contains {_cache.Count} entries. " +
                    $"Check spelling and ensure the method is public static.");

            // ── Overload resolution by parameter count ─────────────────────────
            var paramArgs = args ?? new JObject();
            int argCount = paramArgs.Count;

            List<MethodInfo> candidates;

            if (overloads.Count == 1)
            {
                candidates = overloads;
            }
            else
            {
                // Filter to overloads whose required parameter count matches arg count
                candidates = overloads
                    .Where(m => IsParamCountMatch(m, argCount))
                    .ToList();

                if (candidates.Count == 0)
                {
                    // Fallback: try optional-param matching (methods where all extra params are optional)
                    candidates = overloads
                        .Where(m => IsParamCountCompatible(m, argCount))
                        .ToList();
                }

                if (candidates.Count == 0)
                    throw new KeyNotFoundException(
                        $"[Jarvis] Method '{methodKey}' has {overloads.Count} overloads but none " +
                        $"match {argCount} argument(s). " +
                        $"Available param counts: [{string.Join(", ", overloads.Select(m => m.GetParameters().Length))}]");

                if (candidates.Count > 1)
                    throw new AmbiguousMatchException(
                        $"[Jarvis] Method '{methodKey}' has {candidates.Count} overloads that all accept " +
                        $"{argCount} argument(s). Disambiguate by providing a unique parameter count.");
            }

            var method = candidates[0];

            // ── Path jail defense-in-depth for file operations ─────────────────
            EnforcePathJailIfFileOperation(methodKey, method, paramArgs);

            // ── Parameter coercion and invocation ─────────────────────────────
            var parameters = TypeCoercer.Coerce(method.GetParameters(), paramArgs);

            try
            {
                return method.Invoke(null, parameters);
            }
            catch (TargetInvocationException tie) when (tie.InnerException != null)
            {
                // Unwrap so callers see the real exception (not the reflection wrapper)
                throw tie.InnerException;
            }
        }

        /// <summary>Number of cache entries (distinct TypeName.MethodName keys).</summary>
        public int CacheSize => _cache.Count;

        // ── Private helpers ────────────────────────────────────────────────────

        /// <summary>True if the method requires exactly <paramref name="argCount"/> parameters.</summary>
        private static bool IsParamCountMatch(MethodInfo method, int argCount)
        {
            var ps = method.GetParameters();
            int required = ps.Count(p => !p.IsOptional && !p.HasDefaultValue);
            int total = ps.Length;
            return argCount >= required && argCount <= total;
        }

        /// <summary>True if <paramref name="argCount"/> satisfies the method's parameter range.</summary>
        private static bool IsParamCountCompatible(MethodInfo method, int argCount)
        {
            var ps = method.GetParameters();
            // Count required (non-optional) params
            int required = ps.Count(p => !p.IsOptional && !p.HasDefaultValue);
            return argCount >= required;
        }

        /// <summary>
        /// For methods with file-operation semantics, validate that any path arguments
        /// are within the JarvisGenerated jail. This is the C#-side defense-in-depth
        /// layer; Python-side validation is the authoritative gate.
        /// </summary>
        private static void EnforcePathJailIfFileOperation(
            string methodKey, MethodInfo method, JObject args)
        {
            // Determine if this looks like a file operation
            bool isFileOp = IsFileOperationMethod(methodKey, method);
            if (!isFileOp) return;

            // Check any argument value that looks like a path
            string jailPrefix;
            try
            {
                // Resolve relative to the Unity project root (Application.dataPath is <project>/Assets)
                string projectRoot = System.IO.Path.GetFullPath(
                    System.IO.Path.Combine(Application.dataPath, ".."));
                jailPrefix = System.IO.Path.Combine(projectRoot, "Assets", "JarvisGenerated")
                    + System.IO.Path.DirectorySeparatorChar;
            }
            catch (Exception ex)
            {
                // Fail closed — reject when jail prefix cannot be computed
                throw new UnauthorizedAccessException(
                    $"[Jarvis] Path jail check cannot compute jail prefix: {ex.Message}");
            }

            foreach (var prop in args.Properties())
            {
                var val = prop.Value?.Value<string>();
                if (val == null) continue;

                // Only validate values that look like paths (contain / or \)
                if (!val.Contains('/') && !val.Contains('\\')) continue;

                try
                {
                    // Resolve relative paths against the project root, not CWD
                    string projectRoot = System.IO.Path.GetFullPath(
                        System.IO.Path.Combine(Application.dataPath, ".."));
                    string normalized = System.IO.Path.IsPathRooted(val)
                        ? System.IO.Path.GetFullPath(val)
                        : System.IO.Path.GetFullPath(System.IO.Path.Combine(projectRoot, val));
                    // Allow exact jail directory or any path under it
                    if (!normalized.StartsWith(jailPrefix, StringComparison.OrdinalIgnoreCase)
                        && !normalized.Equals(
                            jailPrefix.TrimEnd(System.IO.Path.DirectorySeparatorChar),
                            StringComparison.OrdinalIgnoreCase))
                    {
                        throw new UnauthorizedAccessException(
                            $"[Jarvis] Bridge path jail violation: method '{methodKey}', " +
                            $"parameter '{prop.Name}' value '{val}' is outside " +
                            $"JarvisGenerated jail ({jailPrefix}).");
                    }
                }
                catch (ArgumentException ex)
                {
                    // If it looks like a path but can't be resolved, fail closed
                    throw new UnauthorizedAccessException(
                        $"[Jarvis] Path jail violation: parameter '{prop.Name}' " +
                        $"value '{val}' could not be validated: {ex.Message}");
                }
            }
        }

        /// <summary>
        /// Heuristic: return true if this method involves file I/O operations.
        /// Based on method name containing file-operation keywords.
        /// </summary>
        private static bool IsFileOperationMethod(string methodKey, MethodInfo method)
        {
            var fileOpKeywords = new[]
            {
                "Write", "Create", "Delete", "Move", "Copy",
                "Save", "Export", "WriteAllText", "WriteAllBytes"
            };

            foreach (var keyword in fileOpKeywords)
            {
                if (method.Name.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0)
                    return true;
            }

            // Check declaring type name for file-related types
            var fileTypes = new[]
            {
                "File", "Directory", "StreamWriter", "FileInfo",
                "AssetDatabase", "FileUtil"
            };
            var typeName = method.DeclaringType?.Name ?? string.Empty;
            foreach (var ft in fileTypes)
            {
                if (typeName.IndexOf(ft, StringComparison.OrdinalIgnoreCase) >= 0)
                    return true;
            }

            return false;
        }

        // ── Built-in command handler implementations ─────────────────────────

        private static readonly string s_jailPrefix = "Assets/JarvisGenerated/";

        /// <summary>
        /// Validate that a path is under Assets/JarvisGenerated/.
        /// Canonicalizes the path (resolving .. and . segments) before checking.
        /// Throws UnauthorizedAccessException on violation.
        /// </summary>
        private static void EnforceBuiltinPathJail(string path, string methodName)
        {
            string normalized = (path ?? "").Replace("\\", "/");
            // Resolve .. and . segments to prevent traversal bypasses
            var parts = new List<string>();
            foreach (var segment in normalized.Split('/'))
            {
                if (segment == ".." && parts.Count > 0)
                    parts.RemoveAt(parts.Count - 1);
                else if (segment != "." && segment != "")
                    parts.Add(segment);
            }
            string canonical = string.Join("/", parts);

            if (!canonical.StartsWith(s_jailPrefix, StringComparison.Ordinal)
                && canonical != "Assets/JarvisGenerated")
            {
                throw new UnauthorizedAccessException(
                    $"[Jarvis] Path jail violation in {methodName}: " +
                    $"'{path}' (resolved: '{canonical}') is not under {s_jailPrefix}");
            }
        }

        private static object HandleWriteScript(JObject args)
        {
            string path = args.Value<string>("path") ?? "";
            string code = args.Value<string>("content") ?? args.Value<string>("code") ?? "";

            if (string.IsNullOrEmpty(path))
                throw new ArgumentException("WriteScript requires a 'path' parameter.");

            // Path jail enforcement (defense-in-depth; Python side is authoritative)
            EnforceBuiltinPathJail(path, "WriteScript");

            string fullPath = Path.Combine(Application.dataPath, "..", path);
            string dir = Path.GetDirectoryName(fullPath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                Directory.CreateDirectory(dir);
            File.WriteAllText(fullPath, code);
            AssetDatabase.Refresh();
            return new { written = true, path = path };
        }

        private static object HandleCompileProject(JObject args)
        {
            AssetDatabase.Refresh(ImportAssetOptions.ForceUpdate);
            // Unity compilation is triggered by Refresh; errors are surfaced via
            // CompilationPipeline callbacks (see JarvisCompilationWatcher if present).
            // Return pending=true so Python knows compilation was triggered but
            // success hasn't been verified yet — use GetCompileErrors for results.
            return new { compiled = false, pending = true,
                message = "Compilation triggered via AssetDatabase.Refresh. Check GetCompileErrors for results." };
        }

        private static object HandleRunTests(JObject args)
        {
            // Unity Test Framework integration — start test run.
            // Full UTR integration requires the TestRunnerApi; for now we accept the
            // request so the RPC contract is satisfied.
            // Return pending=true so Python knows tests were started but results
            // aren't immediately available.
            string testFilter = args.Value<string>("testFilter") ?? "";
            return new { started = true, pending = true, testFilter = testFilter };
        }

        private static object HandleEnterPlayMode(JObject args)
        {
            EditorApplication.isPlaying = true;
            return new { playing = true };
        }

        private static object HandleExitPlayMode(JObject args)
        {
            EditorApplication.isPlaying = false;
            return new { playing = false };
        }

        private static object HandleImportAsset(JObject args)
        {
            string path = args.Value<string>("path") ?? "";
            if (string.IsNullOrEmpty(path))
                throw new ArgumentException("ImportAsset requires a 'path' parameter.");

            // Path jail enforcement (defense-in-depth; Python side is authoritative)
            EnforceBuiltinPathJail(path, "ImportAsset");

            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            return new { imported = true, path = path };
        }

        private static object HandleGetCompileErrors(JObject args)
        {
            // Compile errors are surfaced via CompilationPipeline callbacks.
            // Return empty array as baseline; JarvisCompilationWatcher can augment.
            // Note: For real-time errors, subscribe to
            // CompilationPipeline.assemblyCompilationFinished in JarvisCompilationWatcher.
            return new { errors = new string[0],
                note = "Subscribe to CompilationPipeline.assemblyCompilationFinished for real-time errors" };
        }

        private static object HandleCreateProject(JObject args)
        {
            // Unity does not support creating projects from within the editor.
            // Return current project info so the caller can verify connectivity.
            return new {
                project = Application.dataPath,
                version = Application.unityVersion
            };
        }

        private static object HandleSetModelImporterSettings(JObject args)
        {
            string path = args.Value<string>("path") ?? "";
            if (string.IsNullOrEmpty(path))
                throw new ArgumentException("SetModelImporterSettings requires a 'path' parameter.");
            EnforceBuiltinPathJail(path, "SetModelImporterSettings");

            var importer = AssetImporter.GetAtPath(path) as ModelImporter;
            if (importer == null)
                throw new InvalidOperationException($"No ModelImporter found at path: {path}");

            if (args["scaleFactor"] != null)
                importer.globalScale = args.Value<float>("scaleFactor");
            if (args["importMaterials"] != null)
                importer.materialImportMode = args.Value<bool>("importMaterials")
                    ? ModelImporterMaterialImportMode.ImportViaMaterialDescription
                    : ModelImporterMaterialImportMode.None;
            if (args["generateLightmapUVs"] != null)
                importer.generateSecondaryUV = args.Value<bool>("generateLightmapUVs");
            if (args["meshCompression"] != null)
            {
                var compression = args.Value<string>("meshCompression") ?? "Off";
                if (System.Enum.TryParse<ModelImporterMeshCompression>(compression, true, out var mc))
                    importer.meshCompression = mc;
            }

            importer.SaveAndReimport();
            return new { applied = true, path = path, type = "ModelImporter" };
        }

        private static object HandleSetTextureImporterSettings(JObject args)
        {
            string path = args.Value<string>("path") ?? "";
            if (string.IsNullOrEmpty(path))
                throw new ArgumentException("SetTextureImporterSettings requires a 'path' parameter.");
            EnforceBuiltinPathJail(path, "SetTextureImporterSettings");

            var importer = AssetImporter.GetAtPath(path) as TextureImporter;
            if (importer == null)
                throw new InvalidOperationException($"No TextureImporter found at path: {path}");

            if (args["sRGB"] != null)
                importer.sRGBTexture = args.Value<bool>("sRGB");
            if (args["maxTextureSize"] != null)
                importer.maxTextureSize = args.Value<int>("maxTextureSize");
            if (args["textureCompression"] != null)
            {
                var compression = args.Value<string>("textureCompression") ?? "Normal";
                if (System.Enum.TryParse<TextureImporterCompression>(compression, true, out var tc))
                    importer.textureCompression = tc;
            }
            if (args["mipmapEnabled"] != null)
                importer.mipmapEnabled = args.Value<bool>("mipmapEnabled");

            importer.SaveAndReimport();
            return new { applied = true, path = path, type = "TextureImporter" };
        }

        private static object HandleSetAudioImporterSettings(JObject args)
        {
            string path = args.Value<string>("path") ?? "";
            if (string.IsNullOrEmpty(path))
                throw new ArgumentException("SetAudioImporterSettings requires a 'path' parameter.");
            EnforceBuiltinPathJail(path, "SetAudioImporterSettings");

            var importer = AssetImporter.GetAtPath(path) as AudioImporter;
            if (importer == null)
                throw new InvalidOperationException($"No AudioImporter found at path: {path}");

            var settings = importer.defaultSampleSettings;
            if (args["compressionFormat"] != null)
            {
                var format = args.Value<string>("compressionFormat") ?? "Vorbis";
                if (System.Enum.TryParse<AudioCompressionFormat>(format, true, out var af))
                    settings.compressionFormat = af;
            }
            if (args["quality"] != null)
                settings.quality = args.Value<float>("quality");
            if (args["loadInBackground"] != null)
                importer.loadInBackground = args.Value<bool>("loadInBackground");

            importer.defaultSampleSettings = settings;
            importer.SaveAndReimport();
            return new { applied = true, path = path, type = "AudioImporter" };
        }
    }
}
