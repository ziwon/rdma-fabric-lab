{{/*
The image fields of a NicClusterPolicy component from a lock-pinned image: repository and image split from the
repository path, and the digest as the version. Refuses a tag without a digest.
*/}}
{{- define "rail-networks.image" -}}
{{- $repository := required (printf "images.%s.repository comes from the lock" .name) .image.repository -}}
{{- $tag := required (printf "images.%s.tag comes from the lock" .name) .image.tag -}}
{{- $parts := splitList "@" $tag -}}
{{- if or (ne (len $parts) 2) (not (hasPrefix "sha256:" (last $parts))) -}}
{{- fail (printf "images.%s.tag %s must be <tag>@sha256:<digest>" .name $tag) -}}
{{- end -}}
repository: {{ dir $repository }}
image: {{ base $repository }}
version: {{ last $parts }}
{{- end -}}

{{/* Node selector terms that match every label of nodeSelector. */}}
{{- define "rail-networks.nodeSelectorTerms" -}}
nodeSelectorTerms:
  - matchExpressions:
      {{- range $key, $value := . }}
      - key: {{ $key }}
        operator: In
        values: [{{ $value | quote }}]
      {{- end }}
{{- end -}}

{{/* Fails unless the site values are complete. */}}
{{- define "rail-networks.check" -}}
{{- if not .Values.rails -}}{{- fail "rails is empty. make fleet-render writes it from the site model" -}}{{- end -}}
{{- if not .Values.nodeSelector -}}{{- fail "nodeSelector is empty, which would select every node" -}}{{- end -}}
{{- if lt (int .Values.perNodeBlockSize) 1 -}}{{- fail "perNodeBlockSize must be at least 1" -}}{{- end -}}
{{- $_ := required "namespace names where the NetworkAttachmentDefinitions go" .Values.namespace -}}
{{- $_ := required "resourceName names the RDMA device plugin resource" .Values.resourceName -}}
{{- end -}}
