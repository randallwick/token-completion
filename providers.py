"""Manage local copies of explicitly selected GitHub providers for token-completion."""

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unicodedata

GIT = "git"
ZSH = "zsh"
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
GITHUB = re.compile(r"github:([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*)@([^:]+):(.+)\Z")
FORMAT = 1


class Error(Exception):
    pass


def visible(value):
    """Keep a path, Git diagnostic or configured provider in one terminal cell row."""
    return "".join(c if c.isprintable() and unicodedata.category(c) != "Cf"
                   else ascii(c)[1:-1] for c in str(value))


def git_env():
    # Keep credentials, URL rewrites and transport policy, but never borrow a
    # caller's repository or object database for an install transaction.
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
    return env


def git(*args, cwd=None):
    result = subprocess.run([GIT, *args], cwd=cwd, env=git_env(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip() or "Git operation failed"
        raise Error(message)
    return result.stdout


@dataclass(frozen=True)
class Remote:
    repo: str
    ref: str
    path: str

    @classmethod
    def parse(cls, entry):
        match = GITHUB.fullmatch(entry)
        if not match or any(not c.isprintable() for c in entry):
            raise Error(f"invalid GitHub provider {entry!r}; use github:OWNER/REPO@REF:PATH")
        repo, ref, path = match.groups()
        if any(part in {"", ".", "..", ".git"} for part in path.split("/")) or "\\" in path:
            raise Error(f"expected a relative provider file inside {repo}: {path}")
        # The leading dash and checkout-history shorthand are unsuitable selections.
        if ref.startswith(("-", "@{")) or ref == "@" or ref.endswith(".lock"):
            raise Error(f"invalid Git ref {ref!r}")
        return cls(repo, ref, path)

    @property
    def url(self):
        return f"https://github.com/{self.repo}.git"

    def revision(self):
        if SHA.fullmatch(self.ref):
            return self.ref
        git("check-ref-format", f"refs/heads/{self.ref}")
        branch, tag = f"refs/heads/{self.ref}", f"refs/tags/{self.ref}"
        refs = {}
        for line in git("ls-remote", "--", self.url, branch, tag, tag + "^{}").decode().splitlines():
            revision, name = line.split("\t")
            refs[name] = revision
        if branch in refs and tag in refs:
            raise Error(f"{self.repo}: {self.ref!r} names both a branch and a tag; use a commit")
        revision = refs.get(branch) or refs.get(tag+"^{}") or refs.get(tag)
        if not revision or not SHA.fullmatch(revision):
            raise Error(f"{self.repo}: no branch or tag named {self.ref!r}")
        return revision


@dataclass(frozen=True)
class Pin:
    repo: str
    ref: str
    path: str
    commit: str
    tree: str
    provider: str

    @classmethod
    def checked(cls, entry, fields):
        remote = Remote.parse(entry)
        try:
            pin = cls(**fields)
        except TypeError as exc:
            raise Error(f"invalid lock entry for {entry}") from exc
        if (any(not isinstance(v, str) for v in asdict(pin).values()) or
                (pin.repo, pin.ref, pin.path) != (remote.repo, remote.ref, remote.path) or
                not SHA.fullmatch(pin.commit) or not SHA.fullmatch(pin.tree) or
                not IDENTIFIER.fullmatch(pin.provider)):
            raise Error(f"invalid lock entry for {entry}")
        return pin


def load_lock(path):
    try:
        content = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (UnicodeError, ValueError) as exc:
        raise Error(f"cannot read {path}: {exc}") from exc
    if not isinstance(content, dict) or content.get("version") != FORMAT or not isinstance(content.get("providers"), dict):
        raise Error(f"unsupported or malformed provider lock: {path}")
    return {entry: Pin.checked(entry, obj) for entry, obj in content["providers"].items()}


def write_lock(path, pins):
    data = {"version": FORMAT, "providers": {key: asdict(pin) for key, pin in sorted(pins.items())}}
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as tmp:
        json.dump(data, tmp, indent=2, ensure_ascii=True)
        tmp.write("\n")
    try:
        os.replace(tmp.name, path)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@contextmanager
def writer(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / ".provider-writer.lock").open("ab") as fp:
        fcntl.flock(fp, fcntl.LOCK_EX)
        yield


def git_hash(kind, data):
    h = hashlib.sha1(f"{kind} {len(data)}\0".encode())
    h.update(data)
    return h.digest()


def tree_hash(root):
    """Reconstruct Git's tree object from actual bytes, executable bits and links."""
    base = root.resolve()

    def directory(path):
        nodes = []
        for item in path.iterdir():
            name = os.fsencode(item.name)
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode):
                if Path(os.readlink(item)).is_absolute():
                    raise Error(f"expected a relative symlink in provider checkout: {item.relative_to(root)}")
                resolved = item.resolve(strict=True)
                if not resolved.is_relative_to(base):
                    raise Error(f"symlink leaves provider checkout: {item.relative_to(root)}")
                kind, digest = b"120000", git_hash("blob", os.fsencode(os.readlink(item)))
            elif stat.S_ISDIR(mode):
                kind, digest = b"40000", directory(item)
            elif stat.S_ISREG(mode):
                kind = b"100755" if mode & stat.S_IXUSR else b"100644"
                with item.open("rb") as fp:
                    h = hashlib.sha1(f"blob {os.fstat(fp.fileno()).st_size}\0".encode())
                    while chunk := fp.read(128 * 1024):
                        h.update(chunk)
                digest = h.digest()
            else:
                raise Error(f"unexpected file type in provider checkout: {item.relative_to(root)}")
            order = name + b"/" if kind == b"40000" else name
            nodes.append((order, kind + b" " + name + b"\0" + digest))
        return git_hash("tree", b"".join(record for _, record in sorted(nodes)))

    if root.is_symlink() or not root.is_dir():
        raise Error("missing provider checkout")
    return directory(root).hex()


ABI_CHECK = r"""
emulate -L zsh
unset TOKEN_PROVIDER_ID
builtin source "$1" >/dev/null || exit 1
[[ ${TOKEN_PROVIDER_ID:-} =~ '^[A-Za-z_][A-Za-z0-9_]*$' ]] || exit 1
version=token_provider_${TOKEN_PROVIDER_ID}_version
query=token_provider_${TOKEN_PROVIDER_ID}_query
[[ ${(P)version} == 1 ]] && (( ${+functions[$query]} )) || exit 1
print -rn -- "$TOKEN_PROVIDER_ID"
"""


def abi_id(path):
    result = subprocess.run([ZSH, "-dfc", ABI_CHECK, "provider-validation", str(path)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    name = result.stdout.decode(errors="replace")
    if result.returncode or not IDENTIFIER.fullmatch(name):
        raise Error(f"{path.name}: expected a provider ID, interface version 1 and a query function")
    return name


def provider_file(root, path):
    selected = (root / path).resolve(strict=True)
    if not selected.is_relative_to(root.resolve()) or not selected.is_file():
        raise Error(f"{path}: expected a regular file within the provider checkout")
    return selected


def object_path(cache, repo, commit):
    # One immutable tree per commit; a single repository can export several providers.
    return cache / (repo.replace("/", "+") + "-" + commit)


def file_at_pin(root, pin):
    if tree_hash(root) != pin.tree:
        raise Error("cached provider tree differs from lock")
    return provider_file(root, pin.path)


def checkout(remote, commit, temp):
    """Materialize exact Git blobs without worktree filters or archive attributes."""
    db, out = temp / "git", (temp / "source").resolve()
    git("init", "--bare", "--quiet", str(db))
    git("fetch", "--quiet", "--no-tags", "--depth=1", "--", remote.url, commit, cwd=db)
    if git("rev-parse", "FETCH_HEAD^{commit}", cwd=db).decode().strip() != commit:
        raise Error(f"{remote.repo}: fetched a different commit from the requested pin")
    tree = git("rev-parse", "FETCH_HEAD^{tree}", cwd=db).decode().strip()
    entries = git("ls-tree", "-rz", "FETCH_HEAD", cwd=db).split(b"\0")
    out.mkdir()
    with subprocess.Popen([GIT, "cat-file", "--batch"], cwd=db, env=git_env(),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE) as blobs:
        try:
            for row in filter(None, entries):
                metadata, name = row.split(b"\t", 1)
                mode, kind, oid = metadata.split()
                if kind != b"blob" or mode not in {b"100644", b"100755", b"120000"}:
                    raise Error(f"{remote.repo}: unsupported Git entry {os.fsdecode(name)}")
                parts = name.split(b"/")
                if any(part.lower() in {b"", b".", b"..", b".git"} for part in parts):
                    raise Error(f"{remote.repo}: invalid path in provider tree")
                item = out.joinpath(*map(os.fsdecode, parts))
                if item.parent.resolve() != item.parent or os.path.lexists(item):
                    raise Error(f"{remote.repo}: overlapping paths in provider checkout")
                item.parent.mkdir(parents=True, exist_ok=True)
                blobs.stdin.write(oid + b"\n")
                blobs.stdin.flush()
                header = blobs.stdout.readline().split()
                if len(header) != 3 or header[:2] != [oid, b"blob"]:
                    raise Error(f"{remote.repo}: missing Git blob")
                size = int(header[2])
                if mode == b"120000":
                    item.symlink_to(os.fsdecode(blobs.stdout.read(size)))
                else:
                    with item.open("xb") as fp:
                        while size:
                            chunk = blobs.stdout.read(min(size, 128 * 1024))
                            if not chunk:
                                raise Error(f"{remote.repo}: incomplete Git blob")
                            fp.write(chunk)
                            size -= len(chunk)
                    item.chmod(0o755 if mode == b"100755" else 0o644)
                if blobs.stdout.read(1) != b"\n":
                    raise Error(f"{remote.repo}: incomplete Git blob")
        finally:
            blobs.stdin.close()
    if tree_hash(out) != tree:
        raise Error(f"{remote.repo}: downloaded contents differ from commit {commit}")
    return out, tree


def ready_file(cache, pin):
    return file_at_pin(object_path(cache, pin.repo, pin.commit), pin)


def selected_pin(remote, commit, previous, cache, stage, pending):
    destination = object_path(cache, remote.repo, commit)
    if previous and previous.commit == commit and destination not in pending:
        try:
            ready_file(cache, previous)
            return previous
        except (Error, OSError, RuntimeError):
            pass
    if destination not in pending:
        scratch = Path(tempfile.mkdtemp(dir=stage))
        pending[destination] = checkout(remote, commit, scratch)
    source, tree = pending[destination]
    if previous and previous.commit == commit and tree != previous.tree:
        raise Error(f"{remote.repo}: downloaded Git tree differs from lock")
    if tree_hash(source) != tree:
        raise Error(f"{remote.repo}: provider checkout changed during validation")
    name = abi_id(provider_file(source, remote.path))
    if tree_hash(source) != tree:
        raise Error(f"{remote.repo}: provider source modified its checkout during validation")
    return Pin(**asdict(remote), commit=commit, tree=tree, provider=name)


def tint(text, color):
    colorize = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    return f"\033[{color}m{text}\033[0m" if colorize else text


def existing_id(entry):
    if os.path.isabs(entry):
        path = Path(entry)
        if not path.is_file():
            raise Error(f"missing local provider: {entry}")
        return abi_id(path)
    if not IDENTIFIER.fullmatch(entry):
        raise Error(f"expected a provider name, absolute file or GitHub reference: {entry}")
    return entry


def sync(lock, cache, entries, refresh, reserved):
    with ExitStack() as stack:
        stack.enter_context(writer(lock.parent))
        if cache.resolve() != lock.parent.resolve():
            stack.enter_context(writer(cache))
        stage = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=cache)))
        old = load_lock(lock)
        pins, pending, identifiers = {}, {}, set()
        revisions = {} if refresh else {(p.repo, p.ref): p.commit for p in old.values()}
        for entry in entries:
            if entry.startswith("github:"):
                remote = Remote.parse(entry)
                key = (remote.repo, remote.ref)
                if key not in revisions:
                    revisions[key] = remote.revision()
                commit = old[entry].commit if not refresh and entry in old else revisions[key]
                pin = selected_pin(remote, commit, old.get(entry), cache, stage, pending)
                pins[entry] = pin
                name = pin.provider
            else:
                name = existing_id(entry)
            if entry != name and name in reserved:
                raise Error(f"provider {name!r} is already registered in zsh")
            if name in identifiers:
                raise Error(f"provider {name!r} is selected more than once")
            identifiers.add(name)
        # Publish only verified objects; an interruption before the commit leaves
        # the old lock usable. Existing clean objects have stable absolute paths.
        for destination, (source, tree) in pending.items():
            if os.path.lexists(destination):
                try:
                    if tree_hash(destination) == tree:
                        continue
                except (Error, OSError, RuntimeError):
                    pass
                destination.rename(stage / ("replaced-" + destination.name))
            source.rename(destination)
        if pins != old or not lock.exists():
            write_lock(lock, pins)

    if not pins:
        print("No GitHub providers selected.")
    for entry, pin in pins.items():
        previous = old.get(entry)
        if previous and previous.commit != pin.commit:
            delta = f"{previous.commit[:12]} → {pin.commit[:12]}"
            label = "updated"
        else:
            delta = pin.commit[:12]
            label = "pinned" if previous else "installed"
        print(f"{tint(label, '32'):s}  {visible(entry)}  {delta}")


