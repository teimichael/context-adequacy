package lacuna.analyzer;

import com.ibm.wala.classLoader.IMethod;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.CallGraph;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerAnalysis;
import com.ibm.wala.ipa.cha.ClassHierarchy;
import com.ibm.wala.ipa.slicer.NormalStatement;
import com.ibm.wala.ipa.slicer.SDG;
import com.ibm.wala.ipa.slicer.Slicer;
import com.ibm.wala.ipa.slicer.Statement;
import com.ibm.wala.ssa.IR;
import com.ibm.wala.ssa.SSAInstruction;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;


public final class SdgCrossCheck {

  private SdgCrossCheck() {}

  static Slicer.DataDependenceOptions dataDep(String s) {
    return switch (s) {
      case "NO_BASE_NO_HEAP" -> Slicer.DataDependenceOptions.NO_BASE_NO_HEAP;
      case "NO_HEAP" -> Slicer.DataDependenceOptions.NO_HEAP;
      case "NO_HEAP_NO_EXCEPTIONS" -> Slicer.DataDependenceOptions.NO_HEAP_NO_EXCEPTIONS;
      default ->
          throw new IllegalArgumentException(
              "--data-dep " + s + " is not permitted: heap-carrying options do not "
                  + "terminate at repository scale");
    };
  }

  static Slicer.ControlDependenceOptions controlDep(String s) {
    return switch (s) {
      case "FULL" -> Slicer.ControlDependenceOptions.FULL;
      case "NO_EXCEPTIONAL_EDGES" -> Slicer.ControlDependenceOptions.NO_EXCEPTIONAL_EDGES;
      case "NONE" -> Slicer.ControlDependenceOptions.NONE;
      default -> throw new IllegalArgumentException("unknown --control-dep " + s);
    };
  }

  public static Map<String, Object> run(
      Config cfg, CallGraph cg, PointerAnalysis<InstanceKey> pa, ClassHierarchy cha) {
    Map<String, Object> result = new LinkedHashMap<>();
    if (cfg.seeds.isEmpty()) {
      result.put("status", "no-seeds");
      return result;
    }







    Set<String> wanted = new HashSet<>(List.of(cfg.seeds.split(",")));
    List<Statement> seeds = new ArrayList<>();
    for (CGNode n : cg) {
      IMethod m = n.getMethod();
      if (!wanted.contains(ElementIds.method(m))) {
        continue;
      }
      IR ir = n.getIR();
      if (ir == null) {
        continue;
      }
      for (SSAInstruction ins : ir.getInstructions()) {
        if (ins != null) {
          seeds.add(new NormalStatement(n, ins.iIndex()));
        }
      }
    }
    result.put("seed_statements", seeds.size());
    if (seeds.isEmpty()) {
      result.put("status", "seed-not-in-callgraph");
      return result;
    }

    final SDG<InstanceKey> sdg =
        new SDG<>(cg, pa, dataDep(cfg.dataDep), controlDep(cfg.controlDep));

    ExecutorService ex =
        Executors.newSingleThreadExecutor(
            r -> {
              Thread t = new Thread(r, "sdg-walk");
              t.setDaemon(true);
              return t;
            });
    Future<Map<String, Object>> fut =
        ex.submit(
            () -> {
              Map<Statement, Integer> dist = new HashMap<>();
              Deque<Statement> q = new ArrayDeque<>();
              for (Statement s : seeds) {
                dist.put(s, 0);
                q.add(s);
              }
              Set<String> elements = new HashSet<>();
              long edges = 0;
              while (!q.isEmpty()) {
                Statement cur = q.poll();
                int d = dist.get(cur);
                elements.add(ElementIds.method(cur.getNode().getMethod()));
                if (d >= cfg.depthCap) {
                  continue;
                }
                var it = sdg.getPredNodes(cur);
                while (it.hasNext()) {
                  Statement p = it.next();
                  edges++;
                  if (!dist.containsKey(p)) {
                    dist.put(p, d + 1);
                    q.add(p);
                  }
                }
              }
              Map<String, Object> r = new LinkedHashMap<>();
              r.put("status", "ok");
              r.put("statements", dist.size());
              r.put("sdg_edges_touched", edges);
              r.put("elements", elements.size());
              r.put("element_ids", new ArrayList<>(elements));
              return r;
            });

    try {
      Map<String, Object> r = fut.get(cfg.timeoutSeconds, TimeUnit.SECONDS);
      result.putAll(r);
    } catch (TimeoutException e) {
      fut.cancel(true);
      result.put("status", "timeout");
    } catch (Throwable t) {
      result.put("status", "error:" + t.getClass().getSimpleName());
    } finally {
      ex.shutdownNow();
    }
    return result;
  }
}
