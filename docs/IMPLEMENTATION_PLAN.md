# Document Driven Development 플러그인 구현안

## 1. 제품 정의

이 플러그인은 PRD를 출발점으로 사용자와 기술 결정을 합의하고, 이번
프로젝트에 실제로 필요한 문서만 동적으로 선택해 개별 작성·승인한 뒤,
승인 문서를 구현의 실행 조건으로 만드는 Codex·Claude Code·Antigravity 공용
플러그인이다.

고정하는 것은 문서 목록이 아니라 다음 절차다.

1. PRD와 기존 저장소를 읽는다.
2. 한 번에 질문 하나로 미확정 결정을 드러낸다.
3. 중요한 결정에는 2~3개 접근과 장단점을 제시한다.
4. 결정 결과를 담을 최소 문서 그래프를 제안한다.
5. 사용자가 그래프를 승인한 뒤에만 매니페스트를 만든다.
6. 문서를 하나씩 대화형으로 작성하고 명시적으로 승인받는다.
7. 관련 승인 문서를 잠그기 전에는 구현을 허용하지 않는다.
8. 설계가 바뀌면 문서 재승인과 새 잠금 후 구현을 계속한다.

## 2. 동적 문서 그래프

에이전트는 영속 데이터, 인터페이스, 사용자·역할·테넌트, 실시간·동시성,
배포 제약, 장애 복구·운영, 개인정보·보안, 성능·비용, 벤더 종속성, AI 평가·
근거 추적 등의 관점을 살핀다. 그러나 관점 하나를 문서 하나로 자동 변환하지
않는다. 작은 프로젝트에서는 합치고, 독립 승인·담당·변경 주기가 필요할 때만
분리한다.

`docs/document-manifest.json`은 합의된 결과만 기록한다.

- artifact id, 경로, 목적
- `informed_by`, `depends_on`
- 구현 관련성을 나타내는 동적 `required_for` 태그
- `proposed → drafting → reviewed → approved → superseded` 상태
- 승인자, 승인 시각, 승인된 콘텐츠 해시

## 3. 스킬 구성

문서 종류마다 고정 스킬을 만들지 않고 역할 기반 공용 스킬 여섯 개를 둔다.

- `discover-document-graph`: PRD 인터뷰와 문서 그래프 합의
- `author-project-document`: 선택된 문서 한 개 작성·검토·승인
- `generate-development-harness`: 저장소 하네스 설치
- `prepare-documented-change`: 관련 문서 선택·검증·해시 잠금
- `implement-from-documents`: 잠긴 승인 문서를 근거로 구현
- `verify-document-driven-change`: 문서·코드·테스트 추적성과 드리프트 검증

## 4. 하네스 구조

하네스 생성기는 기존 파일을 지우지 않고 관리 블록과 설정을 병합한다.

```text
AGENTS.md
CLAUDE.md
docs/document-manifest.json
.document-driven/
├── policy.json
├── context-lock.json
├── traceability.json
└── bin/
    ├── docflow.py
    ├── pre_tool_guard.py
    ├── session_context.py
    ├── codex_pre_tool.py
    ├── codex_session_context.py
    ├── claude_pre_tool.py
    ├── claude_session_context.py
    ├── antigravity_pre_tool.py
    └── antigravity_pre_invocation.py
.codex/hooks.json
.claude/settings.json
.agents/hooks.json
.github/workflows/document-driven-development.yml  # GitHub 선택 시
```

## 5. 강제 계층

### 에이전트 지침

`AGENTS.md`와 `CLAUDE.md`에 구현 전 준비, 승인 상태, 변경 시 재승인,
완료 시 추적성·검증 규칙을 영구 기록한다.

### Hook

문서·하네스가 아닌 파일을 쓰기 전에 유효한 컨텍스트 잠금을 검사한다.
잠금은 매니페스트, PRD, 선택된 승인 문서의 SHA-256을 기록한다. 문서나
매니페스트가 바뀌면 즉시 무효화된다. 경로별 필수 문서는 프로젝트 정책의
동적 `path_rules`로 추가할 수 있다.

정책 판정은 공통 엔진에서 수행하되 플랫폼 연결은 분리한다.

| 플랫폼 | 저장소 설정 | 쓰기 도구 | 컨텍스트 주입 |
|---|---|---|---|
| Codex | `.codex/hooks.json` | `Bash`, `apply_patch`, `Edit`, `Write` | `SessionStart` |
| Claude Code | `.claude/settings.json` | `Bash`, `Edit`, `Write` | `SessionStart` |
| Antigravity | `.agents/hooks.json` | `run_command`, `write_to_file`, `replace_file_content`, `multi_replace_file_content` | `PreInvocation` |

Codex와 Claude Code는 snake_case 훅 입력과 `hookSpecificOutput` 차단 응답을
사용한다. Antigravity는 camelCase `toolCall` 입력과 `decision: deny` 응답을
사용하므로 별도 어댑터가 정규화한다. 사용자가 비활성화한 Hook이나 플랫폼이
노출하지 않는 쓰기까지 완전히 통제할 수는 없으므로 Hook은 로컬 가드레일이고,
최종 강제는 CI가 맡는다.

### CI

CI는 매니페스트 스키마, 문서 승인 해시, 컨텍스트 잠금, 요구사항 ID,
요구사항→문서→코드→테스트 추적성, 기준 커밋 이후 변경된 모든 구현 경로의
추적성 포함 여부, 경로별 필수 문서 포함 여부를 검사한다.
플랫폼에 종속적인 의미론적 계약 검증은 프로젝트가 별도 테스트 명령으로
추가한다.

## 6. MVP 완료 기준

- Codex, Claude Code, Antigravity에서 같은 스킬을 발견할 수 있다.
- 고정 문서 목록 없이 승인된 문서 그래프를 만들 수 있다.
- 승인 후 문서 변경이 잠금과 검증을 실패시킨다.
- 잠금 없이 구현 파일 쓰기를 Hook이 거부한다.
- 준비된 작업은 요구사항과 관련 승인 문서를 잠근다.
- 추적성에 코드나 테스트가 빠지면 최종 검증이 실패한다.
- 기존 `AGENTS.md`, `CLAUDE.md`와 세 플랫폼 Hook 설정을 보존하며 하네스를
  재실행할 수 있다.
- 각 스킬, 플러그인, 샘플 시나리오가 자동 검증을 통과한다.

## 7. MVP 이후

- GitLab, Jenkins, Buildkite용 CI 어댑터
- OpenAPI·DB migration·IaC 등 도메인별 의미론적 드리프트 검사기
- 여러 동시 작업을 위한 lock 디렉터리와 PR 단위 추적성
- 문서 그래프 시각화와 변경 영향 분석
- 팀 승인자·서명·보호 브랜치 연동
