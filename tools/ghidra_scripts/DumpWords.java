// Prints the 32-bit word at each address given, plus a short ASCII rendering of
// it. Used against an imported PCSX2 save state to check that what Ghidra sees
// is the LIVE memory image - the loader's runtime tables and the modules on the
// game's heap exist only there, never in the ELF on disk.
//
//   analyzeHeadless <proj> <name> -process <state> -postScript DumpWords.java \
//       <hexaddr> [<hexaddr> ...]
//
//@category MC3

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;

public class DumpWords extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        println("DumpWords: memory blocks in this program:");
        for (var b : currentProgram.getMemory().getBlocks()) {
            println(String.format("  %-16s %s..%s  %d bytes",
                    b.getName(), b.getStart(), b.getEnd(), b.getSize()));
        }

        for (String s : args) {
            Address a = currentProgram.getAddressFactory().getAddress(s);
            try {
                int v = currentProgram.getMemory().getInt(a);
                // The magics in this project are four-character tags, so show
                // them as text as well - 'MC3S' is far easier to recognise than
                // 0x4D433353.
                StringBuilder t = new StringBuilder();
                for (int sh = 24; sh >= 0; sh -= 8) {
                    int c = (v >> sh) & 0xFF;
                    t.append(c >= 32 && c < 127 ? (char) c : '.');
                }
                println(String.format("  %s = %08X  \"%s\"", s, v, t));
            } catch (Exception e) {
                println(String.format("  %s = UNREADABLE (%s)", s,
                        e.getClass().getSimpleName()));
            }
        }
    }
}
