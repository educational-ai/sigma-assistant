#!/bin/bash
LOG=/root/sigma_assistant/eval/_collect_small.log
until grep -q "COLLECT DONE" "$LOG" 2>/dev/null; do sleep 20; done
done_n=$(grep -c "done in" "$LOG")
msg="✅ Сбор моделей закончен: собрано $done_n маленьких моделей (+5 = ~17 в линейке). Запускаю adversarial-workflow грейдинга (Claude-судьи), потом пересоберу страницу и проверю в браузере. Скину чистый лидерборд."
curl -s -m 8 -d "DIRECT:$msg" localhost:9357 >/dev/null
