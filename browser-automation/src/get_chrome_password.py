"""
Decrypt a saved Chrome password for an origin (Windows DPAPI).
Usage:
  python get_chrome_password.py [profile_dir] [origin_substring] [username_substring]
Prints password to stdout (or empty if not found).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

try:
    import win32crypt  # type: ignore
except ImportError:
    win32crypt = None

try:
    from Cryptodome.Cipher import AES  # type: ignore
except ImportError:
    try:
        from Crypto.Cipher import AES  # type: ignore
    except ImportError:
        AES = None


def get_encryption_key(local_state_path: Path) -> bytes | None:
    data = json.loads(local_state_path.read_text(encoding="utf-8"))
    enc = data.get("os_crypt", {}).get("encrypted_key")
    if not enc:
        return None
    raw = __import__("base64").b64decode(enc)
    # Strip DPAPI prefix "DPAPI"
    if raw.startswith(b"DPAPI"):
        raw = raw[5:]
    if win32crypt:
        return win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
    # ctypes fallback
    import ctypes
    import ctypes.wintypes as w

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", w.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    blob_in = DATA_BLOB(len(raw), ctypes.create_string_buffer(raw, len(raw)))
    blob_out = DATA_BLOB()
    if ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    return None


def decrypt_value(buff: bytes, key: bytes) -> str:
    if buff is None:
        return ""
    # Chrome v80+: v10/v11 + AES-GCM
    if buff[:3] in (b"v10", b"v11") and AES and key:
        iv = buff[3:15]
        payload = buff[15:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
        # last 16 bytes tag
        decrypted = cipher.decrypt_and_verify(payload[:-16], payload[-16:])
        return decrypted.decode("utf-8", errors="ignore")
    # Older DPAPI-only
    if win32crypt:
        try:
            return win32crypt.CryptUnprotectData(buff, None, None, None, 0)[1].decode("utf-8")
        except Exception:
            return ""
    return ""


def main() -> int:
    profile = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[1] / "chrome-profile-data"
    )
    origin_sub = (sys.argv[2] if len(sys.argv) > 2 else "google").lower()
    user_sub = (sys.argv[3] if len(sys.argv) > 3 else "").lower()

    local_state = profile / "Local State"
    login_db = profile / "Default" / "Login Data"
    if not login_db.exists():
        login_db = profile / "Login Data"
    if not local_state.exists() or not login_db.exists():
        print("", end="")
        return 1

    key = get_encryption_key(local_state)
    # Copy DB — Chrome may lock original
    tmp = Path(tempfile.gettempdir()) / "jarvis_chrome_login.db"
    shutil.copy2(login_db, tmp)
    conn = sqlite3.connect(str(tmp))
    cur = conn.cursor()
    cur.execute(
        "SELECT origin_url, username_value, password_value FROM logins ORDER BY date_created DESC"
    )
    rows = cur.fetchall()
    conn.close()
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass

    for origin, user, pwd_blob in rows:
        o = (origin or "").lower()
        u = (user or "").lower()
        if origin_sub not in o and "accounts.google" not in o:
            continue
        if user_sub and user_sub not in u:
            continue
        if not pwd_blob:
            continue
        pwd = decrypt_value(pwd_blob, key or b"")
        if pwd:
            print(pwd, end="")
            return 0

    # Fallback: any google password for the user
    for origin, user, pwd_blob in rows:
        u = (user or "").lower()
        o = (origin or "").lower()
        if user_sub in u and ("google" in o or "gmail" in o):
            pwd = decrypt_value(pwd_blob or b"", key or b"")
            if pwd:
                print(pwd, end="")
                return 0

    print("", end="")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
