# Track B image: ibsim, libumad2sim, OpenSM and infiniband-diags from one Ubuntu archive snapshot.
# tracks/b-ibsim/scripts/guest.sh build runs it inside the Track B guest and passes every argument
# from versions.lock.yaml. An empty argument fails the build.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG APT_SNAPSHOT
ARG IBSIM_VERSION
ARG OPENSM_VERSION
ARG INFINIBAND_DIAGS_VERSION

# The base image drops /usr/share/doc, where ibsim-utils ships the upstream net-examples.
COPY dpkg-keep-net-examples /etc/dpkg/dpkg.cfg.d/zz-rfl-keep-net-examples

# The snapshot service is HTTPS only and the base image has no CA bundle, so the guest's bundle is
# mounted for this step alone. apt still checks every index against the archive keyring.
RUN --mount=type=bind,from=guest-ca,source=ca-certificates.crt,target=/etc/ssl/certs/ca-certificates.crt \
    test -n "$APT_SNAPSHOT" -a -n "$IBSIM_VERSION" -a -n "$OPENSM_VERSION" -a -n "$INFINIBAND_DIAGS_VERSION" \
 && rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
 && printf 'Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/%s/\nSuites: noble noble-updates noble-security\nComponents: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' \
      "$APT_SNAPSHOT" > /etc/apt/sources.list.d/ubuntu-snapshot.sources \
 && apt-get update --error-on=any \
 && apt-get install -y --no-install-recommends \
      ibsim-utils="$IBSIM_VERSION" libumad2sim0="$IBSIM_VERSION" \
      opensm="$OPENSM_VERSION" infiniband-diags="$INFINIBAND_DIAGS_VERSION" \
 && rm -rf /var/lib/apt/lists/*

COPY opensm.conf /etc/opensm/rfl-opensm.conf
COPY rfl-ibsim /usr/local/bin/rfl-ibsim
RUN groupadd --gid 10001 rfl \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /run/rfl --shell /usr/sbin/nologin rfl

USER 10001:10001
WORKDIR /run/rfl
ENTRYPOINT ["/usr/local/bin/rfl-ibsim"]
