import re
import os

filepath = r"c:\Users\sadapoorna\OneDrive\Desktop\workspace\sadapoorna\sadapoorna-one-backend\routes\Inventory.py"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# Add the import and definition if it's not already there
if "def convert_utc_to_ist" not in content:
    func_def = """
def convert_utc_to_ist(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST)
    if isinstance(value, dict):
        return {k: convert_utc_to_ist(v) for k, v in value.items()}
    if isinstance(value, list):
        return [convert_utc_to_ist(item) for item in value]
    if isinstance(value, tuple):
        return tuple(convert_utc_to_ist(item) for item in value)
    return value
"""
    content = content.replace("IST = timezone(timedelta(hours=5, minutes=30))", "IST = timezone(timedelta(hours=5, minutes=30))\n" + func_def)

# Find all 'return {' and replace them by matching the closing brace
new_content = ""
idx = 0

while True:
    match = re.search(r"^[ \t]*return \{$", content[idx:], flags=re.MULTILINE)
    if not match:
        new_content += content[idx:]
        break
    
    start_idx = idx + match.start()
    return_str = content[start_idx : idx + match.end()]
    new_content += content[idx : start_idx]
    
    indent = return_str.split("return")[0]
    
    # replace 'return {' with 'return convert_utc_to_ist({'
    new_content += indent + "return convert_utc_to_ist({"
    
    # now find the matching closing brace
    brace_count = 1
    i = idx + match.end()
    while i < len(content):
        if content[i] == '{':
            brace_count += 1
        elif content[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                break
        i += 1
        
    # Append the inner contents
    new_content += content[idx + match.end() : i]
    # Append the closing brace + parenthesis
    new_content += "})"
    idx = i + 1

with open(filepath, "w", encoding="utf-8") as f:
    f.write(new_content)
    
print("Successfully updated Inventory.py")
