package lacuna.analyzer;


public final class Config {
  public String appClasses = "";
  public String testClasses = "";
  public String libJars = "";
  public String sourceRoots = "";
  public String out = "edg.jsonl";
  public String escapesOut = "escapes.json";
  public String indexOut = "index.json";

  public String cg = "RTA";
  public boolean chaAugmented = true;
  public boolean fieldSens = true;
  public String heapModel = "element-field-edges";
  public String entryPoints = "+test-methods";
  public String mode = "FAST";
  public int depthCap = 3;
  public int timeoutSeconds = 600;
  public String dataDep = "NO_BASE_NO_HEAP";
  public String controlDep = "FULL";

  public String seeds = "";

  public static Config parse(String[] args) {
    Config c = new Config();
    for (int i = 0; i < args.length; i++) {
      String a = args[i];
      if (!a.startsWith("--")) {
        throw new IllegalArgumentException("unexpected argument: " + a);
      }
      String key = a.substring(2);
      if (i + 1 >= args.length) {
        throw new IllegalArgumentException("missing value for --" + key);
      }
      String v = args[++i];
      switch (key) {
        case "app-classes" -> c.appClasses = v;
        case "test-classes" -> c.testClasses = v;
        case "lib-jars" -> c.libJars = v;
        case "source-roots" -> c.sourceRoots = v;
        case "out" -> c.out = v;
        case "escapes-out" -> c.escapesOut = v;
        case "index-out" -> c.indexOut = v;
        case "cg" -> c.cg = v;
        case "cha-augmented" -> c.chaAugmented = Boolean.parseBoolean(v);
        case "field-sens" -> c.fieldSens = Boolean.parseBoolean(v);
        case "heap-model" -> c.heapModel = v;
        case "entry-points" -> c.entryPoints = v;
        case "mode" -> c.mode = v;
        case "depth-cap" -> c.depthCap = Integer.parseInt(v);
        case "timeout-seconds" -> c.timeoutSeconds = Integer.parseInt(v);
        case "data-dep" -> c.dataDep = v;
        case "control-dep" -> c.controlDep = v;
        case "seeds" -> c.seeds = v;
        default -> throw new IllegalArgumentException("unknown option --" + key);
      }
    }
    if (c.appClasses.isEmpty()) {
      throw new IllegalArgumentException("--app-classes is required");
    }
    return c;
  }
}
