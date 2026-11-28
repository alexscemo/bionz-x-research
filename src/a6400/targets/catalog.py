"""Registered BIONZ X (``CXD90045``) target profiles.

Each body lives in its own module under ``src/a6400/targets/bodies/`` and is
registered here in ``DEFAULT_REGISTRY``. Every claim is tied to an
``EvidenceRef`` and ``TargetProfile`` refuses to construct unsourced claims.
"""
from __future__ import annotations

from src.a6400.targets.bodies import (
    ILCE_6000,
    ILCE_6100,
    ILCE_6400,
    ILCE_6400A,
    ILCE_6600,
    ILCE_7C,
    ILCE_7M3,
    ILCE_7RM4A,
    ZV_E10,
)
from src.a6400.targets.profile import SOC_CXD90014, TargetProfile, TargetRegistry

DEFAULT_REGISTRY = TargetRegistry(
    (ILCE_6400, ILCE_6400A, ZV_E10, ILCE_6100, ILCE_6600, ILCE_7C, ILCE_7M3, ILCE_7RM4A)
)

#: ILCE-6000 (CXD90014). Kept apart so CXD90045 runners never see it.
CXD90014_REGISTRY = TargetRegistry((ILCE_6000,), soc=SOC_CXD90014)

__all__ = [
    "CXD90014_REGISTRY",
    "DEFAULT_REGISTRY",
    "ILCE_6000",
    "ILCE_6100",
    "ILCE_6400",
    "ILCE_6400A",
    "ILCE_6600",
    "ILCE_7C",
    "ILCE_7M3",
    "ILCE_7RM4A",
    "TargetProfile",
    "TargetRegistry",
    "ZV_E10",
]
