## Itdaing LangSmith 테스트 & 평가 가이드

이 디렉터리는 **Itdaing LangGraph 챗봇**을 LangSmith와 연동해서  
데이터셋을 구축·라벨링·업로드·평가하는 전체 워크플로우를 모아 둔 곳입니다.

- **대상**: `app/graphs/*` LangGraph (consumer/seller) + Postgres(PGVector) 기반 RAG
- **목표**:
  - `/original` 원본 프롬프트로부터 **LangSmith Dataset** 을 구성하고,
  - LLM 보조 라벨링으로 `mode/case_type/...` 메타데이터를 채운 뒤,
  - LangSmith SDK + Target function 으로 **단일턴/멀티턴 챗봇 평가**를 반복 실행.

---

### 1. 폴더 구조

- `langsmith-test/`
  - `original/`
    - 원래 `/home/ubuntu/original` 에 있던 테스트 자산이 그대로 옮겨져 있습니다.
    - `test_prompts.json`  
      - 루트: `{ generated_at, source, count, prompts: [...] }`  
      - 각 prompt: `{ id, role(consumer|seller), section, text, raw }`
    - `test_prompts_30_se*.json`, `test_prompts_100*.json`  
      - 서브셋용 단순 리스트: `[{"input": "..."}, ...]`
  - `input/`
    - 라벨링/정제 후 사용할 **기준 테스트 셋**을 두는 폴더입니다.
    - `test_prompt.md`: 케이스 요약 문서 (C-1.., S-1.., E/PI/PL/...) + 대표 입력 예시.
    - `test_prompts.json`: (선택) 라벨링 완료본. 각 엔트리:  
      `id, case_group, mode, case_type, turn_type, transport, difficulty, expected_behavior, section, input, constraints, ...`
    - `test_prompts_30_se*.json`, `test_prompts_100*.json`: 기준 데이터셋의 서브셋 (PR/배포 전 회귀용).
    - `test_prompts_labeled.json`: `/original/test_prompts.json` 를 LLM으로 1차 라벨링한 결과(오프라인 작업용).
  - 코드 파일
    - `upload_dataset.py`  
      - `original/` 의 JSON들을 LangSmith Dataset 으로 업로드하는 스크립트.
    - `label_dataset.py`  
      - `original/test_prompts.json` 를 읽어 LLM 기반 라벨 제안을 생성하는 오프라인 스크립트.
    - `target_function.py`  
      - LangSmith SDK 평가에서 사용하는 Target function (`run_itdaing_chatbot`);  
        `app.graphs.consumer/seller` 기반 LangGraph 그래프를 직접 호출합니다.
    - `run_langsmith_evals.py`  
      - 지정한 LangSmith Dataset 의 각 example 에 대해 `run_itdaing_chatbot` 을 호출하며  
        응답/latency를 수집하는 평가 러너입니다.

---

### 2. 공통 사전 준비 (환경/시드/DB)

1. **가상환경 활성화**

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate
```

2. **환경 변수 (`chatbot.env`)**

- OpenAI / LangSmith / Postgres / PGVector 관련 설정이 이미 맞춰져 있어야 합니다.
  - `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL`
  - `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_TRACING=true`
  - `PGVECTOR_CONNECTION`, `POSTGRES_*`, `CHECKPOINT_DB_URL` (또는 POSTGRES 기반)

3. **RAG 시드 데이터 로딩 (최초 1회 또는 갱신 시)**

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

python -m app.data.markets_loader --reset   # itdaing_popups
python -m app.data.zones_loader --reset     # itdaing_zone
```

4. **LangGraph 체크포인트용 Postgres**

- FastAPI 기반 **운영 경로**에서는 `AsyncPostgresSaver` 를 사용하므로  
  `DB_SCHEMA.md` 기준으로 `checkpoints*` 테이블이 준비돼 있어야 하고,
  `CHECKPOINT_DB_URL` 또는 `POSTGRES_*` 로 접속 가능한 상태여야 합니다.
- 이 디렉터리에서 실행하는 LangSmith 평가는 in-memory `MemorySaver` 체크포인터를 사용하므로  
  체크포인트 DB가 없어도 동작하지만, 운영 환경과 설정을 맞춰 두면 디버깅에 유리합니다.

