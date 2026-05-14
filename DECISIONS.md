# Decision Log

## Ali Asfahani: Classifier Decisions

### Use ConvNeXt Tiny From Torchvision

Decision: use `torchvision.models.convnext_tiny` with
`ConvNeXt_Tiny_Weights.DEFAULT`.

Reasoning: the project brief requires pretrained backbones from
`torchvision.models` and allows ConvNeXt Tiny or Small. ConvNeXt Tiny is much
lighter than ConvNeXt Small, produces an artifact around 111 MB, and is more
realistic for the required CPU inference budget. It also fits better in Git LFS
and in the local worker container.

Consequence: accuracy may be lower than a larger backbone, but the runtime cost
and artifact size are easier for the full compose stack to handle.

### Classify Layout, Not Text

Decision: do not use OCR.

Reasoning: RVL-CDIP is a document layout classification task and the assignment
explicitly says to classify visual layout, not document text. All images are
loaded as TIFFs, converted to RGB, resized, normalized, and passed directly to
ConvNeXt.

Consequence: model predictions are based on document structure, spacing, and
visual patterns. Text content is ignored.

### Train In Colab And Ship Only Artifacts

Decision: training, evaluation, and golden-set selection happen in Colab. The
repo receives only the generated artifacts.

Reasoning: the full RVL-CDIP archive is large and the local docker-compose stack
must not train or see the full dataset. Colab provides GPU access and temporary
disk, while Google Drive stores the dataset files and generated outputs.

Consequence: the local app only needs to load `classifier.pt`, validate it, and
run inference. The repo stays small enough except for the model artifact, which
is stored through Git LFS.

### Use A Balanced 100k Run For The Current Artifact

Decision: the current classifier artifact is from a balanced 100k-image run, not
the full RVL-CDIP evaluation.

Reasoning: the full archive and full test-set evaluation are expensive in Colab
time and disk. A balanced 100k run still exercises the full training pipeline,
covers every class evenly, and gives meaningful metrics for integration work.

Current run sizes:

- training: 80,000 images
- validation: 10,000 images
- test: 10,000 images

Current metrics:

- test top-1: `0.7261`
- test top-5: `0.9388`
- worst class: `scientific_report`
- worst-class accuracy: `0.4576`

Consequence: the model card is honest and marks `run_mode = balanced_100k` and
`full_run = false`. For a strict final interpretation of the assignment, the
same notebook flow should be rerun with the full official test split.

### Train With Linear Probe Then Partial Unfreeze

Decision: train the replacement classifier head first, then unfreeze only the
final ConvNeXt feature stage and the classifier head.

Reasoning: a linear probe gives a stable baseline while preserving pretrained
ImageNet features. Partial unfreezing adapts the highest-level visual features
to document layouts without the cost and overfitting risk of full fine-tuning.

Consequence: the model card records the freeze policy as
`linear_probe_then_partial_unfreeze_final_stage`.

### Save Runtime Metadata Inside The Classifier Artifact

Decision: save more than just the PyTorch state dict in `classifier.pt`.

Reasoning: runtime inference must rebuild preprocessing and class order exactly.
The artifact therefore includes class names, image size, backbone, weights enum,
freeze policy, normalization mean, and normalization standard deviation.

Consequence: `app/classifier/inference.py` can load the model without guessing
class order or preprocessing constants.

### Validate Artifacts With SHA-256

Decision: compute the SHA-256 of `classifier.pt` and store it in
`model_card.json`.

Reasoning: the assignment requires the API and worker to refuse startup if the
classifier weights are missing or do not match the model card. A SHA-256 check
also catches accidental model swaps and corrupted downloads.

Consequence: startup checks can compare the real hash of `classifier.pt` against
the committed model card before serving or processing jobs.

### Store The Model With Git LFS

Decision: track `app/classifier/models/classifier.pt` with Git LFS.

Reasoning: the model artifact is about 111 MB. GitHub rejects normal Git blobs
over 100 MB, and large binaries should not live in normal Git history.

Consequence: `.gitattributes` marks `classifier.pt` as an LFS object. The model
push requires Git LFS support, but the repository history remains manageable.

### Golden Set Uses CPU Predictions

Decision: write golden expected labels and confidences from a CPU replay model.

Reasoning: CI and local replay are likely to run on CPU. Recomputing expected
values on CPU reduces drift between Colab GPU evaluation and service-side replay.

Consequence: `golden_expected.json` represents the exact CPU inference contract.
The replay check should fail if the predicted label changes or the top-1
confidence differs by more than `1e-6`.

### Keep Inference Code Outside The API Layer

Decision: model loading and prediction live in the classifier module, while the
API only queues jobs.

Reasoning: the project architecture says routers must not run inference or touch
external systems directly. Keeping the model code in `app/classifier/` lets the
worker, golden replay, and local scripts reuse the same inference path.

Consequence: the worker consumes an inference job, reads image bytes from blob
storage, calls the classifier runtime, writes an overlay PNG, and records the
prediction result. The API remains an HTTP and permission boundary, not a model
execution process.
