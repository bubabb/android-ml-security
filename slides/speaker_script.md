# CS 6491 Final Presentation — Speaking Script
**Security Analysis of ML-Powered Android Apps** — Bruna Vasconcelos
*Target: 15 minutes. Keyed to the 10-slide deck. `[beat]` = pause. Italicized lines are the defensive framing — slow down and land them.*

---

## [Slide 1 — Title]  (~30s)

Good afternoon. My name is Bruna Vasconcelos. This is my final project for CS 6491, Software and Systems Security — a static-analysis pipeline for auditing what I'll call the Truth-Serum training-surface precondition in Android applications.

The question is simple: when machine learning runs on a phone, is there a specific attack surface we can find without running the app? And can we audit for it at scale?

[beat]

---

## [Slide 2 — Motivation & Problem]  (~2 min)

The attack I care about is Truth Serum, published by Tramèr and colleagues at CCS 2022. The diagram on the left shows the four-step chain. Step one: the attacker contributes a small number of carefully crafted poisoned samples to a training set. Step two: the model trains on those samples together with the real data. Step three: after training, the poisoned samples exhibit anomalously high loss. Step four: by querying the trained model and watching for that loss signature, the adversary performs membership inference and extracts secrets from other people's training data — medical records, private messages, anything the model learned.

What makes Truth Serum dangerous is the precondition. The attacker doesn't need root on the device. They don't need network interception. They don't need code execution. They just need to contribute training data.

[beat]

On the right side of the slide, three examples of mobile apps where the precondition *can* be met. Keyboards — user input can feed into on-device personalization or federated learning; GBoard does exactly this in production. Translation apps — offline TFLite models whose local weights can update based on user corrections; Pixel's on-device translation is one example. Camera filters — on-device classification on personally identifiable imagery, where adaptation loops are architecturally possible.

*The point is not that every app on this slide runs Truth-Serum-vulnerable training. It's that the necessary architecture — user input reaching an on-device training or personalization loop — already exists at scale, and a static audit can tell us where.*

[beat]

Which gives the research question on the banner at the bottom. Does this training-surface precondition actually exist in real Android apps? And can a defender detect it with static analysis alone — no rooting, no instrumentation, no user interaction?

[beat]

---

## [Slide 3 — Related Work & Gap]  (~1.5 min)

Four prior tools, two pairs.

**FlowDroid**, published at PLDI 2014, does inter-procedural taint analysis on Android. It traces data from permission-guarded sources to network sinks. It is the mature gold standard — and it has no ML-specific sources or sinks. A TFLite interpreter is invisible to it.

**TaintDroid**, from OSDI 2010, is older still. OS-level dynamic taint tracking. Predates on-device ML entirely.

[beat]

On the mobile-ML-security side there are two key pieces of prior work.

**Xu and colleagues at WWW 2019** did the first empirical study of deep-learning apps on smartphones, across sixteen and a half thousand Play Store apps. They measured *presence* — how many apps embed ML models, which frameworks.

**Sun and colleagues at USENIX Security 2021**, with a tool called **ModelXRay**, did a large-scale study of model protection. They found that forty-one percent of ML apps ship entirely unprotected models, and that sixty-six percent of the apps that tried to protect their models were still extractable through lightweight dynamic analysis.

*I want to be explicit about how this project relates to Xu and Sun. Xu measures presence. Sun measures extraction. Neither measures the Truth-Serum precondition — whether user input is fed into on-device training. That is an orthogonal audit dimension, and it is the specific gap this project targets.* The bottom matrix names it: presence, protection, training surface — three dimensions; the first two are covered at scale; the third is what this work targets.

[beat]

---

## [Slide 4 — Threat Model]  (~1 min)

Four cards, left to right.

The **adversary** is a malicious training-data contributor. They can influence data ingested by an on-device training, personalization, or federated-learning loop. They do not need code execution, root, or network interception.

The **defender** is an app auditor. Access is limited to the publicly distributed APK — no running instance, no backend, no runtime instrumentation. Static analysis only. This matches the ModelXRay and FlowDroid setting.

