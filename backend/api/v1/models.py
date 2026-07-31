from fastapi import APIRouter

from backend.schemas.common import ok

router = APIRouter(prefix="/models", tags=["models"])

_MODELS = [
    {
        "name":        "global_lgbm",
        "category":    "Global",
        "status":      "available",
        "description": "Cross-learning model — one fit across the whole catalogue, "
                       "so short and new SKUs borrow the seasonality of the rest",
    },
    {
        "name":        "lightgbm",
        "category":    "ML",
        "status":      "available",
        "description": "Gradient boosted trees — fast, high—accuracy for tabular data",
    },
    {
        "name":        "xgboost",
        "category":    "ML",
        "status":      "available",
        "description": "Extreme gradient boosting — strong baseline for structured series",
    },
    {
        "name":        "prophet",
        "category":    "Statistical",
        "status":      "available",
        "description": "Facebook Prophet — trend + seasonality decomposition",
    },
    {
        "name":        "arima",
        "category":    "Statistical",
        "status":      "available",
        "description": "ARIMA — classical statistical model for stationary series",
    },
    {
        "name":        "sarimax",
        "category":    "Statistical",
        "status":      "available",
        "description": "SARIMAX — seasonal ARIMA that can also read external "
                       "drivers such as price or promotions",
    },
    {
        "name":        "ets",
        "category":    "Statistical",
        "status":      "available",
        "description": "Exponential smoothing — simple, robust seasonal decomposition",
    },
    {
        "name":        "croston",
        "category":    "Statistical",
        "status":      "available",
        "description": "Croston's method — specialized for intermittent/sparse demand",
    },
    {
        "name":        "lstm",
        "category":    "Deep Learning",
        "status":      "beta",
        "description": "LSTM — deep learning for complex non—linear patterns",
    },
]


@router.get("")
def list_models():
    return ok(_MODELS)