---

### 3. 원본 데이터셋 → LangSmith Dataset 업로드

#### 3.1 canonical(278 케이스) 업로드

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

python langsmith-test/upload_dataset.py \
  --dataset-name itdaing-chatbot-original \
  --mode canonical
```

- `langsmith-test/original/test_prompts.json` 를 읽어 LangSmith Dataset `itdaing-chatbot-original` 을 생성/갱신합니다.
  - 각 example:
    - `inputs  = {"message": <text>}`
    - `metadata = {id, role, section, raw, source_file}`

#### 3.2 서브셋(30_se / 100 등) 업로드

```bash
python langsmith-test/upload_dataset.py \
  --dataset-name itdaing-chatbot-original-30se \
  --mode 30_se

python langsmith-test/upload_dataset.py \
  --dataset-name itdaing-chatbot-original-100 \
  --mode 100
```

- 각 서브셋은 별도 Dataset 으로 관리되며:
  - `inputs  = {"message": <input>}`
  - `metadata = {dataset_subset, source_file, index}`

> LangSmith UI에서는 `itdaing-chatbot-original*` Dataset 들을 선택해 평가를 실행하면 됩니다.  
> Dataset 이름/태그는 팀 규칙에 따라 조정하세요.

---

### 4. LLM-assisted 라벨링 워크플로우

라벨링은 **LLM 제안 + 사람 검수** 방식으로 진행합니다.

#### 4.1 LLM으로 1차 라벨 제안 생성

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

python langsmith-test/label_dataset.py --temperature 0.0
```

- 입력: `langsmith-test/original/test_prompts.json`
- 출력: `langsmith-test/input/test_prompts_labeled.json`
  - 각 항목:
    - 원본 필드: `id`, `section`, `raw`, `input`
    - LLM 제안 라벨: `labels = {mode, case_type, turn_type, transport, difficulty, expected_behavior, constraints{table,policy,where}}`

#### 4.2 사람 검수 및 기준 데이터셋 정제

1. `test_prompt.md` 의 C-1/S-1/PI-1 등 케이스 요약을 참고해,  
   `test_prompts_labeled.json` 의 `labels` 값이 의도와 맞는지 검토합니다.
2. 잘못된 라벨은 수동으로 수정하거나, 필요 시 LLM 프롬프트를 조정 후 재생성합니다.
3. 최종적으로 확정된 값을 바탕으로, **공식 기준 데이터셋**인 `input/test_prompts.json` 을 만듭니다.
   - 이 파일은 추가적인 오프라인 분석·실험 스크립트에서 사용할 수 있는 **기준 데이터셋**입니다.

> LangSmith Dataset example 의 `metadata` 에도 이 라벨을 sync 하고 싶다면,  
> 별도 스크립트에서 LangSmith `Client.read_dataset` / `Client.list_examples` / `Client.update_example` 을 호출해  
> example 메타데이터를 업데이트하는 패턴으로 확장할 수 있습니다.

---

### 5. LangGraph 직접 호출 Target function (`target_function.py`)

