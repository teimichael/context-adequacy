package lacuna.analyzer;

import com.ibm.wala.core.util.config.AnalysisScopeReader;
import com.ibm.wala.ipa.callgraph.AnalysisScope;
import com.ibm.wala.ipa.cha.ClassHierarchy;
import com.ibm.wala.ipa.cha.ClassHierarchyFactory;
import com.ibm.wala.types.ClassLoaderReference;
import com.ibm.wala.util.config.FileOfClasses;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;


public final class ScopeBuilder {


  public static final String EXCLUSIONS =
      String.join(
              "\n",
              "java\\/awt\\/.*",
              "javax\\/swing\\/.*",
              "sun\\/awt\\/.*",
              "sun\\/swing\\/.*",
              "com\\/sun\\/.*",
              "sun\\/.*",
              "org\\/netbeans\\/.*",
              "org\\/openide\\/.*",
              "com\\/ibm\\/crypto\\/.*",
              "com\\/ibm\\/security\\/.*",
              "org\\/apache\\/xerces\\/.*",
              "dalvik\\/.*",
              "java\\/io\\/ObjectStreamClass*",
              "apple\\/.*",
              "com\\/apple\\/.*",
              "org\\/omg\\/.*",
              "org\\/w3c\\/.*")
          + "\n";

  private ScopeBuilder() {}

  public static AnalysisScope build(Config cfg) throws IOException {
    java.util.List<String> libs = new java.util.ArrayList<>();
    for (String e : cfg.libJars.split(File.pathSeparator)) {
      if (!e.isEmpty()) {
        libs.add(e);
      }
    }
    return build(cfg, libs);
  }

  public static AnalysisScope build(Config cfg, java.util.List<String> libs)
      throws IOException {
    AnalysisScope scope = AnalysisScopeReader.instance.makePrimordialScope(null);

    StringBuilder app = new StringBuilder(cfg.appClasses);
    if (!cfg.testClasses.isEmpty()) {
      app.append(':').append(cfg.testClasses);
    }
    AnalysisScopeReader.instance.addClassPathToScope(
        app.toString(), scope, ClassLoaderReference.Application);













    skippedLibJars = 0;
    for (String entry : libs) {
      try {
        AnalysisScopeReader.instance.addClassPathToScope(
            entry, scope, ClassLoaderReference.Extension);
      } catch (Throwable t) {
        skippedLibJars++;
      }
    }

    scope.setExclusions(new FileOfClasses(new ByteArrayInputStream(EXCLUSIONS.getBytes())));
    return scope;
  }


  public static int skippedLibJars = 0;

  public static ClassHierarchy hierarchy(AnalysisScope scope) throws Exception {
    return ClassHierarchyFactory.make(scope);
  }


  public static final java.util.List<String> poisonJars = new java.util.ArrayList<>();


  public static final int MAX_HIERARCHY_BUILDS = 40;


  public static int hierarchyBuilds = 0;


  public static boolean bisectionTruncated = false;


  public static ClassHierarchy hierarchyLenient(Config cfg, java.util.List<String> libs)
      throws Exception {
    poisonJars.clear();
    hierarchyBuilds = 0;
    bisectionTruncated = false;
    try {
      hierarchyBuilds++;
      return ClassHierarchyFactory.make(build(cfg, libs));
    } catch (Exception | LinkageError first) {
      java.util.List<String> good = keepGood(cfg, new java.util.ArrayList<>(libs));
      try {
        hierarchyBuilds++;
        return ClassHierarchyFactory.make(build(cfg, good));
      } catch (Exception | LinkageError second) {


        poisonJars.clear();
        poisonJars.addAll(libs);
        try {
          hierarchyBuilds++;
          return ClassHierarchyFactory.make(build(cfg, java.util.List.of()));
        } catch (Exception | LinkageError third) {
          throw new IllegalStateException(
              "class hierarchy fails even with no dependency jars; the application "
                  + "scope itself cannot be loaded", third);
        }
      }
    }
  }


  private static java.util.List<String> keepGood(Config cfg, java.util.List<String> libs) {
    if (hierarchyBuilds >= MAX_HIERARCHY_BUILDS) {



      bisectionTruncated = true;
      poisonJars.addAll(libs);
      return new java.util.ArrayList<>();
    }
    if (libs.isEmpty() || buildsWith(cfg, libs)) {
      return libs;
    }
    if (libs.size() == 1) {
      poisonJars.add(libs.get(0));
      return new java.util.ArrayList<>();
    }
    int mid = libs.size() / 2;
    java.util.List<String> out = new java.util.ArrayList<>(
        keepGood(cfg, new java.util.ArrayList<>(libs.subList(0, mid))));
    out.addAll(keepGood(cfg, new java.util.ArrayList<>(libs.subList(mid, libs.size()))));
    return out;
  }

  private static boolean buildsWith(Config cfg, java.util.List<String> libs) {
    try {
      hierarchyBuilds++;
      ClassHierarchyFactory.make(build(cfg, libs));
      return true;
    } catch (Exception | LinkageError e) {
      return false;
    }
  }
}
