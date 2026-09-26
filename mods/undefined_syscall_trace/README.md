# undefined_syscall_trace

Diagnosis of a hang, not a fix. Loaded as `defer_once`, it replaces only the
BIOS table entries that still point to the generic handler
`# Syscall: undefined (%d)`. On the first hit, it stores the number, EPC,
instruction, Cause, the BIOS frame and the entry registers inside the `.mod`
itself, then enters an intentional loop. Version 2 does the capture in
assembly, before any C++ prologue reuses `a2` or another game register.

```ini
[mods]
core.mod = 1
undefined_syscall_trace.mod = defer_once
```

When the loading stops, save a state and read it (`mc3_inject.py` is in the
mc3boot repo):

```text
python mc3_inject.py state "file.p2s"
```

The `undefined syscall trace` section picks the installed report even when an
already freed Stream buffer still holds another copy of the `USCT` signature.
It shows the first jump to the undefined entry without letting the BIOS
`printf` destroy the entry registers.
