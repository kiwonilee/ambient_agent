# Vertex AI Endpoint Alerting ADK Ambient Agent

Google Cloud Monitoring의 **Vertex AI Endpoint `response_count`** 메트릭 Alerting 이벤트를 Pub/Sub Push Subscription을 통해 수신하고, 정밀 분석 및 대처 방안을 제공하는 **ADK(Agent Development Kit) Ambient Agent** 프로젝트입니다.

---

## 🏗️ 시스템 아키텍처 및 이벤트 흐름

```
[ Vertex AI Endpoint ]
        │
        ▼ (aiplatform.googleapis.com/endpoint/response_count)
[ Cloud Monitoring Alerting Policy ]
        │
        ▼ (Alert Triggered -> Incident 생성)
[ Notification Channel (Pub/Sub Topic: vertex-endpoint-alerts) ]
        │
        ▼ (Pub/Sub Push Subscription: vertex-alert-agent-sub)
[ ADK Ambient Agent (/apps/ambient_agent/trigger/pubsub) ]
```

1. **Vertex AI Endpoint**: 트래픽 증가로 인해 `response_count` 메트릭 임계값 초과.
2. **Cloud Monitoring Alerting Policy**: 설정된 임계값을 모니터링하다가 조건 충족 시 Alert 인시던트 생성.
3. **Notification Channel**: Alert 메시지를 Pub/Sub Topic으로 발행.
4. **Pub/Sub Push Subscription**: Pub/Sub 메시지를 감지하여 Ambient Agent의 `/apps/ambient_agent/trigger/pubsub` 엔드포인트로 HTTP POST 요청 전송.
5. **Ambient Agent Workflow**: 수신한 Pub/Sub 메시지를 ADK **Graph-based Workflow (`Workflow`)**를 통해 파싱, LLM 인시던트 분석, 서포트 케이스 등록, Slack 알림 전송 파이프라인으로 순차 처리.

---

## 🚀 프로젝트 구조

```
ambient_agent/
├── agent.py               # Graph-based Workflow, parse_event, LlmAgent, create_support_case, send_slack_notification 구현
├── tests/
│   └── test_agent.py      # Workflow 파이프라인 단위 테스트 및 Pub/Sub Push Trigger 통합 테스트
├── pyproject.toml         # Python 패키지 및 의존성 설정
├── requirements.txt       # 의존성 패키지 목록
└── README.md              # 프로젝트 가이드 문서
```

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

## 🛠️ GCP 설정 및 연동 가이드 (의존성 순서)

이벤트를 안전하게 수신하고 처리하기 위한 GCP 자원 생성 및 보안 권한 설정 가이드입니다.

```
[ 1. Service Account 생성 & IAM 권한 부여 ] ➔ SA_EMAIL 획득
          │
          ▼
[ 2. ADK Cloud Run 에이전트 배포 ] ➔ Service URL 획득
          │
          ▼
[ 3. Pub/Sub Topic & Push Subscription 생성 ] ➔ --push-endpoint 연결
          │
          ▼
[ 4. Notification Channel 생성 ] ➔ NOTIFICATION_CHANNEL_ID 획득
          │
          ▼
[ 5. Alerting Policy 생성 및 등록 ] ➔ Notification Channel 연결
```

### 1단계: Service Account 생성 및 IAM 권한 부여
에이전트 런타임 및 Pub/Sub Push 인증에 사용할 Service Account를 생성하고 필요한 역할을 부여합니다.

```bash
# 1) GCP Project ID 및 Project Number 설정 (현재 활성 프로젝트 자동 참조)
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project)}"
export PROJECT_NUMBER=$(gcloud projects describe ${GOOGLE_CLOUD_PROJECT} --format="value(projectNumber)")

# 2) Service Account 설정
export SA_NAME="ambient-agent-sa"
export SA_EMAIL="${SA_NAME}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

# 3) 필요 GCP API 활성화
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

# 4) 서비스 계정 생성
gcloud iam service-accounts create ${SA_NAME} \
  --display-name="Ambient Agent Runtime Service Account" \
  --project=${GOOGLE_CLOUD_PROJECT}

# 5) 에이전트 실행에 필요한 IAM 역할 부여
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

# 6) Pub/Sub 서비스 계정 생성 및 Push 권한 설정
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
  -- --allow-unauthenticated --set-env-vars="GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},GOOGLE_CLOUD_LOCATION=global,LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_GENAI_USE_ENTERPRISE=TRUE"

# 배포 완료 후 Cloud Run URL 저장
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

# 출력 결과의 name 항목에서 notificationChannels/12345678 형태의 CHANNEL ID를 확인 후 설정합니다.
export NOTIFICATION_CHANNEL_ID="YOUR_NOTIFICATION_CHANNEL_ID"
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