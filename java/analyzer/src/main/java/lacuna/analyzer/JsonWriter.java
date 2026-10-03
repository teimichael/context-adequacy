package lacuna.analyzer;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.Writer;


public final class JsonWriter implements AutoCloseable {

  public static final String SCHEMA = "1";

  private final BufferedWriter w;
  public int nodes = 0;
  public int edges = 0;

  public JsonWriter(Writer w) throws IOException {
    this.w = new BufferedWriter(w, 1 << 20);
    this.w.write("{\"schema\":\"" + SCHEMA + "\",\"type\":\"header\"}\n");
  }

  private static String esc(String s) {
    StringBuilder b = new StringBuilder(s.length() + 8);
    for (int i = 0; i < s.length(); i++) {
      char c = s.charAt(i);
      switch (c) {
        case '"' -> b.append("\\\"");
        case '\\' -> b.append("\\\\");
        case '\n' -> b.append("\\n");
        case '\r' -> b.append("\\r");
        case '\t' -> b.append("\\t");
        default -> {
          if (c < 0x20) {
            b.append(String.format("\\u%04x", (int) c));
          } else {
            b.append(c);
          }
        }
      }
    }
    return b.toString();
  }

  public void node(String id, String sourceFile, Integer line, String scope) {
    node(id, sourceFile, line, scope, null);
  }


  public void node(String id, String sourceFile, Integer line, String scope,
      Boolean inCallGraph) {
    try {
      StringBuilder b = new StringBuilder(160);
      b.append("{\"type\":\"node\",\"id\":\"").append(esc(id)).append('"');
      if (sourceFile != null) {
        b.append(",\"source_file\":\"").append(esc(sourceFile)).append('"');
      }
      if (line != null && line > 0) {
        b.append(",\"start_line\":").append(line);
      }
      b.append(",\"scope\":\"").append(esc(scope)).append('"');
      if (inCallGraph != null) {
        b.append(",\"in_callgraph\":").append(inCallGraph.booleanValue());
      }
      b.append('}');
      w.write(b.toString());
      w.write('\n');
      nodes++;
    } catch (IOException e) {
      throw new RuntimeException("failed writing EDG node " + id, e);
    }
  }

  public void edge(String src, String dst, String kind) {
    try {
      w.write("{\"type\":\"edge\",\"src\":\"" + esc(src) + "\",\"dst\":\"" + esc(dst)
          + "\",\"kind\":\"" + esc(kind) + "\"}\n");
      edges++;
    } catch (IOException e) {
      throw new RuntimeException("failed writing EDG edge " + src + " -> " + dst, e);
    }
  }

  @Override
  public void close() throws IOException {
    w.flush();
    w.close();
  }
}
