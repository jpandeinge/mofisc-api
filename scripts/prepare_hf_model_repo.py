#!/usr/bin/env python3
"""Build an allowlisted, checksum-verified Hugging Face model repository.

This script intentionally prepares files only. It does not authenticate,
create a remote repository, or upload anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


MODEL_FILES = (
    "category_head.joblib",
    "label_classes.json",
    "merchant_index.npz",
    "metrics.json",
    "model_manifest.json",
    "encoder/1_Pooling/config.json",
    "encoder/config.json",
    "encoder/config_sentence_transformers.json",
    "encoder/model.safetensors",
    "encoder/modules.json",
    "encoder/sentence_bert_config.json",
    "encoder/special_tokens_map.json",
    "encoder/tokenizer.json",
    "encoder/tokenizer_config.json",
    "encoder/vocab.txt",
)

DEPLOYMENT_FILES = (
    ".gitattributes",
    "handler.py",
    "README.md",
    "requirements.txt",
)

RECORDED_CHECKSUMS = {
    "encoder_sha256": "encoder/model.safetensors",
    "category_head_sha256": "category_head.joblib",
    "merchant_index_sha256": "merchant_index.npz",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_source(model_dir: Path) -> dict:
    missing = [
        relative_path
        for relative_path in MODEL_FILES
        if not (model_dir / relative_path).is_file()
    ]
    if missing:
        raise ValueError(f"model source is incomplete; missing: {', '.join(missing)}")

    manifest_path = model_dir / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for manifest_key, relative_path in RECORDED_CHECKSUMS.items():
        expected = manifest.get(manifest_key)
        if not expected:
            raise ValueError(f"model manifest is missing {manifest_key}")
        actual = sha256(model_dir / relative_path)
        if actual != expected:
            raise ValueError(
                f"checksum mismatch for {relative_path}: "
                f"expected {expected}, got {actual}"
            )
    return manifest


def ensure_empty_output(output_dir: Path) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(
            f"output directory must not contain files: {output_dir}. "
            "Choose a new directory so an older bundle cannot contaminate the upload."
        )
    output_dir.mkdir(parents=True, exist_ok=True)


def copy_relative(source_root: Path, output_dir: Path, relative_path: str) -> None:
    source = source_root / relative_path
    if not source.is_file():
        raise ValueError(f"deployment file is missing: {source}")
    destination = output_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_bundle(model_dir: Path, deployment_dir: Path, output_dir: Path) -> dict:
    model_manifest = verify_model_source(model_dir)
    ensure_empty_output(output_dir)

    for relative_path in MODEL_FILES:
        copy_relative(model_dir, output_dir, relative_path)
    for relative_path in DEPLOYMENT_FILES:
        copy_relative(deployment_dir, output_dir, relative_path)

    # SentenceTransformer's Normalize module has no configuration file, but a
    # marker keeps its module directory present in Git/Xet checkouts.
    normalize_marker = output_dir / "encoder/2_Normalize/.gitkeep"
    normalize_marker.parent.mkdir(parents=True, exist_ok=True)
    normalize_marker.touch()

    bundle_files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    upload_manifest = {
        "model_version": model_manifest.get("model_version"),
        "taxonomy": model_manifest.get("internal_taxonomy_version"),
        "files": [
            {
                "path": str(path.relative_to(output_dir)),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in bundle_files
        ],
    }
    manifest_path = output_dir / "HF_UPLOAD_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(upload_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return upload_manifest


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=repo_root / "api/app/resources/models/pfc_v4",
        help="verified local model artifact directory",
    )
    parser.add_argument(
        "--deployment-dir",
        type=Path,
        default=repo_root / "deploy/huggingface/pfc_v4",
        help="custom handler and Hugging Face metadata directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new or empty directory to prepare for upload",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_bundle(
        args.model_dir.resolve(),
        args.deployment_dir.resolve(),
        args.output.resolve(),
    )
    total_bytes = sum(file_info["bytes"] for file_info in manifest["files"])
    print(f"Prepared {len(manifest['files']) + 1} files in {args.output.resolve()}")
    print(f"Model version: {manifest['model_version']}")
    print(f"Bundle bytes (before manifest): {total_bytes}")
    print("No files were uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
