#!/usr/bin/env python3
"""ML-Powered Android App security analysis pipeline.

Static analysis for on-device ML vulnerabilities.

Phases per APK:
  1. SHA-256 hash the APK (reproducibility)
  2. Archive scan for ML artifacts (.tflite, .onnx, .pb, .pt, .pkl, .h5,
     .mlmodel, .pth, .caffemodel) — entropy + magic-byte identification
  3. Native-library scan (libtensorflowlite*.so, libonnxruntime*.so, ...)
  4. apktool decode (with timeout) -> Smali + AndroidManifest.xml
  5. Parse AndroidManifest (defusedxml): package + sensitive permissions
  6. Smali pattern scan: ML-SDK imports + poisoning-surface signatures
  7. Severity scoring (Critical / High / Medium / Info)
  8. Write per-app JSON + aggregated CSV

Requires: Python 3.10+, apktool on PATH, Python `defusedxml` package.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as ET


# --------------------------------------------------------------------------- #
# Artifact detection
# --------------------------------------------------------------------------- #

MODEL_EXTS = {
    ".tflite", ".onnx", ".pb", ".pt", ".pkl",
    ".h5", ".mlmodel", ".pth", ".caffemodel",
}

# Magic bytes for common formats
MAGIC = {
    b"TFL3": "tflite",
    b"ONNX": "onnx",
    b"\x93NUMPY": "numpy",
    b"PK\x03\x04": "zip_container",
}

ML_NATIVE_LIB_PATTERNS = re.compile(
    r"lib(tensorflowlite|tflite|onnxruntime|mediapipe|ml_?kit|pytorch|torch)[^/]*\.so$",
    re.IGNORECASE,
)

# Smali is case-sensitive — no IGNORECASE needed.
ML_SDK_SMALI_PATTERNS = [
    (re.compile(r"org/tensorflow/lite"), "TensorFlow Lite SDK"),
    (re.compile(r"com/google/mlkit"), "Google ML Kit SDK"),
    (re.compile(r"ai/onnxruntime"), "ONNX Runtime SDK"),
    (re.compile(r"com/google/mediapipe"), "MediaPipe SDK"),
    (re.compile(r"org/pytorch"), "PyTorch Mobile SDK"),
]

POISONING_SURFACE_PATTERNS = [
    (re.compile(r"Interpreter;.*->.*runForMultipleInputsOutputs"),
     "Multi-output TFLite inference call (confirms on-device ML; not evidence of training)"),
    (re.compile(r"\bfederated(Learning|Averaging)?\b", re.IGNORECASE),
     "Federated-learning reference"),
    (re.compile(r"(modelPersonalizer|PersonalizationModel|OnDeviceCustomActions)"),
     "TFLite Model Personalization API"),
    (re.compile(r"\bfeedback\b.*\b(re)?train\b", re.IGNORECASE),
     "User-feedback retraining reference"),
    (re.compile(r"differential(Privacy|_privacy|PrivateSGD)", re.IGNORECASE),
     "Differential-privacy API (defensive signal)"),
]
# Note: removed a TensorFlow Python-API regex (GradientTape / keras/.../fit) —
# those are desktop/server TF APIs that do not appear in Android Smali bytecode.
# Android on-device training via TFLite uses narrow signature-runner APIs that
# are not well-captured by a literal string match; deferred to future work.

SENSITIVE_PERMISSIONS = {
    "android.permission.CAMERA": "Visual PII input to ML",
    "android.permission.RECORD_AUDIO": "Audio PII input to ML",
    "android.permission.ACCESS_FINE_LOCATION": "Location PII",
    "android.permission.READ_CONTACTS": "Contact PII",
    "android.permission.READ_SMS": "Message content PII",
    "android.permission.READ_EXTERNAL_STORAGE": "File access (may include model caches)",
    "android.permission.WRITE_EXTERNAL_STORAGE": "Model persistence to external storage",
    "android.permission.INTERNET": "Network (may be used for model updates)",
}

# Runtime caps
APKTOOL_TIMEOUT_SEC = 300
SMALI_SCAN_CAP = 5000            # files per APK
ENTROPY_HIGH_THRESHOLD = 7.9     # compressed OR encrypted


# --------------------------------------------------------------------------- #
# Severity rubric
# --------------------------------------------------------------------------- #

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Info": 3}


@dataclass
class Finding:
    category: str
    severity: str
    title: str
    detail: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class AppReport:
    package: str
    apk_path: str
    apk_size_bytes: int
    apk_sha256: str
    ml_artifacts: list[dict] = field(default_factory=list)
    ml_native_libs: list[str] = field(default_factory=list)
    ml_sdks: list[str] = field(default_factory=list)
    sensitive_permissions: list[dict] = field(default_factory=list)
    poisoning_surfaces: list[dict] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    smali_files_scanned: int = 0
    smali_files_total: int = 0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def sh(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=timeout
    )


def sha256_file(path: Path, chunk: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def shannon_entropy(data: bytes, sample: int = 65536) -> float:
    """Shannon entropy in bits/byte. ~8 => compressed or encrypted (indistinguishable
    by this heuristic); <5 => structured."""
    if not data:
        return 0.0
    if len(data) > sample:
        data = data[:sample]
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    total = len(data)
    entropy = 0.0
    for c in counts:
        if c:
            p = c / total
            entropy -= p * math.log2(p)
    return entropy


def identify_by_magic(data: bytes) -> str | None:
    for magic, name in MAGIC.items():
        if data.startswith(magic):
            return name
    # tflite files: 4-byte flatbuffer header followed by "TFL3" at offset 4
    if len(data) >= 8 and data[4:8] == b"TFL3":
        return "tflite"
    return None


def safe_rmtree(path: Path, allowed_root: Path) -> None:
    """Delete `path` only if it is strictly under `allowed_root`.

    Prevents a misconfigured --decoded from destroying user data.
    """
    path = path.resolve()
    allowed_root = allowed_root.resolve()
    if not str(path).startswith(str(allowed_root) + os.sep):
        raise RuntimeError(
            f"refusing to rmtree {path}: not under allowed_root={allowed_root}"
        )
    if path.exists():
        shutil.rmtree(path)


# --------------------------------------------------------------------------- #
# Analysis phases
# --------------------------------------------------------------------------- #


def scan_archive_entries(apk_path: Path, report: AppReport) -> None:
    """Fast pre-scan: find model files + native libs without full decode."""
    with zipfile.ZipFile(apk_path) as zf:
        for info in zf.infolist():
            name = info.filename
            ext = Path(name).suffix.lower()

            if ext in MODEL_EXTS:
                try:
                    with zf.open(info) as f:
                        data = f.read(65536)
                    entropy = shannon_entropy(data)
                    magic = identify_by_magic(data)
                    report.ml_artifacts.append({
                        "path": name,
                        "size": info.file_size,
                        "entropy": round(entropy, 3),
                        "magic": magic,
                        "high_entropy": entropy > ENTROPY_HIGH_THRESHOLD,
                        "high_entropy_note": (
                            "High entropy is consistent with encryption OR "
                            "compression; the two are indistinguishable from "
                            "a file's bytes alone."
                        ),
                        "format_identified": magic is not None,
                    })
                except Exception as e:
                    report.notes.append(f"error reading {name}: {e}")

            if ML_NATIVE_LIB_PATTERNS.search(name):
                report.ml_native_libs.append(name)


def run_apktool(
    apk_path: Path, out_dir: Path, allowed_root: Path
) -> Path | None:
    decoded = out_dir / apk_path.stem
    try:
        safe_rmtree(decoded, allowed_root=allowed_root)
    except RuntimeError as e:
        print(f"[{apk_path.name}] rmtree refused: {e}", file=sys.stderr)
        return None
    try:
        res = sh(
            ["apktool", "d", "-f", "-q", "-o", str(decoded), str(apk_path)],
            timeout=APKTOOL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[{apk_path.name}] apktool timed out after {APKTOOL_TIMEOUT_SEC}s",
            file=sys.stderr,
        )
        return None
    if res.returncode != 0:
        return None
    return decoded


def parse_manifest(decoded_dir: Path, report: AppReport) -> None:
    """Parse AndroidManifest.xml via defusedxml (resists XXE and entity-expansion)."""
    manifest = decoded_dir / "AndroidManifest.xml"
    if not manifest.exists():
        report.notes.append("AndroidManifest.xml missing from decoded output")
        return
    try:
        tree = ET.parse(str(manifest))
    except ET.ParseError as e:
        report.notes.append(f"manifest parse error: {e}")
        return
    root = tree.getroot()
    pkg = root.attrib.get("package", "")
    report.package = pkg

    ns = "{http://schemas.android.com/apk/res/android}"
    declared = set()
    for perm in root.iter("uses-permission"):
        name = perm.attrib.get(f"{ns}name", "")
        if name:
            declared.add(name)
    for name in declared:
        if name in SENSITIVE_PERMISSIONS:
            report.sensitive_permissions.append({
                "permission": name,
                "relevance": SENSITIVE_PERMISSIONS[name],
            })


def scan_smali(decoded_dir: Path, report: AppReport) -> None:
    """Grep Smali for ML-SDK usage and poisoning-surface signatures.

    Caps at SMALI_SCAN_CAP files to bound runtime. Logs a warning when the
    cap truncates the scan (silent truncation would mask false negatives).
    """
    smali_files: list[Path] = []
    for d in decoded_dir.glob("smali*"):
        if d.is_dir():
            smali_files.extend(d.rglob("*.smali"))
    report.smali_files_total = len(smali_files)

    # Sort to make scan deterministic (reproducibility)
    smali_files.sort()
    if len(smali_files) > SMALI_SCAN_CAP:
        report.notes.append(
            f"smali scan truncated: {len(smali_files)} files found, "
            f"scanning first {SMALI_SCAN_CAP}. Increase SMALI_SCAN_CAP for "
            f"full coverage on large apps."
        )
        smali_files = smali_files[:SMALI_SCAN_CAP]
    report.smali_files_scanned = len(smali_files)

    sdk_hits: set[str] = set()
    surfaces: list[dict[str, str]] = []

    for path in smali_files:
        try:
            text = path.read_text(errors="replace")
        except Exception:
            continue
        for pat, label in ML_SDK_SMALI_PATTERNS:
            if pat.search(text):
                sdk_hits.add(label)
        for pat, label in POISONING_SURFACE_PATTERNS:
            m = pat.search(text)
            if m:
                surfaces.append({
                    "pattern": label,
                    "file": str(path.relative_to(decoded_dir)),
                    "match": m.group(0)[:120],
                })

    report.ml_sdks.extend(sorted(sdk_hits))
    report.poisoning_surfaces.extend(surfaces)


# --------------------------------------------------------------------------- #
# Severity scoring
# --------------------------------------------------------------------------- #


def score_findings(report: AppReport) -> None:
    """Apply the severity rubric from the proposal."""
    for art in report.ml_artifacts:
        if art.get("format_identified") and not art.get("high_entropy"):
            sev = "High"
            detail = (
                f"Model file present in APK with identifiable format "
                f"('{art['magic']}'). Unencrypted; architecture and weights are "
                "readable by anyone who can unzip the APK. No integrity check "
                "is enforced by the APK loader."
            )
        elif art.get("high_entropy"):
            sev = "Medium"
            detail = (
                f"Model file shows high entropy ({art['entropy']}). This is "
                "consistent with encryption, but also with compression — the "
                "two cannot be distinguished from bytes alone. Manual "
                "verification needed before claiming confidentiality."
            )
        else:
            sev = "Medium"
            detail = (
                "Model-extension file present but format not matched by known "
                "magic bytes. Manual inspection recommended."
            )
        report.findings.append(Finding(
            category="Model Exposure",
            severity=sev,
            title=f"Model artifact at {art['path']}",
            detail=detail,
            evidence=[f"size={art['size']} bytes", f"entropy={art['entropy']}"],
        ))

    for s in report.poisoning_surfaces:
        report.findings.append(Finding(
            category="Poisoning Surface",
            severity="High",
            title=s["pattern"],
            detail=(
                "Code pattern consistent with on-device training / "
                "user-feedback retraining. This is the attack-surface shape "
                "Truth Serum exploits."
            ),
            evidence=[s["file"], s["match"]],
        ))

    if report.ml_native_libs and not report.ml_artifacts:
        report.findings.append(Finding(
            category="Model Exposure",
            severity="Info",
            title="ML native libraries present, no local model files",
            detail=(
                "App links ML inference libraries but bundles no model assets. "
                "Models are likely downloaded at runtime — check update channel."
            ),
            evidence=report.ml_native_libs[:3],
        ))

    if not report.ml_artifacts and not report.ml_native_libs and not report.ml_sdks:
        report.findings.append(Finding(
            category="Model Exposure",
            severity="Info",
            title="No on-device ML artifacts detected",
            detail=(
                "App contains no model files, ML native libraries, or ML-SDK "
                "Smali references. Valid negative finding under the stated "
                "threat model — app is outside the Truth Serum attack surface."
            ),
        ))

    report.findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 99))


# --------------------------------------------------------------------------- #
# Pipeline driver
# --------------------------------------------------------------------------- #


def analyze_apk(
    apk_path: Path, decoded_root: Path, quick: bool = False
) -> AppReport:
    report = AppReport(
        package="",
        apk_path=str(apk_path),
        apk_size_bytes=apk_path.stat().st_size,
        apk_sha256=sha256_file(apk_path),
    )
    print(f"[{apk_path.name}] sha256={report.apk_sha256[:16]}...")
    print(f"[{apk_path.name}] scanning archive for ML artifacts...")
    scan_archive_entries(apk_path, report)

    if not quick:
        print(f"[{apk_path.name}] apktool decode (timeout {APKTOOL_TIMEOUT_SEC}s)...")
        decoded = run_apktool(apk_path, decoded_root, allowed_root=decoded_root)
        if decoded is None:
            report.notes.append("apktool decode failed or timed out")
        else:
            print(f"[{apk_path.name}] parsing manifest + scanning Smali...")
            parse_manifest(decoded, report)
            scan_smali(decoded, report)

    score_findings(report)
    return report


def write_outputs(reports: list[AppReport], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    for r in reports:
        pkg = r.package or Path(r.apk_path).stem
        (out_dir / f"{pkg}.json").write_text(json.dumps(r.to_json(), indent=2))

    with (out_dir / "summary.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "apk", "package", "sha256_prefix", "apk_size_mb",
            "smali_scanned", "smali_total",
            "n_model_artifacts", "n_native_libs", "ml_sdks",
            "n_sensitive_perms", "n_poisoning_surfaces",
            "critical", "high", "medium", "info",
            "top_finding",
        ])
        for r in reports:
            sev_counts = {"Critical": 0, "High": 0, "Medium": 0, "Info": 0}
            for fnd in r.findings:
                sev_counts[fnd.severity] = sev_counts.get(fnd.severity, 0) + 1
            top = r.findings[0].title if r.findings else ""
            w.writerow([
                Path(r.apk_path).name,
                r.package,
                r.apk_sha256[:16],
                round(r.apk_size_bytes / 1_000_000, 1),
                r.smali_files_scanned,
                r.smali_files_total,
                len(r.ml_artifacts),
                len(r.ml_native_libs),
                ";".join(r.ml_sdks),
                len(r.sensitive_permissions),
                len(r.poisoning_surfaces),
                sev_counts["Critical"],
                sev_counts["High"],
                sev_counts["Medium"],
                sev_counts["Info"],
                top,
            ])
    print(f"wrote {out_dir / 'summary.csv'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk", type=Path, help="Single APK to analyze")
    ap.add_argument("--apk-dir", type=Path, help="Directory of APKs")
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--decoded", type=Path, default=Path("decompiled"))
    ap.add_argument("--quick", action="store_true",
                    help="Archive-only scan (no apktool)")
    args = ap.parse_args()

    apks: list[Path] = []
    if args.apk:
        apks = [args.apk]
    elif args.apk_dir:
        apks = sorted(args.apk_dir.glob("*.apk"))
    else:
        ap.error("either --apk or --apk-dir is required")

    args.out.mkdir(parents=True, exist_ok=True)
    args.decoded.mkdir(parents=True, exist_ok=True)

    reports = []
    for apk in apks:
        report = analyze_apk(apk, args.decoded, quick=args.quick)
        reports.append(report)
        print(
            f"[{apk.name}] done: {len(report.findings)} findings, "
            f"{len(report.ml_artifacts)} models, "
            f"{len(report.poisoning_surfaces)} poisoning surfaces"
        )

    write_outputs(reports, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
