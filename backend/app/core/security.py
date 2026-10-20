import hashlib
import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet

password_hasher = PasswordHasher()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def private_file(path: Path, value: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


class Secrets:
    def __init__(self, data_dir: Path) -> None:
        key_path = data_dir / "master.key"
        if not key_path.exists():
            private_file(key_path, Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes())

    def encrypt(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode() if value else ""

    def decrypt(self, value: str) -> str:
        return self.cipher.decrypt(value.encode()).decode() if value else ""


def new_token() -> str:
    return secrets.token_urlsafe(32)


def verify_password(hashed: str, candidate: str) -> bool:
    try:
        return password_hasher.verify(hashed, candidate)
    except VerificationError:
        return False
