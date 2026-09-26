# city_pass_trace

Read-only tracer of the eight city draw call sites. It keeps the original calls
and writes into `CPAS` one counter per call site and one mask per model. It
serves to tell apart pass 2, pass 8, pass 64 and the call site shared by groups
2/3 of passes 4/8, separately for components and instances.

It neither fixes nor hides geometry. Read a savestate with
`research/city/read_city_pass_trace.py`.
