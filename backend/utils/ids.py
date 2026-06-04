import uuid


def generate_id(prefix: str = "") -> str:
    uid = uuid.uuid4().hex[:12]
    return f"{prefix}_{uid}" if prefix else uid
