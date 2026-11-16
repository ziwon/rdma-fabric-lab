# Track C image: the SimAI ns-3 network backend and the repository's rfl-star driver, built from pinned
# commits and one Ubuntu archive snapshot. tracks/c-ns3/scripts/guest.sh build runs it inside the Track C
# guest with every argument from versions.lock.yaml and the named context "driver" (tracks/c-ns3/driver).
# The build stage applies the one ns-3 patch of this directory, runs SimAI's own ./scripts/build.sh -c ns3
# unchanged, then builds rfl-star in the same ns-3 tree. Only the build outputs reach the final stage, so
# compiler scratch files never do.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS build

ARG APT_SNAPSHOT
ARG SIMAI_COMMIT
ARG NS3_COMMIT
ARG SIMCCL_COMMIT
ARG NS3_TREE=/opt/simai/astra-sim-alibabacloud/extern/network_backend/ns3-interface/simulation

COPY apt-snapshot-install fetch-sources /usr/local/sbin/
RUN --mount=type=bind,from=guest-ca,source=ca-certificates.crt,target=/run/guest-ca/ca-certificates.crt \
    test -n "$APT_SNAPSHOT" -a -n "$SIMAI_COMMIT" -a -n "$NS3_COMMIT" -a -n "$SIMCCL_COMMIT" \
 && apt-snapshot-install "$APT_SNAPSHOT" build-essential cmake python3 git ca-certificates \
 && ! command -v ninja
RUN fetch-sources /opt/simai "$SIMAI_COMMIT" "$NS3_COMMIT" "$SIMCCL_COMMIT" > /opt/simai-sources.tsv
COPY qbb-resume-qp-lookup.patch /opt/rfl-patches/
RUN git -C /opt/simai/ns-3-alibabacloud apply --check /opt/rfl-patches/qbb-resume-qp-lookup.patch \
 && git -C /opt/simai/ns-3-alibabacloud apply /opt/rfl-patches/qbb-resume-qp-lookup.patch \
 && printf 'qbb-resume-qp-lookup.patch\tsha256:%s\tapplied to ns-3-alibabacloud\n' \
      "$(sha256sum < /opt/rfl-patches/qbb-resume-qp-lookup.patch | cut -d' ' -f1)" >> /opt/simai-sources.tsv
WORKDIR /opt/simai
RUN ./scripts/build.sh -c ns3
COPY --from=driver rfl-star.cc ${NS3_TREE}/scratch/rfl-star.cc
RUN cd "$NS3_TREE" && ./ns3 build rfl-star \
 && test -x build/scratch/ns3.36.1-rfl-star-debug -a -x build/scratch/ns3.36.1-AstraSimNetwork-debug

FROM ${BASE_IMAGE}
ARG APT_SNAPSHOT
ARG NS3_TREE=/opt/simai/astra-sim-alibabacloud/extern/network_backend/ns3-interface/simulation
RUN --mount=type=bind,source=apt-snapshot-install,target=/run/rfl-build/apt-snapshot-install \
    --mount=type=bind,from=guest-ca,source=ca-certificates.crt,target=/run/guest-ca/ca-certificates.crt \
    /run/rfl-build/apt-snapshot-install "$APT_SNAPSHOT" python3 time
COPY --from=build ${NS3_TREE}/build ${NS3_TREE}/build
COPY --from=build /opt/simai/example /opt/simai/example
COPY --from=build /opt/simai/astra-sim-alibabacloud/inputs /opt/simai/astra-sim-alibabacloud/inputs
COPY --from=build /opt/simai-sources.tsv /opt/simai/rfl-sources.tsv
COPY rfl-ns3 /usr/local/bin/rfl-ns3
RUN mkdir -p /opt/simai/bin \
 && ln -s ${NS3_TREE}/build/scratch/ns3.36.1-AstraSimNetwork-debug /opt/simai/bin/SimAI_simulator \
 && ln -s ${NS3_TREE}/build/scratch/ns3.36.1-rfl-star-debug /opt/simai/bin/rfl-star \
 && ln -s /run/rfl/out/astra-sim /etc/astra-sim

USER 10001:10001
ENV HOME=/run/rfl/out PYTHONDONTWRITEBYTECODE=1
WORKDIR /run/rfl/out
ENTRYPOINT ["/usr/local/bin/rfl-ns3"]
