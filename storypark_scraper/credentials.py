from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import base64
import ctypes
from ctypes import wintypes
import json
import os
import re
import shutil
import subprocess
from typing import Optional

import warnings

from storypark_scraper.models import Credentials


GENERIC_CREDENTIAL_TYPE = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
CRYPTPROTECT_LOCAL_MACHINE = 0x4


class CredentialError(RuntimeError):
    """Raised when credential loading fails."""


@dataclass(slots=True)
class CredentialSources:
    username_env: str = "SP_USER"
    password_env: str = "SP_PASS"
    dotenv_filename: str = ".env"


@dataclass(slots=True)
class CredentialStoreOutcome:
    backend: str
    path: Path | None = None
    warning: str | None = None


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIAL_ATTRIBUTEW(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(CREDENTIAL_ATTRIBUTEW)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def load_credentials(root_dir: Path, credential_target: str, sources: CredentialSources | None = None) -> Credentials:
    sources = sources or CredentialSources()
    creds = _load_from_windows_credential_manager(credential_target)
    if creds:
        return creds

    creds = _load_from_dpapi_file(_credential_fallback_path(root_dir), credential_target)
    if creds:
        return creds

    env = _load_dotenv(root_dir / sources.dotenv_filename)
    username = env.get(sources.username_env) or os.getenv(sources.username_env)
    password = env.get(sources.password_env) or os.getenv(sources.password_env)
    if username and password:
        warnings.warn(
            "Storypark credentials were loaded from plaintext .env file. "
            "To improve security, store them in Windows Credential Manager using "
            f"`python -m storypark_scraper store-credentials --username {username}` and remove {sources.password_env} from .env.",
            UserWarning,
            stacklevel=2,
        )
        return Credentials(username=username, password=password, source="dotenv/env")
    raise CredentialError(
        "No Storypark credentials found. Store them in Windows Credential Manager or in the encrypted local "
        f"fallback for {credential_target!r}, or provide SP_USER/SP_PASS in .env."
    )


def store_credentials(root_dir: Path, target: str, username: str, password: str) -> CredentialStoreOutcome:
    try:
        store_windows_credentials(target, username, password)
        return CredentialStoreOutcome(backend="windows-credential-manager")
    except CredentialError as exc:
        fallback_path = _credential_fallback_path(root_dir)
        _store_dpapi_file(fallback_path, target, username, password)
        return CredentialStoreOutcome(
            backend="dpapi-local-file",
            path=fallback_path,
            warning=str(exc),
        )


def store_windows_credentials(target: str, username: str, password: str) -> None:
    if os.name != "nt":
        raise CredentialError("Windows Credential Manager is only available on Windows")

    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    cred_write = advapi32.CredWriteW
    cred_write.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    cred_write.restype = wintypes.BOOL

    password_bytes = password.encode("utf-16-le")
    blob_arr = (ctypes.c_ubyte * len(password_bytes))(*password_bytes)

    cred = CREDENTIALW()
    cred.Flags = 0
    cred.Type = GENERIC_CREDENTIAL_TYPE
    cred.TargetName = target
    cred.Comment = "Storypark Scraper Credentials"
    cred.CredentialBlobSize = len(password_bytes)
    cred.CredentialBlob = ctypes.cast(blob_arr, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = username

    if not cred_write(ctypes.byref(cred), 0):
        error = ctypes.get_last_error()
        raise CredentialError(f"CredWriteW failed with Windows error {error}")


def delete_windows_credentials(target: str) -> bool:
    if os.name != "nt":
        return False
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    cred_delete = advapi32.CredDeleteW
    cred_delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    cred_delete.restype = wintypes.BOOL
    return bool(cred_delete(target, GENERIC_CREDENTIAL_TYPE, 0))


def _load_from_windows_credential_manager(target: str) -> Optional[Credentials]:
    if os.name != "nt":
        return None

    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    cred_read = advapi32.CredReadW
    cred_read.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    cred_read.restype = wintypes.BOOL

    cred_free = advapi32.CredFree
    cred_free.argtypes = [ctypes.c_void_p]
    cred_free.restype = None

    credential_pointer = ctypes.POINTER(CREDENTIALW)()
    if not cred_read(target, GENERIC_CREDENTIAL_TYPE, 0, ctypes.byref(credential_pointer)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise CredentialError(f"CredReadW failed with Windows error {error}")

    try:
        credential = credential_pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        password = blob.decode("utf-16-le")
        username = credential.UserName or ""
        if username and password:
            return Credentials(username=username, password=password, source="windows-credential-manager")
        return None
    finally:
        cred_free(credential_pointer)


def _credential_fallback_path(root_dir: Path) -> Path:
    return root_dir / "state" / "credentials.json"


def _store_dpapi_file(path: Path, target: str, username: str, password: str) -> None:
    if os.name != "nt":
        raise CredentialError("DPAPI credential fallback is only available on Windows")
    encrypted_password = _dpapi_protect(password.encode("utf-8"), local_machine=False)
    payload = {
        "version": 2,
        "backend": "dpapi-local-file",
        "scope": "current_user",
        "target": target,
        "username": username,
        "password": base64.b64encode(encrypted_password).decode("ascii"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _load_from_dpapi_file(path: Path, target: str) -> Optional[Credentials]:
    if os.name != "nt" or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialError(f"Failed to read encrypted credential file {path}: {exc}") from exc

    if payload.get("target") != target:
        return None
    username = str(payload.get("username", ""))
    encoded_password = payload.get("password")
    if not username or not encoded_password:
        return None
    try:
        encrypted_password = base64.b64decode(encoded_password)
        password = _dpapi_unprotect(encrypted_password).decode("utf-8")
    except Exception as exc:
        raise CredentialError(f"Failed to decrypt credential file {path}: {exc}") from exc
    return Credentials(username=username, password=password, source="dpapi-file")


def _dpapi_protect(data: bytes, *, local_machine: bool) -> bytes:
    crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    crypt_protect = crypt32.CryptProtectData
    crypt_protect.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt_protect.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    input_blob, input_buffer = _bytes_to_blob(data)
    output_blob = DATA_BLOB()
    flags = CRYPTPROTECT_LOCAL_MACHINE if local_machine else 0

    if not crypt_protect(ctypes.byref(input_blob), "Storypark Scraper", None, None, None, flags, ctypes.byref(output_blob)):
        error = ctypes.get_last_error()
        raise CredentialError(f"CryptProtectData failed with Windows error {error}")

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            local_free(output_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    crypt_unprotect = crypt32.CryptUnprotectData
    crypt_unprotect.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt_unprotect.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    input_blob, input_buffer = _bytes_to_blob(data)
    output_blob = DATA_BLOB()
    description = ctypes.c_wchar_p()

    if not crypt_unprotect(ctypes.byref(input_blob), ctypes.byref(description), None, None, None, 0, ctypes.byref(output_blob)):
        error = ctypes.get_last_error()
        raise CredentialError(f"CryptUnprotectData failed with Windows error {error}")

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if description:
            local_free(description)
        if output_blob.pbData:
            local_free(output_blob.pbData)


def _bytes_to_blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    line_pattern = re.compile(
        r"""
        ^\s*
        (?:export\s+)?              # Optional export keyword
        ([A-Za-z_][A-Za-z0-9_]*)    # Variable key
        \s*=\s*
        (                           # Value
            '(?:\\'|[^'])*'         # Single-quoted string
            |
            "(?:\\"|[^"])*"         # Double-quoted string
            |
            [^#\r\n]*               # Unquoted value up to comment or newline
        )
        (?:\s*\#.*)?                # Optional trailing comment
        $
        """,
        re.VERBOSE,
    )
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = line_pattern.match(raw_line)
        if not match:
            if "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
            continue
        key, value = match.group(1), match.group(2).strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            try:
                value = bytes(value[1:-1], "utf-8").decode("unicode_escape")
            except Exception:
                value = value[1:-1]
        elif value.startswith("'") and value.endswith("'") and len(value) >= 2:
            value = value[1:-1].replace(r"\'", "'")
        values[key] = value
    return values

