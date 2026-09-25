"""Tests for computer policy, bounds, action budgets, and parameter validation."""

from __future__ import annotations

import re
import pytest

from autonomous_agent.capability_policy import Capability, check_capability
from autonomous_agent.computer.models import (
    ActionBudget,
    ActionBudgetExceededError,
    ComputerSecurityError,
    DisplayInfo,
    ScreenRegion,
    redact_text,
)
from autonomous_agent.computer.policy import (
    ALLOWED_BUTTONS,
    ALLOWED_KEYS,
    BLOCKED_HOTKEYS,
    DENYLISTED_APPS,
    FORBIDDEN_METACHARS,
    MAX_ARGV_COUNT,
    MAX_ARG_LENGTH,
    MAX_CLIPBOARD_LENGTH,
    MAX_TEXT_LENGTH,
    is_windows,
    validate_app_launch,
    validate_click,
    validate_clipboard_text,
    validate_coordinates,
    validate_hotkey,
    validate_typed_text,
)
from autonomous_agent.consequence_policy import ApprovalMode, ConsequenceAwareApprovalPolicy
from autonomous_agent.prompt_injection_guard import TrustLevel
from autonomous_agent.tool_registry import REGISTRY, ReadWriteMode, RiskLevel


# =========================================================================
# 1. Action Budget Tests (15 tests)
# =========================================================================

def test_action_budget_init_default():
    budget = ActionBudget()
    assert budget.limit == 50
    assert budget.used == 0
    assert budget.remaining == 50


def test_action_budget_init_custom():
    budget = ActionBudget(limit=10)
    assert budget.limit == 10
    assert budget.remaining == 10


def test_action_budget_init_invalid():
    with pytest.raises(ValueError, match="positive"):
        ActionBudget(limit=0)
    with pytest.raises(ValueError, match="positive"):
        ActionBudget(limit=-5)


def test_action_budget_consume_single():
    budget = ActionBudget(limit=5)
    budget.consume()
    assert budget.used == 1
    assert budget.remaining == 4


def test_action_budget_consume_multiple():
    budget = ActionBudget(limit=10)
    budget.consume(3)
    assert budget.used == 3
    assert budget.remaining == 7
    budget.consume(4)
    assert budget.used == 7
    assert budget.remaining == 3


def test_action_budget_permits_exact_limit_action():
    """Bug B regression: the final permitted action must not be rejected."""
    budget = ActionBudget(limit=3)
    budget.consume(1)
    budget.consume(1)
    # The 3rd action (used becomes 3 == limit) MUST succeed
    budget.consume(1)
    assert budget.used == 3
    assert budget.remaining == 0


def test_action_budget_rejects_exceeding_limit():
    budget = ActionBudget(limit=3)
    budget.consume(3)
    with pytest.raises(ActionBudgetExceededError, match="action budget exceeded"):
        budget.consume(1)


def test_action_budget_rejects_overconsumption_in_one_step():
    budget = ActionBudget(limit=5)
    with pytest.raises(ActionBudgetExceededError):
        budget.consume(6)


def test_action_budget_consume_zero_or_negative_rejected():
    budget = ActionBudget(limit=10)
    with pytest.raises(ValueError, match="positive"):
        budget.consume(0)
    with pytest.raises(ValueError, match="positive"):
        budget.consume(-1)


def test_action_budget_reset():
    budget = ActionBudget(limit=5)
    budget.consume(4)
    assert budget.used == 4
    budget.reset()
    assert budget.used == 0
    assert budget.remaining == 5


def test_action_budget_consume_after_reset():
    budget = ActionBudget(limit=2)
    budget.consume(2)
    budget.reset()
    budget.consume(2)
    assert budget.used == 2


def test_action_budget_limit_one():
    budget = ActionBudget(limit=1)
    budget.consume(1)
    assert budget.remaining == 0
    with pytest.raises(ActionBudgetExceededError):
        budget.consume(1)


def test_action_budget_large_limit():
    budget = ActionBudget(limit=10_000)
    budget.consume(5_000)
    assert budget.remaining == 5_000


def test_action_budget_state_unchanged_on_rejection():
    budget = ActionBudget(limit=3)
    budget.consume(2)
    with pytest.raises(ActionBudgetExceededError):
        budget.consume(2)
    # used must stay 2, not increment to 4
    assert budget.used == 2


def test_action_budget_remaining_never_negative():
    budget = ActionBudget(limit=5)
    budget.consume(5)
    assert budget.remaining == 0


# =========================================================================
# 2. Coordinate and Screen Bounds Tests (15 tests)
# =========================================================================

@pytest.fixture
def standard_display() -> DisplayInfo:
    return DisplayInfo(width=1920, height=1080)


