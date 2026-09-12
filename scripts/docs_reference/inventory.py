"""Discover authored inputs without consulting a generated reference manifest."""

from pathlib import Path


ROOTS = ("agent", "consumer", "native", "frontend", "tests", "scripts", "packaging", "documentation", "docs", "spec")
IGNORED_DIRECTORIES = {
    "__pycache__", ".pytest_cache", "node_modules", ".git", ".astro", "dist",
    ".cache", "htmlcov", "coverage", ".venv", "vendor",
}
ROOT_CONFIG = {
    "CMakeLists.txt", "Makefile", "Dockerfile", ".gitignore", ".cgignore",
    ".editorconfig", ".prettierignore", ".prettierrc", "README.md", "AGENTS.md", "CLAUDE.md",
}
ROOT_SUFFIXES = {".toml", ".json", ".yaml", ".yml", ".lock", ".ini", ".cfg",
                ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".sh", ".ps1", ".c", ".cpp", ".h", ".hpp"}


class ReferenceError(ValueError):
    """A reference cannot honestly account for the current repository inputs."""


def exclusion(path: str) -> str | None:
    """Classify known generated, third-party, or sensitive paths before reading."""
    parts = Path(path).parts
    if any(part in IGNORED_DIRECTORIES for part in parts):
        return "generated/cache/dependency directory"
    if any(part == ".env" or part.startswith(".env.") for part in parts):
        return "local secrets; contents are never read"
    if path in {".mcp.json", ".coverage"}:
        return "local integration or runtime state"
    if path.startswith(("consumer/web/assets/", "consumer/bin/", "frontend/licenses/", "frontend/public/")):
        return "generated binary or redistributed asset/license"
    if path.startswith("documentation/") and "/public/fonts/" in path:
        return "redistributed font assets and licenses"
    if path.startswith("documentation/") and "/src/content/docs/" in path:
        return "documentation content; excludes generated reference recursion"
    if path.startswith("documentation/") and path.endswith((".md", ".mdx")):
        return "narrative documentation; documented in the site rather than recursive source references"
    if path.startswith("spec/") and Path(path).name in {"spec.kvx", "spec.kvx.lock", "tasks.md", "requirements.md", "design.md"}:
        return "mutable task coordination or generated spec mirror; stable contracts, workflow and executable wrapper remain included"
    if path.endswith((".pyc", ".pyo", ".tsbuildinfo", ".map")):
        return "generated compiler output"
    return None


def inventory(root: Path) -> dict:
    """Walk all declared source trees, recording every inclusion and exclusion."""
    root = root.resolve()
    included, excluded = [], []

    def walk(path):
        relative = path.relative_to(root).as_posix()
        reason = exclusion(relative + "/" if path.is_dir() else relative)
        if reason:
            excluded.append({"path": relative, "reason": reason})
            return
        if path.is_symlink():
            raise ReferenceError(f"Authored input is a symlink: {relative}")
        if path.is_dir():
            for child in sorted(path.iterdir()):
                walk(child)
        elif path.is_file():
            included.append(relative)
        else:
            raise ReferenceError(f"Authored input is not a regular file: {relative}")

    for name in ROOTS:
        path = root / name
        if path.exists():
            walk(path)
    for path in sorted(root.iterdir()):
        if not path.is_dir() and (path.name in ROOT_CONFIG or path.suffix in ROOT_SUFFIXES or exclusion(path.name)):
            walk(path)
    if len(included) != len(set(included)):
        raise ReferenceError("Duplicate independently discovered input")
    return {
        "files": sorted(included), "excluded": sorted(excluded, key=lambda row: row["path"]),
        "policy": {"recursive_roots": list(ROOTS), "root_names": sorted(ROOT_CONFIG),
                   "root_suffixes": sorted(ROOT_SUFFIXES),
                   "scope": "Every regular file in authored implementation/config/test/build trees; runtime state, secrets, dependencies and generated content are excluded explicitly."},
    }
