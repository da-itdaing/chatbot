# Database Schema Documentation

> Source of Truth for the PostgreSQL Database (`itdaing-db`) schema.  
> Generated from live database inspection on **2025-11-25**.

## 1. Overview

- **Database**: PostgreSQL 15 (AWS RDS `itdaing-db`)
- **Schema**: `public`
- **Primary Domains**: identity (`users`), popup catalog (`popup` + tagging), geo leasing (`zone_*`), messaging, analytics/recommendations, LangChain vector stores, LangGraph checkpoints.
- **Naming Convention**: `snake_case` tables/columns with bigint surrogate PKs via sequences (unless noted).

### 운영 규칙 (2025-11-25 업데이트)

- `GET /api/popups`는 기본적으로 `end_date`가 오늘 이전인 팝업을 제외하며, 과거 데이터가 필요한 경우 `includeEnded=true` 쿼리 파라미터로 명시적으로 요청해야 한다.
- `/api/uploads/images` 엔드포인트는 JPEG/PNG/GIF/WebP만 허용하고, **파일당 10MB / 요청당 최대 10개**까지 저장한다. 비로그인 사용자는 `userId=0` 경로로 저장된다.
- 위시리스트 API는 Spring Security 컨텍스트에서 `Long` 타입 사용자 ID를 직접 주입하여 SpEL 오류를 방지한다.

### Cross-cutting Automation & Conventions

- `update_updated_at_column()` trigger refreshes `updated_at` on: `users`, `popup`, `seller_profile`, `category`, `style`, `region`, `feature`, 모든 `user_pref_*`, `daily_*_recommendation`, `zone_area`, `zone_cell`, `zone_availability`.
- All timestamps use `timestamp(6) without time zone` (UTC). Client/UI code handles localization.
- Every FK is enforced with explicit `ON DELETE` semantics (mostly `CASCADE` for relation tables, `SET NULL` for logs & announcements).

---

## 2. Core Domain Tables

### `users`
| Column | Type | Nullable | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `id` | bigint | NO | `nextval('users_id_seq')` | PK |
| `login_id` | varchar(100) | NO | | Unique username |
| `password` | varchar(255) | NO | | BCrypt hash |
| `name` | varchar(100) | YES | | |
| `nickname` | varchar(100) | YES | | |
| `email` | varchar(255) | NO | | Unique email |
| `age_group` | integer | YES | | 10/20/30... |
| `mbti` | varchar(20) | YES | | Optional tag |
| `role` | varchar(20) | NO | | `CONSUMER/SELLER/ADMIN` |
| `profile_image_url` | varchar(500) | YES | | |
| `profile_image_key` | varchar(255) | YES | | |
| `status` | varchar(20) | NO | `'ACTIVE'` | Lifecycle flag |
| `created_at` | timestamp(6) | NO | `CURRENT_TIMESTAMP(6)` | |
| `updated_at` | timestamp(6) | NO | `CURRENT_TIMESTAMP(6)` | |

**Constraints & Indexes**

- `users_pkey`, `uq_users_email`, `uq_users_login`, `chk_users_role`.
- `idx_users_role` optimizes admin/seller filtering.
- Trigger `update_users_updated_at`.

### `popup`
| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | PK |
| `seller_id` | bigint | NO | | FK -> `users.id` |
| `zone_cell_id` | bigint | NO | | FK -> `zone_cell.id` |
| `name` | varchar(200) | NO | | Display name |
| `description` | text | YES | | |
| `start_date` | date | YES | | |
| `end_date` | date | YES | | |
| `operating_time` | varchar(50) | YES | | |
| `approval_status` | varchar(20) | NO | `'PENDING'` | `chk_popup_status` ensures `PENDING/APPROVED/REJECTED` |
| `rejection_reason` | varchar(500) | YES | | |
| `view_count` | bigint | NO | `0` | |
| `favorite_count` | bigint | NO | `0` | denormalized likes |
| `created_at` | timestamp(6) | NO | now | |
| `updated_at` | timestamp(6) | NO | now | |

**Indexes & Triggers**

- `idx_popup_seller`, `idx_popup_cell`, `idx_popup_status`, `idx_popup_period (start_date, end_date)`.
- Trigger `update_popup_updated_at`.

