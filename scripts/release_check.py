from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BRANCH = "main"
EXPECTED_REMOTE_SUFFIX = "Darwin3961/coding-agent.git"

REQUIRED_FILES = (
    ".env.example",
    ".gitignore",
    "README.md",
    "README.txt",
    "requirements.txt",
    "main.py",
    "coding_agent/agent.py",
    "coding_agent/model.py",
    "coding_agent/registry.py",
    "coding_agent/policy.py",
    "coding_agent/session.py",
    "demo/scenarios.py",
    "demo/create_demo_workspace.py",
    "eval/run_eval.py",
    "docs/ARCHITECTURE.md",
    "docs/DEMO.md",
    "docs/VIDEO_SCRIPT.md",
    "docs/INTERVIEW_NOTES.md",
    "scripts/release_check.py",
    "scripts/create_submission_zip.py",
)

SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b",
        re.IGNORECASE,
    ),
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:\$env:)?"
    r"(?:DEEPSEEK_API_KEY|OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|AUTH_TOKEN)"
    r"[ \t]*=[ \t]*[\"']?([^\s\"'\\]+)",
)
SAFE_PLACEHOLDER_PARTS = (
    "fake",
    "test",
    "example",
    "placeholder",
    "redacted",
    "your-key",
    "changeme",
    "dummy",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class ReleaseChecker:
    """Run read-only release checks against one repository root."""

    def __init__(
        self,
        root: str | Path = PROJECT_ROOT,
        *,
        python_executable: str = sys.executable,
        command_runner: CommandRunner = subprocess.run,
    ):
        self.root = Path(root).expanduser().resolve()
        self.python_executable = python_executable
        self.command_runner = command_runner

    def _run(
        self,
        command: Sequence[str],
        *,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        return self.command_runner(
            list(command),
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
            check=False,
        )

    def _git(
        self,
        *arguments: str,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        return self._run(["git", *arguments], timeout=timeout)

    def _check_required_files(self) -> CheckResult:
        missing = [name for name in REQUIRED_FILES if not (self.root / name).is_file()]
        return CheckResult(
            "required files",
            not missing,
            "" if not missing else "missing: " + ", ".join(missing),
        )

    def _check_env_example(self) -> CheckResult:
        path = self.root / ".env.example"
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return CheckResult(".env.example", False, str(exc))

        for line in content.splitlines():
            if not line.strip().startswith("DEEPSEEK_API_KEY"):
                continue
            _, separator, value = line.partition("=")
            if not separator:
                return CheckResult(".env.example", False, "invalid key declaration")
            if value.strip():
                return CheckResult(".env.example", False, "API key must be empty")

        return CheckResult(".env.example", True)

    def _check_readme_length(self) -> CheckResult:
        try:
            content = (self.root / "README.txt").read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return CheckResult("README.txt length", False, str(exc))

        length = len(content)
        return CheckResult(
            "README.txt length",
            0 < length <= 1000,
            f"{length} characters",
        )

    def _check_ignored(self, path: str, label: str) -> CheckResult:
        try:
            result = self._git(
                "check-ignore",
                "-q",
                "--no-index",
                "--",
                path.rstrip("/") + "/",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return CheckResult(label, False, str(exc))

        return CheckResult(
            label,
            result.returncode == 0,
            "" if result.returncode == 0 else (result.stderr.strip() or "not ignored"),
        )

    @staticmethod
    def _looks_like_placeholder(value: str) -> bool:
        lowered = value.lower()
        return (
            value == ""
            or value.startswith("<")
            or value.startswith("${")
            or any(part in lowered for part in SAFE_PLACEHOLDER_PARTS)
        )

    @classmethod
    def _text_has_secret(cls, text: str) -> bool:
        for pattern in SECRET_VALUE_PATTERNS:
            for match in pattern.finditer(text):
                if not cls._looks_like_placeholder(match.group(0)):
                    return True

        for match in SECRET_ASSIGNMENT_PATTERN.finditer(text):
            if not cls._looks_like_placeholder(match.group(1)):
                return True

        return False

    def _check_tracked_secrets(self) -> CheckResult:
        try:
            listed = self._git("ls-files", "-z")
        except (OSError, subprocess.SubprocessError) as exc:
            return CheckResult("tracked secret scan", False, str(exc))

        if listed.returncode != 0:
            return CheckResult(
                "tracked secret scan",
                False,
                listed.stderr.strip() or "git ls-files failed",
            )

        flagged: list[str] = []
        for relative_name in filter(None, listed.stdout.split("\0")):
            path = (self.root / relative_name).resolve(strict=False)
            try:
                path.relative_to(self.root)
                raw = path.read_bytes()
            except (OSError, ValueError):
                continue

            if b"\0" in raw:
                continue

            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue

            if self._text_has_secret(text):
                flagged.append(relative_name)

        return CheckResult(
            "tracked secret scan",
            not flagged,
            "" if not flagged else "possible secret: " + ", ".join(flagged),
        )

    def _check_clean_tree(self) -> CheckResult:
        try:
            result = self._git("status", "--porcelain")
        except (OSError, subprocess.SubprocessError) as exc:
            return CheckResult("working tree clean", False, str(exc))

        return CheckResult(
            "working tree clean",
            result.returncode == 0 and not result.stdout.strip(),
            result.stderr.strip() or result.stdout.strip(),
        )

    def _check_branch(self) -> CheckResult:
        try:
            result = self._git("branch", "--show-current")
        except (OSError, subprocess.SubprocessError) as exc:
            return CheckResult("branch", False, str(exc))

        branch = result.stdout.strip()
        return CheckResult(
            "branch",
            result.returncode == 0 and branch == EXPECTED_BRANCH,
            branch or result.stderr.strip() or "branch unavailable",
        )

    def _check_origin(self) -> CheckResult:
        try:
            result = self._git("remote", "get-url", "origin")
        except (OSError, subprocess.SubprocessError) as exc:
            return CheckResult("origin URL", False, str(exc))

        origin = result.stdout.strip().replace("\\", "/")
        return CheckResult(
            "origin URL",
            result.returncode == 0 and origin.rstrip("/").endswith(
                EXPECTED_REMOTE_SUFFIX
            ),
            origin or result.stderr.strip() or "origin unavailable",
        )

    def _check_pytest(self) -> CheckResult:
        try:
            result = self._run(
                [self.python_executable, "-m", "pytest", "-q"],
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return CheckResult("pytest", False, str(exc))

        detail = result.stdout.strip().splitlines()
        return CheckResult(
            "pytest",
            result.returncode == 0,
            detail[-1] if detail else result.stderr.strip(),
        )

    def run(self) -> list[CheckResult]:
        return [
            self._check_required_files(),
            self._check_env_example(),
            self._check_readme_length(),
            self._check_ignored("traces", "traces ignored"),
            self._check_ignored("eval/results", "eval results ignored"),
            self._check_tracked_secrets(),
            self._check_clean_tree(),
            self._check_branch(),
            self._check_origin(),
            self._check_pytest(),
        ]


def print_results(results: list[CheckResult]) -> None:
    for result in results:
        label = "PASS" if result.passed else "FAIL"
        suffix = f" — {result.detail}" if result.detail else ""
        print(f"[{label}] {result.name}{suffix}")


def main() -> int:
    results = ReleaseChecker().run()
    print_results(results)

    if all(result.passed for result in results):
        print("Release check passed.")
        return 0

    print("Release check failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
