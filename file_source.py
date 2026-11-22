"""Enumerate one document level or resolve one exact document path."""

import configparser
import datetime
from dataclasses import dataclass
import io
import json
from pathlib import Path
import sys
from urllib.parse import quote
from typing import Callable


def parse_ini(data):
    parser = configparser.ConfigParser(interpolation=None, default_section=None)
    parser.optionxform = str
    parser.read_string(data.decode("utf-8-sig"))
    return {name: dict(parser[name]) for name in parser.sections()}


def parse_toml(data):
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    return tomllib.load(io.BytesIO(data))


def parse_yaml(data):
    import yaml
    try:
        return yaml.safe_load(data)
    except yaml.YAMLError as exc:
        error = ValueError("malformed YAML")
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            error.lineno, error.colno = mark.line + 1, mark.column + 1
        raise error from None


@dataclass(frozen=True)
class DocumentParser:
    read: Callable
    extensions: tuple
    selections: frozenset


COMMON = frozenset({"keys", "leaf-values"})
# Stable preference only breaks ties between equivalent, successful parses.
PARSERS = {
    "json": DocumentParser(json.loads, (".json",), COMMON),
    "toml": DocumentParser(parse_toml, (".toml",), COMMON),
    "ini": DocumentParser(parse_ini, (".ini",), COMMON | {"section-names"}),
    "yaml": DocumentParser(parse_yaml, (".yaml", ".yml"), COMMON),
}
PARSE_ERRORS = (ValueError, KeyError, TypeError, configparser.Error, OverflowError, RuntimeError)


def read_document(file):
    path = Path(file).expanduser()
    if not path.is_file():
        raise ValueError("expected a regular file")
    return path.read_bytes()


def load(fmt, file):
    return PARSERS[fmt].read(read_document(file))


class DetectionError(Exception):
    """A diagnostic safe to print without disclosing document contents."""


def navigation_root(node, policy):
    return isinstance(node, dict) or (policy == "leaf-values" and isinstance(node, list))


def same_document(left, right):
    # Compare mapping keys as typed values; regular Python equality conflates
    # true, 1 and 1.0. Visit each pair once, including recursive YAML aliases.
    pending = [(left, right)]
    seen = set()
    while pending:
        a, b = pending.pop()
        if type(a) is not type(b):
            return False
        pair = (id(a), id(b))
        if pair in seen:
            continue
        seen.add(pair)
        if isinstance(a, dict):
            by_key = {(type(k), k): v for k, v in b.items()}
            if len(a) != len(b):
                return False
            for key, child in a.items():
                typed = (type(key), key)
                if typed not in by_key:
                    return False
                pending.append((child, by_key[typed]))
        elif isinstance(a, (list, tuple)):
            if len(a) != len(b):
                return False
            pending.extend(zip(a, b))
        elif isinstance(a, float):
            if a.hex() != b.hex():
                return False
        elif isinstance(a, (datetime.date, datetime.time)):
            if a.isoformat() != b.isoformat():
                return False
        elif a != b:
            return False
    return True


def detect(policy, file, enabled):
    enabled = set(enabled)
    suffix = Path(file).suffix.lower()
    advertised = [name for name, spec in PARSERS.items() if suffix in spec.extensions]
    eligible = [name for name, spec in PARSERS.items() if name in enabled and policy in spec.selections]
    if advertised:
        available = [name for name in advertised if name in enabled]
        if not available:
            raise DetectionError(f"providers for {suffix} are disabled: {', '.join(advertised)}")
        eligible = [name for name in available if name in eligible]
        if not eligible:
            raise DetectionError(f"{', '.join(available)} does not support selection {policy!r}")
    if not eligible:
        raise DetectionError(f"no enabled document parser supports selection {policy!r}")
    try:
        data = read_document(file)
    except (OSError, ValueError, RuntimeError):
        raise DetectionError("cannot read a regular document file") from None
    parsed = {}
    missing = []
    for name in eligible:
        try:
            node = PARSERS[name].read(data)
        except ImportError as exc:
            if len(eligible) == 1:
                raise DetectionError(f"{name} parser unavailable; missing module {exc.name}") from None
            missing.append(str(exc.name))
            continue
        except PARSE_ERRORS as exc:
            if len(eligible) == 1:
                # Parser messages can quote credentials; extract only numeric positions.
                line, column = getattr(exc, "lineno", None), getattr(exc, "colno", None)
                where = f" at line {line}" if isinstance(line, int) else ""
                if where and isinstance(column, int):
                    where += f", column {column}"
                raise DetectionError(f"malformed {name.upper()}{where}") from None
            continue
        if navigation_root(node, policy):
            parsed[name] = node
        elif len(eligible) == 1:
            raise DetectionError(f"{name.upper()} root cannot be navigated for {policy!r}")
    if not parsed:
        message = f"no navigable document for {', '.join(eligible)} ({policy})"
        if missing:
            message += "; missing parser module: " + ", ".join(missing)
        raise DetectionError(message)
    first = next(iter(parsed))
    if any(not same_document(parsed[first], node) for name, node in parsed.items() if name != first):
        raise DetectionError(f"ambiguous document ({', '.join(parsed)}); specify --format")
    return first