### `popup_image`
| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | bigint | NO | sequence | PK |
| `popup_id` | bigint | NO | | FK -> `popup.id` (`ON DELETE CASCADE`) |
| `image_url` | varchar(500) | NO | | |
| `is_thumbnail` | boolean | NO | `false` | marks hero image |
| `created_at` | timestamp(6) | NO | now | |
| `image_key` | varchar(255) | YES | | |

Index `idx_popup_img_thumb (popup_id, is_thumbnail)`.

### `wishlist`
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` bigint PK | NO | sequence |
| `user_id` bigint | NO | FK -> `users.id` (`CASCADE`) |
| `popup_id` bigint | NO | FK -> `popup.id` (`CASCADE`) |
| `created_at` timestamp(6) | NO | now |

Constraint `uk_wishlist (user_id, popup_id)`.

### `review`
| Column | Type | Nullable | Default |
|---|---|---|---|
| `id` bigint PK | NO | sequence |
| `consumer_id` bigint | NO | FK -> `users.id` |
| `popup_id` bigint | NO | FK -> `popup.id` |
| `rating` smallint | NO | | 1–5 |
| `content` varchar(150) | YES | | |
| `created_at` timestamp(6) | NO | now |

Constraints/indexes: `uk_review_once`, `idx_review_consumer`, `idx_review_popup`, `idx_review_sort`.

### `review_image`
Columns: `id`, `review_id` FK -> `review.id`, `image_url`, `image_key`, `created_at`. Index `idx_review_img_review`.

---

## 3. Seller & Auth Tables

### `seller_profile`
| Column | Type | Nullable | Default |
|---|---|---|---|
| `user_id` | bigint | NO | PK / FK -> `users.id` |
| `profile_image_url` | varchar(500) | YES | |
| `profile_image_key` | varchar(255) | YES | |
| `introduction` | varchar(500) | YES | |
| `activity_region` | varchar(100) | YES | |
| `category` | varchar(100) | YES | |
| `contact_phone` | varchar(50) | YES | |
| `sns_url` | varchar(200) | YES | |
| `created_at` / `updated_at` | timestamp(6) | NO | now |

Trigger `update_seller_profile_updated_at`.

### `refresh_tokens`
| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` bigint PK | NO | sequence | |
| `user_id` | bigint | NO | | FK |
| `token_hash` | varchar(128) | NO | | unique |
| `issued_at` | timestamp(6) | NO | | |
| `expires_at` | timestamp(6) | NO | | |
| `revoked` | boolean | NO | `false` | |
| `replaced_by` | varchar(128) | YES | | Chained token id |
| `device_id` | varchar(255) | YES | | |
| `user_agent` | varchar(512) | YES | | |
| `ip` | varchar(45) | YES | | |

Index `idx_rt_user_revoked (user_id, revoked)`.

### `announcement`
Columns: `id`, `author_id` FK -> `users.id`, `audience` (`ALL/SELLER/CONSUMER` via `chk_announcement_audience`), `popup_id` FK -> `popup.id` (`ON DELETE SET NULL`), `title`, `content`, `created_at`. Index `idx_announce_scope (audience, popup_id, created_at)`.

---

## 4. Master Data & Preference Tables

### `category`
Columns: `id`, `name`, `type`, `created_at`, `updated_at`. Constraint `uk_category_type_name`. Trigger `update_category_updated_at`.

### `style`, `region`, `feature`
Each table includes `id`, `name`, `created_at`, `updated_at`, unique constraint on `name`, and its own `update_*_updated_at` trigger.

### `popup_category`
| Column | Type | Notes |
|---|---|---|
| `id` bigint PK | |
| `popup_id` bigint | FK -> `popup.id` (`CASCADE`) |
| `category_id` bigint | FK -> `category.id` (`CASCADE`) |
| `category_role` varchar(20) | `POPUP` / `TARGET` enforced via `chk_popup_category_role` |

Constraint `uk_popup_category (popup_id, category_id, category_role)`. Indexes: `idx_popup_category_popup`, `idx_popup_category_category`.

### `popup_style` / `popup_feature`
Columns: `id`, `popup_id`, `style_id` or `feature_id`. Constraints `uk_popup_style`, `uk_popup_feature`. Indexes on both FK columns.

