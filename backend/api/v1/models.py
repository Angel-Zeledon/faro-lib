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
        # Opt-in only: it is in no default selection and the router never adds
        # it (routing narrows the user's selection, it never grows it). On the
        # synthetic benchmark it ties with Croston on ordinary intermittent
        # demand; what it adds is a forecast that fades when a product stops
        # selling, where Croston keeps forecasting its last rate.
        "name":        "tsb",
        "category":    "Statistical",
        "status":      "available",
        "description": "TSB (Teunter-Syntetos-Babai) — intermittent demand whose "
                       "forecast decays when a product stops selling",
        "recommended_for": "Discontinued or end-of-life products and intermittent "
                           "demand; not selected by default",
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
