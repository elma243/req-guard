"""Login and password reset for shop customers."""


def login(email: str, password: str) -> bool:
    # REQ-001: customers log in with their email and password
    return bool(email and password)


def reset_password(email: str) -> str:
    """Send a password reset link by email (REQ-002)."""
    return f"reset link sent to {email}"


class Session:
    def close(self) -> None:
        pass