def test_validate_coordinates_valid_origin(standard_display):
    assert validate_coordinates(0, 0, standard_display) == (0, 0)


def test_validate_coordinates_valid_max(standard_display):
    assert validate_coordinates(1920, 1080, standard_display) == (1920, 1080)


def test_validate_coordinates_valid_midpoint(standard_display):
    assert validate_coordinates(960, 540, standard_display) == (960, 540)


def test_validate_coordinates_negative_x(standard_display):
    with pytest.raises(ComputerSecurityError, match="negative"):
        validate_coordinates(-1, 500, standard_display)


def test_validate_coordinates_negative_y(standard_display):
    with pytest.raises(ComputerSecurityError, match="negative"):
        validate_coordinates(500, -1, standard_display)


def test_validate_coordinates_x_overflow(standard_display):
    with pytest.raises(ComputerSecurityError, match="exceeds display"):
        validate_coordinates(1921, 500, standard_display)


def test_validate_coordinates_y_overflow(standard_display):
    with pytest.raises(ComputerSecurityError, match="exceeds display"):
        validate_coordinates(500, 1081, standard_display)


def test_validate_coordinates_string_ints(standard_display):
    assert validate_coordinates("100", "200", standard_display) == (100, 200)


def test_validate_coordinates_invalid_string(standard_display):
    with pytest.raises(ComputerSecurityError, match="integers"):
        validate_coordinates("abc", "200", standard_display)


def test_validate_coordinates_float_rejected(standard_display):
    with pytest.raises(ComputerSecurityError, match="integers"):
        validate_coordinates("12.5", 200, standard_display)


def test_screen_region_valid():
    r = ScreenRegion(0, 0, 800, 600)
    assert r.x == 0 and r.y == 0 and r.width == 800 and r.height == 600


def test_screen_region_negative_x():
    with pytest.raises(ComputerSecurityError, match="negative"):
        ScreenRegion(-10, 0, 100, 100)


def test_screen_region_negative_y():
    with pytest.raises(ComputerSecurityError, match="negative"):
        ScreenRegion(0, -5, 100, 100)


def test_screen_region_zero_width():
    with pytest.raises(ComputerSecurityError, match="positive"):
        ScreenRegion(0, 0, 0, 100)


def test_screen_region_negative_height():
    with pytest.raises(ComputerSecurityError, match="positive"):
        ScreenRegion(0, 0, 100, -50)


# =========================================================================
# 3. Mouse Click Validation Tests (10 tests)
# =========================================================================

def test_validate_click_default(standard_display):
    action = validate_click(100, 200, "left", 1, standard_display)
    assert action.action == "click"
    assert action.x == 100 and action.y == 200
    assert action.button == "left"
    assert action.clicks == 1


@pytest.mark.parametrize("button", ["left", "right", "middle"])
def test_validate_click_buttons(button, standard_display):
    action = validate_click(50, 50, button, 1, standard_display)
    assert action.button == button


def test_validate_click_invalid_button(standard_display):
    with pytest.raises(ComputerSecurityError, match="unsupported mouse button"):
        validate_click(50, 50, "wheel", 1, standard_display)


@pytest.mark.parametrize("clicks", [1, 2, 3])
def test_validate_click_valid_counts(clicks, standard_display):
    action = validate_click(50, 50, "left", clicks, standard_display)
    assert action.clicks == clicks


def test_validate_click_zero_clicks(standard_display):
    with pytest.raises(ComputerSecurityError, match="safe range"):
        validate_click(50, 50, "left", 0, standard_display)


def test_validate_click_excessive_clicks(standard_display):
    with pytest.raises(ComputerSecurityError, match="safe range"):
        validate_click(50, 50, "left", 4, standard_display)


def test_validate_click_none_coordinates(standard_display):
    action = validate_click(None, None, "right", 1, standard_display)
    assert action.x == 0 and action.y == 0
    assert action.button == "right"


def test_validate_click_out_of_bounds_coords(standard_display):
    with pytest.raises(ComputerSecurityError, match="exceeds display"):
        validate_click(2000, 500, "left", 1, standard_display)


def test_validate_click_string_clicks(standard_display):
    action = validate_click(10, 10, "left", "2", standard_display)
    assert action.clicks == 2


def test_validate_click_invalid_clicks_type(standard_display):
    with pytest.raises(ComputerSecurityError, match="integer"):
        validate_click(10, 10, "left", "many", standard_display)


# =========================================================================
# 4. Process Launch Validation Tests (20 tests)
# =========================================================================

def test_validate_app_launch_valid():
    app, argv = validate_app_launch("notepad.exe", ["file.txt"])
    assert app == "notepad.exe"
    assert argv == ("file.txt",)


