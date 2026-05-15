"""Golden-set regression check.

For each fixture in ``app/classifier/eval/golden_images/``, this loads
the real ``classifier.pt`` weights, runs inference, and checks that:

  * the predicted top-1 label matches ``expected_label`` byte-for-byte;
  * the predicted top-1 confidence matches ``top1_confidence`` to within
    ``CONFIDENCE_TOL`` (default 1e-4 — strict 1e-6 isn't achievable
    across CPUs / BLAS / torch ABIs; CI runners drift 1–2e-6 from the
    locked-in baseline).

Two ways to run it:

  * ``pytest app/classifier/eval/golden.py`` — pytest collects
    ``test_golden_set`` and reports per-image failures.

  * ``python -m app.classifier.eval.golden`` — standalone runner. Prints
    a one-line summary per image and exits 1 if any image regressed.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pytest

from app.classifier.inference import RVLCDIPClassifier


EVAL_DIR = Path(__file__).resolve().parent
GOLDEN_IMAGES_DIR = EVAL_DIR / "golden_images"
GOLDEN_EXPECTED_PATH = EVAL_DIR / "golden_expected.json"

CONFIDENCE_TOL = 1e-4


@dataclass(frozen=True)
class GoldenRegression:
    """One row of mismatch reporting."""

    filename: str
    expected_label: str
    actual_label: str
    expected_confidence: float
    actual_confidence: float

    def label_ok(self) -> bool:
        return self.actual_label == self.expected_label

    def confidence_ok(self, tol: float = CONFIDENCE_TOL) -> bool:
        return abs(self.actual_confidence - self.expected_confidence) <= tol

    def reason(self, tol: float = CONFIDENCE_TOL) -> str:
        problems: list[str] = []
        if not self.label_ok():
            problems.append(
                f"label expected={self.expected_label!r} got={self.actual_label!r}"
            )
        if not self.confidence_ok(tol):
            problems.append(
                "confidence expected="
                f"{self.expected_confidence:.10f} got={self.actual_confidence:.10f} "
                f"(|Δ|={abs(self.actual_confidence - self.expected_confidence):.2e}, tol={tol:.0e})"
            )
        return "; ".join(problems)


def _load_expected() -> list[dict]:
    with open(GOLDEN_EXPECTED_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{GOLDEN_EXPECTED_PATH} must contain a JSON array")
    return data


def _evaluate(
    classifier: RVLCDIPClassifier, fixtures: Iterable[dict]
) -> list[GoldenRegression]:
    """Run inference per fixture, return one Regression-row per image.

    A row with ``label_ok and confidence_ok`` is a pass; otherwise it's
    a regression. Returning even passing rows lets the standalone runner
    print a per-image summary.
    """
    rows: list[GoldenRegression] = []
    for fixture in fixtures:
        filename = fixture["filename"]
        expected_label = fixture["expected_label"]
        expected_conf = float(fixture["top1_confidence"])

        image_path = GOLDEN_IMAGES_DIR / filename
        prediction = classifier.predict_path(image_path)
        rows.append(
            GoldenRegression(
                filename=filename,
                expected_label=expected_label,
                actual_label=prediction.label,
                expected_confidence=expected_conf,
                actual_confidence=prediction.confidence,
            )
        )
    return rows


@pytest.fixture(scope="module")
def classifier() -> RVLCDIPClassifier:
    """Real classifier, real weights. Skip the suite if the weights are
    missing locally — CI provisions Git LFS so they should always
    materialise there.
    """
    if not (EVAL_DIR.parent / "models" / "classifier.pt").is_file():
        pytest.skip("classifier.pt not present — Git LFS not materialised?")
    return RVLCDIPClassifier.from_default_artifacts()


def test_golden_set(classifier: RVLCDIPClassifier) -> None:
    """One pytest case for the whole set so the failure report is grouped."""
    fixtures = _load_expected()
    rows = _evaluate(classifier, fixtures)
    failures = [r for r in rows if not (r.label_ok() and r.confidence_ok())]
    if failures:
        report = "\n".join(f"  - {r.filename}: {r.reason()}" for r in failures)
        pytest.fail(
            f"{len(failures)}/{len(rows)} golden-set regressions:\n{report}"
        )


def main() -> int:
    """Standalone runner. Exit 0 if all images match, 1 otherwise."""
    fixtures = _load_expected()
    if not fixtures:
        print("[golden] no fixtures in golden_expected.json", file=sys.stderr)
        return 1

    classifier = RVLCDIPClassifier.from_default_artifacts()
    rows = _evaluate(classifier, fixtures)

    passed = 0
    for row in rows:
        ok = row.label_ok() and row.confidence_ok()
        passed += int(ok)
        marker = "PASS" if ok else "FAIL"
        print(
            f"[{marker}] {row.filename:<70} "
            f"label={row.actual_label:<22} "
            f"conf={row.actual_confidence:.8f}"
        )
        if not ok:
            print(f"        -> {row.reason()}")

    total = len(rows)
    print(f"[golden] {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