Three **assets** are at risk: model weights (integrity and confidentiality), the training pipeline (integrity), and user PII routed through the ML loop. The severity driver is whether the training surface is present — presence raises the risk on all three assets.

**Out of scope**: runtime exploits in native libraries, supply-chain compromise, backend-service attacks, and device-side model replacement — which ModelXRay already covers.

[beat]

---

## [Slide 5 — Approach: Five-Stage Pipeline]  (~2 min)

The tool is a single Python file — about 460 lines — structured as five sequential stages.

**Stage one** unzips the APK and matches file extensions for common on-device model formats: tflite, onnx, protobuf, pickle, H5, Core ML, PyTorch. For every hit, Shannon entropy and magic-byte identification. A stock TFLite FlatBuffer has a known magic header and entropy around seven-point-four.

**Stage two** scans native libraries — the `.so` files — for the shared objects that ship with ML SDKs: libtensorflowlite, libonnxruntime, libmlkit, libmediapipe, libpytorch.

**Stage three** runs apktool to decode the APK into Smali bytecode and the manifest. Hardened with a three-hundred-second timeout and a path-prefix-checked `safe_rmtree` for cleanup.

**Stage four** parses the manifest with defusedxml — XXE-safe — and flags permissions that feed PII into ML: camera, microphone, location, SMS, external storage, internet.

**Stage five** is a pattern scan over Smali bytecode looking for ML-SDK imports and poisoning-surface signatures. A 5,000-file-per-app cap bounds runtime.

[beat]

The outputs are a per-app JSON with raw evidence, an aggregated CSV, and a severity rubric — Critical, High, Medium, Info — assigned per finding and propagated to app level.

[beat]

*The disclosure box at the bottom is important. Stage five is pattern-matching, not taint analysis. Full inter-procedural Soot and FlowDroid taint analysis was planned in the original proposal but deferred for scope reasons. The consequence: ProGuard or R8 obfuscation renames the exact class and method names my regexes target. On an obfuscated production app, stage five will likely miss the training surface. This is the dominant source of false-negative risk, and it's disclosed in the limitations.*

[beat]

---

## [Slide 6 — Data & Severity Rubric]  (~1 min)

On the left, the pilot corpus. Six APKs. Five F-Droid open-source applications — F-Droid itself, NewPipe, Aves Gallery, Nextcloud Files, and Fennec which is a Firefox fork — totaling 274 megabytes. Plus one synthetic positive control: Google's MobileNet v1 model packaged into an APK-shaped zip, 16.9 megabytes. F-Droid was chosen for the pilot because its licensing removes the legal friction of downloading Play Store APKs. SHA-256 prefixes are in the table for reproducibility.

On the right, the severity rubric as implemented in the tool. Four levels. Critical is reserved for active poisoning surface plus unauthenticated model-update channel — that's research-question-four territory, deferred from the pilot, and the tool cannot produce a Critical in its current form. High fires on an identified unencrypted model artifact or a poisoning-surface regex hit — this is what fires on the positive control. Medium is reserved for model files with ambiguous entropy — encryption and compression can't be distinguished from bytes alone — and it didn't fire on any app in the pilot corpus. Info fires when no on-device ML is detected; that's what all five F-Droid apps show.

*I want to be honest about this: the pilot produces only High and Info in practice. Critical requires detections I haven't built. Medium requires an entropy signal none of these apps trigger. Slide 9 and slide 10 return to this.*

[beat]

---

## [Slide 7 — Evaluation Design]  (~1.5 min)

Two-arm calibration.

On the left, the **positive control** — a synthetic APK containing an unobfuscated MobileNet v1 model. Expected to fire with High severity. It validates stage one: archive scan, file-extension match, magic bytes, entropy. *It explicitly does not validate stages two through five — the SDK scan, the Smali patterns, and the severity rubric for the training surface. Future work includes a ProGuard-obfuscated variant that exercises the remaining stages.*

On the right, the **null hypothesis** — five F-Droid apps. Expected to produce no detections, because open-source utility apps with reproducible builds don't bundle opaque ML. This validates the absence of spurious alarms at a clean baseline. *It does not validate detection on obfuscated Play Store targets. And the sample size — five apps — is roughly 0.1% of the F-Droid catalogue. This is pilot evidence, not a structural claim.*

