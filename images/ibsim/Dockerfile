# Track B image: ibsim, libumad2sim, OpenSM and infiniband-diags from one Ubuntu archive snapshot.
# tracks/b-ibsim/scripts/guest.sh build runs it inside the Track B guest and passes every argument
# from versions.lock.yaml. An empty argument fails the build. The same arguments give the same bytes:
# no file keeps the build time, and BuildKit stamps every file with SOURCE_DATE_EPOCH, the snapshot's time.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG APT_SNAPSHOT
ARG IBSIM_VERSION
ARG OPENSM_VERSION
ARG INFINIBAND_DIAGS_VERSION
ARG SOURCE_DATE_EPOCH

# The base image drops /usr/share/doc, where ibsim-utils ships the upstream net-examples.
COPY dpkg-keep-net-examples /etc/dpkg/dpkg.cfg.d/zz-rfl-keep-net-examples

# The snapshot service is HTTPS only and the base image has no CA bundle, so the guest's bundle is
# mounted for this step alone. apt still checks every index against the archive keyring.
RUN --mount=type=bind,from=guest-ca,source=ca-certificates.crt,target=/etc/ssl/certs/ca-certificates.crt \
    test -n "$APT_SNAPSHOT" -a -n "$IBSIM_VERSION" -a -n "$OPENSM_VERSION" -a -n "$INFINIBAND_DIAGS_VERSION" -a -n "$SOURCE_DATE_EPOCH" \
 && rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
 && printf 'Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/%s/\nSuites: noble noble-updates noble-security\nComponents: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' \
      "$APT_SNAPSHOT" > /etc/apt/sources.list.d/ubuntu-snapshot.sources \
 && apt-get update --error-on=any \
 && apt-get install -y --no-install-recommends \
      ibsim-utils="$IBSIM_VERSION" libumad2sim0="$IBSIM_VERSION" \
      opensm="$OPENSM_VERSION" infiniband-diags="$INFINIBAND_DIAGS_VERSION" \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/*.bin /var/log/apt /var/log/dpkg.log /var/log/alternatives.log \
      /var/cache/ldconfig/aux-cache /var/cache/debconf/*-old /var/lib/dpkg/*-old

COPY opensm.conf /etc/opensm/rfl-opensm.conf
COPY rfl-ibsim /usr/local/bin/rfl-ibsim
# useradd would write the day of the build into /etc/shadow, so the account lines are written as they are.
RUN printf 'rfl:x:10001:10001::/run/rfl:/usr/sbin/nologin\n' >> /etc/passwd \
 && printf 'rfl:x:10001:\n' >> /etc/group

USER 10001:10001
WORKDIR /run/rfl
ENTRYPOINT ["/usr/local/bin/rfl-ibsim"]
