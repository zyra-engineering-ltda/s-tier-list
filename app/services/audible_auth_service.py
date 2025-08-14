# services/audible_auth_service.py
from __future__ import annotations

import os
import hashlib
from typing import Optional, Tuple, Dict

import audible


class AudibleAuthError(Exception):
    """Base error for Audible auth service."""


class PendingLoginNotFound(AudibleAuthError):
    """Raised when completing auth for a user without a pending login."""


class AudibleAuthService:
    """
    Service wrapper for Audible authentication flow (username/password + OTP/CVF),
    with simple file-based token persistence.

    Typical usage in MVC:
      service = AudibleAuthService(auth_dir="var/auth")
      client, status = service.start_auth(username, password, locale="us")
      if status == "verification_required":
          # prompt user for OTP/CVF in the controller and call complete_auth(...)
      else:
          # client is ready
    """

    def __init__(self, auth_dir: str = "auth_files"):
        self.auth_dir = auth_dir
        os.makedirs(self.auth_dir, exist_ok=True)
        # In-memory store for pending OTP/CVF steps
        self._pending_logins: Dict[str, Dict[str, str]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def start_auth(
        self,
        username: str,
        password: str,
        locale: str = "us",
    ) -> Tuple[Optional[audible.Client], Optional[str]]:
        """
        Start Audible authentication.

        Returns:
            (client, status)
              - If already authenticated: (Client, None)
              - If verification required: (None, "verification_required")
        """
        auth_file = self._auth_file_for(username)

        # If we already have a stored session, use it.
        if os.path.exists(auth_file):
            auth = audible.Authenticator.from_file(auth_file)
            return audible.Client(auth), None

        # Otherwise, store credentials for the verification step.
        self._pending_logins[username] = {"password": password, "locale": locale}
        return None, "verification_required"

    def complete_auth(
        self,
        username: str,
        verification_code: str,
        code_type: str = "otp",  # "otp" or "cvf"
    ) -> audible.Client:
        """
        Complete authentication after start_auth using the provided verification code.

        Args:
            username: Audible username (email).
            verification_code: Code provided by user (OTP or CVF).
            code_type: "otp" or "cvf".

        Raises:
            PendingLoginNotFound: if there was no pending login for this user.
            ValueError: if code_type is invalid.
        """
        if username not in self._pending_logins:
            raise PendingLoginNotFound("No pending login for this user.")

        data = self._pending_logins.pop(username)
        password = data["password"]
        locale = data["locale"]

        auth_file = self._auth_file_for(username)

        if code_type == "otp":
            def otp_callback():
                return verification_code

            auth = audible.Authenticator.from_login(
                username=username,
                password=password,
                locale=locale,
                otp_callback=otp_callback,
            )

        elif code_type == "cvf":
            def cvf_callback():
                return verification_code

            auth = audible.Authenticator.from_login(
                username=username,
                password=password,
                locale=locale,
                cvf_callback=cvf_callback,
            )
        else:
            raise ValueError("Invalid code_type. Use 'otp' or 'cvf'.")

        # Persist session for future reuse
        auth.to_file(auth_file)
        return audible.Client(auth)

    def get_client_if_authenticated(self, username: str) -> Optional[audible.Client]:
        """
        Convenience: return a client if we already have a stored session, else None.
        """
        auth_file = self._auth_file_for(username)
        if not os.path.exists(auth_file):
            return None
        auth = audible.Authenticator.from_file(auth_file)
        return audible.Client(auth)

    def sign_out(self, username: str) -> bool:
        """
        Remove stored session for the given user. Returns True if a session was removed.
        """
        auth_file = self._auth_file_for(username)
        if os.path.exists(auth_file):
            try:
                os.remove(auth_file)
                return True
            except OSError:
                return False
        return False

    def _auth_file_for(self, username: str) -> str:
        safe_name = hashlib.sha256(username.encode()).hexdigest()
        return os.path.join(self.auth_dir, f"auth_{safe_name}.json")
