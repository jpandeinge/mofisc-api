"""Fail CI when a dependency input changes without a matching compiled lock."""

from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
LOCKS = (
    ("requirements.txt", "requirements.lock"),
    ("deploy/huggingface/pfc_v4/requirements.in", "deploy/huggingface/pfc_v4/requirements.txt"),
    ("ml_model/requirements.in", "ml_model/requirements.txt"),
    ("ml_model/requirements-pragma.txt", "ml_model/requirements-pragma.lock"),
)


def requirements(path):
    for line in path.read_text().splitlines():
        line = line.partition("#")[0].strip().removesuffix("\\").strip()
        if line and not line.startswith("-"):
            yield Requirement(line)


def main():
    failures = []
    for source, lock in LOCKS:
        locked = {}
        for requirement in requirements(ROOT / lock):
            locked.setdefault(canonicalize_name(requirement.name), []).append(requirement)
        for requested in requirements(ROOT / source):
            candidates = locked.get(canonicalize_name(requested.name), [])
            if not candidates:
                failures.append(f"{lock}: missing {requested.name}")
            for candidate in candidates:
                versions = [pin.version for pin in candidate.specifier if pin.operator == "=="]
                if not versions or any(not requested.specifier.contains(version, prereleases=True) for version in versions):
                    failures.append(f"{lock}: {candidate} does not satisfy {requested}")
    if failures:
        raise SystemExit("\n".join(failures) + "\nRegenerate the affected lock; see SECURITY_MAINTENANCE.md.")
    print("All dependency inputs match their compiled locks")


if __name__ == "__main__":
    main()
