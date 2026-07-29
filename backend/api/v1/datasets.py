from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.datasets import service as ds_svc
from backend.errors import AppError
from backend.schemas.common import ok

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post("", status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    try:
        meta = await ds_svc.upload_dataset(user.tenant_id, user.user_id, file)
    except AppError:
        # Already carries its own code/params — wrapping it would strip them.
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ok(meta)


@router.get("")
def list_datasets(
    user: CurrentUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    from backend.db.connection import query_one
    items = ds_svc.list_datasets(user.tenant_id, skip=skip, limit=limit)
    row = query_one(
        "SELECT COUNT(*) AS cnt FROM datasets WHERE tenant_id = %s "
        "AND COALESCE(source_type, 'file') != 'sql'",
        (user.tenant_id,),
    )
    total = row["cnt"] if row else 0
    return ok({"items": items, "total": total, "skip": skip, "limit": limit})


@router.get("/{dataset_id}")
def get_dataset(dataset_id: str, user: CurrentUser = Depends(get_current_user)):
    ds = ds_svc.get_dataset(user.tenant_id, dataset_id)
    if not ds:
        raise AppError(
            "dataset_not_found", "Dataset not found",
            status_code=404, params={"dataset_id": dataset_id},
        )
    # Never let SQL connection credentials (even encrypted) reach the client.
    ds = dict(ds)
    ds.pop("sql_config", None)
    return ok(ds)
