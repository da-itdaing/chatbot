# Database Schema Documentation

> Source of Truth for the PostgreSQL Database (`itdaing-db`) schema.
> Generated from active database inspection on 2025-11-24.

---

## 1. Overview

- **Database**: PostgreSQL 15
- **Schema**: `public`
- **Key Tables**: `users`, `popup`, `wishlist`, `review`, `zone_cell`
- **Naming Convention**: `snake_case` for tables and columns

### 운영 규칙 (2025-11-24 업데이트)

- `GET /api/popups`는 기본적으로 `end_date`가 오늘 이전인 팝업을 제외하며, 과거 데이터가 필요한 경우 `includeEnded=true` 쿼리 파라미터로 명시적으로 요청해야 한다.
- `/api/uploads/images` 엔드포인트는 JPEG/PNG/GIF/WebP만 허용하고, **파일당 10MB / 요청당 최대 10개**까지 저장한다. 비로그인 사용자는 `userId=0` 경로로 저장된다.
- 위시리스트 API는 Spring Security 컨텍스트에서 `Long` 타입 사용자 ID를 직접 주입하여 SpEL 오류를 방지한다.

---

## 2. Core Domain Tables

### `users` (Users)
Stores all user accounts (CONSUMER, SELLER, ADMIN).

| Column | Type | Nullable | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK | Auto-increment |
| `login_id` | `varchar(100)` | NO | | Unique login ID |
| `password` | `varchar(255)` | NO | | BCrypt hash |
| `name` | `varchar(100)` | YES | | Real name |
| `nickname` | `varchar(100)` | YES | | Display name |
| `email` | `varchar(255)` | NO | | Unique email |
| `role` | `varchar(20)` | NO | | `CONSUMER`, `SELLER`, `ADMIN` |
| `age_group` | `integer` | YES | | 10, 20, 30, 40, 50, 60... |
| `mbti` | `varchar(20)` | YES | | Optional |
| `profile_image_url` | `varchar(500)` | YES | | S3 URL |
| `profile_image_key` | `varchar(255)` | YES | | S3 Key |
| `status` | `varchar(20)` | NO | `'ACTIVE'` | User status |
| `created_at` | `timestamp` | NO | `NOW()` | |
| `updated_at` | `timestamp` | NO | `NOW()` | |

**Constraints:**
- `uq_users_email`: Unique email
- `uq_users_login`: Unique login_id
- `chk_users_role`: Role must be one of 'CONSUMER', 'SELLER', 'ADMIN'

### `popup` (Popup Stores)
Stores popup store information.

| Column | Type | Nullable | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK | Auto-increment |
| `seller_id` | `bigint` | NO | | FK -> `users.id` |
| `zone_cell_id` | `bigint` | NO | | FK -> `zone_cell.id` |
| `name` | `varchar(200)` | NO | | Popup title |
| `description` | `text` | YES | | Detailed description |
| `start_date` | `date` | YES | | Operation start date |
| `end_date` | `date` | YES | | Operation end date |
| `operating_time` | `varchar(50)` | YES | | e.g. "10:00-22:00" |
| `approval_status` | `varchar(20)` | NO | `'PENDING'` | `PENDING`, `APPROVED`, `REJECTED` |
| `rejection_reason` | `varchar(500)` | YES | | |
| `view_count` | `bigint` | NO | `0` | |
| `favorite_count` | `bigint` | NO | `0` | Denormalized count |
| `created_at` | `timestamp` | NO | `NOW()` | |
| `updated_at` | `timestamp` | NO | `NOW()` | |

**Indexes:**
- `idx_popup_seller`, `idx_popup_cell`, `idx_popup_status`, `idx_popup_period`

### `wishlist` (Favorites)
Many-to-Many relationship between Users and Popups.

| Column | Type | Nullable | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK | Auto-increment |
| `user_id` | `bigint` | NO | | FK -> `users.id` (Cascade) |
| `popup_id` | `bigint` | NO | | FK -> `popup.id` (Cascade) |
| `created_at` | `timestamp` | NO | `NOW()` | |

**Constraints:**
- `uk_wishlist`: Unique (`user_id`, `popup_id`) - prevents duplicate likes

### `review` (Reviews)
Consumer reviews for popups.

| Column | Type | Nullable | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK | Auto-increment |
| `consumer_id` | `bigint` | NO | | FK -> `users.id` (Cascade) |
| `popup_id` | `bigint` | NO | | FK -> `popup.id` (Cascade) |
| `rating` | `smallint` | NO | | 1-5 stars |
| `content` | `varchar(150)` | YES | | Review text |
| `created_at` | `timestamp` | NO | `NOW()` | |

**Constraints:**
- `uk_review_once`: Unique (`consumer_id`, `popup_id`) - 1 review per popup per user

### `review_image`
Images attached to reviews.

| Column | Type | Nullable | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK | |
| `review_id` | `bigint` | NO | | FK -> `review.id` (Cascade) |
| `image_url` | `varchar(500)` | NO | | |
| `image_key` | `varchar(255)` | YES | | |
| `created_at` | `timestamp` | NO | `NOW()` | |

---

## 3. Seller & Auth Tables

### `seller_profile`
Additional profile info for sellers.

