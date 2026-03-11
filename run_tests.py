"""Run pytest and capture results reliably on Windows.

Writes pytest output to a temp file to avoid pipe buffering issues and exits
strictly from pytest's real return code.
"""
import logging
import os
import re
import subprocess
import sys
import tempfile

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
_log = logging.getLogger(__name__)

os.environ["JARVIS_SKIP_EMBED_WARMUP"] = "1"
os.environ["PYTHONUNBUFFERED"] = "1"

RESULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_results.txt")
SUMMARY_RE = re.compile(r"\b\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed)\b")

args = sys.argv[1:] if len(sys.argv) > 1 else [
    "engine/tests/", "-x", "-q", "--tb=short", "-p", "no:xdist", "-o", "addopts=",
]

TIMEOUT = 600
timed_out = False
proc_returncode = 1

# Clear results file before each run.
with open(RESULT_FILE, "w", encoding="utf-8") as results_file:
    results_file.write("")

with tempfile.NamedTemporaryFile(
    mode="w", suffix=".txt", delete=False, dir=".", encoding="utf-8"
) as tf:
    tmp_path = tf.name

try:
    with open(tmp_path, "w", encoding="utf-8") as temp_output:
        completed = subprocess.run(
            [sys.executable, "-u", "-m", "pytest"] + args,
            stdout=temp_output,
            stderr=subprocess.STDOUT,
            timeout=TIMEOUT,
            check=False,
        )
    proc_returncode = completed.returncode
except subprocess.TimeoutExpired:
    timed_out = True
    print("[TIMEOUT] Tests did not complete within 10 minutes", flush=True)
except KeyboardInterrupt:
    _log.debug("Interrupted by user")

try:
    with open(tmp_path, "r", encoding="utf-8") as temp_output:
        output_text = temp_output.read()
    with open(RESULT_FILE, "w", encoding="utf-8") as results_file:
        results_file.write(output_text)
except OSError as exc:
    _log.debug("Error copying test output: %s", exc)
    output_text = ""

# Read full results for display
try:
    with open(RESULT_FILE, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    for ln in all_lines[-15:]:
        print(ln.rstrip(), flush=True)
except OSError as exc:
    _log.debug("Error reading results file: %s", exc)

# Cleanup temp file
try:
    os.unlink(tmp_path)
except OSError as exc:
    _log.debug("Error removing temp file %s: %s", tmp_path, exc)

proc_ok = proc_returncode == 0
if not timed_out and proc_ok:
    try:
        if not SUMMARY_RE.search(output_text):
            _log.debug("Pytest exited cleanly without a terminal summary line.")
    except OSError as exc:
        _log.debug("Error verifying summary line: %s", exc)
sys.exit(0 if proc_ok and not timed_out else 1)