[beat]

The banner at the bottom is the honest summary of what the two-arm design does and doesn't test. On the confirmed side: archive stage fires on a known model, the severity rubric assigns High to an exposed model, the null on a clean corpus produces no false-positive, and the pipeline is re-runnable end-to-end on new APKs. On the not-yet-validated side: the poisoning-surface regexes have never fired a true-positive on a real app, obfuscation resistance is untested, false-positive and false-negative rates can't be computed from n equals five, and there's no baseline comparison against MobSF or ModelXRay.

[beat]

---

## [Slide 8 — Results: Findings]  (~3 min)

Six APKs analysed. Two hundred ninety megabytes of corpus including the positive control. One of six contains on-device ML. Six findings logged in total.

On the per-app table. Let me walk from bottom to top because the interesting row is the positive control.

The **positive control** — the synthetic MobileNet APK — shows one model artifact detected, ML SDKs marked not-applicable because its apktool decode failed so Smali was never scanned, and a High severity finding. That's exactly what the rubric predicts for a visible unencrypted model. *Full disclosure: the detection here is purely from stage one. Stages two through five never ran on this APK because apktool couldn't decode the synthetic zip. The positive control validates archive stage only — which is the only part designed to see a model file in the assets directory.*

The **five F-Droid apps** — Aves, F-Droid itself, Fennec, NewPipe, Nextcloud — all show zero models, zero ML SDKs detected, and one Info finding apiece, which is the rubric's signal for "no on-device ML." The coverage column varies from ten to seventy-four percent. We'll unpack that on the next slide.

[beat]

The headline. *The detector fires on the positive control at High, and correctly produces null on all five F-Droid apps. This is the expected calibration result. The framing I'll insist on is this: a pilot observation on 0.1 percent of the F-Droid catalogue is not a structural claim about F-Droid. It's consistent with the hypothesis that reproducible open-source utility apps don't bundle opaque ML, but confirming that hypothesis requires a much larger sample.*

The target for the next iteration is Play Store apps in categories where on-device ML is known to be common — keyboards, translation, camera. Those are the categories where the Truth-Serum precondition is most likely to hold.

[beat]

---

## [Slide 9 — Smali Coverage: the dominant FN risk]  (~1 min)

The bar chart is the most important piece of evidence in the limitations story. It shows Smali scan coverage per app under the 5,000-file cap.

Nextcloud: ten percent. Fennec: fifteen percent. F-Droid itself: twenty-five percent. NewPipe: fifty-eight percent. Aves: seventy-four percent. The red bars are the worst — the three largest apps were scanned at only ten to twenty-five percent.

*What this means, honestly, is that I cannot rule out on-device ML in the seventy-five to ninety percent of Smali that was not scanned in those three apps. The null result I just reported on slide 8 is strong for Aves and NewPipe, and weaker for the larger apps.*

The fix is trivial. Either raise the cap — nine times the current budget reaches full coverage on Nextcloud — or implement priority-ordered scanning, where ML-SDK-import-carrying files are scanned first. Either is a few lines of code. Neither is in tonight's build. This is the single highest-impact engineering change for the next iteration.

[beat]

---

## [Slide 10 — Limitations & Conclusion]  (~1.5 min)

The limitations list on the left is complete and disclosed up front. Six items. Pattern-match rather than taint analysis. N equals five. The positive control validates only stage one. No baseline comparison run. No manual jadx-based ground truth. And the Smali cap we just discussed.

On the right, six concrete next steps, ordered by impact. A hundred-app Play Store corpus in keyboard, translation, and camera categories. An obfuscation-resistant detector anchored on native-library presence and string-constant extraction rather than literal regex matches. A MobSF and ModelXRay baseline run on the same corpus. Manual spot-checking in jadx on a sample for ground-truth anchoring. Raising the Smali cap. And a ProGuard-obfuscated positive control to exercise the later pipeline stages.

[beat]

