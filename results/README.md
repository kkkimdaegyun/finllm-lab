# Result records

이 디렉터리에는 실제로 측정한 정식 결과만 저장한다.

- 가상 숫자와 설명용 예시는 저장하지 않는다.
- 파일명은 `<date>-<profile>-<model>-<quant>-r<repeat>.json` 형식을 권장한다.
- model과 tokenizer는 immutable revision을 기록한다.
- A6000 제한 실험은 `memory-budget-emulation`으로 표시한다.
- 실제 4090/5090급 장비 실측만 `native-gpu-validation`으로 표시한다.
- 2×A6000은 `quality-reference`이며 배포 프로파일 비교표의 후보로 승격하지 않는다.
- 원시 부하 시험 출력은 `work/`에 두고, 검토가 끝난 요약만 이곳에 저장한다.

커밋 전 검증:

```bash
python3 scripts/finllm_profile.py validate-result results/FILE.json
```

