package lacuna.analyzer;

import com.ibm.wala.classLoader.IBytecodeMethod;
import com.ibm.wala.classLoader.IClass;
import com.ibm.wala.classLoader.IField;
import com.ibm.wala.classLoader.IMethod;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.CallGraph;
import com.ibm.wala.ipa.cha.ClassHierarchy;
import com.ibm.wala.ssa.IR;
import com.ibm.wala.ssa.SSAAbstractInvokeInstruction;
import com.ibm.wala.ssa.SSAGetInstruction;
import com.ibm.wala.ssa.SSAInstruction;
import com.ibm.wala.ssa.SSANewInstruction;
import com.ibm.wala.ssa.SSAPutInstruction;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;


public final class EdgBuilder {


  public static final int BRIDGE_HOPS = 3;


  public static final int BRIDGE_BUDGET = 256;

  private static final char SEP = '|';

  private final CallGraph cg;
  private final ClassHierarchy cha;
  private final Escapes escapes;
  private final JsonWriter out;
  private final Config cfg;

  private final Set<String> emittedNodes = new HashSet<>();
  private final Set<String> emittedEdges = new HashSet<>();


  private final java.util.List<java.io.File> sourceRoots = new java.util.ArrayList<>();

  public int bridgesTruncated = 0;
  public int edgesEmitted = 0;


  private final Set<String> reachable = new HashSet<>();

  public int reachableMethods() {
    return reachable.size();
  }

  public EdgBuilder(
      CallGraph cg, ClassHierarchy cha, Escapes escapes, JsonWriter out, Config cfg) {
    this.cg = cg;
    this.cha = cha;
    this.escapes = escapes;
    this.out = out;
    this.cfg = cfg;
    for (String r : cfg.sourceRoots.split(java.io.File.pathSeparator)) {
      if (!r.isEmpty()) {
        sourceRoots.add(new java.io.File(r));
      }
    }
  }


  private void node(String id, IClass owner, Integer line, String scope) {
    node(id, owner, line, scope, null);
  }

  private void node(String id, IClass owner, Integer line, String scope, Boolean inCg) {
    if (!emittedNodes.add(id)) {
      return;
    }
    out.node(
        id,
        owner == null ? null : ElementIds.sourceRelativePath(owner, sourceRoots),
        line,
        scope,
        inCg);
  }

  private void edge(String src, String dst, String kind) {
    if (src.equals(dst)) {
      return;
    }
    String key = src + SEP + dst + SEP + kind;
    if (!emittedEdges.add(key)) {
      return;
    }
    out.edge(src, dst, kind);
    edgesEmitted++;
  }

  private static Integer lineOf(IMethod m) {
    try {
      if (m instanceof IBytecodeMethod<?> bm) {
        return m.getLineNumber(bm.getBytecodeIndex(0));
      }
    } catch (Throwable ignored) {

    }
    return null;
  }


