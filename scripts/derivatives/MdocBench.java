// mdoc-interop's CLI entry point, called repeatedly in one JVM, so a verification is timed without
// the JVM's start. Run by bench.py: java -cp '<install>/lib/*' MdocBench.java <iterations>
// <warmup> <mdoc-verify arguments...>. Prints {"samples_ns": [...]} on standard output, and stops
// at the first verification that does not exit 0.

import com.bakobo.mdoc.Cli;
import java.util.Arrays;

public class MdocBench {
    public static void main(String[] argv) {
        int iterations = Integer.parseInt(argv[0]);
        int warmup = Integer.parseInt(argv[1]);
        String[] args = Arrays.copyOfRange(argv, 2, argv.length);
        for (int i = 0; i < warmup; i++) {
            check(Cli.INSTANCE.run(args));
        }
        long[] samples = new long[iterations];
        for (int i = 0; i < iterations; i++) {
            long start = System.nanoTime();
            Cli.Result result = Cli.INSTANCE.run(args);
            samples[i] = System.nanoTime() - start;
            check(result);
        }
        StringBuilder out = new StringBuilder("{\"samples_ns\": [");
        for (int i = 0; i < iterations; i++) {
            out.append(i == 0 ? "" : ", ").append(samples[i]);
        }
        System.out.println(out.append("]}"));
    }

    private static void check(Cli.Result result) {
        if (result.getExitCode() != Cli.EXIT_VERIFIED) {
            System.err.print(result.getOutput());
            System.exit(1);
        }
    }
}
