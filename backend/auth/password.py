import bcrypt


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def validate_strength(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    # bcrypt hard-rejects (ValueError) any password whose UTF-8 encoding
    # exceeds 72 bytes — reject it here with a clean message instead of
    # letting hash_password() crash the request with an unhandled 500.
    if len(password.encode("utf-8")) > 72:
        return False, "Password must be at most 72 bytes long"
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit"
    if not any(c.isalpha() for c in password):
        return False, "Password must contain at least one letter"
    return True, ""
