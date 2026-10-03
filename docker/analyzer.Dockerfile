FROM gradle:8.14-jdk21@sha256:4494a8e5b809daae06bdf6c23e2ce19d2fcb00f4f532d28d5f69f529224b3a1c AS build
WORKDIR /src
COPY java/analyzer/ /src/
RUN gradle --no-daemon jar

FROM eclipse-temurin:21-jdk@sha256:1f79c73404fb0cccf9a3459eda22892f368d994b1028d6fb1ae871c1f49749a6
LABEL org.opencontainers.image.title="lacuna-analyzer"
LABEL org.opencontainers.image.description="WALA-based element dependence graph extractor"
COPY --from=build /src/build/libs/lacuna-analyzer.jar /opt/lacuna/lacuna-analyzer.jar
ENV JAVA_TOOL_OPTIONS="-XX:+UseSerialGC"
RUN mkdir -p /work/in /work/out
WORKDIR /work
ENTRYPOINT ["java", "-jar", "/opt/lacuna/lacuna-analyzer.jar"]
