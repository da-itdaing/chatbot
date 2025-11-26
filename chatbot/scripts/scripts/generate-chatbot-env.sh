
#!/usr/bin/env bash
set -euo pipefail

########################################
# 기본 설정
########################################

# AWS 리전 (환경변수 없으면 ap-northeast-2)
REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# 챗봇 애플리케이션 경로
# (신규 LangGraph 기반 챗봇은 /home/ubuntu/chatbot 을 기본으로 사용)
APP_HOME="${APP_HOME:-/home/ubuntu/chatbot}"

# 생성할 env 파일 경로
ENV_FILE_PATH="${ENV_FILE:-$APP_HOME/chatbot.env}"

# Secrets Manager ID (모든 앱/챗봇 시크릿이 들어있는 곳)
SECRET_ID="${SECRET_ID:-itdaing/prod/app-secrets}"

########################################
# 유틸 함수
########################################

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

ensure_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "❌ Required command '$1' not found. Install it first." >&2
    exit 1
  fi
}

ensure_command aws
ensure_command python3

########################################
# SSM / Secrets 조회 함수
########################################

log "Fetching parameters from AWS SSM (region: $REGION)"

get_param() {
  aws ssm get-parameter \
    --region "$REGION" \
    --name "$1" \
    --with-decryption \
    --query "Parameter.Value" \
    --output text
}

log "Fetching secrets from AWS Secrets Manager: $SECRET_ID"
SECRET_JSON=$(aws secretsmanager get-secret-value \
    --region "$REGION" \
    --secret-id "$SECRET_ID" \
    --query "SecretString" \
    --output text)

get_secret_field() {
  local key="$1"
  python3 - <<PY
import json
data = json.loads("""$SECRET_JSON""")
value = data.get("$key")
if value is None:
    raise SystemExit("Secret key '$key' not found in $SECRET_ID")
print(value)
PY
}

get_secret_field_optional() {
  local key="$1"
  python3 - <<PY
import json
data = json.loads("""$SECRET_JSON""")
print(data.get("$key", ""))
PY
}

########################################
# SSM에서 DB / 리전 정보 가져오기
########################################

DB_URL=$(get_param "/itdaing/prod/db/url")
DB_USERNAME=$(get_param "/itdaing/prod/db/username")
DB_PASSWORD=$(get_param "/itdaing/prod/db/password")
AWS_REGION_VALUE=$(get_param "/itdaing/prod/aws/region")

########################################
# Secrets Manager에서 챗봇 관련 시크릿 가져오기
# (app-secrets JSON 안에 모두 들어있음)
########################################

OPENAI_API_KEY_VALUE=$(get_secret_field "openai_api_key")
TAVILY_API_KEY_VALUE=$(get_secret_field "tavily_api_key")

LANGSMITH_API_KEY_VALUE=$(get_secret_field "langsmith_api_key")
LANGSMITH_PROJECT_VALUE=$(get_secret_field "langsmith_project")
LANGSMITH_TRACING_VALUE=$(get_secret_field "langsmith_tracing")

LANGCHAIN_API_KEY_VALUE=$(get_secret_field "langchain_api_key")
LANGCHAIN_TRACING_V2_VALUE=$(get_secret_field_optional "langchain_tracing_v2")
LANGCHAIN_ENDPOINT_VALUE=$(get_secret_field "langchain_endpoint")

OPENAI_MODEL_VALUE=${OPENAI_MODEL:-gpt-4o-mini}
OPENAI_EMBEDDING_MODEL_VALUE=${OPENAI_EMBEDDING_MODEL:-text-embedding-3-small}
VECTOR_DIM_VALUE=${VECTOR_DIM:-1536}

########################################
# DB URL → PGVector용 DSN 및 PG 접속 정보로 변환
# - /itdaing/prod/db/url 형식: jdbc:postgresql://host:port/dbname
########################################

log "Parsing DB URL to build PGVECTOR_CONNECTION and POSTGRES_*"

