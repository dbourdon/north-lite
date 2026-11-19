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
{{- end }}
