# ML-Powered Android App Security Analysis

**CS 6491 — Software & Systems Security, Spring 2026 — University of Utah**
Bruna Vasconcelos

Static-analysis pipeline that detects on-device ML vulnerabilities in
Android APKs. Built in a one-day pilot sprint on 2026-04-20.

## What this is

The Truth Serum attack (Tramèr et al., CCS 2022) poisons training data to
amplify membership inference. It presumes the attacker can contribute to
training — a condition that exists wherever an app collects user input for
on-device retraining, personalization, or federated learning. No prior
Android-security tool audits apps for that condition. This project builds
one.

## Deliverables

| File | What it is |
|---|---|
| `pipeline/pipeline.py` | The static-analysis tool (one Python file, ~460 lines) |
| `slides/ml_android_security_final.pptx` | 10-slide deck with speaker notes |
| `report/report.pdf` | 4-page IEEE conference paper (LaTeX source in `report/latex/report.tex`) |
| `results/summary.csv` | Aggregated findings across the pilot corpus |
| `results/*.json` | Per-app full evidence (one JSON per APK) |
| `apks/*.apk` | Pilot corpus (five F-Droid apps, 274 MB) |
| `decompiled/` | apktool output (created on first run; large) |

## Usage

```bash
# Analyze every APK in apks/
python3 pipeline/pipeline.py --apk-dir apks/ --out results/

# Or a single APK
python3 pipeline/pipeline.py --apk apks/fennec.apk --out results/

# Fast archive-only scan (skip apktool decode)
python3 pipeline/pipeline.py --apk-dir apks/ --out results/ --quick
```

## What the tool does

1. **Archive scan** — unzip the APK and match file extensions in
   `{.tflite, .onnx, .pb, .pt, .pkl, .h5, .mlmodel, .pth, .caffemodel}`.
   For each hit: Shannon entropy + magic-byte identification to detect
   encrypted vs cleartext models.
2. **Native-library scan** — regex over archive entries for
   `libtensorflowlite*.so`, `libonnxruntime*.so`, `libmlkit*.so`,
   `libmediapipe*.so`, `libpytorch*.so`.
3. **apktool decode** — produces Smali + `AndroidManifest.xml` + `assets/`.
4. **Manifest parse** — flags permissions relevant to ML-data collection
   (CAMERA, RECORD_AUDIO, ACCESS_FINE_LOCATION, READ_CONTACTS, READ_SMS,
   READ/WRITE_EXTERNAL_STORAGE, INTERNET).
5. **Smali pattern scan** — detects ML-SDK use
   (`org/tensorflow/lite`, `com/google/mlkit`, `ai/onnxruntime`,
   `com/google/mediapipe`, `org/pytorch`) and poisoning-surface
   signatures (`modelPersonalizer`, federated-learning references,
   user-feedback retraining).
6. **Severity scoring** — Critical / High / Medium / Info per finding,
   aggregated per app.

## Key finding

**F-Droid's open-source mandate is itself a structural defense against
Truth Serum.** None of the five analyzed apps carry on-device ML. A Play
Store corpus is the appropriate next target.

## Dependencies

- Python 3.10+
- `apktool` (tested on 2.7.0)
- `jadx` (tested on 1.5.2) — currently not invoked by the pipeline but
  available for manual inspection of `decompiled/*/`
- Python packages: `python-pptx`, `pandas` (only for the slide-generation
  script in `slides/update_slides.py`)

## What was cut from the proposal for the 1-day pilot

- FlowDroid taint analysis (requires extensive configuration)
- Frida dynamic analysis (requires APK repackaging, ~2 days/batch)
- Corpus reduced from 30+ Play Store apps to 5 F-Droid apps
- Cosine-similarity-based duplicate detection (Antidote integration)

## Directory layout

```
/home/sudosu/cs6491/project/
├── README.md             this file
├── apks/                 pilot corpus (5 APKs, 274 MB)
├── decompiled/           apktool output (created on first run)
├── pipeline/
│   └── pipeline.py       the tool
├── results/
│   ├── summary.csv       aggregated per-app findings
│   └── *.json            full evidence per app
├── slides/
│   ├── ml_android_security_final.pptx   10-slide deck with notes
│   └── update_slides.py  regenerates the deck from pipeline output
├── report/
│   ├── report.md         source
│   └── report.pdf        rendered
└── logs/                 pipeline runtime logs
```

## References

- Tramèr et al., *Truth Serum: Poisoning Machine Learning Models to Reveal
  Their Secrets*, ACM CCS 2022
- Arzt et al., *FlowDroid: Precise Context, Flow, Field, Object-sensitive
  and Lifecycle-aware Taint Analysis for Android Apps*, PLDI 2014
- Enck et al., *TaintDroid*, OSDI 2010
