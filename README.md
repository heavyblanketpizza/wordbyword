# wordbyword

[한국어](README.md) | [English](README.en.md)

`wordbyword`는 원본 문서에서 한국어 학습자용 어휘 자료를 만들고, 수업 원본 자료를 학년 수준에 맞는 한국어로 다시 쓰며, 편집 가능한 DOCX 학습지를 생성하고, 수업에서 사용한 자료의 어휘 신호를 추적합니다.

루트의 `README.md`가 기술 안내의 기준 문서입니다. `BEGINNERS_GUIDE.md`는 버전 관리되는 쉬운 설명서입니다. 생성된 출력 폴더에는 프로젝트 문서를 두지 않습니다.

## 사전 요구 사항

- 의존성 설치 및 명령 실행을 위한 `uv`.
- `pyproject.toml`에 선언된 Python `>=3.14`. 가능하면 `uv`가 인터프리터를 설치하도록 하세요.
- 모델 기반 워크플로를 위한 LLM 제공자. 기본값은 `gemma4:31b`를 사용하는 로컬 Ollama이며, 텍스트 전용 LLM 작업에는 OpenAI, Anthropic/Claude, Google/Gemini도 지원됩니다.
- 수업 원본 PDF 파싱을 위한 PaddleOCR-VL. 프로젝트 의존성 세트는 기본 환경에 PaddleOCR-VL을 설치하지 않으므로, 사이드카 환경이나 `paddleocr`을 import할 수 있는 최신 Python 환경을 사용하세요.
- DOCX 출력을 위한 한국어 지원 글꼴. 렌더러는 macOS, Linux 및 Noto/Nanum의 일반적인 글꼴을 자동으로 감지합니다. 필요한 경우에만 글꼴 경로를 직접 지정하세요.

## macOS 네이티브 스튜디오

프로젝트 폴더에서 흑백 SwiftUI 네이티브 인터페이스를 엽니다.

```bash
uv run wordbyword-gui
```

스튜디오는 **macOS 14 이상**이 필요합니다. 최초 빌드에는 Xcode 명령줄 도구의 **Swift 6 이상**도 필요합니다. 도구가 없다면 `xcode-select --install`로 설치하세요. 런처는 선택한 워크스페이스 안의 `.wordbyword/desktop/WordByWord.app`에 앱을 빌드하고 캐시하며, 네이티브 소스가 변경되면 다시 빌드합니다. CLI는 이러한 macOS 요구 사항 없이도 사용할 수 있습니다.

| 탭 | 워크플로 |
| --- | --- |
| Dictionary | 원본 문서를 가져와 이름을 지정하고, 사전을 빌드하거나 이어서 실행하며, 단계별 산출물을 확인합니다. |
| Classroom | 원본 PDF를 파싱하고, 읽기 자료를 다시 쓰며, 편집 가능한 DOCX 학습지를 만듭니다. |
| Vocabulary | 보관된 수업 자료를 관리하고, 사용 신호를 빌드하며, 현재 어휘집을 업데이트합니다. |
| Reports | 선택한 보관 학습지 한 개로 수업 후 읽기 보고서를 생성합니다. |

데스크톱 앱은 **2학년 전용**입니다. **Studio settings**에서 제공자와 모델을 설정하세요. 파일명 미리 보기, 중복 검토, 개별 워크플로 단계는 **More actions**를 사용하세요. 각 실행 전에는 검토 시트가 열리며, **Run journal**에서 진행 상황과 오류를 확인할 수 있습니다. 모델 및 OCR 사전 요구 사항은 그대로 적용됩니다.

Dictionary 탭의 검은색 **사전 만들기 00–15** 버튼은 2학년 원본 자료로 전체 단계를 실행한 뒤 학습 신호와 등장 횟수 분포 차트를 자동으로 엽니다. 데스크톱 앱에서는 `2-1_국어[나].pdf`와 같은 익숙한 원본 파일명을 바꾸지 않아도 됩니다. **시각화 보기**를 사용하면 모델을 다시 호출하지 않고 기존의 가장 최근 사전 결과를 확인할 수 있습니다.

