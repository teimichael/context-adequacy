package lacuna.analyzer;

import com.ibm.wala.classLoader.IClass;
import com.ibm.wala.classLoader.IField;
import com.ibm.wala.classLoader.IMethod;
import com.ibm.wala.ipa.cha.ClassHierarchy;
import com.ibm.wala.types.TypeReference;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.Writer;
import java.util.ArrayList;
import java.util.List;
import java.util.StringJoiner;


public final class IndexWriter {

  private IndexWriter() {}

  public static void write(ClassHierarchy cha, Writer raw) throws IOException {
    try (BufferedWriter w = new BufferedWriter(raw, 1 << 20)) {
      w.write("{\"schema\":\"1\",\"types\":{");
      boolean firstType = true;
      StringBuilder methodsJson = new StringBuilder("{");
      StringBuilder membersJson = new StringBuilder("{");
      boolean firstMethod = true;
      boolean firstMember = true;

      for (IClass c : cha) {
        if (!ElementIds.isApplication(c)) {
          continue;
        }
        String tid = ElementIds.type(c);

        List<String> fields = new ArrayList<>();
        for (IField f : c.getDeclaredInstanceFields()) {
          fields.add(ElementIds.field(f));
          if (!firstMember) {
            membersJson.append(',');
          }
          firstMember = false;
          membersJson
              .append(q(ElementIds.field(f)))
              .append(":{\"id\":")
              .append(q(ElementIds.field(f)))
              .append(",\"static\":false,\"final\":")
              .append(f.isFinal())
              .append(",\"type_ref\":")
              .append(q(ElementIds.type(f.getFieldTypeReference())))
              .append('}');
        }
        for (IField f : c.getDeclaredStaticFields()) {
          fields.add(ElementIds.field(f));
          if (!firstMember) {
            membersJson.append(',');
          }
          firstMember = false;
          membersJson
              .append(q(ElementIds.field(f)))
              .append(":{\"id\":")
              .append(q(ElementIds.field(f)))
              .append(",\"static\":true,\"final\":")
              .append(f.isFinal())
              .append(",\"type_ref\":")
              .append(q(ElementIds.type(f.getFieldTypeReference())))
              .append('}');
        }

        List<String> methods = new ArrayList<>();
        for (IMethod m : c.getDeclaredMethods()) {
          String mid = ElementIds.method(m);
          methods.add(mid);
          if (!firstMethod) {
            methodsJson.append(',');
          }
          firstMethod = false;
          methodsJson
              .append(q(mid))
              .append(":{\"id\":")
              .append(q(mid))
              .append(",\"owner\":")
              .append(q(tid))
              .append(",\"static\":")
              .append(m.isStatic())
              .append(",\"parameter_types\":")
              .append(paramTypes(m))
              .append(",\"return_type\":")
              .append(
                  m.getReturnType().equals(TypeReference.Void)
                      ? "null"
                      : q(ElementIds.type(m.getReturnType())))
              .append(",\"thrown_types\":")
              .append(thrownTypes(m))
              .append('}');
        }

        if (!firstType) {
          w.write(",");
        }
        firstType = false;
        w.write(q(tid) + ":{\"id\":" + q(tid)
            + ",\"fields\":" + arr(fields)
            + ",\"methods\":" + arr(methods)
            + ",\"supertypes\":" + arr(superTypeIds(c))
            + "}");
      }

      w.write("},\"methods\":" + methodsJson.append('}')
          + ",\"members\":" + membersJson.append('}') + "}\n");
    }
  }

  private static List<String> superTypeIds(IClass c) {
    List<String> out = new ArrayList<>();
    IClass sup = c.getSuperclass();
    if (sup != null && ElementIds.isApplication(sup)) {
      out.add(ElementIds.type(sup));
    }
    for (IClass i : c.getDirectInterfaces()) {
      if (ElementIds.isApplication(i)) {
        out.add(ElementIds.type(i));
      }
    }
    return out;
  }

  private static String paramTypes(IMethod m) {
    List<String> out = new ArrayList<>();
    int start = m.isStatic() ? 0 : 1;
    for (int i = start; i < m.getNumberOfParameters(); i++) {
      out.add(ElementIds.type(m.getParameterType(i)));
    }
    return arr(out);
  }

  private static String thrownTypes(IMethod m) {
    List<String> out = new ArrayList<>();
    try {
      TypeReference[] ex = m.getDeclaredExceptions();
      if (ex != null) {
        for (TypeReference t : ex) {
          out.add(ElementIds.type(t));
        }
      }
    } catch (Throwable ignored) {

    }
    return arr(out);
  }

  private static String arr(List<String> xs) {
    StringJoiner j = new StringJoiner(",", "[", "]");
    for (String x : xs) {
      j.add(q(x));
    }
    return j.toString();
  }

  private static String q(String s) {
    return '"' + s.replace("\\", "\\\\").replace("\"", "\\\"") + '"';
  }
}
