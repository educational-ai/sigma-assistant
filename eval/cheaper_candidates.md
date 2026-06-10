# Cheaper-than-prod candidates (tool+vision, not yet benchmarked)

Prod = google/gemini-3.5-flash @ $1.50/$9.00 per M (blended ~$2.25/M, $0.033/q in bench).
Already proven: openai/gpt-5-nano = 28/29 @ $0.0025/q — 13× cheaper AND better than prod.

| model | vis | $in/M | $out/M |
|---|---|---|---|
| google/gemma-4-26b-a4b-it | 👁 | 0.060 | 0.330 |
| openai/gpt-4.1-nano | 👁 | 0.100 | 0.400 |
| openai/gpt-5.4-nano | 👁 | 0.200 | 1.250 |
| openai/gpt-5-mini | 👁 | 0.250 | 2.000 |
| google/gemini-2.5-flash | 👁 | 0.300 | 2.500 |
| google/gemini-3-flash-preview | 👁 | 0.500 | 3.000 |
| google/gemini-2.5-flash-lite-preview-09-2025 | 👁 | 0.100 | 0.400 |
| qwen/qwen3.5-flash-02-23 | 👁 | 0.065 | 0.260 |
| qwen/qwen3-vl-32b-instruct | 👁 | 0.104 | 0.416 |
| qwen/qwen3-vl-30b-a3b-instruct | 👁 | 0.130 | 0.520 |
| bytedance-seed/seed-2.0-mini | 👁 | 0.100 | 0.400 |
| mistralai/ministral-3b-2512 | 👁 | 0.100 | 0.100 |
| mistralai/mistral-medium-3.1 | 👁 | 0.400 | 2.000 |
| amazon/nova-2-lite-v1 | 👁 | 0.300 | 2.500 |
| z-ai/glm-4.6v | 👁 | 0.300 | 0.900 |
| minimax/minimax-m3 | 👁 | 0.300 | 1.200 |