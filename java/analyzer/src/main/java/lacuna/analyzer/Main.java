package lacuna.analyzer;

import com.ibm.wala.classLoader.IClass;
import com.ibm.wala.classLoader.IMethod;
import com.ibm.wala.classLoader.Language;
import com.ibm.wala.ipa.callgraph.AnalysisCacheImpl;
import com.ibm.wala.ipa.callgraph.AnalysisOptions;
import com.ibm.wala.ipa.callgraph.AnalysisScope;
import com.ibm.wala.ipa.callgraph.CallGraph;
import com.ibm.wala.ipa.callgraph.CallGraphBuilder;
import com.ibm.wala.ipa.callgraph.Entrypoint;
import com.ibm.wala.ipa.callgraph.IAnalysisCacheView;
import com.ibm.wala.ipa.callgraph.impl.DefaultEntrypoint;
import com.ibm.wala.ipa.callgraph.impl.Util;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerAnalysis;
import com.ibm.wala.ipa.cha.ClassHierarchy;

import java.io.FileWriter;
import java.io.PrintWriter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;


public final class Main {

  public static void main(String[] args) {
    Config cfg;
    try {
      cfg = Config.parse(args);
    } catch (RuntimeException e) {
      System.err.println("lacuna-analyzer: " + e.getMessage());
      System.exit(2);
      return;
    }
    try {
      System.exit(run(cfg));
    } catch (OutOfMemoryError e) {
      System.err.println("lacuna-analyzer: OOM");
      System.exit(5);
    } catch (Throwable t) {
      System.err.println("lacuna-analyzer: " + t);
      t.printStackTrace();
      System.exit(6);
    }
  }

  static int run(Config cfg) throws Exception {
    Map<String, Object> stats = new LinkedHashMap<>();
    long t0 = System.nanoTime();

    java.util.List<String> libs = new java.util.ArrayList<>();
    for (String e : cfg.libJars.split(java.io.File.pathSeparator)) {
      if (!e.isEmpty()) {
        libs.add(e);
      }
    }
    ClassHierarchy cha = ScopeBuilder.hierarchyLenient(cfg, libs);
    AnalysisScope scope = ScopeBuilder.build(
        cfg, libs.stream().filter(l -> !ScopeBuilder.poisonJars.contains(l)).toList());
    long msCha = ms(t0);
    int appClasses = 0;
    for (IClass c : cha) {
      if (ElementIds.isApplication(c)) {
        appClasses++;
      }
    }
    stats.put("cha_classes", cha.getNumberOfClasses());
    stats.put("skipped_lib_jars", ScopeBuilder.skippedLibJars);
    stats.put("poison_lib_jars", ScopeBuilder.poisonJars.size());
    stats.put("hierarchy_builds", ScopeBuilder.hierarchyBuilds);
    stats.put("bisection_truncated", ScopeBuilder.bisectionTruncated);
    stats.put("app_classes", appClasses);
    stats.put("ms_cha", msCha);

    List<Entrypoint> eps = entryPoints(cha, cfg.entryPoints);
    stats.put("entry_points", eps.size());
    if (eps.isEmpty()) {
      System.err.println("lacuna-analyzer: no entry points under model " + cfg.entryPoints);
      writeStats(cfg, stats, new Escapes());
      return 3;
    }

    AnalysisOptions options = new AnalysisOptions(scope, eps);



    options.setReflectionOptions(AnalysisOptions.ReflectionOptions.NONE);
    IAnalysisCacheView cache = new AnalysisCacheImpl();

    long t1 = System.nanoTime();
    CallGraphBuilder<InstanceKey> builder =
        switch (cfg.cg) {
          case "RTA" -> Util.makeRTABuilder(options, cache, cha);
          case "0-CFA" -> Util.makeZeroCFABuilder(Language.JAVA, options, cache, cha);
          case "0-1-CFA" -> Util.makeZeroOneCFABuilder(Language.JAVA, options, cache, cha);
          default -> throw new IllegalArgumentException("unknown --cg " + cfg.cg);
        };
    CallGraph cg = builder.makeCallGraph(options, null);
    PointerAnalysis<InstanceKey> pa = builder.getPointerAnalysis();
    stats.put("cg_nodes", cg.getNumberOfNodes());
    stats.put("ms_cg", ms(t1));

    Escapes escapes = new Escapes();
    long t2 = System.nanoTime();
    int edges;
    try (JsonWriter out = new JsonWriter(new FileWriter(cfg.out))) {
      EdgBuilder edg = new EdgBuilder(cg, cha, escapes, out, cfg);
      edg.build();
      edges = out.edges;
      stats.put("edg_nodes", out.nodes);
      stats.put("edg_edges", out.edges);
      stats.put("bridges_truncated", edg.bridgesTruncated);
      stats.put("reachable_app_methods", edg.reachableMethods());
      if (edg.bridgesTruncated > 0) {
        escapes.generated("__bridge_truncated__");
      }
    }
    stats.put("ms_edg", ms(t2));

    long tIdx = System.nanoTime();
    try (FileWriter iw = new FileWriter(cfg.indexOut)) {
      IndexWriter.write(cha, iw);
    }
    stats.put("ms_index", ms(tIdx));

    if (cfg.mode.equals("SDG") || cfg.mode.equals("BOTH")) {
      long t3 = System.nanoTime();
      Map<String, Object> cross = SdgCrossCheck.run(cfg, cg, pa, cha);
      cross.put("ms_sdg", ms(t3));
      stats.put("sdg_crosscheck", cross);
    }

    stats.put("ms_total", ms(t0));
    writeStats(cfg, stats, escapes);
    System.out.println("lacuna-analyzer: " + stats.get("edg_nodes") + " nodes, " + edges
        + " edges, " + stats.get("ms_total") + " ms");
    return 0;
  }

