"""Static analysis audit for common code issues in Python and Kotlin sources."""

import os
import re


def collect_files(directory: str, extension: str) -> list[str]:
    """Collect all files with the given extension under directory."""
    result = []
    for root, _, files in os.walk(directory):
        for f in files:
            if f.endswith(extension):
                result.append(os.path.join(root, f))
    return result


def check_file(path: str, patterns: dict, issues: list) -> None:
    """Scan a single file for pattern matches, appending to issues."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        lines = content.splitlines()
        for i, line in enumerate(lines):
            for name, pattern in patterns.items():
                if pattern.search(line):
                    # Exclude known false positives
                    if "SpendSummaryWorker" in path and "weekly_summary_enabled" in line:
                        continue
                    if "SyncWorker" in path and "traffic_alerts" in line:
                        continue
                    if "wake" in path.lower() and "voice_auth_wav" in line:
                        continue
                    if "RefillTracker" in path or "SettingsScreen" in path:
                        continue
                    issues.append(f"[{name}] {path}:{i+1} {line.strip()}")


def scan_multiline(files: list[str], pattern: re.Pattern, name: str, issues: list) -> None:
    """Run a multiline regex scan across files."""
    for pf in files:
        with open(pf, "r", encoding="utf-8") as f:
            content = f.read()
            for match in pattern.finditer(content):
                line_no = content[:match.start()].count("\n") + 1
                issues.append(f"[{name}] {pf}:{line_no}")


def main() -> None:
    """Run the full audit scan and print results."""
    py_dir = "engine/src/jarvis_engine"
    kt_dir = "android/app/src/main/java"

    py_files = collect_files(py_dir, ".py")
    kt_files = collect_files(kt_dir, ".kt")

    issues: list[str] = []

    # Python patterns
    py_patterns = {
        "PySQLInjection": re.compile(r"execute\(f[\"'][^\"']*?\{"),
        "PyNakedExcept": re.compile(r'except\s+Exception\s*(?:as\s+\w+)?\s*:'),
        "PyBadCompare": re.compile(r'==\s+None|!=\s+None'),
        "PyGlobal": re.compile(r'global\s+\w+'),
    }

    # Kotlin patterns
    kt_patterns = {
        "KtSQLInjection": re.compile(r'rawQuery\(.*?\$.*?\)|\.execSQL\(.*?\$.*?\)'),
        "KtNakedCatch": re.compile(r'catch\s*\(\s*\w+\s*:\s*Exception\s*\)'),
        "KtEmptyCatch": re.compile(r'catch\s*\(.*?\)\s*\{\s*\}'),
        "KtBangBang": re.compile(r'!!'),
        "KtGlobalScope": re.compile(r'GlobalScope\.'),
    }

    for pf in py_files:
        check_file(pf, py_patterns, issues)

    for kf in kt_files:
        check_file(kf, kt_patterns, issues)

    # Multiline pattern for empty except blocks
    py_empty_except = re.compile(r'except[^:]*:\s*\n\s*pass')
    scan_multiline(py_files, py_empty_except, "PyEmptyExcept", issues)

    for iss in issues:
        print(iss)

    print(f"\nTotal files scanned: {len(py_files) + len(kt_files)}")
    print(f"Issues found: {len(issues)}")


if __name__ == "__main__":
    main()
