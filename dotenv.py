"""Single-line dotenv assignments with literal values; shell evaluation is never used."""

from pathlib import Path
import re
import sys


def values():
    data = {}
    for line in Path(".env").read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)", line)
        if not match:
            continue
        key, raw = match.groups()
        value = raw.strip()
        if value.startswith(('"', "'")):
            quoted = re.fullmatch(r"""(["'])(.*?)\1(?:\s*#.*)?""", value)
            if not quoted:
                continue
            value = quoted[2]
        else:
            value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        data[key] = value
    return data


if __name__ == "__main__":
    try:
        data = values()
        if sys.argv[1] == "keys":
            for key in data:
                print(key)
        else:
            sys.stdout.write(data[sys.argv[2]])
    except (OSError, UnicodeError, KeyError) as exc:
        if sys.argv[1] != "keys":
            sys.exit(f"token-completion: .env lookup for {sys.argv[2]}: {exc}")
