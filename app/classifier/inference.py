"""Model loading and visual-layout inference for the RVL-CDIP classifier.

This module deliberately has no FastAPI, database, Redis, or MinIO imports.
It owns only model artifact validation, image preprocessing, and prediction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from collections.abc import Callable
from functools import lru_cache, wraps
from io import BytesIO
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = APP_ROOT / "classifier" / "models" / "classifier.pt"
DEFAULT_MODEL_CARD_PATH = APP_ROOT / "classifier" / "models" / "model_card.json"


@dataclass(frozen=True)
class Prediction:
    """Top-k prediction returned by the classifier."""

    label: str
    label_id: int
    confidence: float
    top_k: list[dict[str, float | int | str]]


@dataclass(frozen=True)
class ClassifierArtifacts:
    """Resolved classifier artifact paths."""

    model_path: Path = DEFAULT_MODEL_PATH
    model_card_path: Path = DEFAULT_MODEL_CARD_PATH


class ClassifierArtifactError(RuntimeError):
    """Raised when classifier artifacts are missing or fail integrity checks."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_card(path: Path = DEFAULT_MODEL_CARD_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def _model_card_sha256(model_card: dict[str, Any]) -> str | None:
    if "sha256" in model_card:
        return str(model_card["sha256"])
    model_section = model_card.get("model", {})
    value = model_section.get("sha256")
    return str(value) if value else None


def _model_card_test_top1(model_card: dict[str, Any]) -> float | None:
    metrics = model_card.get("metrics", {})
    value = metrics.get("test_top1")
    return float(value) if value is not None else None


def assert_classifier_artifacts(
    artifacts: ClassifierArtifacts = ClassifierArtifacts(),
    *,
    min_test_top1: float | None = None,
) -> None:
    """Validate artifact presence, SHA-256, and optional accuracy threshold.

    The API and worker can call this during startup to satisfy the "refuse to
    boot" requirement from the project brief.
    """

    if not artifacts.model_path.is_file():
        raise ClassifierArtifactError(f"Missing classifier weights: {artifacts.model_path}")
    if not artifacts.model_card_path.is_file():
        raise ClassifierArtifactError(f"Missing model card: {artifacts.model_card_path}")

    model_card = load_model_card(artifacts.model_card_path)
    expected_sha = _model_card_sha256(model_card)
    if not expected_sha:
        raise ClassifierArtifactError("model_card.json does not contain a classifier SHA-256")

    actual_sha = sha256_file(artifacts.model_path)
    if actual_sha != expected_sha:
        raise ClassifierArtifactError(
            "classifier.pt SHA-256 mismatch: "
            f"expected {expected_sha}, got {actual_sha}"
        )

    if min_test_top1 is not None:
        test_top1 = _model_card_test_top1(model_card)
        if test_top1 is None:
            raise ClassifierArtifactError("model_card.json does not contain metrics.test_top1")
        if test_top1 < min_test_top1:
            raise ClassifierArtifactError(
                f"Model test_top1 {test_top1:.4f} is below required {min_test_top1:.4f}"
            )


def _import_torchvision() -> tuple[Any, Any, Any]:
    import torch
    from torchvision import transforms
    from torchvision.models import convnext_tiny

    return torch, transforms, convnext_tiny


def _build_convnext_tiny(num_classes: int) -> Any:
    torch, _transforms, convnext_tiny = _import_torchvision()
    model = convnext_tiny(weights=None)
    in_features = model.classifier[2].in_features
    model.classifier[2] = torch.nn.Linear(in_features, num_classes)
    return model


_F = TypeVar("_F", bound=Callable[..., Any])


