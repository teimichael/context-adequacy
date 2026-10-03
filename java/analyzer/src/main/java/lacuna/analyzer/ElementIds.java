package lacuna.analyzer;

import com.ibm.wala.classLoader.IClass;
import com.ibm.wala.classLoader.IField;
import com.ibm.wala.classLoader.IMethod;
import com.ibm.wala.types.ClassLoaderReference;
import com.ibm.wala.types.FieldReference;
import com.ibm.wala.types.MethodReference;
import com.ibm.wala.types.TypeReference;


public final class ElementIds {

  private ElementIds() {}


  public static String dotted(TypeReference t) {
    return dotted(t.getName().toString());
  }

  public static String dotted(String internal) {
    String s = internal;
    while (s.startsWith("[")) {
      s = s.substring(1);
    }
    if (s.startsWith("L")) {
      s = s.substring(1);
    }
    if (s.endsWith(";")) {
      s = s.substring(0, s.length() - 1);
    }
    return s.replace('/', '.');
  }

  public static String type(IClass c) {
    return "T:" + dotted(c.getName().toString());
  }

  public static String type(TypeReference t) {
    return "T:" + dotted(t);
  }

  public static String method(IMethod m) {
    return "M:"
        + dotted(m.getDeclaringClass().getName().toString())
        + "#"
        + m.getName().toString()
        + m.getDescriptor().toString();
  }

  public static String method(MethodReference m) {
    return "M:"
        + dotted(m.getDeclaringClass().getName().toString())
        + "#"
        + m.getName().toString()
        + m.getDescriptor().toString();
  }

  public static String field(IField f) {
    return "F:"
        + dotted(f.getDeclaringClass().getName().toString())
        + "."
        + f.getName().toString()
        + ":"
        + f.getFieldTypeReference().getName().toString();
  }

  public static String field(FieldReference f) {
    return "F:"
        + dotted(f.getDeclaringClass().getName().toString())
        + "."
        + f.getName().toString()
        + ":"
        + f.getFieldType().getName().toString();
  }

  public static boolean isApplication(IClass c) {
    return c.getClassLoader().getReference().equals(ClassLoaderReference.Application);
  }


  public static String sourceRelativePath(IClass c) {
    return sourceRelativePath(c, null);
  }


  public static String sourceRelativePath(IClass c, java.util.List<java.io.File> roots) {
    String fq = dotted(c.getName().toString());
    int lastDot = fq.lastIndexOf('.');
    String pkg = lastDot < 0 ? "" : fq.substring(0, lastDot + 1);
    String simple = fq.substring(lastDot + 1);

    java.util.List<String> candidates = new java.util.ArrayList<>();
    candidates.add(simple);

    for (int i = simple.indexOf('$', 1); i > 0; i = simple.indexOf('$', i + 1)) {
      candidates.add(simple.substring(0, i));
    }

    for (String candidate : candidates) {
      String rel = (pkg + candidate).replace('.', '/') + ".java";
      if (roots == null) {
        continue;
      }
      for (java.io.File root : roots) {
        if (new java.io.File(root, rel).isFile()) {
          return rel;
        }
      }
    }


    String outermost = candidates.get(candidates.size() - 1);
    return (pkg + outermost).replace('.', '/') + ".java";
  }
}
