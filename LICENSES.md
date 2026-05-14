# Licenses and Third-Party Notices

This project uses third-party datasets, model weights, libraries, and container
images. This file records the licensing constraints that matter for the Week 6
document-classifier service.

## RVL-CDIP Dataset

- Source: https://adamharley.com/rvl-cdip/
- Classes: 16 document layout classes used for visual document classification.
- Use in this project: training, validation, test evaluation, and the 50-image
  golden replay set under `app/classifier/eval/golden_images/`.
- Important restriction: RVL-CDIP is provided for academic and research use.
  The dataset and derived golden images should not be treated as unrestricted
  commercial assets.
- Project handling: the full dataset is not committed to the repository. Only
  the trained classifier artifact, model card, and 50 selected golden TIFFs are
  committed for reproducible evaluation.

## Pretrained Model Weights

- Source: `torchvision.models.ConvNeXt_Tiny_Weights.DEFAULT`
- Use in this project: initialization for fine-tuning the RVL-CDIP classifier.
- The final artifact at `app/classifier/models/classifier.pt` contains the
  fine-tuned model state and classifier metadata generated from the Colab run.
- The model card records the exact backbone, weights enum, freeze policy,
  metrics, environment fingerprint, and SHA-256 hash for integrity checks.

## Python Libraries

The service is expected to use open-source Python libraries including:

- PyTorch and torchvision for classifier inference.
- Pillow for TIFF/image loading and overlay generation.
- FastAPI and pydantic for the API and domain models.
- SQLAlchemy and Alembic for database access and migrations.
- Redis/RQ for background jobs.
- fastapi-users, Casbin, fastapi-cache2, and MinIO client libraries according
  to the project brief.

Exact dependency versions should be pinned in the project dependency files when
the full stack is finalized.

## Container Images and Services

The local compose stack is expected to use third-party service images including:

- `postgres:16`
- `redis:7`
- `minio/minio`
- `atmoz/sftp`
- `hashicorp/vault`

Each image remains governed by its upstream license and terms.

## Project Code

Unless the team adds a separate repository license file, the code in this
student project should be treated as course project work owned by the listed
team members. Do not assume a public open-source license for this repository
without an explicit `LICENSE` file.
