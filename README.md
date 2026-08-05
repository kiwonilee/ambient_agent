# Vertex AI Endpoint Alerting ADK Ambient Agent

Google Cloud Monitoring의 **Vertex AI Endpoint `response_count`** 메트릭 Alerting 이벤트를 Pub/Sub Push Subscription을 통해 수신하고, 정밀 분석 및 대처 방안을 제공하는 **ADK(Agent Development Kit) Ambient Agent** 프로젝트입니다.

---


## 🤖 `agent.py` Graph-based Agent Workflow 구조

ADK **`Workflow`** 기반의 [agent.py](file:///home/user/workspace/ambient_agent/agent.py)는 순차적 그래프 노드 파이프라인으로 구성되어 있습니다:

```
[ START ] ──► [ parse_event ] ──► [ generate_case_summary_agent ] ──► [ create_support_case ] ──► [ send_slack_notification ]
               (Direct Pass-through)      (LLM 인시던트 분석 + Callback)    (Support Case 등록)           (Slack 전달)
```

1. **`parse_event` (결정적 Python 노드)**:
   - Pub/Sub 이벤트 메시지(`node_input`)를 그대로 수신하여 출력하고, `ctx.state['parsed_event']`에 저장하여 하위 노드로 전달.
2. **`generate_case_summary_agent` (LLM 인시던트 분석 에이전트)**:
   - `LlmAgent` (Gemini 3.5-flash)가 이전 노드의 `node_input`에서 `endpoint_id`, `location`, `project_id`, `started_at`, `ended_at`, `state`, `condition_name`, `summary` 등의 필드를 추출하여 요약 리포트 생성.
   - **`after_agent_callback` (`log_summary_callback`)**: 에이전트 생성 완료 직후 요약 메시지를 즉시 표준 출력(`print`)합니다.
3. **`create_support_case` (결정적 Python 노드)**:
   - 인시던트 입력 정보와 LLM 요약 메시지를 출력하고 서포트 케이스 등록을 진행.
4. **`send_slack_notification` (결정적 Python 노드)**:
   - 인시던트 입력 정보와 LLM 요약 메시지를 출력하고 사용자 Slack 채널로 알림 전달.

---

### 1단계: Service Account 생성 및 IAM 권한 부여
에이전트 런타임 및 Pub/Sub Push 인증에 사용할 Service Account를 생성하고 필요한 역할을 부여합니다.

```bash
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project)}"
```
```bash
export SA_NAME="ambient-agent-sa"
export SA_EMAIL="${SA_NAME}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
export PROJECT_NUMBER=$(gcloud projects describe ${GOOGLE_CLOUD_PROJECT} --format="value(projectNumber)")

# 0) 필요 GCP API 활성화
gcloud services enable \
    aiplatform.googleapis.com \
    run.googleapis.com \
    pubsub.googleapis.com \
    monitoring.googleapis.com \
    artifactregistry.googleapis.com \
    logging.googleapis.com \
    cloudtrace.googleapis.com \
    storage.googleapis.com \
    iam.googleapis.com \
    --project=${GOOGLE_CLOUD_PROJECT}

# 1) 서비스 계정 생성
gcloud iam service-accounts create ${SA_NAME} \
  --display-name="Ambient Agent Runtime Service Account" \
  --project=${GOOGLE_CLOUD_PROJECT}

# 2) 에이전트 실행에 필요한 IAM 역할 부여
ROLES=(
    "roles/aiplatform.user"         # Vertex AI Gemini 모델 사용 권한
    "roles/logging.logWriter"       # Cloud Logging 런타임 로그 기록 권한
    "roles/storage.objectAdmin"     # Staging Bucket 아티팩트 읽기/쓰기 권한
)

for ROLE in "${ROLES[@]}"; do
    gcloud projects add-iam-policy-binding ${GOOGLE_CLOUD_PROJECT} \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${ROLE}"
done

# 3) Pub/Sub 서비스 계정 생성 및 Push 권한 설정
gcloud beta services identity create --service=pubsub.googleapis.com --project=${GOOGLE_CLOUD_PROJECT}

PUBSUB_SA="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

gcloud iam service-accounts add-iam-policy-binding ${SA_EMAIL} \
  --member="serviceAccount:${PUBSUB_SA}" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project=${GOOGLE_CLOUD_PROJECT}
```

---

### 2단계: ADK Cloud Run 에이전트 배포
`adk deploy cloud_run` 커맨드를 사용하여 ADK 에이전트를 Cloud Run 서비스로 배포합니다. Pub/Sub Direct Push 엔드포인트(`/apps/ambient_agent/trigger/pubsub`)가 자동으로 노출됩니다.

```bash
adk deploy cloud_run \
  --project="${GOOGLE_CLOUD_PROJECT}" \
  --region="us-central1" \
  --service_name="ambient-agent-service" \
  --app_name="ambient_agent" \
  --trigger_sources="pubsub" \
  --artifact_service_uri="gs://${GOOGLE_CLOUD_PROJECT}-bucket" \
  /home/user/workspace/ambient_agent \
  -- --allow-unauthenticated --set-env-vars="GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},GOOGLE_CLOUD_LOCATION=global,LOCATION=global"
```
```bash
export CLOUD_RUN_URL="https://ambient-agent-service-${PROJECT_NUMBER}.us-central1.run.app"
```

---

### 3단계: Pub/Sub Topic 및 Direct Push Subscription 생성
Cloud Monitoring 경고를 받아줄 Pub/Sub Topic을 생성하고, Cloud Run의 ADK Pub/Sub 트리거 URL(`/apps/ambient_agent/trigger/pubsub`)로 메시지를 전달하는 Direct Push Subscription을 생성합니다.

```bash
# 1) Pub/Sub Topic 생성
gcloud pubsub topics create vertex-endpoint-alerts --project ${GOOGLE_CLOUD_PROJECT}

# 2) Cloud Monitoring 알림 서비스 계정 생성 및 Pub/Sub Topic 게시(Publisher) 권한 부여
gcloud beta services identity create --service=monitoring.googleapis.com --project=${GOOGLE_CLOUD_PROJECT}

export MONITORING_SA="service-${PROJECT_NUMBER}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"

gcloud pubsub topics add-iam-policy-binding vertex-endpoint-alerts \
  --member="serviceAccount:${MONITORING_SA}" \
  --role="roles/pubsub.publisher" \
  --project=${GOOGLE_CLOUD_PROJECT}

# 3) Pub/Sub Direct Push Subscription 생성
gcloud pubsub subscriptions create vertex-alert-agent-sub \
  --topic=vertex-endpoint-alerts \
  --push-endpoint="${CLOUD_RUN_URL}/apps/ambient_agent/trigger/pubsub" \
  --project=${GOOGLE_CLOUD_PROJECT}
```

---

### 4단계: Cloud Monitoring Notification Channel 생성
Pub/Sub Topic(`vertex-endpoint-alerts`)을 수신처로 지정하는 Notification Channel을 생성합니다.

```bash
gcloud alpha monitoring channels create \
  --display-name="Vertex Endpoint Alerts PubSub Channel" \
  --type="pubsub" \
  --channel-labels=topic=projects/${GOOGLE_CLOUD_PROJECT}/topics/vertex-endpoint-alerts
```
```
NOTIFICATION_CHANNEL_ID=15260977979104931430
```

---

### 5단계: Cloud Monitoring Alerting Policy 생성 및 등록
Vertex AI Endpoint의 `response_count` 메트릭에 대해 HTTP 응답 코드별(`200`, `400`, `429`, `500`)로 Alerting Policy를 등록합니다:

```bash
for CODE in 200 400 429 500; do
  gcloud alpha monitoring policies create \
    --display-name="Vertex AI Endpoint High Response Count Alert (HTTP ${CODE})" \
    --condition-display-name="Vertex AI Endpoint response_count (HTTP ${CODE}) > 0" \
    --condition-filter="resource.type = \"aiplatform.googleapis.com/Endpoint\" AND metric.type = \"aiplatform.googleapis.com/prediction/online/response_count\" AND metric.label.response_code = \"${CODE}\"" \
    --duration="0s" \
    --if="> 0" \
    --combiner="OR" \
    --aggregation='{"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_RATE"}' \
    --notification-channels="projects/${GOOGLE_CLOUD_PROJECT}/notificationChannels/${NOTIFICATION_CHANNEL_ID}"
done
```