from types import SimpleNamespace

from agent.permissions import check_permission


cases = [
    ("read_file", {"path": "main.py"}),
    ("bash", {"command": "sudo whoami"}),
    ("bash", {"command": "del playground/demo.txt"}),
]

for name, args in cases:
    block = SimpleNamespace(name=name, input=args)
    print(f"\nChecking: {name} {args}")
    print("Allowed:", check_permission(block))