def component(key):
    # The inserted path is a plain zsh word. Reserve '.' for traversal and
    # encode arbitrary document keys without evaluating them as shell syntax.
    return quote(key, safe="").replace(".", "%2E").replace("~", "%7E")


def children(node):
    if isinstance(node, list):
        return {str(i): child for i, child in enumerate(node)}
    if isinstance(node, dict):
        return {component(k): node[k] for k in node if isinstance(k, str) and k and "\0" not in k}
    return {}


def container(node):
    return isinstance(node, (dict, list))


def root_keys(root):
    return (
        {component(k): k for k in root if isinstance(k, str) and k and "\0" not in k}
        if isinstance(root, dict)
        else {}
    )


def candidates(root, policy, fragment):
    parts = fragment.split(".")
    if policy in {"keys", "section-names"}:
        for key in root_keys(root):
            if key.casefold().startswith(fragment.casefold()):
                yield "leaf", key, key
        return

    prefix = []
    node = root
    for index, part in enumerate(parts):
        items = children(node)
        matches = [key for key in items if key.casefold().startswith(part.casefold())]
        last = index + 1 == len(parts)
        if not last:
            matches = [key for key in matches if container(items[key])]
            if part in matches or len(matches) == 1:
                key = part if part in matches else matches[0]
                prefix.append(key)
                node = items[key]
                continue
        for key in matches:
            path = ".".join([*prefix, key])
            if container(items[key]):
                yield "branch", path + ".", path + "." + ".".join(parts[index + 1 :])
            else:
                yield "leaf", path, path
        return


def lookup(root, policy, path):
    if policy in {"keys", "section-names"}:
        return root_keys(root)[path]
    node = root
    for key in path.split("."):
        node = children(node)[key]
    if container(node):
        raise ValueError("expected a leaf")
    if isinstance(node, str):
        if "\0" in node:
            raise ValueError("NUL in value")
        return node
    if isinstance(node, (datetime.date, datetime.time)):
        return node.isoformat()
    if node is None or isinstance(node, (bool, float, int)):
        return json.dumps(node)
    raise ValueError("unsupported scalar")


def main(action, fmt, policy, file, path):
    try:
        if fmt not in PARSERS or policy not in PARSERS[fmt].selections:
            raise ValueError("unsupported selection policy")
        root = load(fmt, file)
        if action == "list":
            for row in candidates(root, policy, path):
                sys.stdout.buffer.write(("\0".join(row) + "\0").encode())
        else:
            sys.stdout.write(lookup(root, policy, path))
    except ImportError as exc:
        print(f"token-completion: missing parser module {exc.name}", file=sys.stderr)
        return 1
    except (
        OSError,
        ValueError,
        KeyError,
        configparser.Error,
        RecursionError,
        TypeError,
        OverflowError,
        RuntimeError,
    ):
        # Parser exceptions can reproduce complete source lines, including credentials.
        if action != "list":
            print(f"token-completion: cannot resolve {path!r} in {file!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["detect"] and len(sys.argv) >= 4:
        _, _, selection, document, *formats = sys.argv
        try:
            print(detect(selection, document, formats))
        except DetectionError as exc:
            sys.exit(f"token-completion: {document!r}: {exc}")
    elif len(sys.argv) == 6 and sys.argv[1] in {"list", "get"}:
        sys.exit(main(*sys.argv[1:]))
    else:
        sys.exit("usage: detect SELECT FILE [FORMAT ...] | list/get FORMAT SELECT FILE PATH")
