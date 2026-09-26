import os
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'research', 'city', 'analyze_zero_instance_matrices.py'), encoding="utf-8").read().replace(
    "if all(x == 0.0 for x in rm):",
    "if rm[9] == 0.0 and rm[10] == 0.0 and rm[11] == 0.0:"
).replace(
    "runtime_all_zero=",
    "runtime_zero_translation="
))
