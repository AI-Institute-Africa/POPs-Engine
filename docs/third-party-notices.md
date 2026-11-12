# Third-party notices and what was borrowed

The ID/KYC module (`backend/app/kyc`) was designed after reviewing the projects below. **No source
code was copied from any of them.** Approaches that come from MIT-licensed projects were
reimplemented in this codebase's own style, and are credited here as a courtesy and in the
relevant module docstrings. Projects without a clear open licence, or with a commercial or GPL
licence, contributed ideas only, or nothing.

| Project | Licence | What it contributed | Where |
| --- | --- | --- | --- |
| [Final-Project-OCR](https://github.com/TomerGibor/Final-Project-OCR) (TomerGibor) | MIT | Approach to finding a page or card: probabilistic Hough lines, outermost horizontal and vertical lines, their four intersections, perspective warp. Its EMNIST character classifier was **not** used (it reads lowercase letters and digits only, and is not suited to IDs). | `kyc/doc/detect.py` |
| [image_text_reader](https://github.com/yardstick17/image_text_reader) (yardstick17) | MIT | Approach to preparing an image for Tesseract: bring it to a usable DPI, remove noise, threshold, then OCR. | `kyc/readers.py` (`for_id_ocr`) |
| [DocFace](https://github.com/seasonSH/DocFace) (seasonSH) | MIT | The framing that ID-photo-to-selfie matching is its own problem and should be a calibrated threshold on an embedding similarity. Its TensorFlow 1.x, MATLAB and Caffe stack was not used; the matcher runs any ONNX embedding model instead. No weights are bundled. | `kyc/cv/face.py` |
| [ID-Verification-OpenKYC](https://github.com/FaceOnLive/ID-Verification-OpenKYC) (FaceOnLive) | not stated on the repository page | Workflow ideas only: capture, document recognition, document and face liveness, face-to-ID match, webhooks, role-based review. | design of `docs/kyc.md` |
| [ID-Card-Passport-Recognition-Android](https://github.com/MiniAiLive/ID-Card-Passport-Recognition-Android) (MiniAiLive) | proprietary, commercial SDK | Feature checklist only (document classification, MRZ, portrait extraction, hologram and NFC checks as possible later steps). | none |
| [easyVision](https://github.com/albertoruiz/easyVision) (albertoruiz) | not stated | Nothing: a Haskell research framework with obsolete native dependencies. | none |
| [ubi_reader](https://github.com/onekey-sec/ubi_reader) (onekey-sec) | GPL-3.0 | Nothing: it extracts flash-image filesystems and has no relation to document recognition. Deliberately not vendored. | none |

## Runtime dependencies added

- `onnxruntime` (MIT) for CPU inference of the face model; optional (`pip install .[kyc]`).
- Existing dependencies are reused: OpenCV, Pillow, Tesseract via `pytesseract`, `imagehash`.

## Model weights

None are shipped. Face-embedding and detector models are operator-supplied
(`POPS_KYC_FACE_MODEL_PATH`, `POPS_KYC_FACE_DETECTOR_PATH`). Check the licence of any model you use:
several widely used pretrained face models are released for non-commercial research only, and the
public DocFace base model was trained on a dataset with its own terms.

## MIT licence text (for the credited projects)

The MIT licence requires that its copyright notice and permission notice accompany substantial
portions of the software. Because no code was copied, none is required here; the licence text of
each credited project is in its own repository.