**인터페이스 언어:** **Studio settings → Language / 언어**를 열고 **한국어**를 선택한 다음 **Save defaults / 설정 저장**을 누르세요. 설정 시트에서 선택한 언어가 즉시 미리 보기에 반영되며, **Cancel / 취소**를 누르면 변경 사항이 취소됩니다. 선택 사항은 워크스페이스별 `.wordbyword/studio-settings.json`에 저장됩니다. 언어 선택 항목이 없는 기존 설정은 기본적으로 영어를 사용합니다. 파일명, 문서 내용, CLI 명령 및 원시 엔진 로그는 원래 텍스트를 유지합니다.

기존 레거시 폴더 구조의 파일은 **읽기 전용**으로 표시됩니다. 스튜디오를 여는 것만으로 원본 데이터가 마이그레이션되지는 않습니다. 자료를 가져오거나 워크플로를 실행하기 전에 **Review layout**을 선택하고, 제안된 이동 내용을 검토한 다음, 마이그레이션을 명시적으로 승인하세요. 충돌이 있거나 불완전한 마이그레이션 계획은 스튜디오에서 적용할 수 없습니다.

제공자 API 키는 실행 환경에만 둡니다: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` 또는 `GEMINI_API_KEY`. 실행 전에 export하세요. API 키는 스튜디오 설정에 저장되지 않습니다. 호스팅 모델 작업은 원본 콘텐츠를 선택한 제공자에게 전송하며 비용이 발생할 수 있습니다. 비밀 정보가 아닌 설정은 사용자가 저장을 요청할 때만 `.wordbyword/studio-settings.json`에 저장됩니다.

**Stop run**은 중단을 요청한 뒤 워커가 종료되고 정리 작업을 마칠 때까지 기다립니다. 실행 결과를 롤백하지 않으므로 완료되었거나 일부만 생성된 산출물이 남을 수 있습니다. 활성 세션을 닫을 때 사용하는 **Stop and Quit**도 같은 방식으로 기다립니다.

기존의 다른 데이터 워크스페이스를 선택하려면 다음을 실행하세요.

```bash
uv run wordbyword-gui --workspace "/absolute/path/to/workspace"
```

창의 폴더 선택기에서도 워크스페이스를 변경할 수 있습니다. 앱을 열지 않고 준비만 하려면 `--build-only`를, 네이티브 코드를 강제로 다시 컴파일하려면 `--rebuild`를 사용하세요. 두 옵션 모두 사전 또는 수업 자료를 강제로 다시 빌드하지는 않습니다.

**학년/레이아웃 참고:** GUI는 항상 `--target-grade grade2`를 전달하며, 기존의 범위 미지정 사전 원본과 결과도 2학년 폴더로 정리합니다. GUI의 현재 사전 원본과 출력은 `build-dictionary/original/grade2/` 및 `build-dictionary/output/grade2/`입니다. CLI는 계속 `grade1`~`grade12`를 지원하므로 아래 예제에는 필요한 `--target-grade`를 명시하세요.

## 빠른 시작

프로젝트를 설치하고 검증합니다.

```bash
uv sync --dev
uv run wordbyword --help
uv run pytest -q
uv run ruff check .
```

명령 예제는 설치된 콘솔 스크립트인 `uv run wordbyword`를 사용합니다. 소스에서 `uv run python -m wordbyword`로 실행해도 동일합니다.

`build-dictionary/original/`에 파일을 추가한 뒤 사전 원본 파일명을 관리합니다.

```bash
uv run wordbyword --rename-dictionary-source-archive --ollama-model gemma4:31b
uv run wordbyword --rename-dictionary-source-archive --apply-dictionary-source-renames --ollama-model gemma4:31b
```

관리되는 원본 보관소에서 전체 사전을 빌드합니다.

```bash
uv run wordbyword --build-dictionary --ollama-model gemma4:31b
```

이 명령은 마지막으로 검증된 체크포인트부터 이어서 실행하고, 변경이 필요한 단계만 `15`까지 처리합니다. `13`단계 이후에도 생성 예문 오류가 남아 있으면 중지하며, 그다음 `14_vocabulary_with_meanings.jsonl`과 `15_final_dictionary.jsonl`을 작성합니다.

초기 산출물을 디버깅할 때 결정론적 기본 단계만 빌드하려면 다음을 실행하세요.

```bash
uv run wordbyword --prep-dictionary
```

원본 자료에서 수업용 인쇄물을 만듭니다.

```bash
uv run wordbyword --build-class-printouts --ollama-model gemma4:31b
```

이 전체 수업 인쇄물 명령은 `classroom-printout-studio/class-source-materials/grade1`에서 시작합니다. 파싱 전에 원본 파일의 해시를 계산하고, 수업 인쇄물 원장에 성공적으로 출력 가능한 DOCX와 연결된 원본은 건너뜁니다. 새로운 PDF는 PaddleOCR-VL로 파싱하고, 기준 현재 어휘집이 있으면 이를 사용해 다시 쓴 뒤, 편집 가능한 DOCX 학습지를 `classroom-printout-studio/printable-for-class/grade1-ko-a4-ollama/` 아래에 작성합니다.

## 로컬 폴더

버전 관리되는 자리표시자:

- `build-dictionary/original/.gitkeep`
- `build-dictionary/references/.gitkeep`

Git에서 무시되는 로컬 작업 폴더:

- `build-dictionary/`: 생성된 사전 산출물과 로컬에서 관리되는 원본/참조 파일.
- `classroom-printout-studio/`: 로컬 수업 원본 자료, 파싱 번들, 다시 쓴 번들 및 인쇄물.
- `wbw-vocabulary-intelligence/`: 로컬 수업 보관 자료, 사용 신호, 현재 어휘집 및 수업 후 보고서.

사전 원본 파일을 다음 위치에 넣으세요.

```text
build-dictionary/original/
```

그런 다음 사전을 빌드하기 전에 사전 원본 보관소 이름 변경의 시험 실행과 적용 명령을 실행하세요. 보관소 원장은 원본 파일과 마찬가지로 로컬에만 존재하며 Git에서 무시됩니다.

참조 뜻 사전은 다음 위치에 넣으세요.

```text
build-dictionary/references/reference-dictionary.jsonl
```

수업 원본 PDF는 다음 위치에 넣으세요.

```text
classroom-printout-studio/class-source-materials/grade1/
```

생성된 단계별 출력은 다시 만들 수 있습니다. 전체 사전 빌드는 검증된 체크포인트 캐시 메타데이터를 `00_summary.json`에 기록하며, 스키마, 입력, 옵션, 의존성 및 산출물 해시가 모두 일치할 때만 저장된 단계를 재사용합니다. 원본별 파싱, 다시 쓰기, 인쇄 및 수업 후 보고서 출력은 각각의 출력 폴더에서 다시 생성됩니다.

## 사전 워크플로

일반적인 전체 워크플로는 `build-dictionary/original/`에서 관리되는 `.pdf`, `.txt`, `.md` 파일을 읽고 `00`부터 `15`까지의 단계를 작성합니다.

```bash
uv run wordbyword --rename-dictionary-source-archive --ollama-model gemma4:31b
uv run wordbyword --rename-dictionary-source-archive --apply-dictionary-source-renames --ollama-model gemma4:31b
```

`--apply-dictionary-source-renames`를 지정하지 않으면 이름 변경 명령은 시험 실행만 수행합니다. 이 명령은 원본 SHA-256을 키로 사용하는 `build-dictionary/original/.wordbyword-dictionary-source-ledger.json`을 작성합니다. 빌드는 사전 산출물을 작성하기 전에 관리되지 않은 파일, 이름이 바뀐 파일, 변경된 파일 또는 중복 원본 파일을 거부합니다.

원본 보관소에 완전히 동일한 파일이나 형식은 다르지만 텍스트가 완전히 같은 중복 파일이 있다면, 먼저 중복 파일을 격리하세요.

```bash
uv run wordbyword --resolve-dictionary-source-duplicates
uv run wordbyword --resolve-dictionary-source-duplicates --yes
```

중복 처리기는 파일을 삭제하지 않습니다. 중복 파일을 `build-dictionary/original/.wordbyword-dictionary-source-duplicates/`로 옮기고 감사 매니페스트를 작성합니다.

원본 파일을 관리 상태로 만든 뒤 빌드합니다.

```bash
uv run wordbyword --build-dictionary --ollama-model gemma4:31b
```

전체 워크플로는 기본적으로 이어서 실행할 수 있습니다. 각 체크포인트에 `SKIP`, `RUN` 또는 `REBUILD`를 출력하고 캐시 메타데이터를 `00_summary.json["build_cache"]` 아래에 저장합니다. 유효하게 저장된 작업을 의도적으로 무시하려면 `--force`를 사용하세요.

```bash
uv run wordbyword --build-dictionary --force --ollama-model gemma4:31b
```

전체 워크플로는 엄격하게 동작합니다. `13_generated_example_failures.jsonl`에 해결되지 않은 행이 남아 있으면 명령은 0이 아닌 종료 코드를 반환하고 뜻을 지정하기 전에 중지합니다. 복구 명령으로 `13`단계를 수정한 다음, `--add-reference-meanings`와 `--generate-missing-meanings`를 실행하여 수정된 저장 산출물부터 마무리하세요.

준비 명령은 결정론적인 `00`~`09`단계를 작성하며 의도적으로 Ollama를 호출하지 않습니다.

```bash
uv run wordbyword --prep-dictionary
```

유용한 준비 옵션:

```bash
uv run wordbyword --prep-dictionary --input build-dictionary/original --output build-dictionary
uv run wordbyword --prep-dictionary --include-numbers
uv run wordbyword --prep-dictionary --include-punctuation
```

복구 및 디버깅 명령은 저장된 산출물을 재사용합니다.

```bash
uv run wordbyword --clean-example-sentences --ollama-model gemma4:31b
uv run wordbyword --clean-dictionary-candidates --ollama-model gemma4:31b
uv run wordbyword --link-cleaned-examples
uv run wordbyword --generate-missing-examples --ollama-model gemma4:31b
uv run wordbyword --retry-generated-examples --ollama-model gemma4:31b
uv run wordbyword --add-reference-meanings
uv run wordbyword --generate-missing-meanings --ollama-model gemma4:31b
```

가장 최근의 사전 형식 JSONL을 확인합니다.

```bash
uv run wordbyword --show-distribution
uv run wordbyword --show-distribution build-dictionary/15_final_dictionary.jsonl
```

옵션이 없는 명령은 더 이상 아무것도 빌드하지 않습니다. 결정론적 `00`~`09`단계에는 `--prep-dictionary`를 사용하고, 검증된 저장 산출물에서 엄격한 전체 워크플로를 이어서 실행하려면 `--build-dictionary`를 사용하세요.

## 사전 산출물

| 단계 | 파일 | 용도 |
| --- | --- | --- |
| 00 | `00_summary.json` | 실행 메타데이터, 개수, 출력 목록 및 파이프라인 단계 맵. |
| 01 | `01_extracted_pages.csv` | 각 PDF 또는 텍스트 원본에서 추출하고 정리한 페이지별 텍스트. |
| 02 | `02_sentences.csv` | 레이아웃 정리 및 노이즈 필터링 후의 문장 형태 청크. |
| 03 | `03_tokens.csv` | 형태, 품사 태그, 오프셋 및 원본 문장을 포함하는 Kiwi 토크나이저 출력. |
| 04 | `04_surface_words.csv` | 정리된 원본 텍스트에 나타난 그대로의 단어 표기. |
| 05 | `05_vocabulary_raw.csv` | 한국어 형태소 분석으로 집계한 원시 어휘 항목. |
| 06 | `06_candidate_terms.csv` | 정리, 그룹화 및 점수화된 단어 후보. |
| 07 | `07_rejected_terms.csv` | 결정론적 정리 규칙으로 제거된 어휘 항목. |
| 08 | `08_dictionary_candidates.jsonl` | 수동 또는 LLM 선별을 위한 어휘 후보 패킷. |
| 09 | `09_example_sentences.csv` | 어휘 예시로 사용할 고품질 문장 목록. |
| 10 | `10_cleaned_example_sentences.csv` | Ollama가 유지, 수정, 재작성 또는 삭제한 원본 문장. |
| 11 | `11_cleaned_dictionary_candidates.jsonl` | Ollama로 필터링한 학습자용 사전 후보. |
| 12 | `12_dictionary_candidates_with_cleaned_examples.jsonl` | 가능한 경우 고유한 정제 예문과 연결된 사전 후보. |
| 13 | `13_vocabulary_with_generated_examples.jsonl` | 좋은 예문이 아직 없는 행에 대해 예문을 생성한 선택 어휘. |
| 13 보조 파일 | `13_generated_example_attempts.jsonl` | 예문 생성 패스의 시도 로그. |
| 13 보조 파일 | `13_generated_example_failures.jsonl` | 현재 해결되지 않은 예문 생성 오류. |
| 14 | `14_vocabulary_with_meanings.jsonl` | 키가 일치할 때 참조 사전의 뜻이 추가된 어휘. |
| 15 | `15_final_dictionary.jsonl` | 남은 빈칸에 Ollama가 작성한 한국어 뜻이 포함된 최종 사전. |

품질이 잘못되었다고 판단되면 번호가 붙은 파일을 순서대로 확인하세요. 처음 문제가 나타난 산출물로 수정이 필요한 단계를 대개 찾을 수 있습니다.

## 수업 인쇄물 워크플로

일반적인 수업 학습지 생성:

```bash
uv run wordbyword --build-class-printouts --ollama-model gemma4:31b
```

단계별 명령은 디버깅하거나 워크플로의 한 부분만 다시 실행할 때 사용합니다.

원본 자료만 파싱:

```bash
uv run wordbyword --parse-source-materials
uv run wordbyword --parse-source-material classroom-printout-studio/class-source-materials/grade1/example.pdf
```

기본 파싱 디렉터리는 `classroom-printout-studio/class-source-materials/grade1`입니다. PDF는 PaddleOCR-VL로 파싱합니다. PDF가 아닌 확장자의 원본 자료도 탐색될 수 있지만, 현재 완전한 파싱 번들은 PDF에서만 생성됩니다.

PaddleOCR-VL 사이드카 동작 방식:

- 명시적으로 지정한 `--paddleocr-vl-sidecar-python` 또는 `--paddleocr-vl-sidecar-env`가 가장 우선합니다.
- 그다음 `WORDBYWORD_PADDLEOCR_VL_SIDECAR_PYTHON` 또는 `WORDBYWORD_PADDLEOCR_VL_SIDECAR_ENV`를 사용합니다.
- 그다음 자동 탐색이 현재 작업 디렉터리와 프로젝트 루트 아래의 `.venv-paddleocr-vl` 및 `.venv-paddleocr`을 확인합니다.
- 사이드카를 찾지 못하면 현재 Python 환경에서 `paddleocr`을 import할 수 있어야 합니다.
- 사용자가 `PYTHONPATH`를 export할 필요는 없습니다. 사이드카 실행 시 저장소 루트가 자동으로 삽입됩니다.

유용한 파싱 옵션:

```bash
uv run wordbyword --parse-source-materials --paddleocr-vl-device cpu
uv run wordbyword --parse-source-materials --paddleocr-vl-sidecar-env .venv-paddleocr-vl --paddleocr-vl-device cpu
uv run wordbyword --parse-source-materials --skip-pdf-parse
uv run wordbyword --parse-source-materials --pdf-render-dpi 180
uv run wordbyword --parse-source-materials --paddleocr-vl-rec-backend vllm-server --paddleocr-vl-rec-server-url http://localhost:8000/v1 --paddleocr-vl-rec-api-model-name PaddlePaddle/PaddleOCR-VL-1.6
```

원본별 파싱 출력:

- `manifest.json`
- `content.md`
- `chunks.json`
- `document_model.json`
- `parser_output.json`
- `quality_report.json`
- `assets/`

파싱한 자료를 수동으로 다시 씁니다.

```bash
uv run wordbyword --rewrite-vocabulary-guided-source-materials --ollama-model gemma4:31b
uv run wordbyword --rewrite-vocabulary-guided-source-material classroom-printout-studio/class-source-materials/parsed/grade1/example --ollama-model gemma4:31b
uv run wordbyword --rewrite-whole-source-materials --ollama-model gemma4:31b
```

어휘 안내 기반 다시 쓰기의 기본 경로는 `classroom-printout-studio/class-source-materials/parsed/grade1`입니다. `--rewrite-dictionary`를 지정하지 않으면 `wbw-vocabulary-intelligence/live-grade-lexicon/grade1-live-lexicon-canonical.jsonl`이 있을 때 이를 어휘 지침으로 사용하고, 없으면 `build-dictionary/15_final_dictionary.jsonl`을 사용합니다. 번들은 `classroom-printout-studio/rewritten-for-class/grade1-ko-ollama-vocabulary-guided/` 아래에 작성됩니다.

`--build-class-printouts`는 워크플로 상태를 `classroom-printout-studio/class-source-materials/parsed/grade1/.wordbyword-class-printout-ledger.json`에 저장합니다. 원장은 원본 파일 SHA-256을 키로 사용합니다. 따라서 파일명만 바뀌고 내용이 동일한 원본은 인쇄물 생성에 성공한 뒤 건너뛰지만, 같은 파일명이어도 내용 바이트가 바뀌면 새 원본으로 처리합니다. 원장이 건너뛸 원본의 파싱, 다시 쓰기, 인쇄 출력을 다시 생성하려면 `--force-class-printouts`를 사용하세요.

다시 쓴 자료를 수동으로 인쇄합니다.

```bash
uv run wordbyword --print-class-materials --ollama-model gemma4:31b
uv run wordbyword --print-class-material classroom-printout-studio/rewritten-for-class/grade1-ko-ollama-vocabulary-guided/example --ollama-model gemma4:31b
uv run wordbyword --print-class-materials --print-body-font-size 13
uv run wordbyword --print-class-materials --print-font-path /System/Library/Fonts/AppleSDGothicNeo.ttc
```

원본별 인쇄 가능 출력:

- `print_plan.json`
- `content.md`
- `material.docx`
- `attempts.jsonl`
- `failures.jsonl`
- `quality_report.json`
- `manifest.json`
- `assets/`

인쇄 단계에서는 Ollama에 엄격한 JSON 인쇄 계획을 요청한 뒤 로컬에서 DOCX를 렌더링합니다. 모델이 Word 파일을 직접 작성하지는 않습니다.

## 수업 보관 자료 및 사용 신호

사용한 수업 인쇄물을 다음 위치에 넣으세요.

```text
wbw-vocabulary-intelligence/class-session-archive/
```

보관 파일 이름 변경을 미리 확인합니다.

```bash
uv run wordbyword --rename-class-session-archive --ollama-model gemma4:31b
```

보관 파일 이름 변경을 적용합니다.

```bash
uv run wordbyword --rename-class-session-archive --apply-archive-renames --ollama-model gemma4:31b
```

이름 변경 명령은 바로 아래에 있는 `.docx` 및 `.pdf` 파일만 처리합니다. 문서 내용을 수정하거나 파일을 삭제하지 않으며, 보관 폴더 밖으로 파일을 이동하거나 기존 파일을 덮어쓰지 않습니다.

보관소에 중복된 수업 인쇄물이 있으면 이름 변경 및 사용 신호 명령은 출력을 작성하기 전에 중지합니다. 중복 파일을 대화형으로 검토하고 격리합니다.

```bash
uv run wordbyword --resolve-archive-duplicates
```

격리하기 전에 Ollama로 보수적인 유사 중복 후보까지 검토하려면 유사 중복 계층을 포함하세요.

```bash
uv run wordbyword --resolve-archive-duplicates --include-near-duplicates --ollama-model gemma4:31b
```

명시적인 비대화형 정리를 수행하려면 권장 격리 계획을 적용하세요.

```bash
uv run wordbyword --resolve-archive-duplicates --yes
```

중복 처리기는 파일을 삭제하지 않습니다. 중복 파일을 `wbw-vocabulary-intelligence/class-session-archive/.wordbyword-duplicates/`로 이동하고 감사 매니페스트를 작성합니다.

관리되는 보관 파일명에서 결정론적 사용 신호를 빌드합니다.

```bash
uv run wordbyword --build-usage-signals
```

현재 학년별 어휘집을 업데이트합니다.

```bash
uv run wordbyword --update-live-lexicon
```

`--build-usage-signals`는 `wbw-vocabulary-intelligence/usage-signal-build/` 아래에 `00`~`09` 사용 산출물을 작성합니다. `--update-live-lexicon`은 `wbw-vocabulary-intelligence/live-grade-lexicon/grade1-live-lexicon-canonical.jsonl`, `grade1-live-lexicon-supplemental.jsonl` 및 일치하지 않은 원시 사용 감사 파일을 관리합니다.

## 수업 후 보고서

관리되는 보관 수업 인쇄물 한 개에서 성인용 한국어 보고서 하나를 생성합니다.

```bash
uv run wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --ollama-model gemma4:31b
```

입력은 파일명이 이미 관리되는 보관 패턴과 일치하는 `.docx` 또는 `.pdf` 파일 하나여야 합니다. 경로 없는 파일명은 `wbw-vocabulary-intelligence/class-session-archive/` 안에서 찾습니다. 특수하거나 수동인 워크플로에는 명시적 경로도 계속 지원됩니다. 파일명이 관리되는 보관 패턴과 일치하지 않으면 먼저 보관 파일명 변경 시험 실행과 적용 단계를 수행하세요.

보고서 출력은 `wbw-vocabulary-intelligence/after-class-reports/grade1-ko-a4-ollama/<source-id>/` 아래에 작성됩니다.

- `heuristic-analysis/`
- `report_plan.json`
- `content.md`
- `after_class_report.docx`
- `attempts.jsonl`
- `failures.jsonl`
- `quality_report.json`
- `manifest.json`

유용한 재정의 옵션:

```bash
uv run wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --after-class-report-output-root wbw-vocabulary-intelligence/after-class-reports/grade1-ko-a4-ollama
uv run wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --after-class-report-font-path /System/Library/Fonts/AppleSDGothicNeo.ttc
```

## LLM 설정

모델 기반 워크플로는 기본적으로 로컬 Ollama를 사용합니다.

```bash
uv run wordbyword --build-dictionary --llm-provider ollama --llm-model gemma4:31b
uv run wordbyword --build-dictionary --llm-provider ollama --llm-model gemma4:31b --llm-base-url http://localhost:11434
```

이전 Ollama 플래그도 별칭으로 계속 작동합니다.

```bash
uv run wordbyword --build-dictionary --ollama-model gemma4:31b --ollama-base-url http://localhost:11434
```

호스팅되는 텍스트 전용 LLM 작업에는 제공자를 선택하고 환경에 해당 API 키를 설정하세요. API 키는 프로젝트 파일에 저장되지 않습니다.

```bash
export OPENAI_API_KEY=...
uv run wordbyword --build-dictionary --llm-provider openai --llm-model gpt-4o

export ANTHROPIC_API_KEY=...
uv run wordbyword --rename-class-session-archive --llm-provider claude --llm-model claude-3-5-sonnet-20240620

export GEMINI_API_KEY=...
uv run wordbyword --generate-missing-meanings --llm-provider google --llm-model gemini-1.5-pro
```

제공자 별칭도 사용할 수 있습니다. `anthropic`은 `claude`로, `gemini`는 `google`로 매핑됩니다. 이번 릴리스에서 Ollama 이외의 제공자는 텍스트 중심으로 작동합니다. 이미지 기반 원본 자료 다시 쓰기 및 인쇄용 이미지 선택에는 여전히 비전 기능이 있는 Ollama 모델이 필요합니다.

## 검증

```bash
uv run pytest -q
uv run ruff check .
```

로컬에서 `gemma4:31b`를 사용할 수 있을 때만 Ollama 실시간 스모크 테스트를 실행하세요.

```bash
WORDBYWORD_RUN_OLLAMA_LIVE_TESTS=1 uv run python -m unittest tests.test_llm.LiveOllamaIntegrationTest
```
