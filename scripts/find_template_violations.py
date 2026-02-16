#!/usr/bin/env python3
"""Find modifications to template-owned files in a downstream app.

Reads the Copier configuration to determine which files are template-owned
(overwritten on `copier update`) vs app-owned (`_skip_if_exists`), evaluates
feature-flag exclusions against the app's `.copier-answers.yml`, and detects
drift using two complementary methods:

1. Git diff: modifications since the last `copier update` commit.
2. Source comparison: direct comparison of non-Jinja template source files
   against the app's current files (catches pre-existing divergence).

Usage:
    python scripts/find_template_violations.py /path/to/downstream/app
    python scripts/find_template_violations.py /path/to/downstream/app --diff
    python scripts/find_template_violations.py /path/to/downstream/app --commits --diff
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

import yaml


# ── Copier config parsing ────────────────────────────────────────────


def load_copier_config(template_repo: Path) -> dict:
    return yaml.safe_load((template_repo / "copier.yml").read_text())


def load_app_answers(app_dir: Path) -> dict:
    return yaml.safe_load((app_dir / ".copier-answers.yml").read_text())


def get_template_dir(template_repo: Path, config: dict) -> Path:
    subdir = config.get("_subdirectory", ".")
    return template_repo / subdir


def get_skip_if_exists(config: dict) -> set[str]:
    return set(config.get("_skip_if_exists", []))


def evaluate_excludes(config: dict, answers: dict) -> set[str]:
    """Evaluate Jinja conditionals in _exclude against the app's feature flags.

    Each entry looks like: {% if not use_database %}app/extensions.py{% endif %}
    When the condition is true (flag is false), the file IS excluded (not generated).
    """
    excluded = set()
    pattern = re.compile(
        r"\{%\s*if\s+not\s+(\w+)\s*%\}(.+?)\{%\s*endif\s*%\}"
    )

    for entry in config.get("_exclude", []):
        m = pattern.match(entry.strip())
        if not m:
            continue
        flag_name = m.group(1)
        file_path = m.group(2).strip()
        flag_value = answers.get(flag_name, False)
        if not flag_value:
            # Flag is false → file is excluded (not generated)
            excluded.add(file_path)

    return excluded


# ── Template file inventory ──────────────────────────────────────────


def enumerate_template_files(template_dir: Path) -> list[tuple[str, str, bool]]:
    """Walk the template directory and return (output_path, source_path, is_jinja) tuples.

    Skips the copier answers file template.
    """
    files = []
    for p in sorted(template_dir.rglob("*")):
        if not p.is_file():
            continue

        rel = str(p.relative_to(template_dir))

        # Skip the copier answers template itself
        if "{{" in rel or "{%" in rel:
            continue

        is_jinja = rel.endswith(".jinja")
        output_path = rel[: -len(".jinja")] if is_jinja else rel

        files.append((output_path, rel, is_jinja))

    return files


def classify_files(
    template_dir: Path,
    skip_if_exists: set[str],
    excluded: set[str],
) -> tuple[list[tuple[str, str, bool]], list[str], list[str]]:
    """Classify template files into template-owned, app-owned, and excluded.

    Returns (template_owned, app_owned, excluded_files) where template_owned
    contains (output_path, source_path, is_jinja) tuples.
    """
    all_files = enumerate_template_files(template_dir)

    template_owned = []
    app_owned = []
    excluded_files = []

    for output_path, source_path, is_jinja in all_files:
        if output_path in skip_if_exists:
            app_owned.append(output_path)
        elif output_path in excluded or any(
            output_path.startswith(e + "/") for e in excluded
        ):
            excluded_files.append(output_path)
        else:
            template_owned.append((output_path, source_path, is_jinja))

    return template_owned, app_owned, excluded_files


# ── Git operations ───────────────────────────────────────────────────


def find_copier_commit(app_dir: Path) -> str | None:
    """Find the most recent commit that changed .copier-answers.yml."""
    result = subprocess.run(
        ["git", "log", "--format=%H", "-1", "--", ".copier-answers.yml"],
        cwd=app_dir,
        capture_output=True,
        text=True,
    )
    commit = result.stdout.strip()
    return commit if commit else None


def file_modified_since(app_dir: Path, commit: str, file_path: str) -> bool:
    """Check if a file was modified between commit and HEAD."""
    result = subprocess.run(
        ["git", "diff", "--name-only", commit, "HEAD", "--", file_path],
        cwd=app_dir,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def get_diff(app_dir: Path, commit: str, file_path: str) -> str:
    """Get the actual diff for a file between commit and HEAD."""
    result = subprocess.run(
        ["git", "diff", commit, "HEAD", "--", file_path],
        cwd=app_dir,
        capture_output=True,
        text=True,
    )
    return result.stdout


def get_commit_log_for_file(
    app_dir: Path, since_commit: str, file_path: str
) -> list[str]:
    """Get commit messages that touched a file since a given commit."""
    result = subprocess.run(
        [
            "git",
            "log",
            "--oneline",
            f"{since_commit}..HEAD",
            "--",
            file_path,
        ],
        cwd=app_dir,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.strip().splitlines() if line]


def count_diff_lines(diff_text: str) -> tuple[int, int]:
    """Count added and removed lines in a unified diff."""
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


# ── Source comparison ────────────────────────────────────────────────


def compare_to_source(
    app_dir: Path,
    template_dir: Path,
    template_owned: list[tuple[str, str, bool]],
) -> tuple[list[dict], list[dict]]:
    """Compare non-Jinja template source files directly to app files.

    Returns (diverged, jinja_files) where:
    - diverged: list of dicts with file info for non-Jinja files that differ
    - jinja_files: list of dicts for Jinja files (can't compare directly)
    """
    diverged = []
    jinja_files = []

    for output_path, source_path, is_jinja in template_owned:
        app_file = app_dir / output_path
        template_file = template_dir / source_path

        if not app_file.exists():
            continue

        if is_jinja:
            jinja_files.append({"file": output_path, "source": source_path})
            continue

        # Direct byte comparison for non-Jinja files
        app_content = app_file.read_bytes()
        template_content = template_file.read_bytes()

        if app_content != template_content:
            # Generate a unified diff for display
            app_lines = app_file.read_text(errors="replace").splitlines(
                keepends=True
            )
            template_lines = template_file.read_text(
                errors="replace"
            ).splitlines(keepends=True)
            diff = list(
                difflib.unified_diff(
                    template_lines,
                    app_lines,
                    fromfile=f"template/{source_path}",
                    tofile=output_path,
                )
            )
            diff_text = "".join(diff)
            added, removed = count_diff_lines(diff_text)
            diverged.append(
                {
                    "file": output_path,
                    "source": source_path,
                    "added": added,
                    "removed": removed,
                    "diff": diff_text,
                }
            )

    return diverged, jinja_files


# ── Rendering helpers ────────────────────────────────────────────────


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def c(color: str, text: str) -> str:
    if supports_color():
        return f"{color}{text}{Colors.RESET}"
    return text


def print_diff(diff_text: str) -> None:
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            print(f"    {c(Colors.GREEN, line)}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"    {c(Colors.RED, line)}")
        elif line.startswith("@@"):
            print(f"    {c(Colors.CYAN, line)}")
        else:
            print(f"    {line}")
    print()


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find modifications to template-owned files in a downstream app.",
    )
    parser.add_argument(
        "app_dir",
        type=Path,
        help="Path to the downstream app directory",
    )
    parser.add_argument(
        "--template-repo",
        type=Path,
        default=None,
        help="Path to the template repo (default: resolved from .copier-answers.yml _src_path)",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show full diffs for modified files",
    )
    parser.add_argument(
        "--commits",
        action="store_true",
        help="Show commit log for each modified file",
    )
    args = parser.parse_args()

    app_dir: Path = args.app_dir.resolve()
    if not (app_dir / ".copier-answers.yml").exists():
        print(f"Error: {app_dir} does not contain .copier-answers.yml")
        sys.exit(1)

    # Load app answers
    answers = load_app_answers(app_dir)

    # Resolve template repo
    if args.template_repo:
        template_repo = args.template_repo.resolve()
    else:
        src_path = answers.get("_src_path", "")
        if src_path:
            template_repo = (app_dir / src_path).resolve()
        else:
            print(
                "Error: cannot determine template repo path. Use --template-repo."
            )
            sys.exit(1)

    if not (template_repo / "copier.yml").exists():
        print(f"Error: {template_repo} does not contain copier.yml")
        sys.exit(1)

    # Load copier config
    config = load_copier_config(template_repo)
    template_dir = get_template_dir(template_repo, config)
    skip_if_exists = get_skip_if_exists(config)
    excluded = evaluate_excludes(config, answers)

    # Classify files
    template_owned, app_owned, excluded_files = classify_files(
        template_dir, skip_if_exists, excluded
    )

    # Find the copier adoption commit
    copier_commit = find_copier_commit(app_dir)
    if not copier_commit:
        print("Error: no commits found that modified .copier-answers.yml")
        sys.exit(1)

    # Get short hash for display
    result = subprocess.run(
        ["git", "log", "--format=%h %s", "-1", copier_commit],
        cwd=app_dir,
        capture_output=True,
        text=True,
    )
    copier_commit_desc = result.stdout.strip()

    # ── Header ────────────────────────────────────────────────────

    print()
    print(c(Colors.BOLD, f"Template Violation Report: {app_dir.name}"))
    print(c(Colors.DIM, "=" * 60))
    print()

    # Feature flags
    flags = {
        k: v
        for k, v in answers.items()
        if k.startswith("use_") and isinstance(v, bool)
    }
    print(c(Colors.BOLD, "Feature flags:"))
    for flag, value in sorted(flags.items()):
        color = Colors.GREEN if value else Colors.DIM
        print(f"  {flag}: {c(color, str(value))}")
    print()

    print(c(Colors.BOLD, "File inventory:"))
    print(f"  Template-owned: {len(template_owned)} files")
    print(f"  App-owned (_skip_if_exists): {len(app_owned)} files")
    print(f"  Excluded by feature flags: {len(excluded_files)} files")
    print()

    print(c(Colors.BOLD, "Baseline commit (last copier update):"))
    print(f"  {copier_commit_desc}")
    print()

    had_findings = False

    # ── Section 1: Git diff (post-adoption changes) ──────────────

    git_violations = []
    missing = []

    for output_path, _source_path, _is_jinja in template_owned:
        full_path = app_dir / output_path
        if not full_path.exists():
            missing.append(output_path)
            continue

        if file_modified_since(app_dir, copier_commit, output_path):
            diff_text = get_diff(app_dir, copier_commit, output_path)
            added, removed = count_diff_lines(diff_text)
            commits = get_commit_log_for_file(
                app_dir, copier_commit, output_path
            )
            git_violations.append(
                {
                    "file": output_path,
                    "added": added,
                    "removed": removed,
                    "diff": diff_text,
                    "commits": commits,
                }
            )

    if git_violations:
        had_findings = True
        header = f"POST-ADOPTION CHANGES: {len(git_violations)} template-owned files modified since copier update"
        print(c(Colors.BOLD + Colors.RED, header))
        print(c(Colors.DIM, "-" * 60))
        print()

        for v in git_violations:
            size_desc = f"+{v['added']}/-{v['removed']}"
            print(
                f"  {c(Colors.YELLOW, v['file'])}  {c(Colors.DIM, size_desc)}"
            )

            if args.commits and v["commits"]:
                for commit_line in v["commits"]:
                    print(f"    {c(Colors.CYAN, commit_line)}")

            if (args.commits or args.diff) and (v["commits"] or v["diff"]):
                print()

            if args.diff and v["diff"]:
                print_diff(v["diff"])

        if not args.commits and not args.diff:
            print()
    else:
        print(
            c(
                Colors.GREEN,
                "No post-adoption changes. All template-owned files unchanged since copier update.",
            )
        )
        print()

    # ── Section 2: Source comparison (all divergence) ─────────────

    diverged, jinja_files = compare_to_source(
        app_dir, template_dir, template_owned
    )

    # Filter out files already reported in git violations (to avoid double-reporting)
    git_violation_files = {v["file"] for v in git_violations}
    pre_existing = [d for d in diverged if d["file"] not in git_violation_files]

    if pre_existing:
        had_findings = True
        header = f"PRE-EXISTING DIVERGENCE: {len(pre_existing)} non-Jinja template files differ from source"
        print(c(Colors.BOLD + Colors.YELLOW, header))
        print(
            c(
                Colors.DIM,
                "(These differences existed at adoption time or were introduced outside git.)",
            )
        )
        print(c(Colors.DIM, "-" * 60))
        print()

        for d in pre_existing:
            size_desc = f"+{d['added']}/-{d['removed']}"
            print(
                f"  {c(Colors.YELLOW, d['file'])}  {c(Colors.DIM, size_desc)}"
            )

            if args.diff and d["diff"]:
                print()
                print_diff(d["diff"])

        if not args.diff:
            print()

    # Always show Jinja files that can't be compared
    if jinja_files:
        jinja_non_git = [
            j for j in jinja_files if j["file"] not in git_violation_files
        ]
        if jinja_non_git:
            print(
                c(
                    Colors.BOLD + Colors.BLUE,
                    f"JINJA FILES: {len(jinja_non_git)} template files use Jinja (cannot compare directly)",
                )
            )
            print(
                c(
                    Colors.DIM,
                    "(These files are rendered by Copier with variable substitution. "
                    "Run `copier update --pretend` to check for drift.)",
                )
            )
            print(c(Colors.DIM, "-" * 60))
            for j in jinja_non_git:
                source = j["source"]
                print(f"  {c(Colors.DIM, j['file'])}  {c(Colors.DIM, f'← {source}')}")
            print()

    # Report diverged files also found in git violations
    also_diverged = [d for d in diverged if d["file"] in git_violation_files]
    if also_diverged:
        print(
            c(
                Colors.DIM,
                f"  ({len(also_diverged)} file(s) diverge from source AND were modified post-adoption — reported above.)",
            )
        )
        print()

    # ── Section 3: Missing files ─────────────────────────────────

    if missing:
        print(
            c(
                Colors.BOLD + Colors.YELLOW,
                f"MISSING: {len(missing)} template-owned files not found in app",
            )
        )
        print(c(Colors.DIM, "-" * 60))
        for f in missing:
            print(f"  {c(Colors.DIM, f)}")
        print()
        print(
            c(
                Colors.DIM,
                "  (May be expected if deleted intentionally or app predates these additions.)",
            )
        )
        print()

    # ── Summary ──────────────────────────────────────────────────

    total_files = len(template_owned)
    violation_files = git_violation_files | {d["file"] for d in pre_existing}
    missing_set = set(missing)
    clean_count = total_files - len(violation_files) - len(missing_set)
    # Don't double-count files that are both missing and would show as diverged
    jinja_only_count = len(
        [j for j in jinja_files if j["file"] not in violation_files and j["file"] not in missing_set]
    )

    print(c(Colors.BOLD, "Summary:"))
    print(
        f"  {c(Colors.GREEN, str(clean_count - jinja_only_count))} template-owned files match source exactly"
    )
    if jinja_only_count:
        print(
            f"  {c(Colors.BLUE, str(jinja_only_count))} Jinja-rendered files (need `copier update --pretend` to verify)"
        )
    if git_violations:
        print(
            f"  {c(Colors.RED, str(len(git_violations)))} files modified since last copier update"
        )
    if pre_existing:
        print(
            f"  {c(Colors.YELLOW, str(len(pre_existing)))} files with pre-existing divergence from template source"
        )
    if missing:
        print(
            f"  {c(Colors.YELLOW, str(len(missing)))} template-owned files missing"
        )
    print()

    if had_findings:
        sys.exit(1)


if __name__ == "__main__":
    main()