def inspect(entries, available, pins, cache, machine, unavailable):
    rows = []
    seen = set()
    for entry in entries:
        detail = ""
        state = "ready"
        if entry.startswith("github:"):
            pin = pins.get(entry)
            if pin is None:
                state, detail = "unpinned", "run providers install"
            else:
                try:
                    path = ready_file(cache, pin)
                except (Error, OSError, RuntimeError) as exc:
                    state, detail = "unavailable", f"{exc}; run providers install"
                else:
                    if machine:
                        sys.stdout.buffer.write(os.fsencode(entry) + b"\0" + os.fsencode(path) + b"\0")
                    detail = pin.commit[:12]
        elif os.path.isabs(entry):
            state = "local" if Path(entry).is_file() else "missing"
        elif entry in available:
            state = "loaded"
        else:
            state = "waiting"
            detail = "load the provider in zsh"
        if entry in unavailable:
            state, detail = "unavailable", unavailable[entry]
        elif entry in seen:
            state, detail = "duplicate", "already selected above"
        seen.add(entry)
        rows.append((entry, state, detail))
    if machine:
        return
    if not rows:
        print("No providers selected.")
        return
    width = max(8, max(len(visible(entry)) for entry, _, _ in rows))
    print(tint(f"{'PROVIDER':<{width}}  {'STATUS':<11}  PIN / DETAIL", "1"))
    for entry, state, detail in rows:
        badge = tint(f"{state:<11}", "36" if state in {"ready", "loaded", "local"} else "33")
        print(f"{visible(entry):<{width}}  {badge}  {visible(detail)}".rstrip())