The take-away strip is the single sentence I'd ask you to remember: **the tool is calibrated, and the science starts at the Play Store corpus.** The pilot demonstrates that the detection chain works end-to-end on a known positive and doesn't raise spurious alarms on clean apps. What it does *not* demonstrate — training-surface detection on obfuscated production apps — is what the next iteration is for.

The contributions bar names three items. One: a static-analysis pipeline targeting the training-surface gap that sits orthogonal to Xu 2019 and Sun 2021. Two: a severity-rubric *design* tied to the Truth-Serum precondition — High and Info demonstrated in the pilot, Critical and Medium reserved for detectors in future work. Three: a calibrated pilot with documented coverage gaps.

Reproducibility is in the footer. One command runs the whole pipeline; SHA-256 APK prefixes are on slide 6; raw JSON and CSV evidence ships with the submission.

[beat]

Thank you. I'm happy to take questions.

---

## Timing guide

| Slide | Topic | Budget | Running |
|---|---|---|---|
| 1 | Title + hook | 0:30 | 0:30 |
| 2 | Motivation + research question | 2:00 | 2:30 |
| 3 | Related work + gap matrix | 1:30 | 4:00 |
| 4 | Threat model | 1:00 | 5:00 |
| 5 | Approach + five-stage pipeline | 2:00 | 7:00 |
| 6 | Data + severity rubric | 1:00 | 8:00 |
| 7 | Evaluation design | 1:30 | 9:30 |
| 8 | Results: findings | 3:00 | 12:30 |
| 9 | Coverage chart | 1:00 | 13:30 |
| 10 | Limitations + next steps + contributions | 1:30 | 15:00 |

## Q&A landmines — rehearse these

**"Would your tool detect federated learning in Gboard?"**
Not reliably. GBoard is heavily obfuscated — ProGuard renames the exact class names my regexes target. The tool would flag the RECORD_AUDIO permission and the `libtensorflowlite.so` native library, but would not identify the federated-learning training surface from bytecode alone. Obfuscation-resistant detection is on the next-steps list.

**"How is this different from ModelXRay?"**
ModelXRay audits model protection — can the model be stolen? This audits the training surface — is user input fed into training? Adjacent threat vectors, orthogonal audit dimensions. The slide-3 matrix shows this explicitly.

**"What's your false-positive and false-negative rate?"**
Undefined with n equals five. A labeled ground-truth corpus of a hundred-plus apps is on the future-work list for exactly this reason.

**"Did you manually verify the null results?"**
No. Manual jadx-based spot-checking of five or ten apps is on the next-steps list. At pilot scale, the positive control is the primary calibration against tool error.

**"Why F-Droid, when F-Droid's open-source mandate arguably selects against on-device ML?"**
F-Droid was chosen for legal friction, not threat-model fit. The null is calibration — showing the tool doesn't raise spurious alarms on a clean corpus. The actual target is Play Store apps in keyboard, translation, and camera categories.

**"Isn't the positive control trivially weak — a tflite file in a zip?"**
Yes, it validates only stage one of the five-stage pipeline. A stronger positive control — a ProGuard-obfuscated synthetic APK with realistic class and method names — is on the next-steps list. This is the single weakest part of the evaluation design as it stands.

**"Why is the positive control not Critical?"**
Critical requires an active poisoning surface plus unauthenticated model update. The positive control demonstrates model exposure — unencrypted weights readable by anyone with unzip — which the rubric correctly classifies as High, not Critical.

**"What does your tool actually contribute that doesn't already exist?"**
The training-surface audit dimension and its severity rubric. Presence (Xu) and extraction (Sun) are measured at scale already; the Truth-Serum precondition is not. The tool itself is modest — 460 lines of Python — but it sits in an audit niche nothing currently fills.

## If you run long
Trim the stage-by-stage walkthrough on slide 5 (say "five stages, honest scope disclosure: stage five is pattern-matching, not taint analysis" and move on). Saves 45 seconds. Also: shorten slide 8 to just the headline banner if needed.

## If you run short
Add a concrete Truth-Serum example on slide 2: a keyboard app's training data contains a user's poisoned passphrase; after training, the attacker queries the model and extracts the membership signal on the passphrase. That's 45 seconds.
