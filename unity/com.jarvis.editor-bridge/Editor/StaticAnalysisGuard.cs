// Copyright (c) 2026 Conner McCarthy. All rights reserved.
// Unity Editor Bridge — C#-side static analysis guard (defense-in-depth layer).
//
// The PRIMARY security gate is the Python-side pre-write regex scan in unity_tool.py.
// This class provides a secondary C#-side check executed before any file-operation
// method dispatch. It is defense-in-depth, not the authoritative gate.

using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace Jarvis.EditorBridge
{
    /// <summary>
    /// Scans C# source code strings for dangerous API patterns before the bridge
    /// executes any file-write dispatch. Returns a list of violation messages —
    /// an empty list means the code passed the guard.
    /// </summary>
    public static class StaticAnalysisGuard
    {
        // ── Dangerous pattern registry ─────────────────────────────────────────

        private static readonly (Regex Pattern, string Message)[] DangerousPatterns =
        {
            // Process execution — arbitrary shell command injection
            (
                new Regex(@"\bProcess\.Start\b", RegexOptions.Compiled),
                "Forbidden: Process.Start allows arbitrary command execution"
            ),
            (
                new Regex(@"\bSystem\.Diagnostics\.Process\b", RegexOptions.Compiled),
                "Forbidden: System.Diagnostics.Process allows arbitrary command execution"
            ),

            // File/directory deletion — irreversible data loss outside jail
            (
                new Regex(@"\bFile\.Delete\b", RegexOptions.Compiled),
                "Forbidden: File.Delete — use AssetDatabase operations within JarvisGenerated only"
            ),
            (
                new Regex(@"\bDirectory\.Delete\b", RegexOptions.Compiled),
                "Forbidden: Directory.Delete — use AssetDatabase operations within JarvisGenerated only"
            ),
            (
                new Regex(@"\bFileUtil\.DeleteFileOrDirectory\b", RegexOptions.Compiled),
                "Forbidden: FileUtil.DeleteFileOrDirectory — requires explicit approval"
            ),
            (
                new Regex(@"\bAssetDatabase\.DeleteAsset\b", RegexOptions.Compiled),
                "Forbidden: AssetDatabase.DeleteAsset — requires explicit approval"
            ),

            // Dynamic assembly loading — arbitrary code execution
            (
                new Regex(@"\bAssembly\.LoadFrom\b", RegexOptions.Compiled),
                "Forbidden: Assembly.LoadFrom allows arbitrary code execution"
            ),
            (
                new Regex(@"\bAssembly\.Load\s*\(", RegexOptions.Compiled),
                "Forbidden: Assembly.Load allows arbitrary code execution"
            ),

            // Path traversal sequences — escape from JarvisGenerated jail
            (
                new Regex(@"\.\.[/\\]", RegexOptions.Compiled),
                "Forbidden: Path traversal sequence detected (../ or ..\\)"
            ),
        };

        // ── Public API ─────────────────────────────────────────────────────────

        /// <summary>
        /// Scans <paramref name="csharpCode"/> for dangerous API patterns.
        /// </summary>
        /// <param name="csharpCode">C# source code string to analyze.</param>
        /// <returns>
        /// List of human-readable violation messages.
        /// An empty list means no dangerous patterns were found.
        /// </returns>
        public static List<string> ScanForDangerousPatterns(string csharpCode)
        {
            var violations = new List<string>();

            if (string.IsNullOrEmpty(csharpCode))
                return violations;

            foreach (var (pattern, message) in DangerousPatterns)
            {
                if (pattern.IsMatch(csharpCode))
                    violations.Add(message);
            }

            return violations;
        }

        /// <summary>
        /// Returns true if the code is safe (no dangerous patterns found).
        /// </summary>
        public static bool IsSafe(string csharpCode)
        {
            return ScanForDangerousPatterns(csharpCode).Count == 0;
        }
    }
}
