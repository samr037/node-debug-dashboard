{{- define "node-debug-dashboard.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "node-debug-dashboard.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "node-debug-dashboard.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "node-debug-dashboard.labels" -}}
helm.sh/chart: {{ include "node-debug-dashboard.chart" . }}
{{ include "node-debug-dashboard.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "node-debug-dashboard.selectorLabels" -}}
app.kubernetes.io/name: {{ include "node-debug-dashboard.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "node-debug-dashboard.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "node-debug-dashboard.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "node-debug-dashboard.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{/*
Cluster-scoped objects (ClusterRole, ClusterRoleBinding) are not namespaced,
so two releases of the same name in different namespaces would fight over
one object. Prefix them with the release namespace.
*/}}
{{- define "node-debug-dashboard.clusterName" -}}
{{- printf "%s-%s" .Release.Namespace (include "node-debug-dashboard.fullname" .) -}}
{{- end -}}

{{/*
A credential value as a string. `--set auth.password=0` (or `false`) arrives
as an int/bool, which Go templates treat as empty: testing it directly would
silently drop the credential. Only a missing/null value or "" counts as unset.
*/}}
{{- define "node-debug-dashboard.str" -}}
{{- if not (kindIs "invalid" .) -}}
{{- toString . -}}
{{- end -}}
{{- end -}}

{{/*
Chart-managed Secret holding the credentials passed as plain values, so they
never appear in the DaemonSet / Pod spec (readable with the `view` role).
*/}}
{{- define "node-debug-dashboard.secretName" -}}
{{- printf "%s-credentials" (include "node-debug-dashboard.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Keys the chart-managed Secret would hold, as a dict (empty: no Secret). */}}
{{- define "node-debug-dashboard.secretData" -}}
{{- $data := dict -}}
{{- if not .Values.auth.existingSecret -}}
{{- with include "node-debug-dashboard.str" .Values.auth.token -}}
{{- $_ := set $data "auth-token" . -}}
{{- end -}}
{{- with include "node-debug-dashboard.str" .Values.auth.password -}}
{{- $_ := set $data "auth-password" . -}}
{{- end -}}
{{- with include "node-debug-dashboard.generatedPassword" . -}}
{{- $_ := set $data "auth-password" . -}}
{{- end -}}
{{- end -}}
{{- if not .Values.ssh.existingSecret -}}
{{- with include "node-debug-dashboard.str" .Values.ssh.password -}}
{{- $_ := set $data "ssh-password" . -}}
{{- end -}}
{{- end -}}
{{- toYaml $data -}}
{{- end -}}

{{/*
Whether the chart should generate a dashboard password: no auth.token,
auth.password or auth.existingSecret, and auth.disabled not set.
*/}}
{{- define "node-debug-dashboard.wantsGeneratedPassword" -}}
{{- if not (or .Values.auth.disabled .Values.auth.existingSecret (include "node-debug-dashboard.str" .Values.auth.token) (include "node-debug-dashboard.str" .Values.auth.password)) -}}
true
{{- end -}}
{{- end -}}

{{/*
A random HTTP Basic password, so an install with no credentials is not
served open on every node's address. A password rather than a token because
the browser UI can only prompt for Basic auth; scripts can use it too.

Kept across upgrades by reading the existing Secret back with `lookup`.
`lookup` only works against a live cluster: under `helm template` (and so
Argo CD / Flux rendering) it returns nothing, and a fresh random value on
every render would churn the Secret and restart the pods on each sync. So
without cluster access nothing is generated and auth stays off, as before;
GitOps installs should set auth.existingSecret.
*/}}
{{- define "node-debug-dashboard.generatedPassword" -}}
{{- if include "node-debug-dashboard.wantsGeneratedPassword" . -}}
{{- if lookup "v1" "Namespace" "" .Release.Namespace -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (include "node-debug-dashboard.secretName" .) -}}
{{- $old := "" -}}
{{- if $existing -}}
{{- $old = index ($existing.data | default dict) "auth-password" | default "" -}}
{{- end -}}
{{- if $old -}}
{{- $old | b64dec -}}
{{- else -}}
{{- randAlphaNum 32 -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
