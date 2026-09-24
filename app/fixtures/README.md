# fixtures

외부 API가 실패하거나 키가 없을 때 쓰는 샘플 응답이다.
실제 응답 구조와 같은 형태를 유지하고, 개인정보나 키는 넣지 않는다.

- `airkorea_sample.json`: 에어코리아 측정소별 실시간 측정정보
- `kma_nowcast_sample.json`: 기상청 초단기실황(VEC·WSD·T1H·REH)
- `kma_forecast_sample.json`: 기상청 단기예보(12시간, VEC·WSD·TMP·REH·POP)

폴백으로 쓸 때는 고정된 시각을 실행 시각으로 평행 이동해 저장한다(`app/collector.py`의 `_rebase`). 그대로 저장하면 며칠 뒤 최근 24시간 조회가 비어 버린다.
