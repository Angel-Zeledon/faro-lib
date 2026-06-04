"""
Session state machine — defines valid status transitions.

        DRAFT
          │
          └─▶ DATASET_LOADED
                  │
                  └─▶ INSPECTED
                          │
                          └─▶ COLUMNS_CONFIGURED
                                  │
                                  └─▶ FEATURES_CONFIGURED
                                          │
                                          └─▶ MODELS_CONFIGURED ──────┐
                                                  │                    │ (re-train)
                                                  └─▶ QUEUED          │
                                                        │              │
                                                        ├─▶ RUNNING   │
                                                        │      │       │
                                                        │      ├─▶ COMPLETED ──┘
                                                        │      ├─▶ FAILED ─────┘
                                                        │      └─▶ CANCELLED
                                                        └─▶ CANCELLED
"""

_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT":               {"DATASET_LOADED"},
    "DATASET_LOADED":      {"INSPECTED", "DATASET_LOADED"},
    "INSPECTED":           {"COLUMNS_CONFIGURED", "DATASET_LOADED"},
    "COLUMNS_CONFIGURED":  {"FEATURES_CONFIGURED", "INSPECTED"},
    "FEATURES_CONFIGURED": {"MODELS_CONFIGURED", "COLUMNS_CONFIGURED"},
    "MODELS_CONFIGURED":   {"QUEUED", "FEATURES_CONFIGURED"},
    "QUEUED":              {"RUNNING", "CANCELLED"},
    "RUNNING":             {"COMPLETED", "FAILED", "CANCELLED"},
    "COMPLETED":           {"QUEUED", "MODELS_CONFIGURED"},
    "FAILED":              {"QUEUED", "MODELS_CONFIGURED"},
    "CANCELLED":           {"QUEUED", "MODELS_CONFIGURED"},
}

# Steps that allow re-training without re-configuration
TRAINING_STATES = {"QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"}
PRE_TRAIN_STATES = {"MODELS_CONFIGURED", "COMPLETED", "FAILED", "CANCELLED"}


def can_transition(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(current, set())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise ValueError(
            f"Invalid state transition: {current} → {target}. "
            f"Allowed from {current}: {sorted(_TRANSITIONS.get(current, set()))}"
        )
