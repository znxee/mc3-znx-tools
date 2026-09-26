// Decompiles the functions named on the command line and writes each one's C to
// a file. Used to compare Ghidra-with-the-EmotionEngine-extension against the
// IDA output this project already has, on one MMI-heavy function and one
// without, so the comparison is about the R5900 instruction set rather than
// about decompiler taste in general.
//
//   analyzeHeadless <proj> <name> -process <file> -postScript DumpFuncs.java \
//       <outdir> <hexaddr> [<hexaddr> ...]
//
//@category MC3

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DumpFuncs extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("DumpFuncs: need <outdir> and at least one address");
            return;
        }
        File outDir = new File(args[0]);
        outDir.mkdirs();

        DecompInterface dec = new DecompInterface();
        dec.openProgram(currentProgram);

        for (int i = 1; i < args.length; ++i) {
            Address a = currentProgram.getAddressFactory().getAddress(args[i]);
            Function f = getFunctionAt(a);
            if (f == null) {
                // Not every address the project cares about was found by
                // auto-analysis; make one rather than silently skipping it.
                f = createFunction(a, null);
            }
            if (f == null) {
                println("DumpFuncs: no function at " + args[i]);
                continue;
            }

            DecompileResults res = dec.decompileFunction(f, 120, monitor);
            File out = new File(outDir, args[i] + ".c");
            PrintWriter w = new PrintWriter(out, "UTF-8");
            if (res != null && res.decompileCompleted()) {
                w.println(res.getDecompiledFunction().getC());
            } else {
                w.println("// decompilation FAILED: " +
                          (res == null ? "null" : res.getErrorMessage()));
            }
            w.close();
            println("DumpFuncs: wrote " + out.getAbsolutePath());
        }
        dec.dispose();
    }
}
