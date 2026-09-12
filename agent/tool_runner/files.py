"""Descriptor-rooted POSIX file operations with conditional atomic mutation."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import threading

from agent.protocol import CoreError, new_id


def digest(data):
    return hashlib.sha256(data).hexdigest()


class WorkspaceFs:
    def __init__(self, root, *, max_bytes=1048576, max_entries=1000):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir() or type(max_bytes) is not int or max_bytes < 1 or type(max_entries) is not int or max_entries < 1:
            raise CoreError("invalid_root", "Invalid workspace or limits")
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._lock = threading.RLock()

    @contextmanager
    def _parent(self, path, *, create=False):
        if not isinstance(path, str) or not path or "\x00" in path:
            raise CoreError("path_escape", "A relative path is required")
        parsed = Path(path)
        if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
            raise CoreError("path_escape", "Path must remain in the workspace")
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parsed.parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd, parsed.name
        except OSError as exc:
            raise CoreError("path_error", "Rooted file operation failed", errno=exc.errno) from exc
        finally:
            os.close(fd)

    def _read_at(self, fd, name):
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(handle, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CoreError("path_error", "Only regular files can be read")
            data = stream.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise CoreError("output_limit", "File exceeds configured byte limit")
        return data

    def read(self, path):
        with self._parent(path) as (fd, name):
            return self._read_at(fd, name)

    def _check(self, fd, name, expected):
        try:
            before = self._read_at(fd, name)
        except FileNotFoundError:
            before = None
        actual = digest(before) if before is not None else None
        if expected != actual:
            raise CoreError("precondition", "File preimage changed", expected=expected, actual=actual)
        return actual

    def write(self, path, data, *, expected_sha256=None):
        if not isinstance(data, bytes) or len(data) > self.max_bytes:
            raise CoreError("validation", "File data must be bounded bytes")
        with self._lock, self._parent(path, create=True) as (fd, name):
            before = self._check(fd, name, expected_sha256)
            temporary = new_id("write")
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
            return {"path": path, "before_sha256": before, "after_sha256": digest(data), "bytes": len(data)}

    def delete(self, path, *, expected_sha256):
        with self._lock, self._parent(path) as (fd, name):
            before = self._check(fd, name, expected_sha256)
            if before is None:
                raise CoreError("precondition", "Cannot delete an absent file")
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)
            return {"path": path, "before_sha256": before, "after_sha256": None}

    def copy(self, source, destination, *, expected_source, expected_destination=None):
        with self._lock:
            data = self.read(source)
            if digest(data) != expected_source:
                raise CoreError("precondition", "Copy source changed")
            return {**self.write(destination, data, expected_sha256=expected_destination), "source": source}

    def rename(self, source, destination, *, expected_source, expected_destination=None):
        with self._lock, self._parent(source) as (source_fd, source_name), self._parent(destination, create=True) as (dest_fd, dest_name):
            before = self._check(source_fd, source_name, expected_source)
            self._check(dest_fd, dest_name, expected_destination)
            if before is None:
                raise CoreError("precondition", "Cannot rename an absent file")
            os.replace(source_name, dest_name, src_dir_fd=source_fd, dst_dir_fd=dest_fd)
            os.fsync(source_fd)
            os.fsync(dest_fd)
            return {"path": destination, "source": source, "after_sha256": before}

    def edit(self, path, old, new, *, expected_sha256):
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise CoreError("validation", "Edit requires nonempty old text and string replacement")
        with self._lock:
            data = self.read(path)
            try:
                text = data.decode()
            except UnicodeError as exc:
                raise CoreError("validation", "Text edit requires UTF-8") from exc
            if text.count(old) != 1:
                raise CoreError("precondition", "Edit target must match exactly once")
            return self.write(path, text.replace(old, new).encode(), expected_sha256=expected_sha256)

    def list(self, path="."):
        if path == ".":
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        else:
            with self._parent(path) as (parent_fd, name):
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            names = []
            with os.scandir(fd) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names) > self.max_entries:
                        raise CoreError("output_limit", "Directory exceeds entry limit")
            result = []
            for name in sorted(names):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                result.append({"name": name, "directory": stat.S_ISDIR(info.st_mode),
                               "file": stat.S_ISREG(info.st_mode), "bytes": info.st_size})
            return result
        finally:
            os.close(fd)

    def search(self, needle, *, path="."):
        if not isinstance(needle, str) or not needle:
            raise CoreError("validation", "A search term is required")
        pending, matches, seen, total, directories = [path], [], 0, 0, 0
        while pending:
            directories += 1
            if directories > self.max_entries:
                raise CoreError("output_limit", "Search exceeds directory budget")
            directory = pending.pop()
            for entry in self.list(directory):
                relative = str(Path(directory) / entry["name"])
                if entry["directory"]:
                    pending.append(relative)
                elif entry["file"]:
                    seen += 1
                    data = self.read(relative)
                    total += len(data)
                    if seen > self.max_entries or total > self.max_bytes:
                        raise CoreError("output_limit", "Search exceeds workspace budget")
                    for line, text in enumerate(data.decode(errors="replace").splitlines(), 1):
                        if needle in text:
                            matches.append({"path": relative, "line": line, "text": text})
                            if len(matches) > self.max_entries:
                                raise CoreError("output_limit", "Search exceeds result budget")
        return matches