def test_validate_app_launch_empty_app():
    with pytest.raises(ComputerSecurityError, match="empty"):
        validate_app_launch("   ")


@pytest.mark.parametrize("denied", [
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "bash", "bash.exe", "sh", "sh.exe", "wsl", "wsl.exe", "zsh",
    "cscript.exe", "wscript.exe", "mshta.exe", "certutil.exe", "reg.exe",
    "regedit.exe", "rundll32.exe", "installutil.exe", "bitsadmin.exe",
    "vssadmin.exe", "format.com", "diskpart.exe", "curl.exe", "wget.exe",
])
def test_validate_app_launch_denylisted_basenames(denied):
    with pytest.raises(ComputerSecurityError, match="denylisted"):
        validate_app_launch(denied)


def test_validate_app_launch_denylisted_path_windows():
    with pytest.raises(ComputerSecurityError, match="denylisted"):
        validate_app_launch("C:\\Windows\\System32\\cmd.exe")


def test_validate_app_launch_denylisted_path_posix():
    with pytest.raises(ComputerSecurityError, match="denylisted"):
        validate_app_launch("/usr/bin/bash")


@pytest.mark.parametrize("char", [";", "&", "|", "`", "$", ">", "<", "\n", "\r"])
def test_validate_app_launch_metachars_in_app(char):
    with pytest.raises(ComputerSecurityError, match="forbidden metacharacter"):
        validate_app_launch(f"notepad.exe{char}calc.exe")


@pytest.mark.parametrize("char", [";", "&", "|", "`", "$", ">", "<", "\n", "\r"])
def test_validate_app_launch_metachars_in_argv(char):
    with pytest.raises(ComputerSecurityError, match="forbidden metacharacter"):
        validate_app_launch("notepad.exe", [f"doc.txt{char}echo pwned"])


def test_validate_app_launch_too_many_argv():
    args = [f"arg{i}" for i in range(MAX_ARGV_COUNT + 1)]
    with pytest.raises(ComputerSecurityError, match="count exceeds"):
        validate_app_launch("notepad.exe", args)


def test_validate_app_launch_arg_too_long():
    long_arg = "a" * (MAX_ARG_LENGTH + 1)
    with pytest.raises(ComputerSecurityError, match="exceeds max length"):
        validate_app_launch("notepad.exe", [long_arg])


def test_validate_app_launch_non_string_arg():
    with pytest.raises(ComputerSecurityError, match="strings"):
        validate_app_launch("notepad.exe", [123])  # type: ignore


def test_validate_app_launch_non_sequence_argv():
    with pytest.raises(ComputerSecurityError, match="list or tuple"):
        validate_app_launch("notepad.exe", "single-string")  # type: ignore


def test_validate_app_launch_allowed_app_with_spaces():
    app, argv = validate_app_launch("C:\\Program Files\\App\\app.exe", ["--mode", "safe"])
    assert app == "C:\\Program Files\\App\\app.exe"
    assert argv == ("--mode", "safe")


def test_validate_app_launch_null_byte_rejection():
    with pytest.raises(ComputerSecurityError, match="forbidden metacharacter"):
        validate_app_launch("app.exe\x00extra")


# =========================================================================
# 5. Hotkey Validation Tests (10 tests)
# =========================================================================

def test_validate_hotkey_ctrl_c():
    assert validate_hotkey(["ctrl", "c"]) == ("ctrl", "c")


def test_validate_hotkey_alt_tab():
    assert validate_hotkey(["alt", "tab"]) == ("alt", "tab")


def test_validate_hotkey_case_insensitive():
    assert validate_hotkey(["CTRL", "Shift", "Escape"]) == ("ctrl", "shift", "escape")


def test_validate_hotkey_empty():
    with pytest.raises(ComputerSecurityError, match="non-empty"):
        validate_hotkey([])


def test_validate_hotkey_unknown_key():
    with pytest.raises(ComputerSecurityError, match="unrecognized"):
        validate_hotkey(["ctrl", "super_secret_key"])


@pytest.mark.parametrize("blocked", [
    ["ctrl", "alt", "delete"],
    ["control", "alt", "delete"],
    ["win", "l"],
    ["windows", "l"],
])
def test_validate_hotkey_blocked_combinations(blocked):
    with pytest.raises(ComputerSecurityError, match="blocked by security policy"):
        validate_hotkey(blocked)


def test_validate_hotkey_function_keys():
    for f in range(1, 13):
        assert validate_hotkey([f"f{f}"]) == (f"f{f}",)


def test_validate_hotkey_digits():
    assert validate_hotkey(["ctrl", "1"]) == ("ctrl", "1")


