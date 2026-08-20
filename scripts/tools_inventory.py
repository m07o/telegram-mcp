"""Tool inventory — list every @mcp.tool decorator with its function name."""

import re
import glob

tools = []
for f in sorted(glob.glob("telegram_mcp/tools/*.py")):
    with open(f, encoding="utf-8") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        if "@mcp.tool(" in line or line.strip() == "@mcp.tool":
            # find the next function definition
            for j in range(i + 1, min(i + 30, len(lines))):
                m = re.match(
                    r"^\s*(?:async )?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
                    lines[j],
                )
                if m:
                    tools.append((f.replace("telegram_mcp/tools/", ""), m.group(1), i + 1))
                    break

# Group by file
from collections import defaultdict

by_file = defaultdict(list)
for f, fn, ln in tools:
    by_file[f].append((fn, ln))

for f in sorted(by_file.keys()):
    print(f"\n=== {f} ({len(by_file[f])} tools) ===")
    for fn, ln in by_file[f]:
        print(f"  {ln:4d}  {fn}")

print(f"\n\n=== TOTAL: {len(tools)} tools ===")