| Column | Type | Nullable | Notes |
| :--- | :--- | :--- | :--- |
| `user_id` | `bigint` | NO | PK, FK -> `users.id` (Cascade) |
| `profile_image_url` | `varchar(500)` | YES | |
| `introduction` | `varchar(500)` | YES | |
| `activity_region` | `varchar(100)` | YES | |
| `category` | `varchar(100)` | YES | |
| `contact_phone` | `varchar(50)` | YES | |
| `sns_url` | `varchar(200)` | YES | |

### `refresh_tokens`
JWT refresh tokens.

| Column | Type | Nullable | Notes |
| :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK |
| `user_id` | `bigint` | NO | FK -> `users.id` |
| `token_hash` | `varchar(128)` | NO | Unique hash |
| `issued_at` | `timestamp` | NO | |
| `expires_at` | `timestamp` | NO | |
| `revoked` | `boolean` | NO | Default `false` |

### `announcement`
System announcements.

| Column | Type | Nullable | Notes |
| :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK |
| `author_id` | `bigint` | NO | FK -> `users.id` |
| `audience` | `varchar(20)` | NO | `ALL`, `SELLER`, `CONSUMER` |
| `popup_id` | `bigint` | YES | Optional FK -> `popup.id` |
| `title` | `varchar(200)` | NO | |
| `content` | `text` | YES | |

---

## 4. Master Data & Relations

### `category`
| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | `bigint` | PK |
| `name` | `varchar(100)` | |
| `type` | `varchar(20)` | `POPUP` or `CONSUMER` |

### `style`
| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | `bigint` | PK |
| `name` | `varchar(100)` | |

### `region`
| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | `bigint` | PK |
| `name` | `varchar(100)` | |

### `feature`
| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | `bigint` | PK |
| `name` | `varchar(100)` | |

### `popup_*` Relation Tables
- `popup_category`: Links popup to category (`category_role`: `POPUP` or `TARGET`)
- `popup_style`: Links popup to style
- `popup_feature`: Links popup to feature
- `popup_image`: Stores popup images (`is_thumbnail` boolean)

### `user_pref_*` Relation Tables
- `user_pref_category`
- `user_pref_style`
- `user_pref_region`
- `user_pref_feature`

---

## 5. Geo & Zone Tables

### `zone_area`
Larger areas (e.g., parks, districts).

| Column | Type | Nullable | Notes |
| :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK |
| `region_id` | `bigint` | NO | FK -> `region.id` |
| `name` | `varchar(100)` | NO | |
| `geometry_data` | `text` | YES | GeoJSON/WKT |
| `status` | `varchar(20)` | NO | `AVAILABLE`, `UNAVAILABLE`, `HIDDEN` |

### `zone_cell`
Specific slots within an area.

| Column | Type | Nullable | Notes |
| :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK |
| `zone_area_id` | `bigint` | NO | FK -> `zone_area.id` |
| `owner_id` | `bigint` | NO | FK -> `users.id` |
| `label` | `varchar(100)` | YES | e.g. "A-1" |
| `lat`, `lng` | `double` | NO | |
| `status` | `varchar(20)` | NO | `PENDING`, `APPROVED`... |

### `zone_availability`
Availability and pricing for cells.

| Column | Type | Nullable | Notes |
| :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK |
| `zone_cell_id` | `bigint` | NO | FK -> `zone_cell.id` |
| `start_date` | `date` | NO | |
| `end_date` | `date` | NO | |
| `daily_price` | `numeric` | NO | |

### `approval_record`
Admin approval history.

| Column | Type | Nullable | Notes |
| :--- | :--- | :--- | :--- |
| `id` | `bigint` | NO | PK |
| `target_type` | `varchar(20)` | NO | `POPUP` |
| `target_id` | `bigint` | NO | |
| `decision` | `varchar(20)` | NO | `APPROVE`, `REJECT` |
| `reason` | `varchar(1000)` | YES | |
| `admin_id` | `bigint` | NO | FK -> `users.id` |

---

## 6. Message System

### `message_thread`
| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | `bigint` | PK |
| `seller_id` | `bigint` | FK -> `users` |
| `admin_id` | `bigint` | FK -> `users` (Nullable) |
| `subject` | `varchar` | |

### `message`
| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | `bigint` | PK |
| `thread_id` | `bigint` | FK -> `message_thread` |
| `sender_id` | `bigint` | FK -> `users` |
| `receiver_id` | `bigint` | FK -> `users` |
| `content` | `text` | |

### `message_attachment`
| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | `bigint` | PK |
| `message_id` | `bigint` | FK -> `message` |
| `file_url` | `varchar` | |

---

## 7. AI & Logs

### `chatbot_prompt` & `chatbot_prompt_embedding`
Stores prompts and their vector embeddings for RAG.

### `event_log` & `event_log_category`
User activity logs (`VIEW`, `CLICK`, `FAVORITE`, `REVIEW`) for analytics.

### `metric_daily_*`
Aggregated daily metrics for categories (`metric_daily_category`) and popups (`metric_daily_popup`).

### `daily_*_recommendation`
Pre-calculated recommendations for consumers and sellers.

### `user_reco_dismissal`
Tracks recommendations dismissed by users.

### `langchain_pg_*`
LangChain vector store tables.

### `guardrail_policy`
Safety policies for AI generation.
