{{/*
Expand the name of the chart.
*/}}
{{- define "north-lite.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "north-lite.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "north-lite.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "north-lite.labels" -}}
helm.sh/chart: {{ include "north-lite.chart" . }}
{{ include "north-lite.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "north-lite.selectorLabels" -}}
app.kubernetes.io/name: {{ include "north-lite.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Labels for test pods: common labels minus the selector labels. A Ready test pod
carrying the selector labels would be added to the Service's endpoints and
receive traffic.
*/}}
{{- define "north-lite.testLabels" -}}
helm.sh/chart: {{ include "north-lite.chart" . }}
app.kubernetes.io/part-of: {{ include "north-lite.name" . }}
app.kubernetes.io/component: helm-test
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Label the NetworkPolicy selects on. Separate from the selector labels so the
egress test pod can be put under the same policy without the Service routing
traffic to it.
*/}}
{{- define "north-lite.policyLabels" -}}
north-lite.dev/egress-policy: {{ include "north-lite.fullname" . }}
{{- end }}

{{/*
Argo CD hook annotations for the test pods: run after every sync, replacing the
previous run's pod (a failed one stays until the next sync, for `kubectl logs`).
*/}}
{{- define "north-lite.argoPostSync" -}}
argocd.argoproj.io/hook: PostSync
argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "north-lite.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "north-lite.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Name of the Secret holding the Cohere API key: a pre-existing one (preferred,
e.g. created by External Secrets) or the one this chart creates from a value.
*/}}
{{- define "north-lite.secretName" -}}
{{- default (include "north-lite.fullname" .) .Values.apiKey.existingSecret }}
{{- end }}

{{/*
Renders "true" when the chart has some source for the API key, else nothing.
(Templates return strings, so "empty" is the false value.)
*/}}
{{- define "north-lite.hasApiKey" -}}
{{- if or .Values.apiKey.existingSecret .Values.apiKey.value }}true{{- end }}
{{- end }}

{{/*
Fail at render time on settings that would only break once deployed.
*/}}
{{- define "north-lite.validate" -}}
{{- if and (eq .Values.config.backend "cohere") (not (include "north-lite.hasApiKey" .)) }}
{{- fail "config.backend=cohere needs an API key: set apiKey.existingSecret (preferred) or apiKey.value, or use config.backend=fake" }}
{{- end }}
{{- with .Values.networkPolicy }}
{{- if and .enabled (eq $.Values.config.backend "cohere") (not .extraEgress) }}
{{- fail "networkPolicy blocks all external egress, so config.backend=cohere can't reach the API: allow it via networkPolicy.extraEgress or use an in-cluster backend (fake stands in here)" }}
{{- end }}
{{- end }}
{{- end }}
