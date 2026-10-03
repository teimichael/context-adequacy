package lacuna.analyzer;

import com.ibm.wala.classLoader.IClass;
import com.ibm.wala.classLoader.IMethod;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.cha.ClassHierarchy;
import com.ibm.wala.ssa.SSAAbstractInvokeInstruction;
import com.ibm.wala.types.MethodReference;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;


public final class Escapes {


  public final Map<String, Integer> fired = new TreeMap<>();

  public final Map<String, Long> magnitude = new TreeMap<>();

  public final Map<String, TreeSet<String>> elementReasons = new LinkedHashMap<>();

  private static final String[] REFLECTION_CLASSES = {
    "Ljava/lang/reflect/Method",
    "Ljava/lang/reflect/Constructor",
    "Ljava/lang/reflect/Field",
    "Ljava/lang/invoke/MethodHandle",
    "Ljava/lang/invoke/MethodHandles",
    "Ljava/lang/reflect/Proxy",
  };

  private static final String[] INJECTION_ANNOTATIONS = {
    "Inject", "Autowired", "Resource", "Bean", "Component", "Service", "Repository",
    "Controller", "RestController", "Configuration", "Provides", "Produces",
  };

  private void fire(String name, String element, String reason, long mag) {
    fired.merge(name, 1, Integer::sum);
    if (mag > 0) {
      magnitude.merge(name, mag, Long::sum);
    }
    if (reason != null && element != null) {
      elementReasons.computeIfAbsent(element, k -> new TreeSet<>()).add(reason);
    }
  }

  public void inspectClass(IClass c, String typeId) {
    for (var ann : c.getAnnotations()) {
      String n = ElementIds.dotted(ann.getType().getName().toString());
      String simple = n.substring(n.lastIndexOf('.') + 1);
      for (String inj : INJECTION_ANNOTATIONS) {
        if (simple.equals(inj)) {


          fire("dependency_injection", typeId, "binding", 0);
          fire("framework_callbacks", typeId, "entry-points", 0);
          return;
        }
      }
    }
  }

  public void inspectMethod(IMethod m, String methodId) {
    if (m.isNative()) {
      fire("native_code", methodId, "native", 0);
    }
    if (m.isSynthetic()) {


      fire("synthetic_method", methodId, null, 0);
    }
  }

  public void inspectCallSite(
      CGNode node, SSAAbstractInvokeInstruction inv, String methodId, ClassHierarchy cha) {
    MethodReference target = inv.getDeclaredTarget();
    String owner = target.getDeclaringClass().getName().toString();

    for (String refl : REFLECTION_CLASSES) {
      if (owner.startsWith(refl)) {
        fire("reflection", methodId, "reflection", 0);
        return;
      }
    }
    if (owner.equals("Ljava/lang/Class") && target.getName().toString().equals("forName")) {
      fire("reflection", methodId, "reflection", 0);
      return;
    }

    if (inv.isDispatch()) {
      int targets = cha.getPossibleTargets(target).size();
      if (targets > 1) {


        fire("dynamic_dispatch", null, null, targets);
      }
    }
  }


  public void noWriter(String fieldId) {
    fire("serialisation", fieldId, "no-writer", 0);
  }

  public void generated(String elementId) {
    fire("generated_code", elementId, "generated", 0);
  }

  public void timeout(String stage) {
    fire("analysis_timeout", null, null, 0);
    elementReasons.computeIfAbsent("__run__", k -> new TreeSet<>()).add("timeout");
  }
}
