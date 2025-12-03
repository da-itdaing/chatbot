-- =============================================================================
-- 자동 임베딩 시스템 설정 (DB Trigger + Queue)
-- =============================================================================
-- 
-- 이 스크립트는 popup, zone_area 테이블 변경 시 자동으로 임베딩 큐에 작업을 등록합니다.
-- FastAPI 워커가 주기적으로 큐를 polling하여 임베딩을 처리합니다.
--
-- 사용법:
--   psql -h <host> -U <user> -d <database> -f setup_auto_embedding.sql
-- =============================================================================

-- 1. embedding_queue 테이블 생성
CREATE TABLE IF NOT EXISTS embedding_queue (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(20) NOT NULL,      -- 'popup' | 'zone_area'
    entity_id BIGINT NOT NULL,
    action VARCHAR(10) NOT NULL,            -- 'INSERT' | 'UPDATE' | 'DELETE'
    status VARCHAR(20) DEFAULT 'PENDING',   -- 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'
    retry_count INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP,
    CONSTRAINT unique_pending_task UNIQUE (entity_type, entity_id, action, status)
);

-- 인덱스 생성 (워커 polling 최적화)
CREATE INDEX IF NOT EXISTS idx_embedding_queue_status ON embedding_queue (status, created_at);
CREATE INDEX IF NOT EXISTS idx_embedding_queue_entity ON embedding_queue (entity_type, entity_id);

-- 2. popup 변경 감지 트리거 함수
CREATE OR REPLACE FUNCTION notify_popup_change()
RETURNS TRIGGER AS $$
BEGIN
    -- 중복 방지: 동일 entity에 대해 PENDING 상태가 있으면 무시
    INSERT INTO embedding_queue (entity_type, entity_id, action)
    VALUES ('popup', COALESCE(NEW.id, OLD.id), TG_OP)
    ON CONFLICT (entity_type, entity_id, action, status) 
    DO UPDATE SET created_at = NOW();
    
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- 3. zone_area 변경 감지 트리거 함수
CREATE OR REPLACE FUNCTION notify_zone_area_change()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO embedding_queue (entity_type, entity_id, action)
    VALUES ('zone_area', COALESCE(NEW.id, OLD.id), TG_OP)
    ON CONFLICT (entity_type, entity_id, action, status) 
    DO UPDATE SET created_at = NOW();
    
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- 4. 기존 트리거 삭제 (재실행 시 충돌 방지)
DROP TRIGGER IF EXISTS popup_embedding_trigger ON popup;
DROP TRIGGER IF EXISTS zone_area_embedding_trigger ON zone_area;

-- 5. 트리거 생성
CREATE TRIGGER popup_embedding_trigger
AFTER INSERT OR UPDATE OR DELETE ON popup
FOR EACH ROW EXECUTE FUNCTION notify_popup_change();

CREATE TRIGGER zone_area_embedding_trigger
AFTER INSERT OR UPDATE OR DELETE ON zone_area
FOR EACH ROW EXECUTE FUNCTION notify_zone_area_change();

-- 6. 큐 상태 조회 뷰
CREATE OR REPLACE VIEW embedding_queue_summary AS
SELECT 
    entity_type,
    status,
    COUNT(*) as count,
    MIN(created_at) as oldest,
    MAX(created_at) as newest
FROM embedding_queue
GROUP BY entity_type, status
ORDER BY entity_type, status;

-- 7. 오래된 완료 작업 정리 함수 (선택적 실행)
CREATE OR REPLACE FUNCTION cleanup_embedding_queue(days_to_keep INTEGER DEFAULT 7)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM embedding_queue 
    WHERE status = 'COMPLETED' 
      AND processed_at < NOW() - (days_to_keep || ' days')::INTERVAL;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- 완료 메시지
DO $$
BEGIN
    RAISE NOTICE '✅ 자동 임베딩 시스템 설정 완료!';
    RAISE NOTICE '   - embedding_queue 테이블 생성됨';
    RAISE NOTICE '   - popup 트리거 설정됨';
    RAISE NOTICE '   - zone_area 트리거 설정됨';
END $$;