### `user_pref_category`, `user_pref_style`, `user_pref_region`, `user_pref_feature`
Columns: `id`, `user_id` FK -> `users.id`, FK to the corresponding master table, `created_at`, `updated_at`. Unique constraints (`uk_user_category`, `uk_user_style`, `uk_user_region`, `uk_user_feature`). Triggers `update_user_pref_*_updated_at`.

---

## 5. Geo & Zone Tables

### `zone_area`
| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` bigint PK | NO | sequence | |
| `region_id` | bigint | NO | | FK -> `region.id` |
| `name` | varchar(100) | NO | | |
| `geometry_data` | text | YES | | GeoJSON/WKT |
| `status` | varchar(20) | NO | | `AVAILABLE/UNAVAILABLE/HIDDEN` (`chk_zone_area_status`) |
| `max_capacity` | integer | YES | | Optional cap |
| `notice` | varchar(1000) | YES | | |
| `created_at` / `updated_at` | timestamp(6) | NO | now |

Index `idx_zone_area_region`; trigger `update_zone_area_updated_at`.

### `zone_cell`
| Column | Type | Notes |
|---|---|---|
| `id` bigint PK | |
| `zone_area_id` bigint | FK -> `zone_area.id` |
| `owner_id` bigint | FK -> `users.id` |
| `label` varchar(100) | Optional slot label |
| `detailed_address` varchar(255) | Optional |
| `lat` / `lng` | double precision | Coordinates |
| `status` | varchar(20) | `PENDING/APPROVED/REJECTED/HIDDEN` (`chk_zone_cell_status`) |
| `max_capacity` | integer | Optional |
| `notice` | varchar(1000) | Optional |
| `created_at` / `updated_at` | timestamp(6) | now |

Indexes: `idx_zone_cell_area`, `idx_zone_cell_owner`. Trigger `update_zone_cell_updated_at`.

### `zone_availability`
| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` bigint PK | NO | sequence | |
| `zone_cell_id` | bigint | NO | | FK -> `zone_cell.id` |
| `start_date` / `end_date` | date | NO | | `chk_zone_availability_dates` ensures `start_date <= end_date` |
| `daily_price` | numeric(14,2) | NO | | |
| `max_concurrent_slots` | integer | NO | `1` | Overbooking guardrail |
| `status` | varchar(20) | NO | `'ACTIVE'` | |
| `created_at` / `updated_at` | timestamp(6) | NO | now |

Index `idx_zone_avail_range (zone_cell_id, start_date, end_date)`; trigger `update_zone_availability_updated_at`.

### `approval_record`
Columns: `id`, `target_type` (`POPUP` enforced via `chk_approval_target`), `target_id`, `decision` (`APPROVE/REJECT` via `chk_approval_decision`), `reason`, `admin_id` FK -> `users.id`, `created_at`. Index `idx_approval_target`.

---

## 6. Message System

### `message_thread`
Columns: `id`, `seller_id` FK -> `users.id`, `admin_id` FK -> `users.id` (`ON DELETE SET NULL`), `subject`, `unread_for_seller`, `unread_for_admin`, `created_at`, `updated_at`.

Indexes: `idx_thread_seller`, `idx_thread_admin`.

### `message`
Columns: `id`, `thread_id` FK -> `message_thread.id` (`ON DELETE CASCADE`), `sender_id` FK -> `users.id`, `receiver_id` FK -> `users.id`, `title`, `content`, `sent_at`, `read_at`, `sender_deleted_at`, `receiver_deleted_at`.

Indexes: `idx_msg_thread (thread_id, sent_at DESC)`, `idx_msg_inbox (receiver_id, read_at)`.

### `message_attachment`
Columns: `id`, `message_id` FK -> `message.id` (`ON DELETE CASCADE`), `file_url`, `mime_type`, `file_key`, `original_name`, `size_bytes`.

---

## 7. Analytics, Recommendations & AI

### Engagement Logging

#### `event_log`
| Column | Type | Notes |
|---|---|---|
| `id` bigint PK | |
| `user_id` bigint | FK -> `users.id` (`SET NULL`) |
| `popup_id` bigint | FK -> `popup.id` (`SET NULL`) |
| `zone_cell_id` bigint | FK -> `zone_cell.id` (`SET NULL`) |
| `action_type` varchar(20) | `VIEW/FAVORITE/REVIEW/CLICK` (`chk_event_log_action`) |
| `created_at` timestamp(6) | now |
| `session_id` varchar(255) | dedup key |
| `source` varchar(100) | acquisition channel |

