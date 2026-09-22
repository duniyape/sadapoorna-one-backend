import re
import os

def refactor_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Remove def convert_utc_to_ist
    # Using regex to remove the function block
    pattern = r"def convert_utc_to_ist\(value\):.*?(?=\n\n(?:def|@|\w+ =))"
    content = re.sub(pattern, "", content, flags=re.DOTALL)
    
    # Check if IST is defined, and remove it
    content = re.sub(r"IST = timezone\(timedelta\(hours=5, minutes=30\)\)\n?", "", content)
    
    # Add from utils import convert_utc_to_ist if not there
    if "from utils import convert_utc_to_ist" not in content:
        imports_pattern = r"(from .*? import .*?\n)+"
        match = re.search(imports_pattern, content)
        if match:
            # insert after the last from ... import
            idx = match.end()
            content = content[:idx] + "from utils import convert_utc_to_ist\n" + content[idx:]
        else:
            content = "from utils import convert_utc_to_ist\n" + content
            
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

base_dir = r"c:\Users\sadapoorna\OneDrive\Desktop\workspace\sadapoorna\sadapoorna-one-backend\routes"
refactor_file(os.path.join(base_dir, "orders.py"))
refactor_file(os.path.join(base_dir, "Inventory.py"))

print("Successfully refactored orders.py and Inventory.py")
