ea = 0x3B2038
ida_funcs.add_func(ea)
ida_auto.auto_wait()
emit(str(ida_hexrays.decompile(ea)))
