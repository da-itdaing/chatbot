#!/bin/bash
# Secrets Manager에서 환경변수 갱신
cd /home/ubuntu/chatbot
aws secretsmanager get-secret-value \
  --secret-id chatbot-env \
  --query SecretString \
  --output text > chatbot.env 2>/dev/null || echo "Secrets Manager에서 env 가져오기 실패, 기존 파일 사용"
chmod 600 chatbot.env
