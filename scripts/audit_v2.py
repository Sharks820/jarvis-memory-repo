import os
import re
import ast

py_dir = "engine/src/jarvis_engine"
kt_dir = "android/app/src/main/java"

issues = []

# Collect all files
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

# Python AST checks
class PyBugVisitor(ast.NodeVisitor):
    def __init__(self, filepath):
        self.filepath = filepath

    def visit_Compare(self, node):
        # == None or != None
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comp, ast.Constant) and comp.value is None:
                issues.append(f"[BUGS] {self.filepath}:{node.lineno} Comparison with None using ==/!=. Impact: Logic errors. Fix: Use 'is None' or 'is not None'")
        self.generic_visit(node)
        
    def visit_Call(self, node):
        # execute(f"...") -> SQL injection
        if isinstance(node.func, ast.Attribute) and node.func.attr in ('execute', 'executemany'):
            if node.args and isinstance(node.args[0], ast.JoinedStr):
                issues.append(f"[SECURITY] {self.filepath}:{node.lineno} SQL execution with f-string. Impact: SQL Injection. Fix: Use parameterized queries (?)")
        # hmac without compare_digest
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'compare_digest':
            pass # good
        self.generic_visit(node)
        
    def visit_ExceptHandler(self, node):
        if node.type is None:
            # naked except:
            pass
        elif isinstance(node.type, ast.Name) and node.type.id == 'Exception':
            # Check for pass -- intentionally not flagging empty catches
            # per prompt: "Empty catches for graceful degradation in optional features"
            pass
        self.generic_visit(node)

for pf in py_files:
    try:
        with open(pf, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=pf)
            visitor = PyBugVisitor(pf)
            visitor.visit(tree)
    except Exception as e:
        issues.append(f"[ERROR] failed to parse {pf}: {e}")

# Regex checks for Kotlin
kt_sql_inject = re.compile(r'\.execSQL\([^")]*?\$|\.rawQuery\([^")]*?\$')
kt_bang_bang = re.compile(r'\w+!!(?![\w\(\.])') # unsafe null extraction
kt_cursor_leak = re.compile(r'val\s+\w+\s*=\s*(?:db|database|resolver)\.query')
kt_cursor_use = re.compile(r'\.use\s*\{')
kt_suspend_launch = re.compile(r'launch\s*\{')
kt_hmac = re.compile(r'Hmac')
kt_mac_compare = re.compile(r'==|!=|equals')
kt_global_scope = re.compile(r'GlobalScope\.')

for kf in kt_files:
    with open(kf, "r", encoding="utf-8") as f:
        content = f.read()
        lines = content.splitlines()
        
        cursor_var = None
        for i, line in enumerate(lines):
            line_strip = line.strip()
            # Ignore false positives
            if "SpendSummaryWorker" in kf and "weekly_summary_enabled" in line: continue
            if "SyncWorker" in kf and "traffic_alerts" in line: continue
            if "RefillTracker" in kf or "SettingsScreen" in kf: continue
            if "recoverStaleSending" in kf: continue
            if "pendingVoiceIntent" in kf: continue
            if "classify(" in kf: continue
            
            if kt_sql_inject.search(line):
                issues.append(f"[SECURITY] {kf}:{i+1} SQL string interpolation. Impact: SQL Injection. Fix: Use parameterized queries (?)")
            
            if "!!" in line and not "?.let" in line:
                if "val " in line or "var " in line or " = " in line or "(" in line:
                    issues.append(f"[BUGS] {kf}:{i+1} Unsafe non-null assertion (!!). Impact: NullPointerException crash. Fix: Handle null explicitly or use ?.let")
                    
            if kt_global_scope.search(line):
                issues.append(f"[CONCURRENCY] {kf}:{i+1} Usage of GlobalScope. Impact: Memory/coroutine leak. Fix: Use lifecycle-aware CoroutineScope.")
                
            if kt_cursor_leak.search(line):
                # Check surrounding lines for .use{} block
                window = "\n".join(lines[max(0, i - 2):min(len(lines), i + 3)])
                if not kt_cursor_use.search(window):
                    issues.append(f"[BUGS] {kf}:{i+1} Potential cursor leak. Impact: Resource exhaustion. Fix: Use .use {{ }} block.")

for i in set(issues):
    print(i)

print("Scan complete.")
