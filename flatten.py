import os
import shutil
import re

ROOT = r"e:\project\june\real_things\voice_pill"

dirs_to_flatten = ['api', 'audio', 'ui', 'utils']

# Step 1: Move all .py files to ROOT
for d in dirs_to_flatten:
    dir_path = os.path.join(ROOT, d)
    if not os.path.exists(dir_path):
        continue
        
    for filename in os.listdir(dir_path):
        if filename.endswith(".py") and filename != "__init__.py":
            src = os.path.join(dir_path, filename)
            dst = os.path.join(ROOT, filename)
            print(f"Moving {src} -> {dst}")
            shutil.move(src, dst)
            
    # Delete __pycache__ if exists
    pycache = os.path.join(dir_path, "__pycache__")
    if os.path.exists(pycache):
        shutil.rmtree(pycache)
        
    # Delete __init__.py if exists
    init_py = os.path.join(dir_path, "__init__.py")
    if os.path.exists(init_py):
        os.remove(init_py)

    # Remove the empty directory
    try:
        os.rmdir(dir_path)
        print(f"Removed directory {dir_path}")
    except OSError as e:
        print(f"Warning: Could not remove {dir_path}: {e}")

# Step 2: Fix import statements across all .py files in ROOT
import_pattern = re.compile(r'^(from|import)\s+(api|audio|ui|utils)\.([a-zA-Z0-9_]+)', re.MULTILINE)

def replacement(match):
    prefix = match.group(1) # from or import
    module = match.group(3)
    return f"{prefix} {module}"

for filename in os.listdir(ROOT):
    if filename.endswith(".py"):
        filepath = os.path.join(ROOT, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        new_content, count = import_pattern.subn(replacement, content)
        
        if count > 0:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Updated {count} imports in {filename}")

print("Flattening complete!")