PG_INFO=$(DB_URL="$DB_URL" DB_USERNAME="$DB_USERNAME" DB_PASSWORD="$DB_PASSWORD" python3 - <<'PY'
import os, urllib.parse

db_url = os.environ["DB_URL"]
if db_url.startswith("jdbc:"):
    db_url = db_url[len("jdbc:"):]  # 'postgresql://...' 로 변환

parsed = urllib.parse.urlparse(db_url)
host = parsed.hostname or "localhost"
port = parsed.port or 5432
db   = (parsed.path or "/postgres").lstrip("/")

user = os.environ["DB_USERNAME"]
password = os.environ["DB_PASSWORD"]

dsn = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"

print(host)
print(port)
print(db)
print(dsn)
PY
)

POSTGRES_HOST=$(echo "$PG_INFO" | sed -n '1p')
POSTGRES_PORT=$(echo "$PG_INFO" | sed -n '2p')
POSTGRES_DB=$(echo "$PG_INFO"   | sed -n '3p')
PGVECTOR_CONNECTION=$(echo "$PG_INFO" | sed -n '4p')

########################################
# .env 파일 생성
########################################

log "Writing environment file to $ENV_FILE_PATH"

cat > "$ENV_FILE_PATH" <<EOF2
# App / 환경
APP_ENV=prod

# OpenAI / Tavily
OPENAI_API_KEY=${OPENAI_API_KEY_VALUE}
TAVILY_API_KEY=${TAVILY_API_KEY_VALUE}

OPENAI_MODEL=${OPENAI_MODEL_VALUE}
OPENAI_EMBEDDING_MODEL=${OPENAI_EMBEDDING_MODEL_VALUE}

# PGVector 연결 정보
PGVECTOR_CONNECTION=${PGVECTOR_CONNECTION}
VECTOR_COLLECTION=itdaing_popups
VECTOR_DIM=${VECTOR_DIM_VALUE}

# PostgreSQL (PGVector) 설정
POSTGRES_USER=${DB_USERNAME}
POSTGRES_PASSWORD=${DB_PASSWORD}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_HOST=${POSTGRES_HOST}
POSTGRES_PORT=${POSTGRES_PORT}

# AWS Region
AWS_REGION=${AWS_REGION_VALUE}

# LangSmith / LangChain 설정
LANGSMITH_API_KEY=${LANGSMITH_API_KEY_VALUE}
LANGSMITH_PROJECT=${LANGSMITH_PROJECT_VALUE}
LANGSMITH_TRACING=${LANGSMITH_TRACING_VALUE}

LANGCHAIN_API_KEY=${LANGCHAIN_API_KEY_VALUE}
LANGCHAIN_TRACING_V2=${LANGCHAIN_TRACING_V2_VALUE}
LANGCHAIN_ENDPOINT=${LANGCHAIN_ENDPOINT_VALUE}

# LangGraph / RAG 설정
RAG_TOP_K=3
ZONE_RAG_TOP_K=3
MAX_MESSAGE_HISTORY=6
ZONE_MAX_MESSAGE_HISTORY=6

# Web search
WEBSEARCH_ENABLED=${WEBSEARCH_ENABLED:-false}
WEBSEARCH_PROVIDER=${WEBSEARCH_PROVIDER:-duckduckgo}
WEBSEARCH_TOP_K=${WEBSEARCH_TOP_K:-3}

# 존 RAG용 PGVector (별도 URL 없으면 공통 PGVECTOR_CONNECTION 사용)
PGVECTOR_ZONE_URL=
PGVECTOR_ZONE_COLLECTION=itdaing_zone

# 시드 데이터 경로 (EC2 기본 경로 기준, 필요 시 APP_HOME 기반으로 조정)
MARKETS_SEED_PATH=/home/ubuntu/markets_seed.json
ZONES_SEED_PATH=/home/ubuntu/zones_seed.json

# LangGraph encrypted checkpoint key (32-byte AES key string)
LANGGRAPH_AES_KEY=abcdEFGHijklMNOPqrstUVWXyz012345
EOF2

chmod 600 "$ENV_FILE_PATH"
log "✅ Generated $ENV_FILE_PATH"