def main():
    parser = argparse.ArgumentParser(prog="token-completion providers",
                                     description="Inspect providers and manage reproducible GitHub pins.")
    parser.add_argument("action", choices=["list", "install", "update", "resolve"])
    parser.add_argument("--available", action="append", default=[])
    parser.add_argument("--reserved", action="append", default=[])
    parser.add_argument("--unavailable", action="append", nargs=2, default=[])
    # The shell preserves the ordered provider array after the '--' delimiter.
    argv = sys.argv[1:]
    separator = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:separator])
    entries = argv[separator + 1:]
    config = Path(os.environ.get("TOKEN_COMPLETION_CONFIG_DIR") or
                  Path(os.environ.get("XDG_CONFIG_HOME") or Path.home()/".config")/"token-completion")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home()/".cache")/"token-completion"/"providers"
    lock = config/"providers.lock"
    try:
        if args.action in {"install", "update"}:
            sync(lock, cache, entries, args.action == "update", {"shell", "dotenv", "ini", "toml", "json", "yaml", *args.reserved})
        else:
            inspect(entries, args.available, load_lock(lock), cache, args.action == "resolve",
                    dict(args.unavailable))
    except (Error, OSError, RuntimeError) as exc:
        if args.action != "resolve":
            print(f"token-completion: {visible(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
