"""Substitute ${VAR} placeholders in a manifest from the environment."""

import os
import re
import sys


def sub(match):
    name = match.group(1)
    if name not in os.environ:
        sys.exit(f"render: ${{{name}}} is not set in the environment")
    return os.environ[name]


for path in sys.argv[1:]:
    with open(path) as fh:
        sys.stdout.write(re.sub(r"\$\{(\w+)\}", sub, fh.read()))
    sys.stdout.write("\n---\n")
