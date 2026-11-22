"""Enumerate one document level or resolve one exact document path."""

import configparser
import datetime
import json
from pathlib import Path
import sys
from urllib.parse import quote


def load(fmt, file):
    path = Path(file).expanduser()
    if not path.is_file():
        raise ValueError("expected a regular file")
    if fmt == "ini":
        parser = configparser.ConfigParser(interpolation=None, default_section=None)
        parser.optionxform = str
        parser.read_string(path.read_text(encoding="utf-8-sig"))
        return {name: parser[name] for name in parser.sections()}
    if fmt == "json":
        return json.loads(path.read_bytes())
    if fmt == "toml":
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with path.open("rb") as stream:
            return tomllib.load(stream)
    if fmt == "yaml":
        import yaml

        try:
            return yaml.safe_load(path.read_bytes())
        except yaml.YAMLError:
            raise ValueError("malformed YAML") from None
    raise ValueError("unknown format")


def component(key):
    # The inserted path is a plain zsh word. Reserve '.' for traversal and
    # encode arbitrary document keys without evaluating them as shell syntax.
    return quote(key, safe="").replace(".", "%2E").replace("~", "%7E")


def children(node):
    if isinstance(node, list):
        return {str(i): child for i, child in enumerate(node)}
    if isinstance(node, (dict, configparser.SectionProxy)):
        return {component(k): node[k] for k in node if isinstance(k, str) and k and "\0" not in k}
    return {}


def container(node):
    return isinstance(node, (dict, list, configparser.SectionProxy))


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
        if policy not in {"keys", "leaf-values"} and (fmt, policy) != ("ini", "section-names"):
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
    sys.exit(main(*sys.argv[1:]))
