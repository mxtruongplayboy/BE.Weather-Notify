from fastapi import Header, HTTPException


def get_instance_id(
    x_app_instance_id: str = Header(..., alias="X-App-Instance-Id"),
) -> str:
    val = (x_app_instance_id or "").strip()
    if len(val) < 8:
        raise HTTPException(status_code=400, detail="X-App-Instance-Id header required (min 8 chars)")
    return val