Indexes: `idx_evt_user_time`, `idx_evt_popup_time`, `idx_evt_zone_time`, `idx_event_log_session_id`, `idx_event_log_source`.

#### `event_log_category`
Columns: `id`, `user_id` FK -> `users.id` (`SET NULL`), `category_id` FK -> `category.id` (`CASCADE`), `action_type`, `created_at`. Index `idx_evt_cat_time (category_id, created_at)`.

### Metrics & Recommendations

- `metric_daily_category`: (`id`, `category_id`, `date`, `clicks` default 0). Unique `uk_mdc_cat_date`. FK `fk_mdc_category`.
- `metric_daily_popup`: (`id`, `popup_id`, `date`, `views`, `unique_users`, `favorites`, `reviews` default 0). Unique `uk_mdp_popup_date`. FK `fk_mdp_popup`.
- `daily_consumer_recommendation`: `id`, `consumer_id` FK -> `users.id` (`CASCADE`), `popup_id` FK -> `popup.id` (`CASCADE`), `recommendation_date`, `score numeric(6,3)` default 0, `model_version`, `reason_json`, `created_at`, `updated_at`. Unique constraint `uk_dcr_dedup`. Trigger `update_daily_consumer_recommendation_updated_at`.
- `daily_seller_recommendation`: `seller_id` FK -> `users.id`, `zone_area_id` FK -> `zone_area.id`, identical metadata. Unique `uk_dsr_dedup`. Trigger `update_daily_seller_recommendation_updated_at`.
- `user_reco_dismissal`: `id`, `consumer_id` FK -> `users.id` (`CASCADE`), `popup_id` FK -> `popup.id` (`CASCADE`), `date`, `dismissed_at`. Unique `uk_reco_dismiss (consumer_id, date, popup_id)`.

### RAG Assets & Guardrails

- `chatbot_prompt`: `id`, `prompt_id` (unique natural key), `title`, `source_context`, `category`, `created_at`, `updated_at`.
- `chatbot_prompt_embedding`: `id`, `prompt_id` FK -> `chatbot_prompt.id`, `chunk_index`, `chunk_text`, `embedding vector(1536)`, `metadata jsonb`, `created_at`. Indexes `idx_chatbot_prompt_embedding_prompt`, `idx_chatbot_prompt_embedding_vector` (IVFFlat cosine).
- `langchain_pg_collection`: `uuid` PK, `name` (unique), `cmetadata json`.
- `langchain_pg_embedding`: `id` (varchar PK), `collection_id` FK -> `langchain_pg_collection.uuid`, `embedding vector`, `document varchar`, `cmetadata jsonb`. GIN index `ix_cmetadata_gin` on metadata.
- `guardrail_policy`: `id` integer PK, `service_area` unique, `forbidden_keywords text[]`, `disallowed_topics text[]`, `updated_at`.

### LangGraph Checkpoint Tables
| Table | Purpose | Key Columns |
|---|---|---|
| `checkpoint_migrations` | Tracks LangGraph migration version | `v` |
| `checkpoints` | Latest serialized state per thread | PK (`thread_id`,`checkpoint_ns`,`checkpoint_id`), columns `parent_checkpoint_id`, `type`, `checkpoint jsonb`, `metadata jsonb`. Index `checkpoints_thread_id_idx`. |
| `checkpoint_blobs` | Stores large payload blobs per channel | PK (`thread_id`,`checkpoint_ns`,`channel`,`version`), columns `version`, `type`, `blob`. Index `checkpoint_blobs_thread_id_idx`. |
| `checkpoint_writes` | Pending task writes/events | PK (`thread_id`,`checkpoint_ns`,`checkpoint_id`,`task_id`,`idx`), columns `task_path`, `channel`, `type`, `blob`. Index `checkpoint_writes_thread_id_idx`. |

`thread_id` follows `consumer:{user_id}:{session}` / `seller:{user_id}:{session}` (UUID suffix for recovery). `checkpoint_ns` defaults to `''`.

---

This document mirrors the live schema exported on **2025-11-25**.
