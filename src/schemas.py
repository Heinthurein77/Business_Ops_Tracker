"""
Pydantic request bodies for the JSON API. Response payloads are returned as
plain dicts (FastAPI's jsonable_encoder handles date/Decimal/datetime), so
only *incoming* request shapes are modeled here.
"""
import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# bcrypt only reads the first 72 BYTES of a password and silently ignores the
# rest, so a longer "password" would be weaker than it looks; the floor is a
# minimum length rather than a composition rule.
PASSWORD_MIN_BYTES = 12
PASSWORD_MAX_BYTES = 72
_USERNAME_PATTERN = re.compile(r"[a-z0-9_]{3,80}")

# Words nobody may start their password with (compared in lower case, so
# "Welcome123!x" and "PASSWORD-2026" are caught too, not just the exact word).
_PASSWORD_DENYLIST = (
    "welcome123!",
    "password",
    "password123",
    "admin",
    "chairman",
    "qwerty",
    "abcmib",
    "businessops",
)


def _reject_nul(value: str) -> str:
    # NUL bytes crash some DB drivers and bcrypt refuses them outright.
    if "\x00" in value:
        raise ValueError("Value contains invalid characters")
    return value


def password_policy_error(password: str, username: Optional[str] = None, current_password: Optional[str] = None) -> Optional[str]:
    """Returns a human-readable reason the password is unacceptable, or None
    if it is fine. Deliberately a plain function (not only a pydantic
    validator) so routers can return it as a 400 with a readable `detail`
    string — the frontend shows that string as-is, whereas a pydantic 422
    carries a list of error objects."""
    if "\x00" in password:
        return "Password contains invalid characters"
    size = len(password.encode("utf-8"))
    if size < PASSWORD_MIN_BYTES:
        return f"Password must be at least {PASSWORD_MIN_BYTES} characters"
    if size > PASSWORD_MAX_BYTES:
        return f"Password is too long — at most {PASSWORD_MAX_BYTES} bytes (accented and non-Latin characters count as more than one)"
    lowered = password.lower()
    if any(lowered.startswith(word) for word in _PASSWORD_DENYLIST):
        return "That password is too easy to guess — choose something less predictable"
    if len(set(lowered)) < 5:
        return "Password is too repetitive — use a mix of different characters"
    if username and len(username) >= 3 and username.lower() in lowered:
        return "Password must not contain your username"
    if current_password is not None and password == current_password:
        return "New password must be different from your current password"
    return None


class LoginRequest(BaseModel):
    username: str = Field(max_length=80)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username", "password")
    @classmethod
    def no_nul_bytes(cls, value: str) -> str:
        return _reject_nul(value)


class ForgotPasswordRequest(BaseModel):
    # No content validation on purpose: the endpoint answers every input the
    # same way, so a malformed name must not produce a different response.
    username: str = Field(max_length=80)


class ForcePasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    # Length/strength rules live in password_policy_error() (called by the
    # router) so violations come back as a readable 400; this only bounds the
    # payload and rejects NUL.
    new_password: str = Field(min_length=1, max_length=128)
    confirm_password: str = Field(max_length=128)

    @field_validator("current_password", "new_password", "confirm_password")
    @classmethod
    def no_nul_bytes(cls, value: str) -> str:
        return _reject_nul(value)


class MonthlyTargetRequest(BaseModel):
    target_month: str = Field(description="First day of the target month, ISO format e.g. 2026-08-01")
    monthly_revenue_target: float = Field(ge=0)
    # No ge=0 here, unlike the other three targets — a CEO can legitimately
    # target negative cash flow (e.g. a planned cash-burn month), and both
    # the CEO's own target form and the Chairman/HO override form already
    # allow negative input (no min="0") to match.
    monthly_cash_flow_target: float
    monthly_max_receivable: float = Field(ge=0)
    monthly_max_payable: float = Field(ge=0)


class FinancialNoteRequest(BaseModel):
    note_text: str = Field(min_length=1, max_length=2000)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    full_name: str = Field(min_length=1, max_length=150)
    role: str
    business_unit_id: Optional[int] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip().lower()
        # ASCII only: str.isalnum() accepts any Unicode letter/digit, which
        # allowed look-alike names (Cyrillic "а" for "a") and made the
        # attempted-username audit trail ambiguous.
        if not _USERNAME_PATTERN.fullmatch(value):
            raise ValueError("Username must be 3-80 characters: lowercase letters a-z, digits 0-9 and underscores only")
        return value

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        # Chairman is deliberately not creatable here — there's exactly one,
        # seeded at boot; adding another is a code change, not a self-service
        # admin action.
        if value not in ("CEO", "HO", "Accountant"):
            raise ValueError('role must be "CEO", "HO" or "Accountant"')
        return value


class SetUserActiveRequest(BaseModel):
    is_active: bool


class UpdateUserRequest(BaseModel):
    # The field is required-but-nullable on purpose: an edit must say what
    # the account's BU assignment should become (null = unassign, which
    # only group-level roles may end up in — the endpoint enforces the
    # per-role rules).
    business_unit_id: Optional[int]


class CeoProfileRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    executive_title: str = Field(min_length=1, max_length=120, default="Chief Executive Officer")
    phone_number: str = Field(min_length=1, max_length=30)
    email: Optional[str] = Field(default=None, max_length=255)
    date_of_birth: date
    address: str = Field(min_length=1)
    business_details: Optional[str] = Field(default=None, max_length=4000)
    # No profile_pic_url text field — the profile photo is a real file
    # upload (multipart), handled as a separate UploadFile parameter
    # alongside this form model, not a client-supplied URL string.

    @field_validator("full_name", "executive_title", "phone_number", "address")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        # min_length=1 above only counts raw characters, so a lone space
        # would pass it — stripped here instead so a whitespace-only value
        # is rejected as the empty field it actually is, rather than being
        # silently stored (User.full_name in particular then breaks the
        # frontend's "prefer full_name over username" display logic, which
        # treats any non-empty string as usable).
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, value: date) -> date:
        today = date.today()
        if value >= today:
            raise ValueError("Date of birth must be in the past")
        age_years = (today - value).days / 365.25
        if age_years < 18:
            raise ValueError("CEO profile requires an age of at least 18")
        if age_years > 100:
            raise ValueError("Date of birth is implausibly old — please check it")
        return value
