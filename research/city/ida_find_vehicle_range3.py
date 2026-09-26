import ida_auto
import ida_funcs
import idautils
import idc

ida_auto.auto_wait()
NEEDLES = ("0x32C(", "0x390(", "0x1590(", "0x1710(",
           ", 0x32C", ", 0x390", ", 0x1590", ", 0x1710")
for ea in idautils.Heads():
    line = idc.GetDisasm(ea)
    if any(needle.lower() in line.lower() for needle in NEEDLES):
        func = ida_funcs.get_func(ea)
        name = ida_funcs.get_func_name(func.start_ea) if func else "?"
        emit("%08X %-72s %s" % (ea, name, line))