  private static long ms(long since) {
    return (System.nanoTime() - since) / 1_000_000L;
  }


  static List<Entrypoint> entryPoints(ClassHierarchy cha, String model) {
    List<Entrypoint> eps = new ArrayList<>();
    for (IClass c : cha) {
      if (!ElementIds.isApplication(c)) {
        continue;
      }
      String cn = c.getName().toString();
      boolean testClass = cn.contains("Test") || cn.contains("IT") || cn.endsWith("Spec");
      for (IMethod m : c.getDeclaredMethods()) {
        if (m.isAbstract()) {
          continue;
        }
        boolean take =
            switch (model) {
              case "declared-mains" -> m.isStatic() && m.getName().toString().equals("main");
              case "+test-methods" ->
                  (m.isStatic() && m.getName().toString().equals("main"))
                      || (testClass && m.isPublic() && !m.isStatic());
              case "+framework-annotated" ->
                  (m.isStatic() && m.getName().toString().equals("main"))
                      || (testClass && m.isPublic() && !m.isStatic())
                      || hasFrameworkAnnotation(c);
              case "all-public" -> m.isPublic();
              default -> throw new IllegalArgumentException("unknown entry-point model " + model);
            };
        if (take) {
          eps.add(new DefaultEntrypoint(m, cha));
        }
      }
    }
    return eps;
  }

  private static boolean hasFrameworkAnnotation(IClass c) {
    for (var ann : c.getAnnotations()) {
      String n = ElementIds.dotted(ann.getType().getName().toString());
      String simple = n.substring(n.lastIndexOf('.') + 1);
      switch (simple) {
        case "Component", "Service", "Repository", "Controller", "RestController",
            "Configuration", "Bean", "Entity", "WebServlet" -> {
          return true;
        }
        default -> {

        }
      }
    }
    return false;
  }

  private static void writeStats(Config cfg, Map<String, Object> stats, Escapes escapes)
      throws Exception {
    StringBuilder b = new StringBuilder("{\n");
    b.append("  \"stats\": ").append(json(stats)).append(",\n");
    b.append("  \"escapes_fired\": ").append(json(escapes.fired)).append(",\n");
    b.append("  \"escapes_magnitude\": ").append(json(escapes.magnitude)).append(",\n");
    b.append("  \"element_reasons\": ").append(json(escapes.elementReasons)).append("\n}\n");
    Files.writeString(Path.of(cfg.escapesOut), b.toString());
  }

  @SuppressWarnings("unchecked")
  private static String json(Object o) {
    if (o == null) {
      return "null";
    }
    if (o instanceof Number || o instanceof Boolean) {
      return o.toString();
    }
    if (o instanceof Map<?, ?> m) {
      StringBuilder b = new StringBuilder("{");
      boolean first = true;
      for (var e : m.entrySet()) {
        if (!first) {
          b.append(", ");
        }
        first = false;
        b.append('"').append(e.getKey()).append("\": ").append(json(e.getValue()));
      }
      return b.append('}').toString();
    }
    if (o instanceof Iterable<?> it) {
      StringBuilder b = new StringBuilder("[");
      boolean first = true;
      for (Object e : it) {
        if (!first) {
          b.append(", ");
        }
        first = false;
        b.append(json(e));
      }
      return b.append(']').toString();
    }
    return '"' + o.toString().replace("\\", "\\\\").replace("\"", "\\\"") + '"';
  }


  static void unused(PrintWriter p) {}
}