def self_no_grad(func: _F) -> _F:
    """Decorator using the classifier instance's torch.no_grad context."""

    @wraps(func)
    def wrapper(self: "RVLCDIPClassifier", *args: Any, **kwargs: Any) -> Any:
        with self.torch.no_grad():
            return func(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


class RVLCDIPClassifier:
    """Small wrapper around the trained ConvNeXt model."""

    def __init__(
        self,
        *,
        model_path: Path = DEFAULT_MODEL_PATH,
        model_card_path: Path = DEFAULT_MODEL_CARD_PATH,
        device: str | None = None,
        min_test_top1: float | None = None,
    ) -> None:
        self.artifacts = ClassifierArtifacts(model_path=model_path, model_card_path=model_card_path)
        assert_classifier_artifacts(self.artifacts, min_test_top1=min_test_top1)

        self.model_card = load_model_card(model_card_path)
        self.torch, self.transforms, _convnext_tiny = _import_torchvision()
        self.device = device or ("cuda" if self.torch.cuda.is_available() else "cpu")

        artifact = self.torch.load(model_path, map_location=self.device)
        if isinstance(artifact, dict) and "model_state_dict" in artifact:
            state_dict = artifact["model_state_dict"]
            self.class_names = list(artifact.get("class_names") or self._class_names_from_card())
            self.image_size = int(artifact.get("image_size") or self._image_size_from_card())
            mean = artifact.get("normalization_mean") or self._normalization_from_card("mean")
            std = artifact.get("normalization_std") or self._normalization_from_card("std")
        else:
            state_dict = artifact
            self.class_names = self._class_names_from_card()
            self.image_size = self._image_size_from_card()
            mean = self._normalization_from_card("mean")
            std = self._normalization_from_card("std")

        self.model = _build_convnext_tiny(num_classes=len(self.class_names)).to(self.device)
        self.model.load_state_dict(state_dict)
        self.model.eval()

        self.transform = self.transforms.Compose(
            [
                self.transforms.Resize((self.image_size, self.image_size)),
                self.transforms.ToTensor(),
                self.transforms.Normalize(mean=mean, std=std),
            ]
        )

    def _class_names_from_card(self) -> list[str]:
        model_section = self.model_card.get("model", {})
        class_names = model_section.get("class_names")
        if not class_names:
            raise ClassifierArtifactError("model_card.json does not define model.class_names")
        return list(class_names)

    def _image_size_from_card(self) -> int:
        return int(self.model_card.get("model", {}).get("image_size", 224))

    def _normalization_from_card(self, field: str) -> list[float]:
        key = "normalization_mean" if field == "mean" else "normalization_std"
        value = self.model_card.get("model", {}).get(key)
        if not value:
            return [0.485, 0.456, 0.406] if field == "mean" else [0.229, 0.224, 0.225]
        return [float(x) for x in value]

    @classmethod
    def from_default_artifacts(
        cls,
        *,
        device: str | None = None,
        min_test_top1: float | None = None,
    ) -> "RVLCDIPClassifier":
        return cls(device=device, min_test_top1=min_test_top1)

    def predict_path(self, path: str | Path, *, top_k: int = 5) -> Prediction:
        with Image.open(path) as image:
            return self.predict_image(image, top_k=top_k)

    def predict_bytes(self, content: bytes, *, top_k: int = 5) -> Prediction:
        with Image.open(BytesIO(content)) as image:
            return self.predict_image(image, top_k=top_k)

    @self_no_grad
    def predict_image(self, image: Image.Image, *, top_k: int = 5) -> Prediction:
        rgb = image.convert("RGB")
        tensor = self.transform(rgb).unsqueeze(0).to(self.device)
        logits = self.model(tensor)
        probabilities = self.torch.softmax(logits, dim=1)[0]
        k = min(top_k, len(self.class_names))
        top_probs, top_indices = self.torch.topk(probabilities, k=k, dim=0)

        top_values: list[dict[str, float | int | str]] = []
        for probability, index in zip(top_probs.tolist(), top_indices.tolist()):
            label_id = int(index)
            top_values.append(
                {
                    "label": self.class_names[label_id],
                    "label_id": label_id,
                    "confidence": float(probability),
                }
            )

        first = top_values[0]
        return Prediction(
            label=str(first["label"]),
            label_id=int(first["label_id"]),
            confidence=float(first["confidence"]),
            top_k=top_values,
        )


@lru_cache(maxsize=1)
def get_default_classifier() -> RVLCDIPClassifier:
    return RVLCDIPClassifier.from_default_artifacts()


def predict_file(path: str | Path, *, top_k: int = 5) -> Prediction:
    """Convenience function for scripts and tests."""

    return get_default_classifier().predict_path(path, top_k=top_k)