  public void build() {



    for (CGNode n : cg) {
      IMethod m = n.getMethod();
      if (ElementIds.isApplication(m.getDeclaringClass())) {
        reachable.add(ElementIds.method(m));
      }
    }


    for (IClass c : cha) {
      if (!ElementIds.isApplication(c)) {
        continue;
      }
      String tid = ElementIds.type(c);
      node(tid, c, null, scopeOf(c));
      escapes.inspectClass(c, tid);

      for (IField f : c.getDeclaredInstanceFields()) {
        node(ElementIds.field(f), c, null, scopeOf(c));
      }
      for (IField f : c.getDeclaredStaticFields()) {
        node(ElementIds.field(f), c, null, scopeOf(c));
      }
      for (IMethod m : c.getDeclaredMethods()) {
        String mid = ElementIds.method(m);
        node(mid, c, lineOf(m), scopeOf(c), reachable.contains(mid));
        edge(mid, tid, "type-ref");
        escapes.inspectMethod(m, mid);
      }
      for (IClass sup : superTypes(c)) {
        if (ElementIds.isApplication(sup)) {
          edge(tid, ElementIds.type(sup), "type-ref");
        }
      }
    }


    for (CGNode caller : cg) {
      IMethod cm = caller.getMethod();
      if (!ElementIds.isApplication(cm.getDeclaringClass())) {
        continue;
      }
      String callerId = ElementIds.method(cm);
      for (Target t : appTargetsFrom(caller)) {
        String calleeId = ElementIds.method(t.node.getMethod());
        if (t.direct) {
          edge(callerId, calleeId, "return");
          edge(calleeId, callerId, "call");
        } else {









          edge(callerId, calleeId, "summary");
          edge(calleeId, callerId, "call");
        }
      }
    }


    for (CGNode n : cg) {
      IMethod m = n.getMethod();
      if (!ElementIds.isApplication(m.getDeclaringClass())) {
        continue;
      }
      IR ir = n.getIR();
      if (ir == null) {
        continue;
      }
      String mid = ElementIds.method(m);
      for (SSAInstruction ins : ir.getInstructions()) {
        if (ins == null) {
          continue;
        }
        if (ins instanceof SSAGetInstruction g) {



          IField f = heapEdges() ? cha.resolveField(g.getDeclaredField()) : null;
          if (f != null && ElementIds.isApplication(f.getDeclaringClass())) {
            edge(mid, heapTarget(f), "heap-read");
          }
        } else if (ins instanceof SSAPutInstruction p) {
          IField f = heapEdges() ? cha.resolveField(p.getDeclaredField()) : null;
          if (f != null && ElementIds.isApplication(f.getDeclaringClass())) {

            edge(heapTarget(f), mid, "heap-write");
          }
        } else if (ins instanceof SSANewInstruction nw) {
          IClass t = cha.lookupClass(nw.getConcreteType());
          if (t != null && ElementIds.isApplication(t)) {
            edge(mid, ElementIds.type(t), "type-ref");
          }
        } else if (ins instanceof SSAAbstractInvokeInstruction inv) {
          escapes.inspectCallSite(n, inv, mid, cha);
        }
      }
    }





    for (IClass c : cha) {
      if (!cfg.chaAugmented) {
        break;
      }
      if (!ElementIds.isApplication(c)) {
        continue;
      }
      for (IMethod m : c.getDeclaredMethods()) {
        if (m.isStatic() || m.isPrivate() || m.isInit() || m.isClinit()) {
          continue;
        }
        String declaredId = ElementIds.method(m);
        for (IClass sub : cha.computeSubClasses(c.getReference())) {
          if (sub.equals(c) || !ElementIds.isApplication(sub)) {
            continue;
          }
          IMethod impl = sub.getMethod(m.getSelector());
          if (impl != null && !impl.equals(m) && !impl.isAbstract()) {
            edge(declaredId, ElementIds.method(impl), "override");
          }
        }
      }
    }
  }


  private record Target(CGNode node, boolean direct) {}


  private List<Target> appTargetsFrom(CGNode caller) {
    List<Target> out = new ArrayList<>();
    Map<CGNode, Integer> seen = new HashMap<>();
    Deque<CGNode> queue = new ArrayDeque<>();
    seen.put(caller, 0);
    queue.add(caller);
    int explored = 0;
    boolean cut = false;
    while (!queue.isEmpty()) {
      CGNode cur = queue.poll();
      int hops = seen.get(cur);
      var it = cg.getSuccNodes(cur);
      while (it.hasNext()) {
        CGNode next = it.next();
        if (seen.containsKey(next)) {
          continue;
        }
        boolean isApp = ElementIds.isApplication(next.getMethod().getDeclaringClass());
        if (isApp) {
          seen.put(next, hops);
          out.add(new Target(next, hops == 0));
        } else if (hops < BRIDGE_HOPS && explored < BRIDGE_BUDGET) {
          seen.put(next, hops + 1);
          explored++;
          queue.add(next);
        } else {



          cut = true;
        }
      }
    }
    if (cut) {
      bridgesTruncated++;
    }
    return out;
  }


  private boolean heapEdges() {
    return !"none".equals(cfg.heapModel);
  }


  private String heapTarget(IField f) {
    return cfg.fieldSens
        ? ElementIds.field(f)
        : ElementIds.type(f.getDeclaringClass());
  }

  private static String scopeOf(IClass c) {
    String n = c.getName().toString();
    return (n.contains("Test") || n.contains("test")) ? "test" : "app";
  }

  private List<IClass> superTypes(IClass c) {
    List<IClass> out = new ArrayList<>();
    IClass sup = c.getSuperclass();
    if (sup != null) {
      out.add(sup);
    }
    out.addAll(c.getDirectInterfaces());
    return out;
  }
}
