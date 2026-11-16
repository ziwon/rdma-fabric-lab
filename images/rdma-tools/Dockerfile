# RDMA test image of the hostNetwork RDMA pods: the RXE userspace provider, ibv_devinfo, rping and perftest
# at the versions the lab workers run, and iproute2 for rdma resource show, every package from one Ubuntu
# archive snapshot. images/rdma-tools/build.sh runs it inside a harness guest and passes every argument from
# versions.lock.yaml. An empty argument fails the build.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG APT_SNAPSHOT
ARG RDMA_CORE_VERSION
ARG PERFTEST_VERSION

# The snapshot service is HTTPS only and the base image has no CA bundle, so the guest's bundle is
# mounted for this step alone. apt still checks every index against the archive keyring. The logs and
# the ldconfig cache hold build times, so they go too.
RUN --mount=type=bind,from=guest-ca,source=ca-certificates.crt,target=/etc/ssl/certs/ca-certificates.crt \
    test -n "$APT_SNAPSHOT" -a -n "$RDMA_CORE_VERSION" -a -n "$PERFTEST_VERSION" \
 && rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
 && printf 'Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/%s/\nSuites: noble noble-updates noble-security\nComponents: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' \
      "$APT_SNAPSHOT" > /etc/apt/sources.list.d/ubuntu-snapshot.sources \
 && apt-get update --error-on=any \
 && apt-get install -y --no-install-recommends \
      ibverbs-providers="$RDMA_CORE_VERSION" ibverbs-utils="$RDMA_CORE_VERSION" rdmacm-utils="$RDMA_CORE_VERSION" \
      perftest="$PERFTEST_VERSION" iproute2 \
 && rm -rf /var/lib/apt/lists/* /var/log/apt /var/log/dpkg.log /var/log/alternatives.log /var/cache/ldconfig/aux-cache \
      /var/cache/debconf/*-old /var/lib/dpkg/*-old

# Every installed package built from the rdma-core source carries the locked version, libraries included.
RUN dpkg-query -W -f '${source:Package} ${Package} ${Version}\n' | awk -v want="$RDMA_CORE_VERSION" \
      '$1 == "rdma-core" { n++; print; if ($3 != want) bad++ } END { exit (n == 0 || bad) }'

USER 65534:65534
