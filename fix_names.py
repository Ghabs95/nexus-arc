import re

files = [
    "tests/test_yaml_loader.py",
    "tests/test_workflow_callbacks.py",
    "tests/test_workflow_dry_run.py"
]

for filename in files:
    with open(filename, 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    for line in lines:
        if re.search(r'^\s*"name":\s*"(?:Test Workflow|Retry Test|Retry|Parallel Test|Tiered|Bad Ref|Bad Cond|Bad Retry|Bad Backoff|Bad Parallel|Parallel Warn|Retry Mapping|Retry Precedence|Parallel|Multi Parallel|Backoff Test|Delay Test|Constant Backoff|Tiered Test\\n|Conditional|Name Error|With Router)",?$', line) or re.search(r'data = \{"name": "[^"]*", ', line) or re.search(r'base = \{\s*"name": "[^"]*",', line):
            line = re.sub(r'("name":\s*"[^"]*(?:\\n)?")', r'"metadata": {\1}', line)
        new_lines.append(line)
        
    with open(filename, 'w') as f:
        f.writelines(new_lines)
