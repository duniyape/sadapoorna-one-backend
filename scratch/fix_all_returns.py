import os
import re

base_dir = r"c:\Users\sadapoorna\OneDrive\Desktop\workspace\sadapoorna\sadapoorna-one-backend\routes"

for filename in os.listdir(base_dir):
    if not filename.endswith(".py"):
        continue
    filepath = os.path.join(base_dir, filename)
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Check if IST or convert_utc_to_ist is defined inline and remove them
    pattern = r"def convert_utc_to_ist\(.*?\):.*?(?=\n\n(?:def|@|\w+ =))"
    content = re.sub(pattern, "", content, flags=re.DOTALL)
    content = re.sub(r"IST = timezone\(timedelta\(hours=5, minutes=30\)\)\n?", "", content)
    
    # Add from utils import convert_utc_to_ist if not there
    if "from utils import convert_utc_to_ist" not in content:
        imports_pattern = r"(from .*? import .*?\n)+"
        match = re.search(imports_pattern, content)
        if match:
            idx = match.end()
            content = content[:idx] + "from utils import convert_utc_to_ist\n" + content[idx:]
        else:
            content = "from utils import convert_utc_to_ist\n" + content
            
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
        new_content += indent + "return convert_utc_to_ist({"
        
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
            
        new_content += content[idx + match.end() : i]
        new_content += "})"
        idx = i + 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)
        
print("Successfully applied to all routes.")