LangSmith 공식 문서([Evaluation quickstart SDK](https://docs.langchain.com/langsmith/evaluation-quickstart#sdk),  
[챗봇 평가 튜토리얼](https://docs.langchain.com/langsmith/evaluate-chatbot-tutorial)) 에 맞춰,  
이 프로젝트에서는 **LangGraph를 직접 호출하는 Target function** 을 사용합니다.

#### 5.1 함수 시그니처

`langsmith-test/target_function.py`:

```python
def run_itdaing_chatbot(
    inputs: dict,
    config: dict | None = None,
) -> dict:
    ...
```

- `inputs` 예시:
  - `{"message": "...", "mode": "consumer"|"seller", "user_id": "...", "session_id": "...", "transport": "async"}`  
  - `mode`/`user_id`/`session_id`/`transport` 가 없으면 각각 `consumer` / `eval-user` / `eval-session` / `async` 로 처리.
- `config`:
  - LangSmith에서 `run_on_dataset` 호출 시 넘길 수 있는 메타데이터 딕셔너리.
  - 예: `{"experiment_id": "baseline_v0", "graph_version": "v1", ...}` → LangGraph `config.metadata` 로 그대로 전달.

#### 5.2 내부 동작 요약

1. `.env` 로드 (`chatbot.env`) + `get_settings()` 호출.
2. LangSmith 평가 경로에서는 `MemorySaver` 기반 LangGraph 체크포인터를 사용합니다.  
   - 실제 운영 환경에서는 Postgres 기반 체크포인터를 사용하지만,  
     대량 평가 시 커넥션 종료 문제를 피하기 위해 **평가 전용으로 메모리 체크포인터**를 사용합니다.
3. `build_consumer_graph_async` / `build_seller_graph_async` 로 async 그래프 빌드 (프로세스 당 1회).
4. `thread_id = "{mode}:{user_id}:{session_id}"` 로 설정.
5. LangGraph `ainvoke` 호출:
   - 초깃값 `state = {"messages": [{"role": "user", "content": message}]}`.
   - `config = {"configurable": {"thread_id": thread_id}, "metadata": {...}}`.
6. 결과 state 에서 마지막 `AIMessage` 의 `content` 를 추출해:
   - `{"answer": <string>, "thread_id": <thread_id>}` 형태로 반환.

---

### 6. LangSmith SDK로 평가 실행 (`run_langsmith_evals.py`)

이 스크립트는 LangSmith SDK 로 Dataset example 들을 순회하면서  
각 example 에 대해 Target function 을 호출하는 형태로 챗봇을 평가합니다.

```bash
cd /home/ubuntu/chatbot
. .venv/bin/activate

python langsmith-test/run_langsmith_evals.py \
  --dataset-name itdaing-chatbot-original \
  --experiment baseline_v0
```

- 인자:
  - `--dataset-name`: LangSmith Dataset 이름 (예: `itdaing-chatbot-original`).
  - `--experiment`: 논리적인 실험 id (예: `baseline_v0`, `guardrail_v1`).
  - `--run-name` (옵션): LangSmith 상에서 보일 run/evaluation 이름 (기본값은 `experiment` 와 동일).
  - `--use-custom-evaluator` (옵션): 추후 커스텀 LLM-as-judge evaluator를 붙일 때 사용.
- 내부 동작(개념):
  - `Client = langsmith.Client()` 초기화 후 `client.list_examples(dataset_name=...)` 로 example 들을 가져옵니다.
  - 각 example 의 `inputs` 를 그대로 `run_itdaing_chatbot` 에 넘겨 호출하고,  
    응답의 앞부분과 에러 여부를 콘솔에 출력하면서 `tqdm` progress bar 로 진행 상황을 확인합니다.
  - LangSmith UI의 Datasets & Experiments 화면에서는 trace/latency 를 중심으로 확인할 수 있습니다.

> Evaluator(LLM-as-judge)는 아직 붙이지 않았으며,  
> 우선은 **정성적인 trace/응답 품질 확인 + latency 관찰** 용도로 사용할 수 있습니다.  
> 이후 LangSmith criteria/custom evaluator 를 붙여 metric 까지 연동하는 것이 자연스러운 다음 단계입니다.

---

### 7. 추천 워크플로우 요약

1. **원본 정리 & 업로드**
   - `/original/test_prompts*.json` 구조를 변경하지 말고 그대로 유지.
   - `upload_dataset.py` 로 LangSmith Dataset(`itdaing-chatbot-original*`) 생성.
2. **LLM-assisted 라벨링**
   - `label_dataset.py` 실행 → `test_prompts_labeled.json` 생성.
   - `test_prompt.md` 기준으로 라벨 검수/수정 → 최종 기준 `input/test_prompts.json` 정리.
3. **LangSmith 평가**
   - `run_langsmith_evals.py` 로 Dataset+Target function 기반 평가 실행.
   - LangSmith UI에서 응답 품질/trace/latency를 함께 검토.

이 흐름을 반복하면서,  
프롬프트/LangGraph/RAG/guardrail/DB 스키마 변경이 **어떤 케이스 그룹의 어떤 metric** 을 개선/악화시키는지를  
계속 기록·분석하는 것이 이 디렉터리의 핵심 역할입니다.



