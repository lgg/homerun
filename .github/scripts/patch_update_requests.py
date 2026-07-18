from pathlib import Path

root = Path(__file__).resolve().parents[2]

for path in root.rglob("*.rs"):
    if "target" in path.parts:
        continue

    text = path.read_text(encoding="utf-8")
    original = text
    cursor = 0

    while True:
        start = text.find("UpdateRunnerRequest {", cursor)
        if start == -1:
            break
        brace = text.find("{", start)
        depth = 0
        end = None
        for index in range(brace, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is None:
            raise RuntimeError(f"Unbalanced UpdateRunnerRequest block in {path}")

        block = text[start : end + 1]
        if "display_name:" not in block:
            line_start = text.rfind("\n", 0, end) + 1
            closing_indent = text[line_start:end]
            field_indent = f"{closing_indent}    "
            text = text[:line_start] + f"{field_indent}display_name: None,\n" + text[line_start:]
            end += len(field_indent) + len("display_name: None,\n")
        cursor = end + 1

    if text != original:
        path.write_text(text, encoding="utf-8")

print("UpdateRunnerRequest literals patched")
