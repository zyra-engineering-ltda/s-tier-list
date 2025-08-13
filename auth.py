import audible
import os
import hashlib

AUTH_DIR = "auth_files"
os.makedirs(AUTH_DIR, exist_ok=True)

# In-memory store for pending OTP logins
pending_logins = {}

def _get_auth_filename(username: str) -> str:
    """
    Create a safe filename for storing user's auth file.
    """
    safe_name = hashlib.sha256(username.encode()).hexdigest()
    return os.path.join(AUTH_DIR, f"auth_{safe_name}.json")

def start_auth(username: str, password: str, locale: str = "us"):
    """
    Start Audible authentication.
    If OTP or CVF verification is required, store credentials for later.
    """
    auth_file = _get_auth_filename(username)

    # Already authenticated
    if os.path.exists(auth_file):
        auth = audible.Authenticator.from_file(auth_file)
        return audible.Client(auth), None

    # Save credentials to use after verification
    pending_logins[username] = {
        "password": password,
        "locale": locale
    }
    return None, "verification_required"

def complete_auth(username: str, verification_code: str, code_type="otp"):
    """
    Complete Audible authentication using stored credentials + verification code.
    code_type can be 'otp' or 'cvf' depending on verification method.
    """
    if username not in pending_logins:
        raise ValueError("No pending login for this user.")

    data = pending_logins.pop(username)
    password = data["password"]
    locale = data["locale"]

    auth_file = _get_auth_filename(username)

    # Create appropriate callback for the verification type
    if code_type == "otp":
        def otp_callback():
            return verification_code
        auth = audible.Authenticator.from_login(
            username=username,
            password=password,
            locale=locale,
            otp_callback=otp_callback
        )

    elif code_type == "cvf":
        def cvf_callback():
            return verification_code
        auth = audible.Authenticator.from_login(
            username=username,
            password=password,
            locale=locale,
            cvf_callback=cvf_callback
        )
    else:
        raise ValueError("Invalid code_type")

    auth.to_file(auth_file)
    return audible.Client(auth)