def test_validate_hotkey_non_string_item():
    with pytest.raises(ComputerSecurityError, match="non-empty string"):
        validate_hotkey(["ctrl", 1])  # type: ignore


# =========================================================================
# 6. Text and Clipboard Validation / Redaction Tests (10 tests)
# =========================================================================

def test_validate_typed_text_valid():
    assert validate_typed_text("Hello, world!") == "Hello, world!"


def test_validate_typed_text_too_long():
    with pytest.raises(ComputerSecurityError, match="exceeds max allowed length"):
        validate_typed_text("a" * (MAX_TEXT_LENGTH + 1))


def test_validate_typed_text_non_string():
    with pytest.raises(ComputerSecurityError, match="must be a string"):
        validate_typed_text(12345)  # type: ignore


@pytest.mark.parametrize("secret_text", [
    "api_key: secret_12345",
    "password=supersecret",
    "Authorization: Bearer token_987",
    "Client_Secret: sec_abc",
    "Bearer ya29.test_token",
])
def test_validate_typed_text_detects_credentials(secret_text):
    with pytest.raises(ComputerSecurityError, match="credentials must never leak"):
        validate_typed_text(secret_text)


def test_validate_clipboard_text_valid():
    assert validate_clipboard_text("some safe text") == "some safe text"


def test_validate_clipboard_text_too_long():
    with pytest.raises(ComputerSecurityError, match="exceeds max allowed length"):
        validate_clipboard_text("x" * (MAX_CLIPBOARD_LENGTH + 1))


def test_validate_clipboard_text_detects_credentials():
    with pytest.raises(ComputerSecurityError, match="credentials must never leak"):
        validate_clipboard_text("token: my_access_token_12345")


def test_redact_text_safe_no_secrets():
    assert redact_text("regular text without secrets") == "regular text without secrets"


def test_redact_text_redacts_password_and_token():
    """Bug A regression: ensure regex substitution handles capture groups safely."""
    raw = "User config: api_key=abc12345 and password: mysecretpassword"
    redacted = redact_text(raw)
    assert "abc12345" not in redacted
    assert "mysecretpassword" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_text_redacts_bearer_token():
    raw = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.xyz"
    redacted = redact_text(raw)
    assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
    assert "[REDACTED]" in redacted


# =========================================================================
# 7. Consequence Policy & Risk Classification Tests (10 tests)
# =========================================================================

@pytest.fixture
def policy() -> ConsequenceAwareApprovalPolicy:
    return ConsequenceAwareApprovalPolicy()


def test_read_only_computer_tools_autonomous(policy):
    for tool_name in [
        "computer.screen.capture",
        "computer.window.list",
        "computer.window.active",
        "computer.window.focus",
        "computer.mouse.move",
        "computer.clipboard.read",
    ]:
        spec = REGISTRY.get(tool_name)
        assert spec is not None
        assert spec.read_write_mode is ReadWriteMode.READ_ONLY
        assert spec.safe_autonomous is True
        decision = policy.evaluate(spec, explicitly_approved=False)
        assert decision.mode is ApprovalMode.AUTONOMOUS


def test_mutating_computer_tools_require_approval(policy):
    for tool_name in [
        "computer.app.launch",
        "computer.mouse.click",
        "computer.keyboard.type",
        "computer.keyboard.hotkey",
        "computer.clipboard.write",
    ]:
        spec = REGISTRY.get(tool_name)
        assert spec is not None
        assert spec.read_write_mode is ReadWriteMode.CONTROLLED_WRITE
        assert spec.safe_autonomous is False
        decision = policy.evaluate(spec, explicitly_approved=False)
        assert decision.mode is ApprovalMode.REQUIRE_APPROVAL


def test_mutating_computer_tools_permitted_with_approval(policy):
    for tool_name in [
        "computer.app.launch",
        "computer.mouse.click",
        "computer.keyboard.type",
        "computer.keyboard.hotkey",
        "computer.clipboard.write",
    ]:
        spec = REGISTRY.get(tool_name)
        decision = policy.evaluate(spec, explicitly_approved=True)
        assert decision.mode is ApprovalMode.AUTONOMOUS


def test_untrusted_origin_requires_approval_for_writes(policy):
    spec = REGISTRY.get("computer.mouse.click")
    decision = policy.evaluate(spec, origin_trust=TrustLevel.EXTERNAL, explicitly_approved=False)
    assert decision.mode is ApprovalMode.REQUIRE_APPROVAL


def test_computer_capability_is_grantable():
    decision = check_capability("computer", granted=["computer"])
    assert decision.allowed


def test_computer_capability_denied_without_grant():
    decision = check_capability("computer", granted=["filesystem_workspace"])
    assert not decision.allowed
