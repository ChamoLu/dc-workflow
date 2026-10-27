"""Check the Git index or all reachable Git history without printing credentials."""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import PurePosixPath
import re
import subprocess
import sys
import zipfile

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
KEY_PATTERN = re.compile(rb"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")
PRIVATE_HOST = re.compile(rb"(?<![\d.])(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?![\d.])|(?:[A-Za-z0-9-]+\.)*gildata\.com", re.I)
# Deliberately fake keys exercise redaction in an existing regression test.
TEST_FILE = "agent/tests/test_v4_workflow_nodes.py"
TEST_KEY_HASHES = {'60be80e30d6d90cbc8b9167ae1632328709faf2566123ce61550be82aaa7f490', 'c2c4a3582d5d878aedb0e8bc66ff567b4f720f73535b0382c4036d57ba3546b2'}
FORBIDDEN_DIRS = {"runtime", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", ".owners", ".staging", ".versions", "_capture"}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL)


def read_objects(entries):
    refs = b"\n".join(ref.encode("utf-8") for _, ref in entries) + b"\n"
    output = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, input=refs, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout
    offset = 0
    for path, _ in entries:
        end = output.index(b"\n", offset)
        header = output[offset:end].split()
        if len(header) != 3 or not header[2].isdigit():
            raise RuntimeError("Cannot read Git object for " + path)
        size = int(header[2])
        offset = end + 1
        data = output[offset:offset + size]
        offset += size + 1
        if header[1] == b"blob":
            yield path, data


def inspect(path: str, data: bytes) -> list[str]:
    p = PurePosixPath(path)
    problems = []
    if FORBIDDEN_DIRS.intersection(p.parts) or p.name == ".runtime.json":
        problems.append("private runtime/cache path")
    if (p.name == ".env" or p.name.startswith(".env.") or p.name.endswith(".env")) and not p.name.endswith(".example"):
        problems.append("private environment configuration")
    if p.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db", ".log", ".png", ".jpg", ".jpeg"}:
        problems.append("private or unreviewed binary artifact")
    parts = [(path, data)]
    if p.suffix.lower() == ".xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                parts = [(path + " (workbook XML)", archive.read(n)) for n in archive.namelist() if n.endswith((".xml", ".rels"))]
        except zipfile.BadZipFile:
            problems.append("invalid workbook archive")
    for _, part in parts:
        if PRIVATE_HOST.search(part):
            problems.append("internal service address")
        for m in KEY_PATTERN.finditer(part):
            if path == TEST_FILE and hashlib.sha256(m.group()).hexdigest() in TEST_KEY_HASHES:
                continue
            problems.append("possible credential; value withheld")
        # Nonempty real values in environment templates must not be committed.
        for m in re.finditer(rb"(?im)^[ \t]*DC_(?:MODEL_API_KEY|API_TOKEN)[ \t]*=[ \t]*([^\r\n]*)", part):
            value = m.group(1).strip().strip(b"\"'")
            if value and not value.lower().startswith((b"replace-", b"replace_", b"your_")):
                problems.append("non-placeholder credential in configuration")
    return sorted(set(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="check all index entries (default)")
    mode.add_argument("--history", action="store_true", help="check all blobs reachable from all refs")
    args = parser.parse_args()
    entries = []
    if args.history:
        for row in git("rev-list", "--objects", "--all").splitlines():
            oid, _, raw_path = row.partition(b" ")
            if raw_path:
                entries.append((raw_path.decode("utf-8"), oid.decode()))
    else:
        entries = [(p.decode("utf-8"), ":" + p.decode("utf-8")) for p in git("ls-files", "-z").split(b"\0") if p]
    if not entries:
        print("No files to check.")
        return 1
    failures = []
    checked = 0
    for path, data in read_objects(entries):
        checked += 1
        problems = inspect(path, data)
        if problems:
            failures.append((path, problems))
    for path, problems in failures:
        print(path + ": " + "; ".join(problems))
    print(f"Checked {checked} files/blobs; {len(failures)} findings. Credential values are never printed.")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
