"""ONNX export + version management — US-093."""
from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Lazy imports for optional deps
_onnxmltools = None
_onnx = None


def _get_onnxmltools():
    global _onnxmltools
    if _onnxmltools is None:
        try:
            import onnxmltools
            _onnxmltools = onnxmltools
        except ImportError:
            raise ImportError("onnxmltools required: pip install onnxmltools")
    return _onnxmltools


def _get_onnx():
    global _onnx
    if _onnx is None:
        try:
            import onnx
            _onnx = onnx
        except ImportError:
            raise ImportError("onnx required: pip install onnx")
    return _onnx


class ONNXExporter:
    """XGBoost → ONNX 변환 + 버전 관리.

    모델 저장소 구조:
        models/
        ├── latest/
        │   ├── model.onnx
        │   └── meta.json
        ├── v001/
        │   ├── model.onnx
        │   └── meta.json
        └── versions.json
    """

    MODEL_FILE = "model.onnx"
    META_FILE = "meta.json"
    VERSIONS_FILE = "versions.json"
    LATEST_DIR = "latest"

    def __init__(
        self,
        models_dir: str = "models",
        opset_version: int = 15,
    ) -> None:
        self._models_dir = Path(models_dir)
        self._opset_version = opset_version

    @property
    def models_dir(self) -> Path:
        return self._models_dir

    @property
    def opset_version(self) -> int:
        return self._opset_version

    def export(
        self,
        xgb_model,
        n_features: int,
        feature_names: list[str] | None = None,
        model_name: str = "xgb_signal",
        extra_meta: dict[str, Any] | None = None,
    ) -> str:
        """XGBoost Booster → ONNX 변환 + 저장.

        Parameters:
            xgb_model: trained xgb.Booster
            n_features: number of input features
            feature_names: optional feature name list
            model_name: model identifier
            extra_meta: additional metadata to store
        Returns:
            path to saved ONNX model
        """
        onnxmltools = _get_onnxmltools()
        from onnxmltools.convert.common.data_types import FloatTensorType

        # Convert XGBoost → ONNX
        initial_type = [("features", FloatTensorType([None, n_features]))]
        onnx_model = onnxmltools.convert_xgboost(
            xgb_model,
            initial_types=initial_type,
            target_opset=self._opset_version,
        )

        # Determine version
        version = self._next_version()
        version_dir = self._models_dir / version
        latest_dir = self._models_dir / self.LATEST_DIR

        # Save versioned
        version_dir.mkdir(parents=True, exist_ok=True)
        model_path = version_dir / self.MODEL_FILE
        onnxmltools.utils.save_model(onnx_model, str(model_path))

        # Meta
        meta = {
            "version": version,
            "model_name": model_name,
            "n_features": n_features,
            "feature_names": feature_names or [],
            "opset_version": self._opset_version,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "file_size_bytes": model_path.stat().st_size,
        }
        if extra_meta:
            meta.update(extra_meta)

        meta_path = version_dir / self.META_FILE
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        # Update latest symlink (copy)
        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        shutil.copytree(version_dir, latest_dir)

        # Update versions registry
        self._register_version(version, meta)

        logger.info(
            "onnx_exporter: exported %s (v%s) → %s (%d bytes)",
            model_name, version, model_path, meta["file_size_bytes"],
        )
        return str(model_path)

    def validate(self, model_path: str | None = None) -> bool:
        """ONNX 모델 유효성 검증."""
        onnx = _get_onnx()
        path = model_path or str(self._models_dir / self.LATEST_DIR / self.MODEL_FILE)

        if not Path(path).exists():
            logger.warning("onnx_exporter: model not found at %s", path)
            return False

        try:
            model = onnx.load(path)
            onnx.checker.check_model(model)
            logger.info("onnx_exporter: model validation passed")
            return True
        except Exception as exc:
            logger.error("onnx_exporter: validation failed: %s", exc)
            return False

    def test_inference(
        self,
        model_path: str | None = None,
        n_features: int = 20,
        n_samples: int = 100,
    ) -> dict[str, float]:
        """ONNX Runtime 추론 테스트 + 레이턴시 측정.

        Returns:
            {"latency_ms": float, "throughput_per_sec": float}
        """
        try:
            import onnxruntime as ort
        except ImportError:
            raise ImportError("onnxruntime required: pip install onnxruntime")

        path = model_path or str(self._models_dir / self.LATEST_DIR / self.MODEL_FILE)
        session = ort.InferenceSession(path)

        # Warmup
        dummy = np.random.randn(1, n_features).astype(np.float32)
        input_name = session.get_inputs()[0].name
        session.run(None, {input_name: dummy})

        # Benchmark
        test_data = np.random.randn(n_samples, n_features).astype(np.float32)
        start = time.perf_counter()
        for i in range(n_samples):
            session.run(None, {input_name: test_data[i:i+1]})
        elapsed = time.perf_counter() - start

        latency_ms = (elapsed / n_samples) * 1000
        throughput = n_samples / elapsed

        logger.info(
            "onnx_exporter: inference test — latency=%.3fms, throughput=%.0f/s",
            latency_ms, throughput,
        )
        return {"latency_ms": latency_ms, "throughput_per_sec": throughput}

    def list_versions(self) -> list[dict[str, Any]]:
        """등록된 모든 모델 버전 목록."""
        versions_path = self._models_dir / self.VERSIONS_FILE
        if not versions_path.exists():
            return []
        with open(versions_path) as f:
            data = json.load(f)
        return data.get("versions", [])

    def get_latest_version(self) -> str | None:
        """최신 버전 문자열."""
        versions = self.list_versions()
        return versions[-1]["version"] if versions else None

    def rollback(self, version: str) -> bool:
        """특정 버전을 latest로 복원."""
        version_dir = self._models_dir / version
        latest_dir = self._models_dir / self.LATEST_DIR

        if not version_dir.exists():
            logger.error("onnx_exporter: version %s not found", version)
            return False

        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        shutil.copytree(version_dir, latest_dir)

        logger.info("onnx_exporter: rolled back to %s", version)
        return True

    def _next_version(self) -> str:
        """다음 버전 번호 (v001, v002, ...)."""
        versions = self.list_versions()
        if not versions:
            return "v001"
        last = versions[-1]["version"]
        num = int(last[1:]) + 1
        return f"v{num:03d}"

    def _register_version(self, version: str, meta: dict) -> None:
        """버전 레지스트리에 등록."""
        versions_path = self._models_dir / self.VERSIONS_FILE
        if versions_path.exists():
            with open(versions_path) as f:
                data = json.load(f)
        else:
            self._models_dir.mkdir(parents=True, exist_ok=True)
            data = {"versions": []}

        data["versions"].append({
            "version": version,
            "model_name": meta.get("model_name", "unknown"),
            "exported_at": meta.get("exported_at", ""),
            "file_size_bytes": meta.get("file_size_bytes", 0),
        })

        with open(versions_path, "w") as f:
            json.dump(data, f, indent=2)
