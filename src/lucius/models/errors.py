from __future__ import annotations

from enum import StrEnum


class ModelErrorCode(StrEnum):
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_DISABLED = "MODEL_DISABLED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    PRIVACY_POLICY_BLOCK = "PRIVACY_POLICY_BLOCK"
    AUTHORITY_BLOCK = "AUTHORITY_BLOCK"
    UNSAFE_REQUEST = "UNSAFE_REQUEST"
    COST_POLICY_BLOCK = "COST_POLICY_BLOCK"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CONTEXT_LIMIT_EXCEEDED = "CONTEXT_LIMIT_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    STRUCTURED_OUTPUT_INVALID = "STRUCTURED_OUTPUT_INVALID"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NO_ELIGIBLE_MODEL = "NO_ELIGIBLE_MODEL"


RETRYABLE_MODEL_ERRORS = {
    ModelErrorCode.PROVIDER_UNAVAILABLE,
    ModelErrorCode.TIMEOUT,
    ModelErrorCode.RATE_LIMIT,
    ModelErrorCode.TRANSIENT_ERROR,
    ModelErrorCode.INVALID_RESPONSE,
    ModelErrorCode.STRUCTURED_OUTPUT_INVALID,
    ModelErrorCode.PROVIDER_ERROR,
}

NON_FALLBACK_MODEL_ERRORS = {
    ModelErrorCode.PRIVACY_POLICY_BLOCK,
    ModelErrorCode.AUTHORITY_BLOCK,
    ModelErrorCode.UNSAFE_REQUEST,
    ModelErrorCode.BUDGET_EXCEEDED,
    ModelErrorCode.COST_POLICY_BLOCK,
}


class ModelGatewayError(Exception):
    def __init__(self, code: ModelErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ModelProviderError(ModelGatewayError):
    pass
