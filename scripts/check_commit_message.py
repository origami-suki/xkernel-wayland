#!/usr/bin/env python3
"""Validate the project's commit-message convention; does not certify tests."""

import re
import sys
from pathlib import Path

TYPES = "feat|fix|test|build|ci|docs|refactor|perf|chore"
SCOPES = "project|env|rootfs|kernel|ipc|mm|fs|graphics|input|chromium|observe|tests|docs|ci"


def validate(message):
    lines = message.splitlines()
    subject = lines[0] if lines else ""
    errors = []
    if len(subject) > 100 or not re.fullmatch(
        rf"({TYPES})\(({SCOPES})\): \S.*", subject
    ):
        errors.append("首行需为 type(scope): summary，类型/范围见 CONTRIBUTING.md，最多 100 字符")
    if len(lines) < 2 or lines[1] != "":
        errors.append("首行与正文之间必须留空行")
    if not re.search(r"^Task: M[0-6]-\d{3}$", message, re.MULTILINE):
        errors.append("缺少 Task: M0-001 格式的任务编号")
    if not re.search(r"^Tests: \S.*$", message, re.MULTILINE):
        errors.append("缺少 Tests: 实际验证结果")
    return errors


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法：python3 scripts/check_commit_message.py <message-file>")
    problems = validate(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if problems:
        sys.exit("\n".join(problems))
