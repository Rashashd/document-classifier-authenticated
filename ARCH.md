# Architecture Notes

## Ali Asfahani: RVL-CDIP Classifier

Ali Asfahani owns the visual document classifier path: training the model in Colab,
shipping the classifier artifacts, and defining the runtime inference contract
used by the worker. The classifier is intentionally separate from the API,
database, queue, and blob-storage layers.

The model classifies RVL-CDIP document images by visual layout only. It does not
run OCR and does not use document text as input. The sixteen output classes are:

- letter
- form
- email
- handwritten
- advertisement
- scientific_report
- scientific_publication
- specification
- file_folder
- news_article
- budget
- invoice
- presentation
- questionnaire
- resume
- memo

## Training And Artifacts

Training happens in Google Colab, not in the local docker-compose stack. The
notebook used for the current artifact is
`Ali_rvl_cdip_convnext_colab_pro_v2_CLEAN_RUN_ALL.ipynb`.

The notebook performs a balanced 100k-image RVL-CDIP run:

- 80,000 training images, 5,000 per class
- 10,000 validation images, 625 per class
- 10,000 test images, 625 per class

The current run is explicitly not the full RVL-CDIP train/validation/test run.
The model card records this with `run_mode = balanced_100k` and
`full_run = false`.

The notebook produces the repo-ready classifier files:

- `app/classifier/models/classifier.pt`
- `app/classifier/models/model_card.json`
- `app/classifier/eval/golden_expected.json`
- `app/classifier/eval/golden_images/`

`classifier.pt` stores the trained ConvNeXt Tiny state dict plus the metadata
needed to rebuild preprocessing at runtime: class names, image size, backbone
name, weights enum, freeze policy, ImageNet normalization mean, and ImageNet
normalization standard deviation.

`model_card.json` records the SHA-256 hash of `classifier.pt`, the dataset
source, the no-OCR constraint, run sizes, model architecture, training
hyperparameters, test top-1/top-5 accuracy, per-class accuracy, worst class, and
the Colab environment fingerprint.

Current classifier metrics from the balanced 100k run:

- test top-1: `0.7261`
- test top-5: `0.9388`
- worst class: `scientific_report`
- worst-class accuracy: `0.4576`

The classifier weights are stored with Git LFS because the artifact is about
111 MB.

## Model Training Flow

The Colab notebook follows this flow:

1. Mount Google Drive and check the GPU and Colab disk.
2. Verify the RVL-CDIP split files and archive/subset archive.
3. Restore or extract the local TIFF subset under `/content`.
4. Read the official RVL-CDIP split files.
5. Select balanced train, validation, and test rows per class.
6. Filter unreadable test TIFFs before final evaluation.
7. Build PyTorch datasets and dataloaders.
8. Convert grayscale TIFFs to RGB, resize to `224x224`, and normalize using the
   ConvNeXt ImageNet preprocessing constants.
9. Train a ConvNeXt Tiny classifier head with the pretrained backbone frozen.
10. Partially unfreeze the final ConvNeXt feature stage and fine-tune with a
    smaller learning rate.
11. Evaluate top-1, top-5, and per-class accuracy.
12. Save `classifier.pt`, compute its SHA-256, and write `model_card.json`.
13. Select and copy the 50-image golden set.
14. Replay the golden set on CPU to verify deterministic expected outputs.
15. Package the repo-ready artifacts into a zip for local extraction and commit.

## Golden Set

The golden set is a 50-image subset selected from the test rows used by the
current run. The selection spans all sixteen RVL-CDIP classes and deliberately
mixes low-confidence, medium-confidence, and high-confidence examples.

The expected output file, `app/classifier/eval/golden_expected.json`, stores the
model's CPU prediction for every golden image:

- source filename
- true label and true label id
- expected top-1 label and label id
- top-1 confidence
- top-5 labels, ids, and confidences

The replay invariant is:

- predicted label must match exactly
- top-1 confidence must match within `1e-6`

This protects the project from accidental changes to preprocessing, class order,
model loading, or the classifier artifact.

## Runtime Inference Boundary

The classifier runtime code belongs under `app/classifier/`. It should have no
FastAPI, SQLAlchemy, Redis, RQ, MinIO, or cache imports. Its responsibility is
limited to:

- locating the model artifacts
- validating that the model and model card exist
- checking the `classifier.pt` SHA-256 against `model_card.json`
- rebuilding ConvNeXt Tiny with the correct 16-class head
- applying the exact same preprocessing used during training
- returning top-k predictions for an image path, bytes payload, or PIL image

The API should never run inference directly. The API enqueues work. The
inference worker consumes a job, loads the image from blob storage, calls the
classifier runtime, writes an overlay PNG, and persists the prediction result
through the service/repository path owned by the API/database teammates.

This boundary keeps the classifier reusable in three places:

- worker inference jobs
- golden-set replay tests
- local smoke scripts
