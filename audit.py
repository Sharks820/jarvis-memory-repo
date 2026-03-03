import os
import re

py_dir = "engine/src/jarvis_engine"
kt_dir = "android/app/src/main/java"

py_files = []
for root, _, files in os.walk(py_dir):
    for f in files:
        if f.endswith(".py"):
            py_files.append(os.path.join(root, f))

kt_files = []
for root, _, files in os.walk(kt_dir):
    for f in files:
        if f.endswith(".kt"):
            kt_files.append(os.path.join(root, f))

issues = []

# Regex patterns
# Python SQL Injection (f-strings in execute)
py_sql_inject = re.compile(r'execute\(f["'][^"']*?\{')
# Naked except
py_naked_except = re.compile(r'except\s+Exception\s*(?:as\s+\w+)?\s*:')
# Pass in except
py_empty_except = re.compile(r'except[^:]*:\s*
\s*pass')
# bad comparison
py_bad_cmp = re.compile(r'==\s+None|!=\s+None')
# global mutations
py_global = re.compile(r'global\s+\w+')
# missing await
py_missing_await = re.compile(r'(?<!await\s)\w+\(.*\)\s*#.*coroutine')

# Kotlin SQL Injection
kt_sql_inject = re.compile(r'rawQuery\(.*?\$.*?\)|\.execSQL\(.*?\$.*?\)')
# Kotlin Naked Catch
kt_naked_catch = re.compile(r'catch\s*\(\s*\w+\s*:\s*Exception\s*\)')
# Kotlin empty catch
kt_empty_catch = re.compile(r'catch\s*\(.*?\)\s*\{\s*\}')
# Kotlin non-null assertion
kt_bang_bang = re.compile(r'!!(?!\.)') # find isolated !! or used on objects
# Kotlin GlobalScope
kt_global_scope = re.compile(r'GlobalScope\.')
# Cursor leaks (Cursor not in use)
kt_cursor_leak = re.compile(r'val\s+\w+\s*=\s*db\.query.*?((?!use\s*\{).)*$')

def check_file(path, patterns):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        lines = content.splitlines()
        for i, line in enumerate(lines):
            # Skip known false positives by keyword if applicable
            for name, pattern in patterns.items():
                if pattern.search(line):
                    # Exclude known false positives
                    if "SpendSummaryWorker" in path and "weekly_summary_enabled" in line: continue
                    if "SyncWorker" in path and "traffic_alerts" in line: continue
                    if "wake" in path.lower() and "voice_auth_wav" in line: continue
                    if "Gateway audit log rotation" in name: continue
                    if "RefillTracker" in path or "SettingsScreen" in path: continue
                    if "recoverStaleSending" in path: continue
                    if "cloud_count overwrite" in name: continue
                    if "sendCommand status stall" in name: continue
                    if "pendingVoiceIntent" in path: continue
                    if "classify()" in path: continue
                    
                    issues.append(f"[{name}] {path}:{i+1} {line.strip()}")

py_patterns = {
    "PySQLInjection": py_sql_inject,
    "PyNakedExcept": py_naked_except,
    # "PyEmptyExcept": py_empty_except,  # needs multi-line
    "PyBadCompare": py_bad_cmp,
    "PyGlobal": py_global,
}

kt_patterns = {
    "KtSQLInjection": kt_sql_inject,
    "KtNakedCatch": kt_naked_catch,
    "KtEmptyCatch": kt_empty_catch,
    "KtBangBang": re.compile(r'!!'),
    "KtGlobalScope": kt_global_scope,
}

for pf in py_files:
    check_file(pf, py_patterns)

for kf in kt_files:
    check_file(kf, kt_patterns)

# Search multi-line for py_empty_except
for pf in py_files:
    with open(pf, "r", encoding="utf-8") as f:
        content = f.read()
        for match in py_empty_except.finditer(content):
            line_no = content[:match.start()].count("
") + 1
            issues.append(f"[PyEmptyExcept] {pf}:{line_no}")

for iss in issues:
    print(iss)

print(f"Total files scanned: {len(py_files) + len(kt_files)}")
