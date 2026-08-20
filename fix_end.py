path = "telegram_mcp/tools/migration.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Truncate broken end
broken = content.rfind("        return format_tool")
if broken != -1:
    nl_before = content.rfind("\n", 0, broken)
    content = content[:nl_before + 1]

rest = open("rest_additions.py", "r", encoding="utf-8").read()
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
    f.write(rest)
print("Fixed.")
